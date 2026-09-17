from __future__ import annotations

import gzip
import hashlib
import io
import json
import logging
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from threading import Lock
from typing import Any

from django.utils import timezone

from simulate.models import HostedHarnessAttempt
from tfc.settings.settings import UPLOAD_BUCKET_NAME
from tfc.utils.storage_client import get_storage_client

logger = logging.getLogger("simulate.hosted_harness_diagnostics")

_ENTRYPOINT_LOG_LIMIT_BYTES = 2 * 1024 * 1024
_PROCESS_LOG_LIMIT_BYTES = 4 * 1024 * 1024
_MIN_SECRET_FRAGMENT_LENGTH = 8
_REDACTION_CACHE_LIMIT = 128
_redaction_cache: dict[str, tuple[str, ...]] = {}
_redaction_cache_lock = Lock()
_PROCESS_LOG_COMMAND = (
    "for f in $(find /work/authoring /work/build /work/worlds "
    "-type f -name '*.log' 2>/dev/null | sort); do "
    'echo "===== $f ====="; tail -400 "$f"; done'
)
_BEARER_TOKEN = re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+")
_QUERY_TOKEN = re.compile(r"(?i)([?&](?:access_)?token=)[^&\s]+")
_ANSI_ESCAPE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
_CONTROL_CHARACTER_TRANSLATION = {
    code: None
    for code in range(128)
    if (code < 32 and code not in {9, 10, 13}) or code == 127
}
_SENSITIVE_ASSIGNMENT = re.compile(
    r"""(?ix)
    (
        ["']?
        (?:authorization|api[_-]?key|secret|token|password)
        ["']?
        \s*[:=]\s*
        ["']?
    )
    [^"',}\s]+
    """
)
_PRIVATE_KEY = re.compile(
    r"-----BEGIN [^-\n]*PRIVATE KEY-----.*?-----END [^-\n]*PRIVATE KEY-----",
    re.DOTALL,
)


@dataclass(frozen=True)
class DaytonaDiagnostics:
    entrypoint_log: str
    process_logs: str
    command_status: str | None
    exit_code: int | None


def redaction_values(
    values: Mapping[str, Any], *, extra: Iterable[str] = ()
) -> tuple[str, ...]:
    selected = [str(value) for value in values.values() if value]
    selected.extend(str(value) for value in extra if value)
    return tuple(selected)


def cache_attempt_redaction_values(
    attempt_id: Any, values: Iterable[str]
) -> tuple[str, ...]:
    key = str(attempt_id)
    cached = tuple(values)
    with _redaction_cache_lock:
        _redaction_cache.pop(key, None)
        if len(_redaction_cache) >= _REDACTION_CACHE_LIMIT:
            _redaction_cache.pop(next(iter(_redaction_cache)))
        _redaction_cache[key] = cached
    return cached


def cached_attempt_redaction_values(attempt_id: Any) -> tuple[str, ...] | None:
    with _redaction_cache_lock:
        return _redaction_cache.get(str(attempt_id))


def forget_attempt_redaction_values(attempt_id: Any) -> None:
    with _redaction_cache_lock:
        _redaction_cache.pop(str(attempt_id), None)


