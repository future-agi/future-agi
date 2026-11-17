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
from tfc.logging import credential_scrub
from tfc.settings.settings import UPLOAD_BUCKET_NAME
from tfc.utils.storage_client import get_storage_client

logger = logging.getLogger("simulate.hosted_harness_diagnostics")

_ENTRYPOINT_LOG_LIMIT_BYTES = 2 * 1024 * 1024
_PROCESS_LOG_LIMIT_BYTES = 4 * 1024 * 1024
# Held values shorter than this are masked only as a named secret slot's whole value.
_MIN_SECRET_FRAGMENT_LENGTH = 8
# The known-value scrubber's own cap on the items one call takes in. Above it a
# text is hidden whole without scrubbing, which keeps the work of one poll bounded.
_HELD_VALUE_TOTAL_LIMIT = 4096
_REDACTION_CACHE_LIMIT = 128
# Per attempt: every held value, then those of them masked as whole values.
_redaction_cache: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {}
_redaction_cache_lock = Lock()
_GRANT_ID = re.compile(r"fit_[0-9a-f]{32}")
_SERVICE_GRANT = re.compile(r"fe1\.[A-Za-z0-9_-]{40,}\.[A-Za-z0-9_-]{43}")
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


def _process_log_command(work: str = "/work") -> str:
    """List every process log, labelled build (carried in the authoring files) or run.

    ``diagnostics`` holds the run's own worker logs, retained there when its worlds are torn
    down. Sorting puts the build's logs first, so a tail-truncated capture keeps the run's.
    """
    return (
        f"for f in $(find {work}/authoring {work}/build {work}/diagnostics {work}/worlds "
        "-type f -name '*.log' 2>/dev/null | sort); do "
        f'case "$f" in {work}/authoring/*) label=build ;; *) label=run ;; esac; '
        'echo "===== $label: $f ====="; tail -400 "$f"; done'
    )


@dataclass(frozen=True)
class SandboxDiagnostics:
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
    attempt_id: Any, values: Iterable[str], *, whole: Iterable[str] = ()
) -> tuple[str, ...]:
    key = str(attempt_id)
    cached = tuple(values)
    whole_values = tuple(whole)
    with _redaction_cache_lock:
        _redaction_cache.pop(key, None)
        if len(_redaction_cache) >= _REDACTION_CACHE_LIMIT:
            _redaction_cache.pop(next(iter(_redaction_cache)))
        _redaction_cache[key] = (cached, whole_values)
    return cached


def cached_attempt_held_values(
    attempt_id: Any,
) -> tuple[tuple[str, ...], tuple[str, ...]] | None:
    """The attempt's held values and those of them masked whole, read in one step."""
    with _redaction_cache_lock:
        return _redaction_cache.get(str(attempt_id))


def cached_attempt_redaction_values(attempt_id: Any) -> tuple[str, ...] | None:
    held = cached_attempt_held_values(attempt_id)
    return None if held is None else held[0]


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
    redacted = _GRANT_ID.sub("[REDACTED]", redacted)
    redacted = _SERVICE_GRANT.sub("[REDACTED]", redacted)
    redacted = _PRIVATE_KEY.sub("[REDACTED PRIVATE KEY]", redacted)
    redacted = _BEARER_TOKEN.sub(r"\1[REDACTED]", redacted)
    redacted = _QUERY_TOKEN.sub(r"\1[REDACTED]", redacted)
    return _SENSITIVE_ASSIGNMENT.sub(r"\1[REDACTED]", redacted)


def _known_value_groups(fragments: Iterable[str]) -> list[tuple[str, ...]]:
    """The held values, in order, in groups the known-value scrubber takes in one call.

    The scrubber refuses a call holding more values, or more characters in all, than
    its own limits, and then hides the whole text. Each group keeps within both limits,
    so a run holding more values than one call takes still has every one masked. A
    value longer than one call takes gets a group of its own, which the scrubber
    refuses, so that text is hidden whole rather than masked in part. There is always
    at least one group, which may be empty.
    """
    groups: list[tuple[str, ...]] = []
    group: list[str] = []
    characters = 0
    for fragment in fragments:
        if group and (
            len(group) >= credential_scrub._KNOWN_VALUE_LIMIT
            or characters + len(fragment) > credential_scrub._KNOWN_CHARACTER_LIMIT
        ):
            groups.append(tuple(group))
            group, characters = [], 0
        group.append(fragment)
        characters += len(fragment)
    groups.append(tuple(group))
    return groups


