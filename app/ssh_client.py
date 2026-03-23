from __future__ import annotations

import base64
import io
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

import paramiko

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

logger = logging.getLogger(__name__)


@dataclass
class SSHExecutionResult:
    stdout: str
    stderr: str
    console_snapshot: str
    exit_code: int
    duration_ms: int
    output_truncated: bool
    sequence: int
    progress_callback_failures: int
    last_progress_callback_error: str | None


def _load_private_key(key_text: str, passphrase: str | None = None) -> paramiko.PKey:
    password = passphrase.encode() if passphrase else None
    key_stream = io.StringIO(key_text)
    key_loaders = [
        paramiko.RSAKey.from_private_key,
        paramiko.ECDSAKey.from_private_key,
        paramiko.Ed25519Key.from_private_key,
        paramiko.DSSKey.from_private_key,
    ]

    for loader in key_loaders:
        key_stream.seek(0)
        try:
            return loader(key_stream, password=password)
        except Exception:  # noqa: BLE001
            continue
    raise ConnectorExecutionError(
        code="invalid_private_key",
        category="validation",
        phase="auth_setup",
        message="Der gelieferte Private Key konnte nicht geladen werden.",
        detail="Das Key-Format oder die Passphrase ist ungültig.",
        retryable=False,
        suggested_action="PEM-Inhalt und Passphrase prüfen.",
    )


def _build_command(request: RemoteExecuteRequest) -> str:
    command = request.command
    if request.working_directory:
        if request.target.os_type == request.target.os_type.windows:
            command = f'cd /d "{request.working_directory}" && {command}'
        else:
            command = f'cd "{request.working_directory}" && {command}'
    if request.shell_prefix:
        return f'{request.shell_prefix} "{command.replace(chr(34), chr(92) + chr(34))}"'
    return command


def execute_ssh_command(
    request: RemoteExecuteRequest,
    runner_job_id: str,
    progress_callback: Callable[[dict], tuple[bool, str | None]] | None = None,
) -> SSHExecutionResult:
    start = time.perf_counter()
    client = paramiko.SSHClient()

    if request.target.verify_host_key:
        client.load_system_host_keys()
        client.set_missing_host_key_policy(paramiko.RejectPolicy())
    else:
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    connect_kwargs = {
        "hostname": request.target.host,
        "port": request.target.port,
        "username": request.target.username,
        "timeout": request.connection_timeout_seconds,
        "banner_timeout": request.connection_timeout_seconds,
        "auth_timeout": request.connection_timeout_seconds,
        "look_for_keys": False,
        "allow_agent": False,
    }

    if request.target.auth_method.value == "password":
        connect_kwargs["password"] = request.target.password
    else:
        key_text = request.target.private_key
        if not key_text and request.target.private_key_b64:
            key_text = base64.b64decode(request.target.private_key_b64.encode()).decode()
        connect_kwargs["pkey"] = _load_private_key(key_text or "", request.target.private_key_passphrase)

    try:
        client.connect(**connect_kwargs)
    except Exception as exc:  # noqa: BLE001
        raise ConnectorExecutionError(**classify_exception(exc, phase="connect", debug_context={
            "mode": request.target.mode.value,
            "host": request.target.host,
            "port": request.target.port,
            "username": request.target.username,
            "os_type": request.target.os_type.value,
        }).__dict__) from exc

    sequence = 0
    progress_state = ProgressState()
    started_at = datetime.now(timezone.utc)

    try:
        try:
            final_command = _build_command(request)
        except Exception as exc:  # noqa: BLE001
            raise ConnectorExecutionError(**classify_exception(exc, phase="build_command", debug_context={
                "working_directory": request.working_directory,
                "shell_prefix": request.shell_prefix,
            }).__dict__) from exc

        logger.info("Executing SSH command for tenant=%s command_id=%s host=%s", request.tenant_id, request.command_id, request.target.host)
        try:
            stdin, stdout_file, stderr_file = client.exec_command(
                final_command,
                timeout=request.timeout_seconds,
                get_pty=request.allocate_pty,
            )
        except Exception as exc:  # noqa: BLE001
            raise ConnectorExecutionError(**classify_exception(exc, phase="exec", debug_context={
                "command": final_command,
                "allocate_pty": request.allocate_pty,
            }).__dict__) from exc

        _ = stdin
        channel = stdout_file.channel

        stdout_chunks: list[str] = []
        stderr_chunks: list[str] = []
        merged_chunks: list[str] = []
        output_truncated = False
        last_sent_snapshot = ""
        last_callback_at = 0.0
        deadline = time.perf_counter() + request.timeout_seconds

        while True:
            received_any = False
            if channel.recv_ready():
                chunk = channel.recv(4096).decode(errors="replace")
                stdout_chunks.append(chunk)
                merged_chunks.append(chunk)
                received_any = True
            if channel.recv_stderr_ready():
                chunk = channel.recv_stderr(4096).decode(errors="replace")
                stderr_chunks.append(chunk)
                merged_chunks.append(chunk)
                received_any = True

            now = time.perf_counter()
            raw_merged = "".join(merged_chunks)
            console_snapshot = normalize_console_text(raw_merged, request.suppress_terminal_animations)
            console_snapshot = redact_secrets(console_snapshot, request.redact_secrets_in_output)
            console_snapshot = tail_text(console_snapshot, request.max_callback_console_chars)

            if request.progress_updates_enabled and progress_callback:
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

            if channel.exit_status_ready() and not channel.recv_ready() and not channel.recv_stderr_ready():
                break

            if now > deadline:
                raise ConnectorExecutionError(
                    code="ssh_command_timeout",
                    category="timeout",
                    phase="exec",
                    message="Der Remote-Befehl hat das konfigurierte Timeout überschritten.",
                    detail=f"timeout_seconds={request.timeout_seconds}",
                    retryable=True,
                    suggested_action="timeout_seconds erhöhen oder Befehl optimieren.",
                    debug_context={"command": final_command},
                )

            if not received_any:
                time.sleep(0.15)

        exit_code = channel.recv_exit_status()
        stdout = redact_secrets("".join(stdout_chunks), request.redact_secrets_in_output)
        stderr = redact_secrets("".join(stderr_chunks), request.redact_secrets_in_output)
        console_snapshot = normalize_console_text("".join(merged_chunks), request.suppress_terminal_animations)
        console_snapshot = redact_secrets(console_snapshot, request.redact_secrets_in_output)

        combined_len = len(stdout) + len(stderr)
        if combined_len > runtime_settings.runner_max_output_chars:
            output_truncated = True
            max_stdout = max(0, runtime_settings.runner_max_output_chars // 2)
            max_stderr = max(0, runtime_settings.runner_max_output_chars - max_stdout)
            stdout = stdout[:max_stdout]
            stderr = stderr[:max_stderr]

        console_snapshot = tail_text(console_snapshot, request.max_callback_console_chars)
        duration_ms = int((time.perf_counter() - start) * 1000)
        return SSHExecutionResult(
            stdout=stdout,
            stderr=stderr,
            console_snapshot=console_snapshot,
            exit_code=exit_code,
            duration_ms=duration_ms,
            output_truncated=output_truncated,
            sequence=sequence,
            progress_callback_failures=progress_state.failures,
            last_progress_callback_error=progress_state.last_error,
        )
    finally:
        client.close()
