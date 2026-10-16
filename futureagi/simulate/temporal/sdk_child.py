from __future__ import annotations

import asyncio
import re
from collections.abc import Mapping
from typing import Any

SDK_CHILD_MODULE = "fi.alk.providers.entrypoint"
SDK_CHILD_KEY_ENV = "FI_PROVIDER_API_KEY"
SDK_CHILD_PROTOCOL = 1

KEYED_OPERATIONS = frozenset({"read_setup", "read_call"})
KEYLESS_OPERATIONS = frozenset({"describe", "convert_calls"})
ENVELOPE_NAMES = frozenset({"protocol", "op", "request_id", "timeout_s"})
MIN_TIMEOUT_SECONDS = 5
MAX_TIMEOUT_SECONDS = 300
_JOB_REF = re.compile(r"[A-Za-z0-9._:-]{1,64}")

_worker_loop: asyncio.AbstractEventLoop | None = None


class SdkChildError(Exception):
    code = "child_error"
    retryable = False

    def __init__(self, *, exit_code: int | None = None) -> None:
        super().__init__(self.code)
        self.exit_code = exit_code


class SdkChildUnavailable(SdkChildError):
    code = "child_unavailable"
    retryable = False


class SdkChildBusy(SdkChildError):
    code = "child_busy"
    retryable = True


class SdkChildTimeout(SdkChildError):
    code = "child_timeout"
    retryable = True


class SdkChildCrashed(SdkChildError):
    code = "child_crashed"

    @property
    def retryable(self) -> bool:
        return self.exit_code is not None and self.exit_code < 0


class SdkChildProtocolError(SdkChildError):
    code = "child_protocol_error"
    retryable = False


def validate_call(
    operation: str,
    fields: Mapping[str, Any],
    *,
    credential_id: str | None,
    timeout_seconds: int,
    job_ref: str,
) -> None:
    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, int)
        or not MIN_TIMEOUT_SECONDS <= timeout_seconds <= MAX_TIMEOUT_SECONDS
    ):
        raise ValueError("timeout_seconds must be an integer from 5 to 300")
    if not isinstance(job_ref, str) or not _JOB_REF.fullmatch(job_ref):
        raise ValueError("job_ref is not a valid request id")
    if ENVELOPE_NAMES & set(fields):
        raise ValueError("fields must not hold envelope names")
    if operation in KEYLESS_OPERATIONS and credential_id is not None:
        raise ValueError(f"credential_id is not allowed for {operation}")
    if operation in KEYED_OPERATIONS and not credential_id:
        raise ValueError(f"credential_id is required for {operation}")


def ensure_off_event_loop() -> None:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return
    raise RuntimeError("run_sdk_child_blocking called on the event loop")


def bind_worker_loop(loop: asyncio.AbstractEventLoop) -> None:
    global _worker_loop
    _worker_loop = loop


async def run_sdk_child(
    operation: str,
    fields: Mapping[str, Any],
    *,
    credential_id: str | None = None,
    timeout_seconds: int,
    job_ref: str,
) -> dict[str, Any]:
    validate_call(
        operation,
        fields,
        credential_id=credential_id,
        timeout_seconds=timeout_seconds,
        job_ref=job_ref,
    )
    raise NotImplementedError("run_sdk_child is not implemented yet")


def run_sdk_child_blocking(
    operation: str,
    fields: Mapping[str, Any],
    *,
    credential_id: str | None = None,
    timeout_seconds: int,
    job_ref: str,
) -> dict[str, Any]:
    validate_call(
        operation,
        fields,
        credential_id=credential_id,
        timeout_seconds=timeout_seconds,
        job_ref=job_ref,
    )
    ensure_off_event_loop()
    if _worker_loop is None:
        raise RuntimeError("no worker loop bound")
    raise NotImplementedError("run_sdk_child_blocking is not implemented yet")
