from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, model_validator


class RemoteMode(str, Enum):
    ssh = "ssh"
    winrm = "winrm"


class OSType(str, Enum):
    linux = "linux"
    windows = "windows"
    unknown = "unknown"


class AuthMethod(str, Enum):
    password = "password"
    private_key = "private_key"


class WinRMTransport(str, Enum):
    ntlm = "ntlm"
    basic = "basic"
    kerberos = "kerberos"
    credssp = "credssp"


class WinRMScheme(str, Enum):
    http = "http"
    https = "https"


class CallbackEventType(str, Enum):
    progress = "progress"
    final = "final"


class ConsoleRenderMode(str, Enum):
    replace = "replace"
    append = "append"


class ErrorDetailLevel(str, Enum):
    summary = "summary"
    standard = "standard"
    verbose = "verbose"


class CallbackConfig(BaseModel):
    url: str = Field(..., description="Vollständige Callback-URL")
    api_key: Optional[str] = Field(default=None, description="Optionaler API-Key")
    api_key_header: str = Field(default="X-API-Key")
    extra_headers: dict[str, str] = Field(default_factory=dict)


class TargetConfig(BaseModel):
    mode: RemoteMode = Field(default=RemoteMode.ssh)
    host: str
    port: Optional[int] = None
    username: str
    os_type: OSType = OSType.unknown
    auth_method: AuthMethod
    password: Optional[str] = None
    private_key: Optional[str] = None
    private_key_b64: Optional[str] = None
    private_key_passphrase: Optional[str] = None
    verify_host_key: bool = False
    winrm_transport: WinRMTransport = WinRMTransport.ntlm
    winrm_scheme: Optional[WinRMScheme] = None
    winrm_path: str = "wsman"
    winrm_server_cert_validation: Literal["validate", "ignore"] = "ignore"

    @model_validator(mode="after")
    def validate_target(self) -> "TargetConfig":
        if self.port is None:
            self.port = 22 if self.mode == RemoteMode.ssh else 5985

        if self.mode == RemoteMode.ssh:
            if self.auth_method == AuthMethod.password and not self.password:
                raise ValueError("password is required when mode=ssh and auth_method=password")
            if self.auth_method == AuthMethod.private_key and not (self.private_key or self.private_key_b64):
                raise ValueError("private_key or private_key_b64 is required when mode=ssh and auth_method=private_key")
        else:
            if self.auth_method != AuthMethod.password:
                raise ValueError("winrm currently supports only auth_method=password")
            if not self.password:
                raise ValueError("password is required when mode=winrm")
            if self.private_key or self.private_key_b64 or self.private_key_passphrase:
                raise ValueError("private key fields are not supported for mode=winrm")
            if self.os_type == OSType.unknown:
                self.os_type = OSType.windows
            if self.winrm_scheme is None:
                self.winrm_scheme = WinRMScheme.https if self.port == 5986 else WinRMScheme.http
        return self


class RemoteExecuteRequest(BaseModel):
    tenant_id: str
    command_id: str
    command: str
    target: TargetConfig
    callback: CallbackConfig
    correlation_id: Optional[str] = None
    timeout_seconds: int = Field(default=120)
    connection_timeout_seconds: int = Field(default=15, ge=3, le=120)
    allocate_pty: bool = Field(default=False)
    working_directory: Optional[str] = None
    shell_prefix: Optional[str] = None
    progress_updates_enabled: bool = Field(default=True)
    progress_callback_interval_ms: int = Field(default=2000, ge=250, le=60000)
    suppress_terminal_animations: bool = Field(default=True)
    max_callback_console_chars: int = Field(default=12000, ge=500, le=250000)
    treat_nonzero_exit_code_as_error: bool = Field(default=True)
    callback_error_detail_level: ErrorDetailLevel = Field(default=ErrorDetailLevel.standard)
    callback_include_debug_context: bool = Field(default=True)
    redact_secrets_in_output: bool = Field(default=True)


class RunnerErrorInfo(BaseModel):
    code: str
    category: str
    phase: str
    message: str
    detail: Optional[str] = None
    retryable: bool = False
    suggested_action: Optional[str] = None
    debug_context: Optional[dict[str, Any]] = None


class TargetMeta(BaseModel):
    mode: RemoteMode
    host: str
    port: int
    username: str
    os_type: OSType


class CallbackDeliveryInfo(BaseModel):
    ok: bool = True
    attempt_count: int = 1
    permanent_error: Optional[str] = None


class RemoteCallbackPayload(BaseModel):
    tenant_id: str
    command_id: str
    runner_job_id: str
    event_type: CallbackEventType
    status: Literal["running", "completed", "error"]
    sequence: int
    console_snapshot: Optional[str] = None
    console_render_mode: ConsoleRenderMode = ConsoleRenderMode.replace
    stdout: Optional[str] = None
    stderr: Optional[str] = None
    exit_code: Optional[int] = None
    output_truncated: bool = False
    error: Optional[RunnerErrorInfo] = None
    callback_delivery: Optional[CallbackDeliveryInfo] = None
    target: Optional[TargetMeta] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    duration_ms: Optional[int] = None


class ConnectorHeartbeat(BaseModel):
    connector_id: str
    connector_name: str
    status: Literal["idle", "running", "error", "starting"]
    current_job_id: Optional[str] = None
    current_mode: Optional[RemoteMode] = None
    last_error: Optional[str] = None
    supported_modes: list[RemoteMode] = Field(default_factory=lambda: [RemoteMode.ssh, RemoteMode.winrm])
    metadata: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class JobPullResponse(BaseModel):
    job: Optional[RemoteExecuteRequest] = None
    status: Optional[str] = None