def _mask_held_values(text: str, held: tuple[str, ...]) -> str:
    """text with every held value masked in every spelling, as one scrubber call masks.

    With one group this is one known-value scrubber call. With more, each group's
    spans are found in the same text and their union is masked once, so a value
    whose occurrence overlaps one from another group is still masked whole. Any
    refusal raises or returns the failure marker; nothing is masked in part.
    """
    groups = _known_value_groups(held)
    if len(groups) == 1:
        return credential_scrub.redact_known_values(
            text, groups[0], marker="[REDACTED]"
        )
    if len(text) > credential_scrub.DECODED_BODY_LIMIT:
        raise ValueError("text budget")
    spans: list[list[int]] = []
    for group in groups:
        spans.extend(credential_scrub._known_spans(text, group))
    merged: list[list[int]] = []
    for start, end in sorted(spans):
        if merged and start < merged[-1][1]:
            merged[-1][1] = max(end, merged[-1][1])
        else:
            merged.append([start, end])
    pieces, cursor = [], 0
    for start, end in merged:
        pieces.append(text[cursor:start])
        pieces.append("[REDACTED]")
        cursor = end
    pieces.append(text[cursor:])
    return "".join(pieces)


def _mask_whole_values(text: str, whole: tuple[str, ...]) -> str:
    """text with each whole value masked wherever it stands as a token of its own.

    A value is a token where no letter, digit or underscore touches it on either
    side, so a short value is masked alone or beside punctuation and is left as it
    is inside a longer word or number.
    """
    if not whole:
        return text
    alternatives = "|".join(
        re.escape(value) for value in sorted(set(whole), key=len, reverse=True)
    )
    return re.sub(rf"(?<!\w)(?:{alternatives})(?!\w)", "[REDACTED]", text)


def _scrub_collected(
    text: str, fragments: Iterable[str], whole: tuple[str, ...] = ()
) -> str:
    """Mask held values in every spelling, then whole values, then known patterns.

    Held values are masked first, over the collected text, so values whose
    occurrences overlap are masked as one span. The values of named secret slots
    are then masked as tokens whatever their length; the legacy pass then runs with
    every value, as before. Above a fixed number of held values the text is hidden
    whole without scrubbing.
    """
    try:
        failed = credential_scrub.MARKER_FORMAT.format("scrub-failed")
        held = tuple(fragments)
        if len(held) > _HELD_VALUE_TOTAL_LIMIT:
            return failed
        result = _mask_held_values(_normalize_log_text(text), held)
        if type(result) is not str or result == failed:
            return failed
        result = _mask_whole_values(result, whole)
        result = credential_scrub.scrub_text(_redact(result, held))
        if type(result) is not str or result == failed:
            return failed
        return result
    except Exception:  # noqa: BLE001 - unsafe diagnostics must never escape
        return "[hidden: scrub-failed]"


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


def poll_sandbox_diagnostics(
    attempt: HostedHarnessAttempt,
    sandbox: Any,
    *,
    session_id: str,
    command_id: str | None = None,
    command: Any | None = None,
    final: bool = False,
    secret_values: Iterable[str] = (),
    whole_values: Iterable[str] = (),
) -> SandboxDiagnostics:
    """Poll one sandbox and persist its latest secret-safe diagnostics snapshot.

    Collection and storage are deliberately best-effort: diagnostics must never change the
    harness verdict or prevent cleanup. A stable object key is overwritten on every poll, so one
    long run cannot create an unbounded number of S3 objects. ``whole_values`` are held
    values also masked as whole tokens whatever their length.
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
    raw_entrypoint_log = ""
    if command_id:
        try:
            raw_entrypoint_log = _command_logs(sandbox, session_id, command_id)
        except Exception as exc:  # noqa: BLE001 - a child log can still be captured
            errors.append(f"entrypoint:{type(exc).__name__}")

    raw_process_logs = ""
    try:
        result = sandbox.process.exec(_process_log_command(), timeout=30)
        raw_process_logs = getattr(result, "result", "") or ""
    except Exception as exc:  # noqa: BLE001 - entrypoint output can still be captured
        errors.append(f"processes:{type(exc).__name__}")

    try:
        fragments = _secret_fragments(secret_values)
        whole = tuple(str(value) for value in whole_values if value)
    except Exception:  # noqa: BLE001 - diagnostics cannot change the harness verdict
        entrypoint_log = process_logs = "[hidden: scrub-failed]"
    else:
        entrypoint_log = _scrub_collected(raw_entrypoint_log, fragments, whole)
        process_logs = _scrub_collected(raw_process_logs, fragments, whole)
    entrypoint_log = _tail_utf8(entrypoint_log, _ENTRYPOINT_LOG_LIMIT_BYTES)
    process_logs = _tail_utf8(process_logs, _PROCESS_LOG_LIMIT_BYTES)
    capture = SandboxDiagnostics(
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
        logger.exception("could not persist sandbox diagnostics attempt=%s", attempt.id)
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