def _json_string_values(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _json_string_values(item)
    elif isinstance(value, list):
        for item in value:
            yield from _json_string_values(item)


def _secret_fragments(values: Iterable[str]) -> list[str]:
    fragments: set[str] = set()
    for value in values:
        text = str(value or "")
        if len(text) >= _MIN_SECRET_FRAGMENT_LENGTH:
            fragments.add(text)
        try:
            document = json.loads(text)
        except (TypeError, ValueError):
            continue
        fragments.update(
            item
            for item in _json_string_values(document)
            if len(item) >= _MIN_SECRET_FRAGMENT_LENGTH
        )
    return sorted(fragments, key=len, reverse=True)


def _normalize_log_text(text: str) -> str:
    without_ansi = _ANSI_ESCAPE.sub("", str(text or ""))
    return without_ansi.translate(_CONTROL_CHARACTER_TRANSLATION)


def _redact(text: str, secret_fragments: Iterable[str]) -> str:
    redacted = _normalize_log_text(text)
    for secret in secret_fragments:
        redacted = redacted.replace(secret, "[REDACTED]")
    redacted = _PRIVATE_KEY.sub("[REDACTED PRIVATE KEY]", redacted)
    redacted = _BEARER_TOKEN.sub(r"\1[REDACTED]", redacted)
    redacted = _QUERY_TOKEN.sub(r"\1[REDACTED]", redacted)
    return _SENSITIVE_ASSIGNMENT.sub(r"\1[REDACTED]", redacted)


def _tail_utf8(text: str, limit: int) -> str:
    encoded = text.encode("utf-8", errors="replace")
    if len(encoded) <= limit:
        return encoded.decode("utf-8")
    return "[earlier output truncated]\n" + encoded[-limit:].decode(
        "utf-8", errors="replace"
    )


def _command_logs(sandbox: Any, session_id: str, command_id: str) -> str:
    logs = sandbox.process.get_session_command_logs(
        session_id,
        command_id,
        request_timeout=30,
    )
    return getattr(logs, "output", None) or "\n".join(
        part
        for part in (
            getattr(logs, "stdout", ""),
            getattr(logs, "stderr", ""),
        )
        if part
    )


def poll_daytona_diagnostics(
    attempt: HostedHarnessAttempt,
    sandbox: Any,
    *,
    session_id: str,
    command_id: str | None = None,
    command: Any | None = None,
    final: bool = False,
    secret_values: Iterable[str] = (),
) -> DaytonaDiagnostics:
    """Poll one sandbox and persist its latest secret-safe diagnostics snapshot.

    Collection and storage are deliberately best-effort: diagnostics must never change the
    harness verdict or prevent cleanup. A stable object key is overwritten on every poll, so one
    long run cannot create an unbounded number of S3 objects.
    """

    errors: list[str] = []
    if command_id is None:
        try:
            command_id = (
                sandbox.fs.download_file("/run/futureagi/entrypoint-command-id", 30)
                .decode()
                .strip()
            )
        except Exception as exc:  # noqa: BLE001 - external diagnostics are fail-open
            errors.append(f"command_id:{type(exc).__name__}")

    if command is None and command_id:
        try:
            command = sandbox.process.get_session_command(
                session_id,
                command_id,
                request_timeout=30,
            )
        except Exception as exc:  # noqa: BLE001 - external diagnostics are fail-open
            errors.append(f"command_status:{type(exc).__name__}")

    command_status = getattr(command, "status", None)
    exit_code = getattr(command, "exit_code", None)
    entrypoint_log = ""
    if command_id:
        try:
            entrypoint_log = _command_logs(sandbox, session_id, command_id)
        except Exception as exc:  # noqa: BLE001 - a child log can still be captured
            errors.append(f"entrypoint:{type(exc).__name__}")

    process_logs = ""
    try:
        result = sandbox.process.exec(_PROCESS_LOG_COMMAND, timeout=30)
        process_logs = getattr(result, "result", "") or ""
    except Exception as exc:  # noqa: BLE001 - entrypoint output can still be captured
        errors.append(f"processes:{type(exc).__name__}")

    fragments = _secret_fragments(secret_values)
    entrypoint_log = _tail_utf8(
        _redact(entrypoint_log, fragments), _ENTRYPOINT_LOG_LIMIT_BYTES
    )
    process_logs = _tail_utf8(
        _redact(process_logs, fragments), _PROCESS_LOG_LIMIT_BYTES
    )
    capture = DaytonaDiagnostics(
        entrypoint_log=entrypoint_log,
        process_logs=process_logs,
        command_status=str(command_status) if command_status is not None else None,
        exit_code=exit_code,
    )
    captured_at = timezone.now()
    document = {
        "schema_version": "futureagi.harness-diagnostics.v1",
        "job_id": str(attempt.job_id),
        "attempt_id": str(attempt.id),
        "attempt_number": attempt.attempt_number,
        "sandbox_id": str(attempt.provider_ref or ""),
        "snapshot_name": str(attempt.snapshot_name or ""),
        "captured_at": captured_at.isoformat(),
        "final": final,
        "command_status": capture.command_status,
        "exit_code": capture.exit_code,
        "collection_errors": errors,
        "entrypoint_log": capture.entrypoint_log,
        "process_logs": capture.process_logs,
    }
    body = gzip.compress(
        json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8"),
        compresslevel=6,
        mtime=0,
    )
    digest = hashlib.sha256(body).hexdigest()
    object_key = (
        f"harness-diagnostics/{attempt.job.organization_id}/{attempt.job_id}/"
        f"{attempt.id}.json.gz"
    )

    try:
        get_storage_client().put_object(
            bucket_name=UPLOAD_BUCKET_NAME,
            object_name=object_key,
            data=io.BytesIO(body),
            length=len(body),
            content_type="application/gzip",
        )
    except Exception as exc:  # noqa: BLE001 - diagnostics cannot block the harness lifecycle
        attempt.diagnostics_error = f"storage:{type(exc).__name__}"
        attempt.save(update_fields=["diagnostics_error", "updated_at"])
        logger.exception("could not persist Daytona diagnostics attempt=%s", attempt.id)
        return capture

    attempt.diagnostics_object_key = object_key
    attempt.diagnostics_sha256 = digest
    attempt.diagnostics_size = len(body)
    attempt.diagnostics_captured_at = captured_at
    attempt.diagnostics_final = final
    attempt.diagnostics_error = ",".join(errors)[:500]
    attempt.save(
        update_fields=[
            "diagnostics_object_key",
            "diagnostics_sha256",
            "diagnostics_size",
            "diagnostics_captured_at",
            "diagnostics_final",
            "diagnostics_error",
            "updated_at",
        ]
    )
    return capture
