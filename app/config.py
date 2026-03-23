from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from app.models import RemoteMode


class ControlPlaneConfig(BaseModel):
    base_url: Optional[str] = None
    api_key: str
    api_key_header: str = "X-API-Key"
    heartbeat_url: str
    job_pull_url: str
    verify_ssl: bool = True
    request_timeout_seconds: int = 30
    extra_headers: dict[str, str] = Field(default_factory=dict)


class PollingConfig(BaseModel):
    heartbeat_interval_seconds: int = 30
    job_poll_interval_seconds: int = 5
    idle_sleep_seconds: int = 2
    error_backoff_seconds: int = 10


class LoggingConfig(BaseModel):
    level: str = "INFO"
    file_path: str = "logs/connector.log"
    max_bytes: int = 5_000_000
    backup_count: int = 5


class ConnectorIdentity(BaseModel):
    connector_id: str
    connector_name: str = "OpenClaw Connector"
    customer_id: Optional[str] = None
    site_id: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class CapabilityConfig(BaseModel):
    supported_modes: list[RemoteMode] = Field(default_factory=lambda: [RemoteMode.ssh, RemoteMode.winrm])
    supported_os: list[Literal["linux", "windows"]] = Field(default_factory=lambda: ["linux", "windows"])


class RuntimeConfig(BaseModel):
    max_concurrent_jobs: int = 1
    default_shell_prefix_windows: str = "powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -Command"


class ConnectorSettings(BaseModel):
    identity: ConnectorIdentity
    control_plane: ControlPlaneConfig
    polling: PollingConfig = Field(default_factory=PollingConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    capabilities: CapabilityConfig = Field(default_factory=CapabilityConfig)
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def load_settings() -> ConnectorSettings:
    config_path = Path(os.getenv("CONNECTOR_CONFIG_PATH", "config/config.json")).expanduser().resolve()
    data = _load_json(config_path)

    env_overlay: dict[str, Any] = {}
    if os.getenv("CONNECTOR_ID"):
        env_overlay.setdefault("identity", {})["connector_id"] = os.getenv("CONNECTOR_ID")
    if os.getenv("CONNECTOR_NAME"):
        env_overlay.setdefault("identity", {})["connector_name"] = os.getenv("CONNECTOR_NAME")
    if os.getenv("CONNECTOR_API_KEY"):
        env_overlay.setdefault("control_plane", {})["api_key"] = os.getenv("CONNECTOR_API_KEY")
    if os.getenv("CONNECTOR_HEARTBEAT_URL"):
        env_overlay.setdefault("control_plane", {})["heartbeat_url"] = os.getenv("CONNECTOR_HEARTBEAT_URL")
    if os.getenv("CONNECTOR_JOB_PULL_URL"):
        env_overlay.setdefault("control_plane", {})["job_pull_url"] = os.getenv("CONNECTOR_JOB_PULL_URL")

    merged = _deep_merge(data, env_overlay)
    return ConnectorSettings.model_validate(merged)
