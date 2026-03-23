from __future__ import annotations

import logging
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Optional

from app.callbacks import try_post_callback_sync
from app.config import ConnectorSettings
from app.console_utils import build_error_info
from app.control_plane import ControlPlaneClient
from app.errors import classify_exception
from app.models import (
    CallbackDeliveryInfo,
    CallbackEventType,
    ConnectorHeartbeat,
    RemoteCallbackPayload,
    RemoteExecuteRequest,
    TargetMeta,
)
from app.runtime import runtime_settings
from app.ssh_client import execute_ssh_command
from app.winrm_client import execute_winrm_command

logger = logging.getLogger(__name__)


class ConnectorService:
    def __init__(self, settings: ConnectorSettings) -> None:
        self.settings = settings
        self.control_plane = ControlPlaneClient(settings)
        self.stop_event = threading.Event()
        self.state_lock = threading.Lock()
        self.status: str = "starting"
        self.current_job_id: Optional[str] = None
        self.current_mode = None
        self.last_error: Optional[str] = None
        runtime_settings.runner_callback_timeout_seconds = settings.control_plane.request_timeout_seconds
        runtime_settings.runner_callback_max_retries = 3
        runtime_settings.runner_verify_ssl = settings.control_plane.verify_ssl

    def set_state(self, *, status: str, current_job_id: Optional[str] = None, current_mode=None, last_error: Optional[str] = None) -> None:
        with self.state_lock:
            self.status = status
            self.current_job_id = current_job_id
            self.current_mode = current_mode
            self.last_error = last_error

    def heartbeat_payload(self) -> ConnectorHeartbeat:
        with self.state_lock:
            return ConnectorHeartbeat(
                connector_id=self.settings.identity.connector_id,
                connector_name=self.settings.identity.connector_name,
                status=self.status,
                current_job_id=self.current_job_id,
                current_mode=self.current_mode,
                last_error=self.last_error,
                supported_modes=self.settings.capabilities.supported_modes,
                metadata={
                    **self.settings.identity.metadata,
                    "customer_id": self.settings.identity.customer_id,
                    "site_id": self.settings.identity.site_id,
                    "supported_os": self.settings.capabilities.supported_os,
                },
            )

    def heartbeat_loop(self) -> None:
        logger.info("Heartbeat loop started")
        while not self.stop_event.is_set():
            try:
                self.control_plane.post_heartbeat(self.heartbeat_payload())
            except Exception as exc:  # noqa: BLE001
                logger.warning("Heartbeat failed: %s", exc)
            self.stop_event.wait(self.settings.polling.heartbeat_interval_seconds)
        logger.info("Heartbeat loop stopped")

    def execute_job(self, request: RemoteExecuteRequest) -> None:
        self.set_state(status="running", current_job_id=request.command_id, current_mode=request.target.mode)
        runner_job_id = str(uuid.uuid4())
        started_at = datetime.now(timezone.utc)
        target_meta = TargetMeta(
            mode=request.target.mode,
            host=request.target.host,
            port=request.target.port or (22 if request.target.mode.value == "ssh" else 5985),
            username=request.target.username,
            os_type=request.target.os_type,
        )

        def progress_callback(payload: dict) -> tuple[bool, str | None]:
            ok, error_text = try_post_callback_sync(request.callback, payload)
            return ok, error_text

        try:
            if request.target.mode.value == "ssh":
                result = execute_ssh_command(request, runner_job_id, progress_callback if request.progress_updates_enabled else None)
            else:
                result = execute_winrm_command(request, runner_job_id, progress_callback if request.progress_updates_enabled else None)

            final_status = "completed"
            if request.treat_nonzero_exit_code_as_error and result.exit_code != 0:
                final_status = "error"

            payload = RemoteCallbackPayload(
                tenant_id=request.tenant_id,
                command_id=request.command_id,
                runner_job_id=runner_job_id,
                event_type=CallbackEventType.final,
                status=final_status,
                sequence=result.sequence + 1,
                console_snapshot=result.console_snapshot,
                stdout=result.stdout,
                stderr=result.stderr,
                exit_code=result.exit_code,
                output_truncated=result.output_truncated,
                callback_delivery=CallbackDeliveryInfo(
                    ok=result.progress_callback_failures == 0,
                    attempt_count=result.progress_callback_failures + 1,
                    permanent_error=result.last_progress_callback_error,
                ),
                target=target_meta,
                started_at=started_at,
                finished_at=datetime.now(timezone.utc),
                duration_ms=result.duration_ms,
            )
            ok, final_error = try_post_callback_sync(request.callback, payload.model_dump(mode="json"))
            if not ok:
                logger.error("Final callback failed for %s: %s", request.command_id, final_error)
            self.set_state(status="idle", current_job_id=None, current_mode=None, last_error=None)
        except Exception as exc:  # noqa: BLE001
            classified = classify_exception(exc, phase="execute", debug_context={
                "connector_id": self.settings.identity.connector_id,
                "mode": request.target.mode.value,
                "host": request.target.host,
                "port": request.target.port,
            })
            logger.exception("Job execution failed: %s", exc)
            error_payload = RemoteCallbackPayload(
                tenant_id=request.tenant_id,
                command_id=request.command_id,
                runner_job_id=runner_job_id,
                event_type=CallbackEventType.final,
                status="error",
                sequence=1,
                error=build_error_info(request, classified),
                callback_delivery=CallbackDeliveryInfo(ok=True, attempt_count=1),
                target=target_meta,
                started_at=started_at,
                finished_at=datetime.now(timezone.utc),
                duration_ms=int((datetime.now(timezone.utc) - started_at).total_seconds() * 1000),
            )
            ok, final_error = try_post_callback_sync(request.callback, error_payload.model_dump(mode="json"))
            if not ok:
                logger.error("Error callback failed for %s: %s", request.command_id, final_error)
            self.set_state(status="error", current_job_id=None, current_mode=None, last_error=classified.message)
            time.sleep(1)
            self.set_state(status="idle", current_job_id=None, current_mode=None, last_error=classified.message)

    def run_forever(self) -> None:
        heartbeat_thread = threading.Thread(target=self.heartbeat_loop, daemon=True)
        heartbeat_thread.start()
        self.set_state(status="idle")
        logger.info("Connector started: %s", self.settings.identity.connector_id)

        while not self.stop_event.is_set():
            try:
                if self.status == "running":
                    time.sleep(self.settings.polling.idle_sleep_seconds)
                    continue
                job = self.control_plane.pull_job()
                if not job:
                    time.sleep(self.settings.polling.job_poll_interval_seconds)
                    continue
                logger.info("Received job %s for tenant %s in mode %s", job.command_id, job.tenant_id, job.target.mode.value)
                self.execute_job(job)
            except KeyboardInterrupt:
                break
            except Exception as exc:  # noqa: BLE001
                logger.exception("Connector loop error: %s", exc)
                self.set_state(status="error", last_error=str(exc))
                time.sleep(self.settings.polling.error_backoff_seconds)
                self.set_state(status="idle")

        self.stop_event.set()
        heartbeat_thread.join(timeout=3)
