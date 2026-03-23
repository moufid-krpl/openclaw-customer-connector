from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx

from app.runtime import runtime_settings
from app.models import CallbackConfig

logger = logging.getLogger(__name__)


def _build_headers(callback: CallbackConfig) -> dict[str, str]:
    headers = {"Content-Type": "application/json", **callback.extra_headers}
    if callback.api_key:
        headers[callback.api_key_header] = callback.api_key
    return headers


async def post_callback(callback: CallbackConfig, payload: dict[str, Any]) -> None:
    headers = _build_headers(callback)
    last_error: Exception | None = None

    for attempt in range(1, runtime_settings.runner_callback_max_retries + 1):
        try:
            async with httpx.AsyncClient(timeout=runtime_settings.runner_callback_timeout_seconds, verify=runtime_settings.runner_verify_ssl) as client:
                response = await client.post(callback.url, json=payload, headers=headers)
                response.raise_for_status()
                return
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            logger.warning("Async callback attempt %s failed: %s", attempt, exc)
            if attempt < runtime_settings.runner_callback_max_retries:
                await asyncio.sleep(min(attempt * 2, 10))

    if last_error:
        raise last_error


def post_callback_sync(callback: CallbackConfig, payload: dict[str, Any]) -> None:
    headers = _build_headers(callback)
    last_error: Exception | None = None

    for attempt in range(1, runtime_settings.runner_callback_max_retries + 1):
        try:
            with httpx.Client(timeout=runtime_settings.runner_callback_timeout_seconds, verify=runtime_settings.runner_verify_ssl) as client:
                response = client.post(callback.url, json=payload, headers=headers)
                response.raise_for_status()
                return
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            logger.warning("Sync callback attempt %s failed: %s", attempt, exc)
            if attempt < runtime_settings.runner_callback_max_retries:
                time.sleep(min(attempt * 2, 10))

    if last_error:
        raise last_error


def try_post_callback_sync(callback: CallbackConfig, payload: dict[str, Any]) -> tuple[bool, str | None]:
    try:
        post_callback_sync(callback, payload)
        return True, None
    except Exception as exc:  # noqa: BLE001
        logger.error("Progress callback delivery failed permanently: %s", exc)
        return False, str(exc)
