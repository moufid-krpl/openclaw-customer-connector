from __future__ import annotations

import shlex
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from winrm.exceptions import WinRMOperationTimeoutError
from winrm.protocol import Protocol

from app.runtime import runtime_settings
from app.console_utils import ProgressState, normalize_console_text, redact_secrets, tail_text
from app.errors import ConnectorExecutionError, classify_exception
from app.models import (
    CallbackEventType,
    CallbackDeliveryInfo,
    ConsoleRenderMode,
    RemoteCallbackPayload,
    RemoteExecuteRequest,
    TargetMeta,
)


@dataclass
class WinRMExecutionResult:
    stdout: str
    stderr: str
    console_snapshot: str
    exit_code: int
    duration_ms: int
    output_truncated: bool
    sequence: int
    progress_callback_failures: int
    last_progress_callback_error: str | None


def _build_endpoint(request: RemoteExecuteRequest) -> str:
    scheme = request.target.winrm_scheme.value
    path = request.target.winrm_path.strip("/")
    return f"{scheme}://{request.target.host}:{request.target.port}/{path}"


def _build_command_and_args(request: RemoteExecuteRequest) -> tuple[str, list[str]]:
    command = request.command
    if request.shell_prefix:
        parts = shlex.split(request.shell_prefix, posix=False)
        if not parts:
            raise ValueError("shell_prefix for winrm is empty")
        return parts[0], [*parts[1:], command]

    return (
        "powershell.exe",
        ["-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", command],
    )


