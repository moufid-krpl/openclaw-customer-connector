from __future__ import annotations

import re
from dataclasses import dataclass

from app.models import ErrorDetailLevel, RemoteExecuteRequest, RunnerErrorInfo

ANSI_ESCAPE_RE = re.compile(r"\x1B\[[0-?]*[ -/]*[@-~]")
SECRET_PATTERNS = [
    re.compile(r"(?i)(api[_-]?key\s*[=:]\s*)([^\s\"']+)"),
    re.compile(r"(?i)(token\s*[=:]\s*)([^\s\"']+)"),
    re.compile(r"(?i)(password\s*[=:]\s*)([^\s\"']+)"),
    re.compile(r"(?i)(secret\s*[=:]\s*)([^\s\"']+)"),
]


@dataclass
class ProgressState:
    failures: int = 0
    last_error: str | None = None


class ClassifiedErrorDataProtocol:
    code: str
    category: str
    phase: str
    message: str
    detail: str | None
    retryable: bool
    suggested_action: str | None
    debug_context: dict | None


def normalize_console_text(raw_text: str, suppress_terminal_animations: bool) -> str:
    text = ANSI_ESCAPE_RE.sub("", raw_text)
    text = text.replace("\r\n", "\n")
    if not suppress_terminal_animations:
        return text

    lines: list[str] = []
    current = ""
    for ch in text:
        if ch == "\r":
            current = ""
        elif ch == "\n":
            lines.append(current)
            current = ""
        else:
            current += ch
    if current:
        lines.append(current)
    return "\n".join(lines)


def tail_text(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[-max_chars:]


def redact_secrets(text: str, enabled: bool) -> str:
    if not enabled or not text:
        return text
    redacted = text
    for pattern in SECRET_PATTERNS:
        redacted = pattern.sub(lambda m: f"{m.group(1)}***REDACTED***", redacted)
    return redacted


def build_error_info(request: RemoteExecuteRequest, data: ClassifiedErrorDataProtocol) -> RunnerErrorInfo:
    detail = None
    debug_context = None

    if request.callback_error_detail_level in {ErrorDetailLevel.standard, ErrorDetailLevel.verbose}:
        detail = data.detail
    if request.callback_error_detail_level == ErrorDetailLevel.verbose and request.callback_include_debug_context:
        debug_context = data.debug_context

    return RunnerErrorInfo(
        code=data.code,
        category=data.category,
        phase=data.phase,
        message=data.message,
        detail=detail,
        retryable=data.retryable,
        suggested_action=data.suggested_action,
        debug_context=debug_context,
    )
