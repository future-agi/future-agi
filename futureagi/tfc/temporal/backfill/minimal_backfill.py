"""Resumable Temporal backfill for historical Vapi recordings.

Source URL state is the ledger. No billing, manifests, or auxiliary status tables.

ClickHouse observability updates use ReplacingMergeTree version-bump inserts:
- attrs Map columns via mapUpdate(s.map, patch_map) with patch-only staging
- attributes_extra (String JSON after migration 013) via exact URL rewrite only
  when the old URL is present in the text
"""

from __future__ import annotations

import copy
import json
import os
import re
import threading
import time
import urllib.parse
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from temporalio import activity, workflow
from temporalio.common import RetryPolicy

PATHS = {
    "mono_combined": (
        ("artifact", "recording", "mono", "combinedUrl"),
        ("recording", "combined"),
    ),
    "mono_customer": (
        ("artifact", "recording", "mono", "customerUrl"),
        ("recording", "customer"),
    ),
    "mono_assistant": (
        ("artifact", "recording", "mono", "assistantUrl"),
        ("recording", "assistant"),
    ),
    "stereo": (("artifact", "recording", "stereoUrl"), ("recording", "stereo")),
}
CH_KEYS = {
    "mono_combined": ("conversation.recording.mono.combined", "recording_url"),
    "mono_customer": ("conversation.recording.mono.customer",),
    "mono_assistant": ("conversation.recording.mono.assistant",),
    "stereo": ("conversation.recording.stereo", "stereo_recording_url"),
}
TOP_FIELD = {"mono_combined": "recording_url", "stereo": "stereo_recording_url"}
VAPI_HOST_MARKERS = ("storage.vapi.ai", "api.vapi.ai", ".r2.dev")
RUN_ID_RE = re.compile(r"^[A-Za-z0-9_]+$")
_API_RATE_LOCK = threading.Lock()
_API_NEXT_REQUEST_AT = 0.0


@dataclass
class VapiBackfillInput:
    source: str = "simulation"
    dry_run: bool = False
    limit: int | None = None
    project_id: str | None = None
    shards: int = 1
    shard: int = 0
    batch_size: int = 100
    # ClickHouse keyset cursor
    cursor_time: str | None = None
    cursor_id: str | None = None
    cursor_trace_id: str | None = None
    # map = cheap attrs Map scan; extra = day-chunked attributes_extra-only scan
    ch_scan_phase: str = "map"
    cursor_day: str | None = None
    # Independent simulation cursors (CE and Snapshot ID spaces differ)
    ce_cursor_time: str | None = None
    ce_cursor_id: str | None = None
    snap_cursor_time: str | None = None
    snap_cursor_id: str | None = None
    run_id: str = "vapi_recording_backfill"
    batch_seq: int = 0
    proof_gate: int | None = None
    min_records_per_second: float = 0.0
    processed: int = 0
    changed: int = 0
    skipped: int = 0
    fatal: int = 0
    errors: list[str] = field(default_factory=list)


@dataclass
class VapiBackfillResult:
    processed: int = 0
    changed: int = 0
    skipped: int = 0
    fatal: int = 0
    cursor_time: str | None = None
    cursor_id: str | None = None
    cursor_trace_id: str | None = None
    ch_scan_phase: str = "map"
    cursor_day: str | None = None
    ce_cursor_time: str | None = None
    ce_cursor_id: str | None = None
    snap_cursor_time: str | None = None
    snap_cursor_id: str | None = None
    done: bool = True
    errors: list[str] = field(default_factory=list)


def _get(value: dict[str, Any], path: tuple[str, ...]) -> Any:
    current: Any = value
    for part in path:
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def _replace(value: dict[str, Any], path: tuple[str, ...], old: str, new: str) -> bool:
    current: Any = value
    for part in path[:-1]:
        if not isinstance(current, dict):
            return False
        current = current.get(part)
    if isinstance(current, dict) and current.get(path[-1]) == old:
        current[path[-1]] = new
        return True
    return False


