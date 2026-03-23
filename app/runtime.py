from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RuntimeSettings:
    runner_callback_timeout_seconds: int = 20
    runner_callback_max_retries: int = 3
    runner_verify_ssl: bool = True


runtime_settings = RuntimeSettings()