def execute_winrm_command(
    request: RemoteExecuteRequest,
    runner_job_id: str,
    progress_callback: Callable[[dict], tuple[bool, str | None]] | None = None,
) -> WinRMExecutionResult:
    start = time.perf_counter()
    started_at = datetime.now(timezone.utc)
    progress_state = ProgressState()
    sequence = 0
    last_sent_snapshot = ""
    last_callback_at = 0.0
    stdout_chunks: list[str] = []
    stderr_chunks: list[str] = []
    merged_chunks: list[str] = []
    output_truncated = False
    shell_id = None
    command_id = None

    try:
        endpoint = _build_endpoint(request)
        protocol = Protocol(
            endpoint=endpoint,
            transport=request.target.winrm_transport.value,
            username=request.target.username,
            password=request.target.password,
            server_cert_validation=request.target.winrm_server_cert_validation,
            read_timeout_sec=max(request.connection_timeout_seconds + 5, 30),
            operation_timeout_sec=min(max(request.connection_timeout_seconds, 10), request.timeout_seconds),
        )
    except Exception as exc:  # noqa: BLE001
        raise ConnectorExecutionError(**classify_exception(exc, phase="winrm_connect_setup", debug_context={
            "mode": request.target.mode.value,
            "host": request.target.host,
            "port": request.target.port,
            "transport": request.target.winrm_transport.value,
            "scheme": request.target.winrm_scheme.value,
        }).__dict__) from exc

    try:
        try:
            shell_id = protocol.open_shell(working_directory=request.working_directory)
        except Exception as exc:  # noqa: BLE001
            raise ConnectorExecutionError(**classify_exception(exc, phase="connect", debug_context={
                "mode": request.target.mode.value,
                "host": request.target.host,
                "port": request.target.port,
                "transport": request.target.winrm_transport.value,
            }).__dict__) from exc

        command, arguments = _build_command_and_args(request)

        # Send one immediate progress event so the dashboard sees a running state even before output exists.
        if request.progress_updates_enabled and progress_callback:
            sequence += 1
            initial_snapshot = f"WinRM-Verbindung aufgebaut. Befehl wird gestartet: {command}"
            payload = RemoteCallbackPayload(
                tenant_id=request.tenant_id,
                command_id=request.command_id,
                runner_job_id=runner_job_id,
                event_type=CallbackEventType.progress,
                status="running",
                sequence=sequence,
                console_render_mode=ConsoleRenderMode.replace,
                console_snapshot=initial_snapshot,
                spinner_suppressed=request.suppress_terminal_animations,
                stdout="",
                stderr="",
                exit_code=None,
                started_at=started_at,
                finished_at=None,
                duration_ms=int((time.perf_counter() - start) * 1000),
                correlation_id=request.correlation_id,
                output_truncated=False,
                target=TargetMeta(
                    mode=request.target.mode,
                    host=request.target.host,
                    port=request.target.port,
                    username=request.target.username,
                    os_type=request.target.os_type,
                ),
                callback_delivery=CallbackDeliveryInfo(
                    progress_callback_failures=progress_state.failures,
                    last_progress_callback_error=progress_state.last_error,
                ),
            ).model_dump(mode="json")
            ok, err = progress_callback(payload)
            if not ok:
                progress_state.failures += 1
                progress_state.last_error = err
            last_sent_snapshot = initial_snapshot
            last_callback_at = time.perf_counter()

        try:
            command_id = protocol.run_command(shell_id, command, arguments)
        except Exception as exc:  # noqa: BLE001
            raise ConnectorExecutionError(**classify_exception(exc, phase="exec", debug_context={
                "command": command,
                "arguments": arguments,
            }).__dict__) from exc

        deadline = time.perf_counter() + request.timeout_seconds
        command_done = False
        exit_code = -1

        while not command_done:
            try:
                stdout, stderr, exit_code, command_done = protocol._raw_get_command_output(shell_id, command_id)  # noqa: SLF001
            except WinRMOperationTimeoutError:
                stdout = b""
                stderr = b""
                exit_code = -1
                command_done = False
            except Exception as exc:  # noqa: BLE001
                raise ConnectorExecutionError(**classify_exception(exc, phase="exec", debug_context={
                    "command": command,
                    "arguments": arguments,
                }).__dict__) from exc

            if stdout:
                decoded = stdout.decode(errors="replace")
                stdout_chunks.append(decoded)
                merged_chunks.append(decoded)
            if stderr:
                decoded = stderr.decode(errors="replace")
                stderr_chunks.append(decoded)
                merged_chunks.append(decoded)

            now = time.perf_counter()
            raw_merged = "".join(merged_chunks)
            console_snapshot = normalize_console_text(raw_merged, request.suppress_terminal_animations)
            console_snapshot = redact_secrets(console_snapshot, request.redact_secrets_in_output)
            console_snapshot = tail_text(console_snapshot, request.max_callback_console_chars)

            if request.progress_updates_enabled and progress_callback and console_snapshot:
                should_send = (
                    console_snapshot != last_sent_snapshot
                    and (now - last_callback_at) * 1000 >= request.progress_callback_interval_ms
                )
                if should_send:
                    sequence += 1
                    payload = RemoteCallbackPayload(
                        tenant_id=request.tenant_id,
                        command_id=request.command_id,
                        runner_job_id=runner_job_id,
                        event_type=CallbackEventType.progress,
                        status="running",
                        sequence=sequence,
                        console_render_mode=ConsoleRenderMode.replace,
                        console_snapshot=console_snapshot,
                        spinner_suppressed=request.suppress_terminal_animations,
                        stdout="",
                        stderr="",
                        exit_code=None,
                        started_at=started_at,
                        finished_at=None,
                        duration_ms=int((now - start) * 1000),
                        correlation_id=request.correlation_id,
                        output_truncated=False,
                        target=TargetMeta(
                            mode=request.target.mode,
                            host=request.target.host,
                            port=request.target.port,
                            username=request.target.username,
                            os_type=request.target.os_type,
                        ),
                        callback_delivery=CallbackDeliveryInfo(
                            progress_callback_failures=progress_state.failures,
                            last_progress_callback_error=progress_state.last_error,
                        ),
                    ).model_dump(mode="json")
                    ok, err = progress_callback(payload)
                    if not ok:
                        progress_state.failures += 1
                        progress_state.last_error = err
                    last_sent_snapshot = console_snapshot
                    last_callback_at = now

            if time.perf_counter() > deadline:
                raise ConnectorExecutionError(
                    code="winrm_command_timeout",
                    category="timeout",
                    phase="exec",
                    message="Der Remote-Befehl hat das konfigurierte Timeout überschritten.",
                    detail=f"timeout_seconds={request.timeout_seconds}",
                    retryable=True,
                    suggested_action="timeout_seconds erhöhen oder Befehl optimieren.",
                    debug_context={"command": command, "arguments": arguments},
                )

        stdout_text = redact_secrets("".join(stdout_chunks), request.redact_secrets_in_output)
        stderr_text = redact_secrets("".join(stderr_chunks), request.redact_secrets_in_output)
        console_snapshot = normalize_console_text("".join(merged_chunks), request.suppress_terminal_animations)
        console_snapshot = redact_secrets(console_snapshot, request.redact_secrets_in_output)

        combined_len = len(stdout_text) + len(stderr_text)
        if combined_len > runtime_settings.runner_max_output_chars:
            output_truncated = True
            max_stdout = max(0, runtime_settings.runner_max_output_chars // 2)
            max_stderr = max(0, runtime_settings.runner_max_output_chars - max_stdout)
            stdout_text = stdout_text[:max_stdout]
            stderr_text = stderr_text[:max_stderr]

        console_snapshot = tail_text(console_snapshot, request.max_callback_console_chars)
        duration_ms = int((time.perf_counter() - start) * 1000)
        return WinRMExecutionResult(
            stdout=stdout_text,
            stderr=stderr_text,
            console_snapshot=console_snapshot,
            exit_code=int(exit_code),
            duration_ms=duration_ms,
            output_truncated=output_truncated,
            sequence=sequence,
            progress_callback_failures=progress_state.failures,
            last_progress_callback_error=progress_state.last_error,
        )
    finally:
        if command_id is not None and shell_id is not None:
            try:
                protocol.cleanup_command(shell_id, command_id)
            except Exception:
                pass
        if shell_id is not None:
            try:
                protocol.close_shell(shell_id)
            except Exception:
                pass