def _is_vapi_url(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    host = (urllib.parse.urlparse(value).hostname or "").lower()
    return host in {"storage.vapi.ai", "api.vapi.ai"} or host.endswith(".r2.dev")


def _artifacts(payload: dict[str, Any] | None) -> tuple[str | None, dict[str, str]]:
    vapi = (payload or {}).get("vapi")
    if not isinstance(vapi, dict):
        return None, {}
    found = {}
    for kind, paths in PATHS.items():
        for path in paths:
            url = _get(vapi, path)
            if _is_vapi_url(url):
                found[kind] = url
                break
    call_id = vapi.get("id") or vapi.get("callId")
    return (str(call_id) if call_id else None), found


def _extra_artifacts(raw: str) -> tuple[str | None, dict[str, str]]:
    """Extract artifact URLs from historical raw Vapi JSON in attributes_extra."""
    try:
        root = json.loads(raw or "{}")
    except (TypeError, json.JSONDecodeError):
        return None, {}
    found: dict[str, str] = {}
    call_id: str | None = None
    key_to_kind = {
        "combinedUrl": "mono_combined",
        "customerUrl": "mono_customer",
        "assistantUrl": "mono_assistant",
        "stereoUrl": "stereo",
    }

    def walk(value: Any) -> None:
        nonlocal call_id
        if isinstance(value, dict):
            if not call_id and ("artifact" in value or "recording" in value):
                candidate = value.get("id") or value.get("callId")
                if candidate:
                    call_id = str(candidate)
            for key, child in value.items():
                kind = key_to_kind.get(key)
                if kind and _is_vapi_url(child):
                    found.setdefault(kind, child)
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(root)
    return call_id, found


def _is_snapshot(row: Any) -> bool:
    return row.__class__.__name__ == "CallExecutionSnapshot"


def _call(row: Any) -> Any:
    """Return the CallExecution-like object that owns identity fields."""
    if _is_snapshot(row):
        return row.call_execution
    return row


def _canonical_call_id(row: Any, nested_call_id: str | None) -> str | None:
    """Full Vapi call ID only. Never use truncated snapshot.service_provider_call_id."""
    if nested_call_id:
        return nested_call_id
    call = _call(row)
    if call is None:
        return None
    value = getattr(call, "service_provider_call_id", None)
    return str(value) if value else None


def _row_artifacts(row: Any) -> tuple[str | None, dict[str, str]]:
    nested_id, found = _artifacts(getattr(row, "provider_call_data", None))
    if _is_vapi_url(getattr(row, "recording_url", None)):
        found.setdefault("mono_combined", row.recording_url)
    if _is_vapi_url(getattr(row, "stereo_recording_url", None)):
        found.setdefault("stereo", row.stereo_recording_url)
    return _canonical_call_id(row, nested_id), found


def _project_id(row: Any) -> str:
    call = _call(row)
    if call is None:
        raise ValueError(f"Snapshot {row.pk} has no parent CallExecution")
    execution = call.test_execution
    agent = execution.agent_definition or getattr(
        execution.run_test, "agent_definition", None
    )
    provider = getattr(agent, "observability_provider", None) if agent else None
    value = getattr(provider, "project_id", None)
    if not value:
        raise ValueError(f"No project_id for {row.pk}")
    return str(value)


def _api_key(row: Any, project_id: str) -> str | None:
    from tracer.utils.vapi_recording import VapiRecordingService

    call = _call(row)
    if call is None:
        return VapiRecordingService.get_api_key_for_project(project_id)
    if (
        str((call.call_metadata or {}).get("call_direction") or "").lower()
        == "outbound"
    ):
        agent_id = call.test_execution.agent_definition_id or getattr(
            call.test_execution.run_test, "agent_definition_id", None
        )
        return VapiRecordingService.get_api_key_for_agent_definition(agent_id)
    return VapiRecordingService.get_api_key_for_project(project_id)


def _wait_for_api_slot(rate: int) -> None:
    """Process-wide limiter; dedicated worker must run one activity slot."""
    global _API_NEXT_REQUEST_AT
    with _API_RATE_LOCK:
        now = time.monotonic()
        delay = max(0.0, _API_NEXT_REQUEST_AT - now)
        if delay:
            time.sleep(delay)
        _API_NEXT_REQUEST_AT = max(now, _API_NEXT_REQUEST_AT) + (1 / rate)


def _is_retryable(exc: Exception) -> bool:
    """Classify only transient provider/storage/network errors for activity retry."""
    from tracer.utils.vapi_recording import (
        VapiArtifactNotReadyError,
        VapiAuthError,
        VapiRateLimitError,
    )

    if isinstance(exc, VapiAuthError):
        return False
    if isinstance(exc, (VapiRateLimitError, VapiArtifactNotReadyError)):
        return True
    try:
        import requests

        if isinstance(exc, (requests.Timeout, requests.ConnectionError)):
            return True
        if isinstance(exc, requests.HTTPError):
            status = getattr(exc.response, "status_code", 0) or 0
            return status == 429 or status >= 500
    except ImportError:
        pass
    if getattr(exc, "code", "") in {
        "SlowDown",
        "InternalError",
        "ServiceUnavailable",
        "RequestTimeout",
    }:
        return True
    status = getattr(getattr(exc, "response", None), "status_code", 0) or 0
    if status in {401, 403, 404, 410}:
        return False
    if status == 429 or status >= 500:
        return True
    return type(exc).__name__ in {
        "ReadTimeout",
        "ConnectTimeout",
        "ConnectError",
        "NetworkError",
        "SocketTimeout",
        "ServiceUnavailable",
    }


def _ch_call_id(attrs: dict[str, str], extra_call_id: str | None = None) -> str | None:
    """Resolve Vapi call identity from current and historical span attribute names."""
    value = (
        attrs.get("vapi.call_id")
        or attrs.get("service_provider_call_id")
        or attrs.get("call.id")
        or attrs.get("gen_ai.voice.call_id")
        or extra_call_id
    )
    return str(value) if value else None


def _api_key_for_attributes(attrs: dict[str, str], project_id: str) -> str | None:
    from tracer.utils.vapi_recording import VapiRecordingService

    direction = (
        attrs.get("call.direction") or attrs.get("call_direction") or ""
    ).lower()
    agent_id = attrs.get("agent_definition_id") or attrs.get("agent.id")
    if direction == "outbound" and agent_id:
        return VapiRecordingService.get_api_key_for_agent_definition(agent_id)
    return VapiRecordingService.get_api_key_for_project(project_id)


def _validate_storage_stat(stat: Any) -> None:
    content_type = str(getattr(stat, "content_type", "") or "")
    if int(getattr(stat, "size", 0) or 0) <= 0 or not content_type.startswith("audio/"):
        raise RuntimeError(f"S3 HEAD validation failed ({content_type!r})")


def _valid_direct_audio(response: Any) -> tuple[bytes, str] | None:
    """Accept a direct URL response only when the body is non-empty real audio.

    storage.vapi.ai and other hosts can return 200 HTML/JSON error pages.
    Reuse ``_detected_audio_extension`` (ffmpeg + supported format map) and
    return the detected extension so ``_stored`` does not run ffmpeg again.
    """
    from simulate.temporal.utils.async_storage import _detected_audio_extension

    content = getattr(response, "content", None) or b""
    if not content:
        return None
    headers = getattr(response, "headers", None) or {}
    content_type = str(headers.get("Content-Type") or headers.get("content-type") or "")
    content_type = content_type.split(";", 1)[0].strip().lower()
    # Explicit non-audio types lose before the expensive detector.
    if content_type.startswith(("text/", "application/json", "application/xml")):
        return None
    try:
        ext = _detected_audio_extension(content)
    except Exception:
        return None
    return content, ext


def _raise_direct_fallback_disabled(direct_error: BaseException | None) -> None:
    if isinstance(direct_error, Exception) and _is_retryable(direct_error):
        raise direct_error
    if direct_error is not None:
        raise RuntimeError(
            f"Direct URL failed and authenticated fallback is disabled: {direct_error}"
        ) from direct_error
    raise RuntimeError("Direct URL failed and authenticated fallback is disabled")


def _download(
    url: str,
    call_id: str,
    kind: str,
    api_key_provider: Any | None = None,
) -> tuple[bytes, str | None]:
    """Direct-URL first with audio validation; API key only on authenticated fallback.

    Returns ``(audio_bytes, detected_extension_or_None)``. Extension is set when
    the direct URL path validated via ``_detected_audio_extension``; API fallback
    leaves it None so ``_stored`` detects once on upload.
    """
    import requests

    from tracer.utils.vapi_recording import VapiRecordingService

    direct_error: BaseException | None = None
    try:
        response = requests.get(url, timeout=60, allow_redirects=True)
        response.raise_for_status()
        validated = _valid_direct_audio(response)
        if validated is not None:
            return validated
        headers = getattr(response, "headers", None) or {}
        content_type = str(
            headers.get("Content-Type") or headers.get("content-type") or ""
        )
        direct_error = RuntimeError(
            f"Direct URL response is not valid audio (content_type={content_type!r}, "
            f"bytes={len(getattr(response, 'content', b'') or b'')})"
        )
    except requests.RequestException as exc:
        direct_error = exc

    rate = int(os.getenv("VAPI_API_RATE_LIMIT_PER_SECOND", "0") or "0")
    if rate <= 0:
        _raise_direct_fallback_disabled(direct_error)

    # Only resolve credentials when authenticated fallback is actually enabled.
    api_key = api_key_provider() if callable(api_key_provider) else None
    if not api_key:
        _raise_direct_fallback_disabled(direct_error)

    artifact = VapiRecordingService.artifact_for_url_type(kind)
    if not artifact:
        raise ValueError(f"Unknown artifact type {kind}")
    _wait_for_api_slot(rate)
    data = VapiRecordingService.download_artifact_sync(call_id, artifact, api_key)
    if not data:
        raise RuntimeError("Empty Vapi artifact")
    return data, None


def _object_key_from_existing_url(base: str, url: str) -> str | None:
    """Map a durable storage URL back to its object key without re-stat."""
    from simulate.temporal.utils.async_storage import (
        _REHOST_AUDIO_EXTENSIONS,
        _rehost_object_key,
    )

    path = urllib.parse.urlparse(url).path.lstrip("/")
    for ext in _REHOST_AUDIO_EXTENSIONS:
        candidate = _rehost_object_key(base, ext)
        if path == candidate or path.endswith("/" + candidate):
            return candidate
    return None


def _stored(
    project_id: str,
    call_id: str,
    kind: str,
    original: str,
    audio: bytes | None,
    audio_ext: str | None = None,
) -> str:
    from simulate.temporal.utils.async_storage import (
        _detected_audio_extension,
        _existing_rehosted_audio,
        _rehost_object_key,
        _rehost_object_key_base,
    )
    from tfc.settings.settings import UPLOAD_BUCKET_NAME
    from tfc.utils.storage import upload_audio_to_s3
    from tfc.utils.storage_client import get_storage_client
    from tracer.utils.vapi_recording import VapiRecordingService

    base = _rehost_object_key_base(call_id, kind, project_id, "vapi")
    existing = _existing_rehosted_audio(base)
    if existing:
        url = existing[0]
        object_key = _object_key_from_existing_url(base, url)
        if not object_key:
            raise RuntimeError("Existing S3 URL has no matching object key")
    else:
        if not audio:
            raise RuntimeError("Missing audio bytes")
        # Prefer extension carried from direct-URL validation (one ffmpeg).
        ext = audio_ext or _detected_audio_extension(audio)
        object_key = _rehost_object_key(base, ext)
        url = upload_audio_to_s3({"bytes": audio}, object_key=object_key)
    if url == original or not VapiRecordingService.is_fagi_s3_url(url):
        raise RuntimeError("Upload returned an invalid storage URL")
    stat = get_storage_client().stat_object(UPLOAD_BUCKET_NAME, object_key)
    _validate_storage_stat(stat)
    return url



def _update_pg(row: Any, kind: str, old: str, new: str) -> bool:
    fields = []
    field_name = TOP_FIELD.get(kind)
    if field_name and getattr(row, field_name, None) == old:
        setattr(row, field_name, new)
        fields.append(field_name)
    original = row.provider_call_data or {}
    vapi = original.get("vapi")
    if isinstance(vapi, dict):
        vapi_copy = copy.deepcopy(vapi)
        nested_changed = False
        for path in PATHS[kind]:
            nested_changed = _replace(vapi_copy, path, old, new) or nested_changed
        if nested_changed:
            payload = dict(original)
            payload["vapi"] = vapi_copy
            row.provider_call_data = payload
            fields.append("provider_call_data")
    if fields:
        row.save(update_fields=list(dict.fromkeys(fields)))
    return bool(fields)


def _pg_pending_q():
    from django.db.models import Q

    pending = Q()
    for marker in VAPI_HOST_MARKERS:
        pending |= Q(recording_url__icontains=marker)
        pending |= Q(stereo_recording_url__icontains=marker)
        pending |= Q(provider_call_data__icontains=marker)
    return pending


def _pg_project_q(model_name: str, project_id: str):
    from django.db.models import Q

    prefix = "" if model_name == "CallExecution" else "call_execution__"
    return Q(
        **{
            f"{prefix}test_execution__agent_definition__observability_provider__project_id": project_id
        }
    ) | Q(
        **{
            f"{prefix}test_execution__run_test__agent_definition__observability_provider__project_id": project_id
        }
    )


def _pg_select_related(model_name: str) -> tuple[str, ...]:
    if model_name == "CallExecution":
        return (
            "test_execution__agent_definition__observability_provider",
            "test_execution__run_test__agent_definition__observability_provider",
        )
    return (
        "call_execution__test_execution__agent_definition__observability_provider",
        "call_execution__test_execution__run_test__agent_definition__observability_provider",
        "call_execution",
    )


def _query_pg_table(
    model: Any,
    *,
    cursor_time: str | None,
    cursor_id: str | None,
    project_id: str | None,
    shards: int,
    shard: int,
    limit: int,
) -> list[Any]:
    from django.db.models import Q

    if limit <= 0:
        return []
    cursor = Q()
    if cursor_time and cursor_id:
        cursor = Q(created_at__lt=cursor_time) | Q(
            created_at=cursor_time, id__lt=cursor_id
        )
    qs = model.objects.filter(_pg_pending_q()).filter(cursor)
    if project_id:
        qs = qs.filter(_pg_project_q(model.__name__, project_id))
    if shards > 1:
        qs = qs.extra(
            where=["MOD(ABS(hashtext(id::text)), %s) = %s"],
            params=[shards, shard],
        )
    return list(
        qs.select_related(*_pg_select_related(model.__name__)).order_by(
            "-created_at", "-id"
        )[:limit]
    )


def _pg_rows(inp: VapiBackfillInput) -> tuple[list[Any], dict[str, str | None]]:
    """Return interleaved newest-first rows with independent per-table cursors."""
    from simulate.models.test_execution import CallExecution, CallExecutionSnapshot

    remaining = (
        inp.batch_size
        if inp.limit is None
        else min(inp.batch_size, inp.limit - inp.processed)
    )
    if remaining <= 0:
        return [], {
            "ce_cursor_time": inp.ce_cursor_time,
            "ce_cursor_id": inp.ce_cursor_id,
            "snap_cursor_time": inp.snap_cursor_time,
            "snap_cursor_id": inp.snap_cursor_id,
        }

    ce_rows = _query_pg_table(
        CallExecution,
        cursor_time=inp.ce_cursor_time,
        cursor_id=inp.ce_cursor_id,
        project_id=inp.project_id,
        shards=inp.shards,
        shard=inp.shard,
        limit=remaining,
    )
    snap_rows = _query_pg_table(
        CallExecutionSnapshot,
        cursor_time=inp.snap_cursor_time,
        cursor_id=inp.snap_cursor_id,
        project_id=inp.project_id,
        shards=inp.shards,
        shard=inp.shard,
        limit=remaining,
    )

    # Merge two already-sorted streams for true union top-N.
    merged: list[Any] = []
    i = j = 0
    while len(merged) < remaining and (i < len(ce_rows) or j < len(snap_rows)):
        take_ce = j >= len(snap_rows) or (
            i < len(ce_rows)
            and (ce_rows[i].created_at, ce_rows[i].id)
            >= (snap_rows[j].created_at, snap_rows[j].id)
        )
        if take_ce:
            merged.append(ce_rows[i])
            i += 1
        else:
            merged.append(snap_rows[j])
            j += 1

    ce_cursor_time, ce_cursor_id = inp.ce_cursor_time, inp.ce_cursor_id
    snap_cursor_time, snap_cursor_id = inp.snap_cursor_time, inp.snap_cursor_id
    for row in merged:
        if _is_snapshot(row):
            snap_cursor_time, snap_cursor_id = row.created_at.isoformat(), str(row.id)
        else:
            ce_cursor_time, ce_cursor_id = row.created_at.isoformat(), str(row.id)

    return merged, {
        "ce_cursor_time": ce_cursor_time,
        "ce_cursor_id": ce_cursor_id,
        "snap_cursor_time": snap_cursor_time,
        "snap_cursor_id": snap_cursor_id,
    }


@activity.defn(name="vapi-simulation-backfill-batch")
def vapi_simulation_backfill_batch(inp: VapiBackfillInput) -> VapiBackfillResult:
    from django.db import close_old_connections

    from simulate.temporal.utils.async_storage import (
        _existing_rehosted_audio,
        _rehost_object_key_base,
    )

    close_old_connections()
    try:
        rows, cursors = _pg_rows(inp)
        out = VapiBackfillResult(done=not rows, **cursors)
        if not rows:
            return out
        for row in rows:
            call_id, artifacts = _row_artifacts(row)
            if not artifacts:
                out.skipped += 1
                out.processed += 1
                continue
            if not call_id:
                out.fatal += len(artifacts)
                out.errors.append(
                    f"{type(row).__name__}:{row.pk}: missing full Vapi call ID"
                )
                out.processed += 1
                continue
            try:
                project_id = _project_id(row)
            except Exception as exc:
                out.fatal += len(artifacts)
                out.errors.append(
                    f"{type(row).__name__}:{row.pk}: identity resolution: {type(exc).__name__}: {exc}"
                )
                out.processed += 1
                continue
            api_key_provider = lambda row=row, project_id=project_id: _api_key(
                row, project_id
            )
            for kind, old in artifacts.items():
                if inp.dry_run:
                    out.changed += 1
                    continue
                try:
                    base = _rehost_object_key_base(call_id, kind, project_id, "vapi")
                    audio = None
                    audio_ext = None
                    if not _existing_rehosted_audio(base):
                        audio, audio_ext = _download(
                            old, call_id, kind, api_key_provider
                        )
                    try:
                        new = _stored(
                            project_id,
                            call_id,
                            kind,
                            old,
                            audio,
                            audio_ext=audio_ext,
                        )
                    finally:
                        # Drop recording bytes immediately — never hold batch audio in RAM.
                        audio = None
                    if not _update_pg(row, kind, old, new):
                        raise RuntimeError(
                            "S3 object stored but source row was not updated"
                        )
                    out.changed += 1
                except Exception as exc:
                    if _is_retryable(exc):
                        raise
                    out.fatal += 1
                    out.errors.append(
                        f"{type(row).__name__}:{row.pk}:{kind}: {type(exc).__name__}: {exc}"
                    )
            out.processed += 1
            activity.heartbeat(out.processed, out.changed, out.fatal)
        remaining = (
            inp.batch_size
            if inp.limit is None
            else min(inp.batch_size, inp.limit - inp.processed)
        )
        out.done = len(rows) < remaining
        return out
    finally:
        close_old_connections()


def _json_replace(raw: str, replacements: dict[str, str]) -> str:
    """Exact URL replace inside String JSON.

    Raises if rewrite is required but the payload is not valid JSON, so we never
    write a half-broken attributes_extra (fail closed → original span kept).
    """
    try:
        value = json.loads(raw or "{}")
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError(
            "attributes_extra contains a Vapi URL but is not valid JSON; "
            "refusing to rewrite"
        ) from exc

    def walk(item: Any) -> Any:
        if isinstance(item, str):
            return replacements.get(item, item)
        if isinstance(item, dict):
            return {key: walk(val) for key, val in item.items()}
        if isinstance(item, list):
            return [walk(val) for val in item]
        return item

    return json.dumps(walk(value), separators=(",", ":"), ensure_ascii=False)


def _mapping_table(run_id: str, shard: int) -> str:
    if not RUN_ID_RE.fullmatch(run_id):
        raise ValueError("invalid run_id")
    if shard < 0:
        raise ValueError("invalid shard")
    return f"backfill_mapping_{run_id}_{shard}"


def _ch_shape(ch: Any) -> tuple[str, str | None, str, str, str]:
    from tracer.services.clickhouse.schema import detect_spans_table_shape

    shape = detect_spans_table_shape(ch.execute)
    if shape not in {"v1", "v2"}:
        raise RuntimeError(f"Unsupported spans shape {shape}")
    active = "attrs_string" if shape == "v2" else "span_attr_str"
    columns = {
        row[0]: row[1]
        for row in ch.execute(
            "SELECT name,type FROM system.columns WHERE database=currentDatabase() AND table='spans'"
        )
    }
    if shape == "v2":
        extra, version_col, deleted_col = "attributes_extra", "_version", "is_deleted"
    else:
        extra, version_col, deleted_col = (
            "span_attributes_raw",
            "_peerdb_version",
            "_peerdb_is_deleted",
        )
    if active not in columns:
        raise RuntimeError(f"spans missing active map column {active}")
    return (
        active,
        (extra if extra in columns else None),
        columns[active],
        version_col,
        deleted_col,
    )


def _ch_map_predicate(active: str) -> str:
    """Pending Vapi/R2 URLs in the active string Map only (cheap, no wide JSON)."""
    keys = [key for group in CH_KEYS.values() for key in group]
    value = " OR ".join(
        f"position({active}['{key}'], '{domain}') > 0"
        for key in keys
        for domain in VAPI_HOST_MARKERS
    )
    return f"({value})"


def _ch_extra_predicate(extra: str) -> str:
    """Pending Vapi/R2 host markers inside attributes_extra String JSON."""
    value = " OR ".join(
        f"position({extra}, '{domain}') > 0" for domain in VAPI_HOST_MARKERS
    )
    return f"({value})"


def _ch_predicate(active: str, extra: str | None) -> str:
    """Full pending predicate. Prefer map-only / day-chunked helpers for scans."""
    map_clause = _ch_map_predicate(active)
    if not extra:
        return map_clause
    return f"({map_clause} OR {_ch_extra_predicate(extra)})"


def _ch_scan_settings(*, max_memory_bytes: int | None = None) -> dict[str, Any]:
    """Conservative settings so cold attributes_extra scans do not OOM the server."""
    memory = max_memory_bytes or int(
        os.getenv("BACKFILL_CH_MAX_MEMORY_BYTES", str(2 * 1024**3))
    )
    return {
        "max_execution_time": int(os.getenv("BACKFILL_CH_MAX_EXECUTION_SECONDS", "120")),
        "max_memory_usage": memory,
        "max_threads": int(os.getenv("BACKFILL_CH_MAX_THREADS", "2")),
        # Prefer external spill over abort when possible.
        "max_bytes_before_external_group_by": memory // 2,
        "max_bytes_before_external_sort": memory // 2,
    }


def _ch_project_days(ch: Any, *, project_id: str, deleted_col: str) -> list[str]:
    """Newest-first partition days for a project (partition key is toDate(start_time))."""
    rows = ch.execute(
        f"SELECT toString(toDate(start_time)) AS day "
        f"FROM spans PREWHERE project_id=%(project)s "
        f"WHERE {deleted_col}=0 "
        f"GROUP BY day ORDER BY day DESC",
        {"project": project_id},
        settings=_ch_scan_settings(max_memory_bytes=512 * 1024**2),
    )
    return [str(row[0]) for row in rows]


def _ch_count_map_pending(
    ch: Any,
    *,
    active: str,
    deleted_col: str,
    project_id: str | None = None,
) -> list[tuple[str, int]] | int:
    """Map-only pending counts. Safe for all-project scans (no attributes_extra)."""
    settings = _ch_scan_settings()
    if project_id:
        return int(
            ch.execute(
                f"SELECT count() FROM spans FINAL "
                f"PREWHERE project_id=%(project)s "
                f"WHERE {deleted_col}=0 AND {_ch_map_predicate(active)}",
                {"project": project_id},
                settings=settings,
            )[0][0]
        )
    # All-project map scan: avoid FINAL here (OOM + is_deleted PREWHERE bug).
    # Soft-deleted dupes are rare for historical Vapi URLs; reconcile is approximate.
    rows = ch.execute(
        f"SELECT toString(project_id), count() FROM spans "
        f"WHERE {deleted_col}=0 AND {_ch_map_predicate(active)} "
        f"GROUP BY project_id ORDER BY count() DESC",
        settings=settings,
    )
    return [(str(project), int(count)) for project, count in rows]


def _ch_count_extra_pending_day_chunked(
    ch: Any,
    *,
    active: str,
    extra: str,
    deleted_col: str,
    project_id: str,
) -> int:
    """Count extra-only pending spans one partition day at a time (avoids Code 241)."""
    total = 0
    settings = _ch_scan_settings()
    for day in _ch_project_days(ch, project_id=project_id, deleted_col=deleted_col):
        total += int(
            ch.execute(
                f"SELECT count() FROM spans FINAL "
                f"PREWHERE project_id=%(project)s AND toDate(start_time)=toDate(%(day)s) "
                f"WHERE {deleted_col}=0 "
                f"AND NOT {_ch_map_predicate(active)} "
                f"AND {_ch_extra_predicate(extra)}",
                {"project": project_id, "day": day},
                settings=settings,
            )[0][0]
        )
    return total


def _proof_light_select(
    stored: list[str], active: str, extra: str | None, version_col: str
) -> tuple[list[str], str]:
    """Columns Python is allowed to pull for proof-gate (never input/output/etc.)."""
    light = ["id", active, version_col]
    if extra:
        light.append(extra)
    ignored = set(light)
    others = [col for col in stored if col not in ignored]
    # Hash remaining wide columns server-side so Python never materializes them.
    other_hash = (
        f"cityHash64(tuple({', '.join(others)}))" if others else "toUInt64(0)"
    )
    return light, other_hash


def _proof_rows(
    ch: Any,
    *,
    stored: list[str],
    active: str,
    extra: str | None,
    version_col: str,
    project_id: str,
    span_ids: list[str],
) -> dict[str, tuple[Any, ...]]:
    """Load only id/map/extra/version + server-side hash of all other columns."""
    if not span_ids:
        return {}
    light, other_hash = _proof_light_select(stored, active, extra, version_col)
    rows = ch.execute(
        f"SELECT {', '.join(light)}, {other_hash} AS other_hash "
        f"FROM spans FINAL WHERE project_id=%(project)s AND id IN %(ids)s",
        {"project": project_id, "ids": tuple(span_ids)},
        settings={"max_execution_time": 120},
    )
    # row layout: light... + other_hash
    return {str(row[0]): row for row in rows}


def _assert_ch_gate_clear(ch: Any) -> None:
    pending_mutations = ch.execute(
        "SELECT count() FROM system.mutations WHERE database=currentDatabase() AND table='spans' AND is_done=0"
    )[0][0]
    if pending_mutations:
        raise RuntimeError(
            f"Proof gate blocked by {pending_mutations} pending spans mutations"
        )
    merge_count, max_elapsed = ch.execute(
        "SELECT count(), coalesce(max(elapsed), 0) FROM system.merges WHERE database=currentDatabase() AND table='spans'"
    )[0]
    max_merges = int(os.getenv("BACKFILL_MAX_ACTIVE_MERGES", "20"))
    max_merge_seconds = int(os.getenv("BACKFILL_MAX_MERGE_SECONDS", "300"))
    if merge_count > max_merges or max_elapsed > max_merge_seconds:
        raise RuntimeError(
            f"Proof gate merge backlog exceeded: count={merge_count}, max_elapsed={max_elapsed}"
        )


def _verify_proof_gate(
    *,
    ch: Any,
    stored: list[str],
    active: str,
    extra: str | None,
    project_id: str,
    before: dict[str, tuple[Any, ...]],
    mappings: list[dict[str, Any]],
    version_col: str = "_version",
) -> None:
    """Validate mapUpdate patches + optional attributes_extra exact rewrite.

    Python only inspects light columns (id/map/extra/version). Unrelated wide
    columns are compared via a server-side cityHash64 so we never OOM on
    input/output/raw payloads.
    """
    _assert_ch_gate_clear(ch)
    expected_ids = [item["span_id"] for item in mappings]
    after = _proof_rows(
        ch,
        stored=stored,
        active=active,
        extra=extra,
        version_col=version_col,
        project_id=project_id,
        span_ids=expected_ids,
    )
    expected = {item["span_id"]: item for item in mappings}
    light, _ = _proof_light_select(stored, active, extra, version_col)
    # layout: id, active, version, [extra], other_hash
    idx_active = 1
    idx_version = 2
    idx_extra = 3 if extra else None
    idx_hash = len(light)  # last
    missing = [span_id for span_id in expected_ids if span_id not in after]
    if missing:
        raise RuntimeError(f"Proof gate lost spans: {missing[:5]}")
    for span_id, item in expected.items():
        new_row = after[span_id]
        old_row = before.get(span_id)
        if old_row is not None and old_row[idx_hash] != new_row[idx_hash]:
            raise RuntimeError(
                f"Proof gate changed unrelated wide columns on {span_id}"
            )
        new_map = dict(new_row[idx_active] or {})
        patch = dict(item.get("patch_map") or {})
        for key, value in patch.items():
            if new_map.get(key) != value:
                raise RuntimeError(f"Proof gate map patch miss on {span_id}:{key}")
        if old_row is not None:
            old_map = dict(old_row[idx_active] or {})
            for key, value in old_map.items():
                if key not in patch and new_map.get(key) != value:
                    raise RuntimeError(
                        f"Proof gate map clobbered unrelated key {key} on {span_id}"
                    )
        if extra and idx_extra is not None:
            if int(item.get("extra_changed") or 0):
                if new_row[idx_extra] != item["extra"]:
                    raise RuntimeError(
                        f"Proof gate attributes_extra mismatch on {span_id}"
                    )
            elif old_row is not None and new_row[idx_extra] != old_row[idx_extra]:
                raise RuntimeError(
                    f"Proof gate unexpectedly rewrote attributes_extra on {span_id}"
                )
        if int(new_row[idx_version]) < int(item["version"]):
            raise RuntimeError(f"Proof gate version did not win on {span_id}")


@activity.defn(name="vapi-observability-backfill-batch")
def vapi_observability_backfill_batch(inp: VapiBackfillInput) -> VapiBackfillResult:
    from simulate.temporal.utils.async_storage import (
        _existing_rehosted_audio,
        _rehost_object_key_base,
    )
    from tracer.services.clickhouse.client import get_clickhouse_client

    if not inp.project_id:
        raise ValueError("--project-id is required for observability")
    if not RUN_ID_RE.fullmatch(inp.run_id):
        raise ValueError("invalid run_id")
    ch = get_clickhouse_client()
    active, extra, map_type, version_col, deleted_col = _ch_shape(ch)
    remaining = (
        inp.batch_size
        if inp.limit is None
        else min(inp.batch_size, inp.limit - inp.processed)
    )
    if remaining <= 0:
        return VapiBackfillResult(done=True, ch_scan_phase=inp.ch_scan_phase)

    columns = ["id", "project_id", "trace_id", "start_time", version_col, active] + (
        [extra] if extra else []
    )
    phase = inp.ch_scan_phase or "map"
    cursor_day = inp.cursor_day
    rows: list[tuple] = []
    scan_settings = _ch_scan_settings()

    def _keyset(params: dict[str, Any]) -> str:
        if inp.cursor_time and inp.cursor_trace_id and inp.cursor_id and (
            phase == "map" or cursor_day is not None
        ):
            # Only apply keyset when staying inside the same phase/day.
            params.update(
                time=inp.cursor_time, trace=inp.cursor_trace_id, id=inp.cursor_id
            )
            return "AND (start_time,trace_id,id) < (%(time)s,%(trace)s,%(id)s)"
        return ""

    if phase == "map":
        params = {
            "project": inp.project_id,
            "shards": inp.shards,
            "shard": inp.shard,
            "limit": remaining,
        }
        cursor = _keyset(params)
        # PREWHERE prunes deleted/project before evaluating Map values.
        rows = ch.execute(
            f"SELECT {','.join(columns)} FROM spans FINAL "
            f"PREWHERE project_id=%(project)s "
            f"WHERE {deleted_col}=0 "
            f"AND cityHash64(trace_id) %% %(shards)s=%(shard)s "
            f"AND {_ch_map_predicate(active)} {cursor} "
            "ORDER BY start_time DESC,trace_id DESC,id DESC LIMIT %(limit)s",
            params,
            settings=scan_settings,
        )
        if not rows and extra:
            phase = "extra"
            cursor_day = None
            # Clear map keyset so extra phase starts from newest day.
            inp = VapiBackfillInput(
                **{
                    **inp.__dict__,
                    "cursor_time": None,
                    "cursor_id": None,
                    "cursor_trace_id": None,
                }
            )

    if phase == "extra" and extra and remaining > 0 and not rows:
        days = _ch_project_days(
            ch, project_id=inp.project_id, deleted_col=deleted_col
        )
        if cursor_day is not None:
            # Resume from current day inclusive, then older days.
            days = [day for day in days if day <= cursor_day]
        for day in days:
            params = {
                "project": inp.project_id,
                "shards": inp.shards,
                "shard": inp.shard,
                "limit": remaining,
                "day": day,
            }
            # Fresh day → no keyset; same day resume → keyset.
            use_keyset = cursor_day == day and inp.cursor_time
            cursor = ""
            if use_keyset and inp.cursor_trace_id and inp.cursor_id:
                params.update(
                    time=inp.cursor_time,
                    trace=inp.cursor_trace_id,
                    id=inp.cursor_id,
                )
                cursor = "AND (start_time,trace_id,id) < (%(time)s,%(trace)s,%(id)s)"
            # Map-negative + extra-positive on a single partition day only.
            day_rows = ch.execute(
                f"SELECT {','.join(columns)} FROM spans FINAL "
                f"PREWHERE project_id=%(project)s AND toDate(start_time)=toDate(%(day)s) "
                f"WHERE {deleted_col}=0 "
                f"AND cityHash64(trace_id) %% %(shards)s=%(shard)s "
                f"AND NOT {_ch_map_predicate(active)} "
                f"AND {_ch_extra_predicate(extra)} {cursor} "
                "ORDER BY start_time DESC,trace_id DESC,id DESC LIMIT %(limit)s",
                params,
                settings=scan_settings,
            )
            if day_rows:
                rows = day_rows
                cursor_day = day
                break
            cursor_day = None  # exhausted this day
            # Do not carry map/extra keyset across days.
            inp = VapiBackfillInput(
                **{
                    **inp.__dict__,
                    "cursor_time": None,
                    "cursor_id": None,
                    "cursor_trace_id": None,
                }
            )

    out = VapiBackfillResult(
        done=not rows,
        ch_scan_phase=phase if rows else ("extra" if phase == "extra" else "map"),
        cursor_day=cursor_day if rows else None,
    )
    if not rows:
        # Finished map and (if present) all extra days.
        out.done = True
        out.ch_scan_phase = "extra" if extra else "map"
        return out
    out.cursor_id, out.cursor_trace_id, out.cursor_time = (
        str(rows[-1][0]),
        str(rows[-1][2]),
        rows[-1][3].isoformat(),
    )
    out.ch_scan_phase = phase
    out.cursor_day = cursor_day
    mappings = []
    for row in rows:
        span_id, project_id, _, _, version, attrs = row[:6]
        attrs, raw_extra = dict(attrs or {}), (row[6] if extra else "{}")
        extra_call_id, extra_urls = _extra_artifacts(raw_extra)
        call_id = _ch_call_id(attrs, extra_call_id)
        replacements: dict[str, str] = {}
        patch_map: dict[str, str] = {}
        for kind, keys in CH_KEYS.items():
            old = next(
                (attrs[key] for key in keys if _is_vapi_url(attrs.get(key))), None
            ) or extra_urls.get(kind)
            if not old:
                continue
            if not call_id:
                out.fatal += 1
                out.errors.append(f"span:{span_id}:{kind}: missing Vapi call ID")
                continue
            if inp.dry_run:
                out.changed += 1
                continue
            try:
                base = _rehost_object_key_base(
                    str(call_id), kind, str(project_id), "vapi"
                )
                audio = None
                audio_ext = None
                if not _existing_rehosted_audio(base):
                    # Resolve credentials only if authenticated Vapi API fallback runs.
                    audio, audio_ext = _download(
                        old,
                        str(call_id),
                        kind,
                        lambda attrs=attrs, project_id=str(project_id): (
                            _api_key_for_attributes(attrs, project_id)
                        ),
                    )
                try:
                    new = _stored(
                        str(project_id),
                        str(call_id),
                        kind,
                        old,
                        audio,
                        audio_ext=audio_ext,
                    )
                finally:
                    # Drop recording bytes immediately — never hold batch audio in RAM.
                    audio = None
                replacements[old] = new
                # Stage only changed Map keys; CH applies mapUpdate server-side.
                for key_name in keys:
                    if attrs.get(key_name) == old:
                        patch_map[key_name] = new
                out.changed += 1
            except Exception as exc:
                if _is_retryable(exc):
                    raise
                out.fatal += 1
                out.errors.append(f"span:{span_id}:{kind}: {type(exc).__name__}: {exc}")
        if replacements and not inp.dry_run:
            raw_extra_text = raw_extra or "{}"
            # attributes_extra is String JSON (migration 013). Rewrite only when
            # an exact old URL appears in the text; otherwise keep s.extra.
            extra_changed = any(old in raw_extra_text for old in replacements)
            mappings.append(
                {
                    "batch_seq": inp.batch_seq,
                    "span_id": str(span_id),
                    "patch_map": patch_map,
                    "extra": (
                        _json_replace(raw_extra_text, replacements)
                        if extra_changed
                        else ""
                    ),
                    "extra_changed": 1 if extra_changed else 0,
                    "version": max(time.time_ns(), int(version) + 1),
                }
            )
        out.processed += 1
        activity.heartbeat(out.processed, out.changed, out.fatal)
    if mappings:
        if inp.proof_gate:
            _assert_ch_gate_clear(ch)
        table = _mapping_table(inp.run_id, inp.shard)
        # Patch-only staging: mapUpdate merges changed keys; extra is rewritten
        # only when extra_changed=1 (String JSON, not typed JSON column).
        ch.execute(
            f"CREATE TABLE IF NOT EXISTS {table} ("
            f"batch_seq UInt64, span_id String, patch_map {map_type}, "
            f"extra String, extra_changed UInt8, version UInt64"
            f") ENGINE=MergeTree ORDER BY (batch_seq,span_id) "
            f"TTL toDateTime(intDiv(version,1000000000))+INTERVAL 90 DAY DELETE"
        )
        ch.insert(
            table,
            mappings,
            [
                "batch_seq",
                "span_id",
                "patch_map",
                "extra",
                "extra_changed",
                "version",
            ],
        )
        stored = [
            row[0]
            for row in ch.execute(
                "SELECT name FROM system.columns WHERE database=currentDatabase() AND table='spans' AND default_kind NOT IN ('MATERIALIZED','ALIAS') ORDER BY position"
            )
        ]
        before = (
            _proof_rows(
                ch,
                stored=stored,
                active=active,
                extra=extra,
                version_col=version_col,
                project_id=inp.project_id,
                span_ids=[item["span_id"] for item in mappings],
            )
            if inp.proof_gate
            else {}
        )
        expressions = []
        for col in stored:
            if col == active:
                # Official Map helper: keep existing keys, overwrite patch keys.
                expressions.append(f"mapUpdate(s.{active}, m.patch_map) AS {col}")
            elif extra and col == extra:
                expressions.append(
                    f"if(m.extra_changed = 1, m.extra, s.{extra}) AS {col}"
                )
            elif col == version_col:
                expressions.append(f"m.version AS {col}")
            else:
                expressions.append(f"s.{col}")
        ch.execute(
            f"INSERT INTO spans ({','.join(stored)}) SELECT {','.join(expressions)} "
            f"FROM spans AS s INNER JOIN {table} AS m "
            f"ON s.id=m.span_id AND m.batch_seq=%(batch)s "
            f"WHERE s.project_id=%(project)s AND s.{deleted_col}=0 "
            f"ORDER BY s.id, s.{version_col} DESC "
            f"LIMIT 1 BY s.id",
            {"batch": inp.batch_seq, "project": inp.project_id},
            settings={"max_execution_time": 120} if inp.proof_gate else None,
        )
        if inp.proof_gate:
            _verify_proof_gate(
                ch=ch,
                stored=stored,
                active=active,
                extra=extra,
                project_id=inp.project_id,
                before=before,
                mappings=mappings,
                version_col=version_col,
            )
    # Map phase not exhausted until a short page AND extra phase is finished.
    if phase == "map":
        if len(rows) >= remaining:
            out.done = False
            out.ch_scan_phase = "map"
        elif extra:
            # Switch to day-chunked extra-only scan on the next activity call.
            out.done = False
            out.ch_scan_phase = "extra"
            out.cursor_day = None
            out.cursor_time = None
            out.cursor_id = None
            out.cursor_trace_id = None
        else:
            out.done = True
    else:
        # Extra phase: short page means try older days on next call via cursor_day.
        if len(rows) >= remaining:
            out.done = False
            out.ch_scan_phase = "extra"
            out.cursor_day = cursor_day
        else:
            # Exhausted this day; advance to older days on next call.
            days = _ch_project_days(
                ch, project_id=inp.project_id, deleted_col=deleted_col
            )
            older = [day for day in days if cursor_day is None or day < cursor_day]
            if older:
                out.done = False
                out.ch_scan_phase = "extra"
                out.cursor_day = older[0]
                out.cursor_time = None
                out.cursor_id = None
                out.cursor_trace_id = None
            else:
                out.done = True
                out.ch_scan_phase = "extra"
    return out


@activity.defn(name="vapi-drop-observability-mapping")
def drop_observability_mapping(run_id: str, shard: int = 0) -> None:
    from tracer.services.clickhouse.client import get_clickhouse_client

    table = _mapping_table(run_id, shard)
    get_clickhouse_client().execute(f"DROP TABLE IF EXISTS {table}")


@activity.defn(name="vapi-backfill-reconcile-sample")
def reconcile_backfill_sample(inp: VapiBackfillInput) -> dict[str, Any]:
    """Report pending counts for operator reconciliation (no mutations).

    Observability never cold-scans attributes_extra across a whole project:
    - map_pending: cheap Map-only count (safe all-project)
    - extra_pending: day-chunked count of extra-only rows (per project)
    - spans_pending: map_pending + extra_pending
    """
    report: dict[str, Any] = {"source": inp.source, "project_id": inp.project_id}
    if inp.source == "simulation":
        from simulate.models.test_execution import CallExecution, CallExecutionSnapshot

        for name, model in (
            ("call_execution", CallExecution),
            ("call_execution_snapshot", CallExecutionSnapshot),
        ):
            qs = model.objects.filter(_pg_pending_q())
            if inp.project_id:
                qs = qs.filter(_pg_project_q(model.__name__, inp.project_id))
            report[name] = qs.count()
        return report
    from tracer.services.clickhouse.client import get_clickhouse_client

    ch = get_clickhouse_client()
    active, extra, _, _, deleted_col = _ch_shape(ch)
    report["shape"] = {
        "active": active,
        "extra": extra,
        "deleted": deleted_col,
    }
    if not inp.project_id:
        # All-project: map-only only (attributes_extra full scan OOMs).
        by_project = _ch_count_map_pending(
            ch, active=active, deleted_col=deleted_col, project_id=None
        )
        assert isinstance(by_project, list)
        report["mode"] = "map_only_all_projects"
        report["projects"] = len(by_project)
        report["map_pending_total"] = sum(count for _, count in by_project)
        report["by_project"] = [
            {"project_id": project, "map_pending": count} for project, count in by_project
        ]
        report["note"] = (
            "extra_pending requires --project-id (day-chunked). "
            "map_pending is the safe all-project baseline."
        )
        return report

    map_pending = _ch_count_map_pending(
        ch, active=active, deleted_col=deleted_col, project_id=inp.project_id
    )
    assert isinstance(map_pending, int)
    report["mode"] = "map_plus_day_chunked_extra"
    report["map_pending"] = map_pending
    if extra:
        extra_pending = _ch_count_extra_pending_day_chunked(
            ch,
            active=active,
            extra=extra,
            deleted_col=deleted_col,
            project_id=inp.project_id,
        )
    else:
        extra_pending = 0
    report["extra_pending"] = extra_pending
    report["spans_pending"] = map_pending + extra_pending
    return report


@workflow.defn(name="VapiMinimalBackfillWorkflow")
class VapiMinimalBackfillWorkflow:
    def __init__(self) -> None:
        self.paused = False
        self.cancelled = False
        self.progress: dict[str, Any] = {"state": "starting"}

    @workflow.signal
    async def pause(self) -> None:
        self.paused = True

    @workflow.signal
    async def resume(self) -> None:
        self.paused = False

    @workflow.signal
    async def cancel(self) -> None:
        # Soft-cancel: current activity drains, then workflow stops before the next batch.
        self.cancelled = True

    @workflow.query
    def status(self) -> dict[str, Any]:
        return {
            **self.progress,
            "paused": self.paused,
            "cancelled": self.cancelled,
        }

    async def _cleanup(self, inp: VapiBackfillInput) -> None:
        if inp.source == "observability" and not inp.dry_run:
            await workflow.execute_activity(
                drop_observability_mapping,
                args=[inp.run_id, inp.shard],
                task_queue="backfill",
                start_to_close_timeout=timedelta(minutes=2),
            )

    @workflow.run
    async def run(self, inp: VapiBackfillInput) -> VapiBackfillResult:
        if inp.source not in {"simulation", "observability"}:
            raise ValueError("invalid source")
        state = inp
        batches_in_history = 0
        self.progress = {
            "state": "running",
            "processed": state.processed,
            "changed": state.changed,
            "skipped": state.skipped,
            "fatal": state.fatal,
            "batch_seq": state.batch_seq,
            "shard": state.shard,
            "note": "cancel is soft: in-flight batch may finish mutating",
        }
        while True:
            await workflow.wait_condition(lambda: not self.paused or self.cancelled)
            if self.cancelled:
                await self._cleanup(state)
                return VapiBackfillResult(
                    processed=state.processed,
                    changed=state.changed,
                    skipped=state.skipped,
                    fatal=state.fatal,
                    errors=state.errors,
                    cursor_time=state.cursor_time,
                    cursor_id=state.cursor_id,
                    cursor_trace_id=state.cursor_trace_id,
                    ch_scan_phase=state.ch_scan_phase,
                    cursor_day=state.cursor_day,
                    ce_cursor_time=state.ce_cursor_time,
                    ce_cursor_id=state.ce_cursor_id,
                    snap_cursor_time=state.snap_cursor_time,
                    snap_cursor_id=state.snap_cursor_id,
                )
            fn = (
                vapi_simulation_backfill_batch
                if state.source == "simulation"
                else vapi_observability_backfill_batch
            )
            started_at = workflow.now()
            batch = await workflow.execute_activity(
                fn,
                state,
                task_queue="backfill",
                start_to_close_timeout=timedelta(minutes=20),
                heartbeat_timeout=timedelta(minutes=2),
                retry_policy=RetryPolicy(
                    initial_interval=timedelta(seconds=2),
                    backoff_coefficient=2.0,
                    maximum_interval=timedelta(minutes=2),
                    maximum_attempts=5,
                ),
            )
            elapsed = max((workflow.now() - started_at).total_seconds(), 0.001)
            totals = {
                "processed": state.processed + batch.processed,
                "changed": state.changed + batch.changed,
                "skipped": state.skipped + batch.skipped,
                "fatal": state.fatal + batch.fatal,
            }
            errors = (state.errors + batch.errors)[-100:]
            self.progress = {
                **totals,
                "batch_seq": state.batch_seq,
                "errors": len(errors),
                "records_per_second": batch.processed / elapsed,
                "state": "running",
                "shard": state.shard,
                "ch_scan_phase": batch.ch_scan_phase,
                "cursor_day": batch.cursor_day,
                "note": "cancel is soft: in-flight batch may finish mutating",
            }
            reached_limit = (
                state.limit is not None and totals["processed"] >= state.limit
            )
            if batch.done or reached_limit:
                await self._cleanup(state)
                if state.proof_gate and (
                    totals["processed"] != state.proof_gate or totals["fatal"] > 0
                ):
                    raise RuntimeError(
                        f"Proof gate failed: expected {state.proof_gate} rows, "
                        f"processed={totals['processed']}, fatal={totals['fatal']}"
                    )
                return VapiBackfillResult(
                    **totals,
                    cursor_time=batch.cursor_time,
                    cursor_id=batch.cursor_id,
                    cursor_trace_id=batch.cursor_trace_id,
                    ch_scan_phase=batch.ch_scan_phase,
                    cursor_day=batch.cursor_day,
                    ce_cursor_time=batch.ce_cursor_time,
                    ce_cursor_id=batch.ce_cursor_id,
                    snap_cursor_time=batch.snap_cursor_time,
                    snap_cursor_id=batch.snap_cursor_id,
                    errors=errors,
                )
            fatal_breach = batch.processed > 0 and batch.fatal / batch.processed > 0.05
            throughput_breach = (
                state.min_records_per_second > 0
                and elapsed >= 60
                and batch.processed / elapsed < state.min_records_per_second * 0.5
            )
            state = VapiBackfillInput(
                **{
                    **state.__dict__,
                    **totals,
                    "errors": errors,
                    "cursor_time": batch.cursor_time,
                    "cursor_id": batch.cursor_id,
                    "cursor_trace_id": batch.cursor_trace_id,
                    "ch_scan_phase": batch.ch_scan_phase,
                    "cursor_day": batch.cursor_day,
                    "ce_cursor_time": batch.ce_cursor_time,
                    "ce_cursor_id": batch.ce_cursor_id,
                    "snap_cursor_time": batch.snap_cursor_time,
                    "snap_cursor_id": batch.snap_cursor_id,
                    "batch_seq": state.batch_seq + 1,
                }
            )
            if fatal_breach or throughput_breach:
                self.paused = True
                self.progress["state"] = "auto_paused"
                self.progress["reason"] = (
                    "fatal_rate_above_5_percent"
                    if fatal_breach
                    else "throughput_below_50_percent"
                )
                await workflow.wait_condition(lambda: not self.paused or self.cancelled)
                if self.cancelled:
                    await self._cleanup(state)
                    return VapiBackfillResult(
                        **totals,
                        errors=errors,
                        cursor_time=state.cursor_time,
                        cursor_id=state.cursor_id,
                        cursor_trace_id=state.cursor_trace_id,
                        ch_scan_phase=state.ch_scan_phase,
                        cursor_day=state.cursor_day,
                        ce_cursor_time=state.ce_cursor_time,
                        ce_cursor_id=state.ce_cursor_id,
                        snap_cursor_time=state.snap_cursor_time,
                        snap_cursor_id=state.snap_cursor_id,
                    )
            if self.paused or self.cancelled:
                await workflow.wait_condition(lambda: not self.paused or self.cancelled)
                if self.cancelled:
                    await self._cleanup(state)
                    return VapiBackfillResult(
                        **totals,
                        errors=errors,
                        cursor_time=state.cursor_time,
                        cursor_id=state.cursor_id,
                        cursor_trace_id=state.cursor_trace_id,
                        ch_scan_phase=state.ch_scan_phase,
                        cursor_day=state.cursor_day,
                        ce_cursor_time=state.ce_cursor_time,
                        ce_cursor_id=state.ce_cursor_id,
                        snap_cursor_time=state.snap_cursor_time,
                        snap_cursor_id=state.snap_cursor_id,
                    )
            batches_in_history += 1
            if (
                batches_in_history >= 200
                or workflow.info().is_continue_as_new_suggested()
            ):
                workflow.continue_as_new(state)


def get_workflows() -> list[type]:
    return [VapiMinimalBackfillWorkflow]


def get_activities() -> list[Any]:
    return [
        vapi_simulation_backfill_batch,
        vapi_observability_backfill_batch,
        drop_observability_mapping,
        reconcile_backfill_sample,
    ]
