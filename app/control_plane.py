from __future__ import annotations

import logging
from typing import Any, Optional

import httpx

from app.config import ConnectorSettings
from app.models import ConnectorHeartbeat, JobPullResponse, RemoteExecuteRequest

logger = logging.getLogger(__name__)


class ControlPlaneClient:
    def __init__(self, settings: ConnectorSettings) -> None:
        self.settings = settings

    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            self.settings.control_plane.api_key_header: self.settings.control_plane.api_key,
        }
        headers.update(self.settings.control_plane.extra_headers)
        return headers

    def post_heartbeat(self, payload: ConnectorHeartbeat) -> None:
        with httpx.Client(timeout=self.settings.control_plane.request_timeout_seconds, verify=self.settings.control_plane.verify_ssl) as client:
            response = client.post(
                self.settings.control_plane.heartbeat_url,
                headers=self._headers(),
                json=payload.model_dump(mode="json"),
            )
            response.raise_for_status()

    def pull_job(self) -> Optional[RemoteExecuteRequest]:
        body = {
            "connector_id": self.settings.identity.connector_id,
            "connector_name": self.settings.identity.connector_name,
            "customer_id": self.settings.identity.customer_id,
            "site_id": self.settings.identity.site_id,
            "supported_modes": [m.value for m in self.settings.capabilities.supported_modes],
            "supported_os": self.settings.capabilities.supported_os,
            "metadata": self.settings.identity.metadata,
        }
        with httpx.Client(timeout=self.settings.control_plane.request_timeout_seconds, verify=self.settings.control_plane.verify_ssl) as client:
            response = client.post(
                self.settings.control_plane.job_pull_url,
                headers=self._headers(),
                json=body,
            )
            if response.status_code == 204:
                return None
            response.raise_for_status()
            data: dict[str, Any] = response.json()
            if not data:
                return None
            if data.get("status") in {"idle", "empty", "no_job"}:
                return None
            if "job" in data and data["job"]:
                return RemoteExecuteRequest.model_validate(data["job"])
            # allow the endpoint to return the job directly
            if "tenant_id" in data and "target" in data and "callback" in data:
                return RemoteExecuteRequest.model_validate(data)
            logger.warning("Unknown job pull response shape: %s", data)
            return None
