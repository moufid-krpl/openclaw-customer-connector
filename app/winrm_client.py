from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

import winrm

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


def _clip_output(stdout_text: str, stderr_text: str) -> tuple[str, str, bool]:
    combined_len = len(stdout_text) + len(stderr_text)
    if combined_len <= runtime_settings.runner_max_output_chars:
        return stdout_text, stderr_text, False

    max_stdout = max(0, runtime_settings.runner_max_output_chars // 2)
    max_stderr = max(0, runtime_settings.runner_max_output_chars - max_stdout)
    return stdout_text[:max_stdout], stderr_text[:max_stderr], True


def execute_winrm_command(
    request: RemoteExecuteRequest,
    runner_job_id: str,
    progress_callback: Callable[[dict], tuple[bool, str | None]] | None = None,
) -> WinRMExecutionResult:
    start = time.perf_counter()
    started_at = datetime.now(timezone.utc)
    progress_state = ProgressState()
    sequence = 0

    endpoint = _build_endpoint(request)

    try:
        session = winrm.Session(
            target=endpoint,
            auth=(request.target.username, request.target.password),
            transport=request.target.winrm_transport.value,
            server_cert_validation=request.target.winrm_server_cert_validation,
            read_timeout_sec=max(request.connection_timeout_seconds + 5, 30),
            operation_timeout_sec=min(max(request.connection_timeout_seconds, 10), request.timeout_seconds),
        )
    except Exception as exc:  # noqa: BLE001
        raise ConnectorExecutionError(
            **classify_exception(
                exc,
                phase="winrm_connect_setup",
                debug_context={
                    "mode": request.target.mode.value,
                    "host": request.target.host,
                    "port": request.target.port,
                    "transport": request.target.winrm_transport.value,
                    "scheme": request.target.winrm_scheme.value,
                },
            ).__dict__
        ) from exc

    logger.info(
        "Prepared WinRM execution | host=%s | port=%s | transport=%s | run_ps=True",
        request.target.host,
        request.target.port,
        request.target.winrm_transport.value,
    )

    if request.progress_updates_enabled and progress_callback:
        sequence += 1
        initial_snapshot = "WinRM-Verbindung aufgebaut. PowerShell-Skript wird ausgeführt."
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

    try:
        response = session.run_ps(request.command)
    except Exception as exc:  # noqa: BLE001
        raise ConnectorExecutionError(
            **classify_exception(
                exc,
                phase="exec",
                debug_context={
                    "command": request.command,
                    "host": request.target.host,
                    "port": request.target.port,
                },
            ).__dict__
        ) from exc

    raw_stdout = response.std_out.decode(errors="replace") if response.std_out else ""
    raw_stderr = response.std_err.decode(errors="replace") if response.std_err else ""

    stdout_text = redact_secrets(raw_stdout, request.redact_secrets_in_output)
    stderr_text = redact_secrets(raw_stderr, request.redact_secrets_in_output)
    stdout_text, stderr_text, output_truncated = _clip_output(stdout_text, stderr_text)

    combined_console = stdout_text
    if stderr_text:
        combined_console = f"{combined_console}\n{stderr_text}" if combined_console else stderr_text

    console_snapshot = normalize_console_text(combined_console, request.suppress_terminal_animations)
    console_snapshot = redact_secrets(console_snapshot, request.redact_secrets_in_output)
    console_snapshot = tail_text(console_snapshot, request.max_callback_console_chars)

    duration_ms = int((time.perf_counter() - start) * 1000)
    exit_code = int(response.status_code)

    logger.info(
        "WinRM command finished | host=%s | exit_code=%s | stdout_len=%s | stderr_len=%s",
        request.target.host,
        exit_code,
        len(stdout_text),
        len(stderr_text),
    )

    if request.progress_updates_enabled and progress_callback and (stdout_text or stderr_text or console_snapshot):
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
            stdout=stdout_text,
            stderr=stderr_text,
            exit_code=None,
            started_at=started_at,
            finished_at=None,
            duration_ms=duration_ms,
            correlation_id=request.correlation_id,
            output_truncated=output_truncated,
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

    return WinRMExecutionResult(
        stdout=stdout_text,
        stderr=stderr_text,
        console_snapshot=console_snapshot,
        exit_code=exit_code,
        duration_ms=duration_ms,
        output_truncated=output_truncated,
        sequence=sequence,
        progress_callback_failures=progress_state.failures,
        last_progress_callback_error=progress_state.last_error,
    )
