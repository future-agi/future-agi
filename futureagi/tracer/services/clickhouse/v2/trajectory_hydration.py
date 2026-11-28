"""Read-side hydration for Phase 0 sentinel compaction and Phase 1 Trajectory Store.

Mirrors the Go write-side contract in ``fi-collector/pkg/trajectory/``:

  - Phase 0 (intra-span compaction):
      ``__fi_promoted_input_key`` / ``__fi_promoted_output_key`` (plus optional
      ``__fi_promoted_*_json`` boolean flags) sit in ``attributes_extra`` when
      duplicate promoted payloads were stripped at ingest. Readers pop these
      sentinels and restore the exact attribute from ``spans.input`` /
      ``spans.output`` (after Phase 1 hydration, when applicable).

  - Phase 1 (content-addressed trajectory store):
      Spans with ``payload_encoding == 'items_v1'`` store compact previews in
      ``spans.input`` / ``spans.output`` alongside ordered hash lists
      (``input_item_hashes``, ``output_item_hashes``, ``tool_def_hashes``),
      ``item_scope_id``, and ``payload_envelope``. A single batched ClickHouse
      read against ``trajectory_items`` keyed by ``(project_id, scope_id)``
      fetches the items, and each extracted side is rebuilt by concatenating
      the envelope's literal gaps with the item contents in slot order. No
      JSON is parsed or re-serialised, so the result is the exact string the
      collector received, checked against the envelope's checksum.

      Envelope (version 2, written by ``pkg/trajectory/reconstruct.go``)::

          {"v": 2,
           "in":  {"g": [gap0, ..., gapN], "r": "smmt...", "sh": "p:<hash>",
                   "il": <code points>, "x": "<sha256 prefix>"},
           "out": {"g": [...], "r": "mm...", "il": ..., "x": ...}}

      ``r`` has one character per slot: ``m`` takes the next entry of the
      side's ``*_item_hashes``, ``t`` the next ``tool_def_hashes`` entry and
      ``s`` the side's ``sh``. ``g`` has ``len(r) + 1`` literals and the side
      is ``g0 + c(r0) + g1 + ... + gN``. ``x`` is the hex of the first 16
      bytes of SHA-256 over the side's UTF-8 bytes, the same digest prefix as
      ``item_hash``.

      Legacy ``payload_encoding == 'inline'`` spans trigger ZERO queries
      against ``trajectory_items``.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import replace
from typing import Any

logger = logging.getLogger(__name__)

PROJECT_SCOPE_ID = "__project__"
PROJECT_HASH_PREFIX = "p:"

ENCODING_INLINE = "inline"
ENCODING_ITEMS_V1 = "items_v1"

SENTINEL_PROMOTED_INPUT_KEY = "__fi_promoted_input_key"
SENTINEL_PROMOTED_INPUT_JSON = "__fi_promoted_input_json"
SENTINEL_PROMOTED_OUTPUT_KEY = "__fi_promoted_output_key"
SENTINEL_PROMOTED_OUTPUT_JSON = "__fi_promoted_output_json"

_SENTINEL_MARKER = "__fi_promoted_"

ENVELOPE_VERSION = 2
SLOT_MESSAGE = "m"
SLOT_TOOL = "t"
SLOT_SYSTEM = "s"


class TrajectoryContentUnavailable(Exception):
    """Stored trajectory content does not rebuild into the original payload.

    Raised when the rebuilt text fails the envelope checksum. Hydrators keep
    the inline preview for that side and log the failure.
    """


def payload_checksum(text: str) -> str:
    """Hex of the first 16 bytes of SHA-256 over ``text`` (UTF-8)."""
    return hashlib.sha256(text.encode("utf-8")).digest()[:16].hex()


TRAJECTORY_SPAN_COLUMNS: tuple[str, ...] = (
    "input_item_hashes",
    "output_item_hashes",
    "tool_def_hashes",
    "item_scope_id",
    "payload_encoding",
    "payload_envelope",
)


def is_trajectory_store_read_enabled() -> bool:
    """Return True only when 040_trajectory_items.sql has been applied and
    read-side trajectory hydration is explicitly enabled via Django settings.

    Defaults to False so deploying app code before running 040 emits zero
    references to the six additive ``spans`` pointer columns or the
    ``trajectory_items`` table.
    """
    try:
        from django.conf import settings as django_settings
        from django.core.exceptions import ImproperlyConfigured
    except ImportError:
        return False
    try:
        return bool(getattr(django_settings, "FI_TRAJECTORY_STORE_READ_ENABLED", False))
    except ImproperlyConfigured:
        return False


def _normalize_hash(value: bytes | bytearray | str) -> str:
    if isinstance(value, (bytes, bytearray)):
        return value.decode("ascii", errors="ignore").rstrip("\x00")
    return str(value).rstrip("\x00")


def _resolve_scope_and_hash(prefixed_hash: str, default_scope: str) -> tuple[str, str]:
    if prefixed_hash.startswith(PROJECT_HASH_PREFIX):
        return PROJECT_SCOPE_ID, prefixed_hash[len(PROJECT_HASH_PREFIX) :]
    return default_scope, prefixed_hash


def _side_slot_refs(
    shape: Mapping[str, Any] | None,
    hashes: Sequence[str] | None,
    tool_hashes: Sequence[str] | None,
) -> list[str] | None:
    """Return the ref of every slot of one side template, in slot order.

    ``None`` when the template is malformed or its slot counts disagree with
    the hash arrays (``count('m') != len(hashes)``, ``count('t') !=
    len(tool_hashes)``, or an ``s`` slot without ``sh``).
    """
    if not isinstance(shape, Mapping):
        return None
    gaps = shape.get("g")
    slots = shape.get("r")
    if (
        not isinstance(gaps, list)
        or not isinstance(slots, str)
        or len(gaps) != len(slots) + 1
        or not all(isinstance(g, str) for g in gaps)
    ):
        return None
    msgs = [str(h) for h in (hashes or ())]
    tools = [str(h) for h in (tool_hashes or ())]
    if slots.count(SLOT_MESSAGE) != len(msgs) or slots.count(SLOT_TOOL) != len(tools):
        return None
    sh = shape.get("sh")
    refs: list[str] = []
    msg_iter = iter(msgs)
    tool_iter = iter(tools)
    for slot in slots:
        if slot == SLOT_MESSAGE:
            refs.append(next(msg_iter))
        elif slot == SLOT_TOOL:
            refs.append(next(tool_iter))
        elif slot == SLOT_SYSTEM:
            if not isinstance(sh, str) or not sh:
                return None
            refs.append(sh)
        else:
            return None
    return refs


def _collect_required_refs(
    *,
    project_id: str,
    item_scope_id: str,
    input_hashes: Sequence[str] | None,
    output_hashes: Sequence[str] | None,
    tool_hashes: Sequence[str] | None,
    envelope: Mapping[str, Any] | None,
) -> list[tuple[str, str, str]]:
    """Return the ``(project_id, scope_id, raw_hash)`` tuples one span's slots reference."""
    if not project_id or not isinstance(envelope, Mapping):
        return []
    side_refs = (
        _side_slot_refs(envelope.get("in"), input_hashes, tool_hashes),
        _side_slot_refs(envelope.get("out"), output_hashes, None),
    )
    refs: list[tuple[str, str, str]] = []
    for slot_refs in side_refs:
        for ref in slot_refs or ():
            if ref:
                scope, raw = _resolve_scope_and_hash(ref, item_scope_id)
                if scope and raw:
                    refs.append((project_id, scope, raw))
    return refs


def _parse_json_item(raw: str) -> Any:
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return raw


def reconstruct_payload(
    shape: Mapping[str, Any] | None,
    hashes: Sequence[str] | None,
    tool_hashes: Sequence[str] | None,
    resolve: Callable[[str], str | None],
) -> str | None:
    """Rebuild one side's original text from its envelope template.

    Pure concatenation of the literal gaps and the resolved item contents in
    slot order. Returns ``None`` when the template does not match the hash
    arrays or an item is missing, so callers keep the inline preview. Raises
    ``TrajectoryContentUnavailable`` when the rebuilt text fails the
    template's checksum.
    """
    slot_refs = _side_slot_refs(shape, hashes, tool_hashes)
    if slot_refs is None or not isinstance(shape, Mapping):
        return None
    gaps: list[str] = shape["g"]
    parts = [gaps[0]]
    for ref, gap in zip(slot_refs, gaps[1:], strict=True):
        content = resolve(ref)
        if content is None:
            return None
        parts.append(content)
        parts.append(gap)
    result = "".join(parts)
    if payload_checksum(result) != shape.get("x"):
        raise TrajectoryContentUnavailable("trajectory payload checksum mismatch")
    return result


def _parse_envelope(raw: Any) -> dict[str, Any] | None:
    if not raw:
        return None
    parsed: Any = raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("trajectory_envelope_unparseable")
            return None
    if not isinstance(parsed, dict):
        return None
    if parsed.get("v") != ENVELOPE_VERSION:
        logger.warning(
            "trajectory_envelope_version_rejected version=%r", parsed.get("v")
        )
        return None
    return parsed


_MAX_SCOPES_PER_QUERY = 500
_MAX_HASHES_PER_QUERY = 1500


def _chunk_project_refs(
    refs: Iterable[tuple[str, str, str]],
) -> list[tuple[str, tuple[str, ...], tuple[str, ...]]]:
    """Group refs by project and split into bounded (project, scopes, hashes) chunks.

    Bounds each ClickHouse query well below max_query_size (256 KiB) and
    max_result_rows even for large dataset exports or multi-hundred-span traces.
    """
    by_proj_scope: dict[str, dict[str, list[str]]] = {}
    seen: set[tuple[str, str, str]] = set()
    for project_id, scope_id, item_hash in refs:
        if not project_id or not scope_id or not item_hash:
            continue
        key = (project_id, scope_id, item_hash)
        if key in seen:
            continue
        seen.add(key)
        by_proj_scope.setdefault(project_id, {}).setdefault(scope_id, []).append(
            item_hash
        )

    chunks: list[tuple[str, tuple[str, ...], tuple[str, ...]]] = []
    for project_id, scope_map in by_proj_scope.items():
        cur_scopes: list[str] = []
        cur_hashes: list[str] = []
        cur_hash_set: set[str] = set()
        for scope_id, hashes in scope_map.items():
            # Split oversized single scopes cleanly so each query stays bounded.
            for off in range(0, len(hashes), _MAX_HASHES_PER_QUERY):
                h_slice = hashes[off : off + _MAX_HASHES_PER_QUERY]
                new_hashes = [h for h in h_slice if h not in cur_hash_set]
                if cur_scopes and (
                    len(cur_scopes) >= _MAX_SCOPES_PER_QUERY
                    or len(cur_hashes) + len(new_hashes) > _MAX_HASHES_PER_QUERY
                ):
                    chunks.append((project_id, tuple(cur_scopes), tuple(cur_hashes)))
                    cur_scopes = []
                    cur_hashes = []
                    cur_hash_set = set()
                    new_hashes = h_slice
                if not cur_scopes or cur_scopes[-1] != scope_id:
                    cur_scopes.append(scope_id)
                for h in new_hashes:
                    cur_hash_set.add(h)
                    cur_hashes.append(h)
        if cur_scopes and cur_hashes:
            chunks.append((project_id, tuple(cur_scopes), tuple(cur_hashes)))
    return chunks


def build_trajectory_items_query(
    *, project_id: str, scope_ids: Iterable[str], item_hashes: Iterable[str]
) -> tuple[str, dict[str, Any]]:
    """Build a single 3-key PK-pruned query against ``trajectory_items``."""
    scopes = tuple(dict.fromkeys(str(s) for s in scope_ids if s))
    hashes = tuple(dict.fromkeys(str(h) for h in item_hashes if h))
    if not project_id or not scopes or not hashes:
        return "", {}
    sql = """
        SELECT
            scope_id,
            item_hash,
            argMax(content, persisted_at) AS content
        FROM trajectory_items
        PREWHERE project_id = %(traj_project_id)s
          AND scope_id IN %(traj_scope_ids)s
          AND item_hash IN %(traj_item_hashes)s
        GROUP BY scope_id, item_hash
    """
    params = {
        "traj_project_id": str(project_id),
        "traj_scope_ids": scopes,
        "traj_item_hashes": hashes,
    }
    return sql, params


def fetch_trajectory_items_dict_rows(
    query_fn: Callable[[str, dict[str, Any]], list[dict[str, Any]]],
    refs: Iterable[tuple[str, str, str]],
) -> dict[tuple[str, str, str], str]:
    """Fetch referenced items using a dict-row query callback (analytics executor)."""
    resolved: dict[tuple[str, str, str], str] = {}
    for project_id, scopes, hashes in _chunk_project_refs(refs):
        sql, params = build_trajectory_items_query(
            project_id=project_id, scope_ids=scopes, item_hashes=hashes
        )
        if not sql:
            continue
        for row in query_fn(sql, params):
            scope = str(row.get("scope_id") or "")
            item_hash = _normalize_hash(row.get("item_hash") or "")
            content = row.get("content")
            if scope and item_hash and content is not None:
                resolved[(project_id, scope, item_hash)] = str(content)
    return resolved


def fetch_trajectory_items_tuple_rows(
    query_fn: Callable[[str, dict[str, Any]], Sequence[tuple[Any, ...]]],
    refs: Iterable[tuple[str, str, str]],
) -> dict[tuple[str, str, str], str]:
    """Fetch referenced items using a tuple-row query callback (CHSpanReader client)."""
    resolved: dict[tuple[str, str, str], str] = {}
    for project_id, scopes, hashes in _chunk_project_refs(refs):
        sql, params = build_trajectory_items_query(
            project_id=project_id, scope_ids=scopes, item_hashes=hashes
        )
        if not sql:
            continue
        for row in query_fn(sql, params):
            if len(row) < 3:
                continue
            scope = str(row[0] or "")
            item_hash = _normalize_hash(row[1] or "")
            content = row[2]
            if scope and item_hash and content is not None:
                resolved[(project_id, scope, item_hash)] = str(content)
    return resolved


def restore_promoted_attributes(
    attrs: dict[str, Any],
    *,
    span_input: str | None = None,
    span_output: str | None = None,
) -> dict[str, Any]:
    """Pop Phase 0 compaction sentinels and re-inject original promoted attributes.

    With the trajectory-store read flag off the attributes are returned untouched:
    the collector writes no sentinels then, and a customer key that merely shares
    a sentinel name must keep its value.
    """
    if not is_trajectory_store_read_enabled():
        return attrs
    in_key = attrs.pop(SENTINEL_PROMOTED_INPUT_KEY, None)
    in_was_json = bool(attrs.pop(SENTINEL_PROMOTED_INPUT_JSON, False))
    out_key = attrs.pop(SENTINEL_PROMOTED_OUTPUT_KEY, None)
    out_was_json = bool(attrs.pop(SENTINEL_PROMOTED_OUTPUT_JSON, False))

    if (
        isinstance(in_key, str)
        and in_key
        and in_key not in attrs
        and span_input is not None
    ):
        attrs[in_key] = _parse_json_item(span_input) if in_was_json else span_input

    if (
        isinstance(out_key, str)
        and out_key
        and out_key not in attrs
        and span_output is not None
    ):
        attrs[out_key] = _parse_json_item(span_output) if out_was_json else span_output

    return attrs


def restore_promoted_in_attributes_extra(
    raw_extra: str | dict[str, Any] | None,
    *,
    span_input: str | None = None,
    span_output: str | None = None,
) -> str | dict[str, Any] | None:
    """Restore Phase 0 promoted attributes inside a raw ``attributes_extra`` JSON string or dict.

    Fast-paths in O(1) when no sentinel marker is present so legacy spans incur
    zero JSON parsing overhead.
    """
    if not is_trajectory_store_read_enabled():
        return raw_extra
    if isinstance(raw_extra, dict):
        if not any(
            k in raw_extra
            for k in (SENTINEL_PROMOTED_INPUT_KEY, SENTINEL_PROMOTED_OUTPUT_KEY)
        ):
            return raw_extra
        copy = dict(raw_extra)
        restore_promoted_attributes(
            copy, span_input=span_input, span_output=span_output
        )
        return copy

    if isinstance(raw_extra, str):
        if _SENTINEL_MARKER not in raw_extra:
            return raw_extra
        try:
            parsed = json.loads(raw_extra)
        except json.JSONDecodeError:
            return raw_extra
        if not isinstance(parsed, dict):
            return raw_extra
        restore_promoted_attributes(
            parsed, span_input=span_input, span_output=span_output
        )
        return json.dumps(parsed, ensure_ascii=False, separators=(",", ":"))

    return raw_extra


def _hydrate_envelope_fields(
    env: dict[str, Any],
    *,
    project_id: str,
    item_scope_id: str,
    input_hashes: Sequence[str] | None,
    output_hashes: Sequence[str] | None,
    tool_hashes: Sequence[str] | None,
    resolved: dict[tuple[str, str, str], str],
) -> tuple[str | None, str | None]:
    def _resolve(prefixed_hash: str) -> str | None:
        s, raw = _resolve_scope_and_hash(prefixed_hash, item_scope_id)
        return resolved.get((project_id, s, raw))

    def _side(
        side: str, hashes: Sequence[str] | None, tools: Sequence[str] | None
    ) -> str | None:
        shape = env.get(side)
        if shape is None:
            return None
        try:
            return reconstruct_payload(shape, hashes, tools, _resolve)
        except TrajectoryContentUnavailable:
            logger.warning(
                "trajectory_hydration_checksum_mismatch side=%s project_id=%s scope_id=%s",
                side,
                project_id,
                item_scope_id,
            )
            return None

    return _side("in", input_hashes, tool_hashes), _side("out", output_hashes, None)


def hydrate_span_dicts(
    rows: list[dict[str, Any]],
    query_fn: Callable[[str, dict[str, Any]], list[dict[str, Any]]],
    *,
    project_id_fallback: str | None = None,
    attr_key: str = "span_attributes",
) -> list[dict[str, Any]]:
    """Hydrate ``input``, ``output``, and Phase 0 sentinels on dict span rows in place.

    Executes ZERO ClickHouse queries if no row has ``payload_encoding == 'items_v1'``.
    """
    if not rows:
        return rows

    envelopes: dict[int, dict[str, Any]] = {}
    refs: list[tuple[str, str, str]] = []
    for idx, row in enumerate(rows):
        if row.get("payload_encoding") != ENCODING_ITEMS_V1:
            continue
        env = _parse_envelope(row.get("payload_envelope"))
        if not env:
            continue
        pid = str(row.get("project_id") or project_id_fallback or "")
        scope = str(row.get("item_scope_id") or "")
        span_refs = _collect_required_refs(
            project_id=pid,
            item_scope_id=scope,
            input_hashes=row.get("input_item_hashes"),
            output_hashes=row.get("output_item_hashes"),
            tool_hashes=row.get("tool_def_hashes"),
            envelope=env,
        )
        if span_refs:
            envelopes[idx] = env
            refs.extend(span_refs)

    resolved: dict[tuple[str, str, str], str] = {}
    if refs:
        resolved = fetch_trajectory_items_dict_rows(query_fn, refs)

    for idx, row in enumerate(rows):
        env = envelopes.get(idx)
        if env is not None:
            pid = str(row.get("project_id") or project_id_fallback or "")
            scope = str(row.get("item_scope_id") or "")
            hydrated_in, hydrated_out = _hydrate_envelope_fields(
                env,
                project_id=pid,
                item_scope_id=scope,
                input_hashes=row.get("input_item_hashes"),
                output_hashes=row.get("output_item_hashes"),
                tool_hashes=row.get("tool_def_hashes"),
                resolved=resolved,
            )
            if env.get("in") is not None and hydrated_in is None:
                logger.warning(
                    "trajectory_hydration_missing_items side=in project_id=%s span_id=%s scope_id=%s",
                    pid,
                    row.get("id", ""),
                    scope,
                )
            elif hydrated_in is not None:
                row["input"] = hydrated_in
            if env.get("out") is not None and hydrated_out is None:
                logger.warning(
                    "trajectory_hydration_missing_items side=out project_id=%s span_id=%s scope_id=%s",
                    pid,
                    row.get("id", ""),
                    scope,
                )
            elif hydrated_out is not None:
                row["output"] = hydrated_out

        if attr_key in row:
            row[attr_key] = restore_promoted_in_attributes_extra(
                row[attr_key],
                span_input=row.get("input"),
                span_output=row.get("output"),
            )

    return rows


def hydrate_chspans(
    spans: list[Any],
    query_fn: Callable[[str, dict[str, Any]], Sequence[tuple[Any, ...]]],
) -> list[Any]:
    """Hydrate ``CHSpan`` instances (frozen dataclasses) via one batched query.

    Executes ZERO ClickHouse queries when all spans have ``payload_encoding != 'items_v1'``.
    """
    if not spans:
        return spans

    envelopes: dict[int, dict[str, Any]] = {}
    refs: list[tuple[str, str, str]] = []
    for idx, span in enumerate(spans):
        if getattr(span, "payload_encoding", ENCODING_INLINE) != ENCODING_ITEMS_V1:
            continue
        env = _parse_envelope(getattr(span, "payload_envelope", ""))
        if not env:
            continue
        pid = str(getattr(span, "project_id", "") or "")
        scope = str(getattr(span, "item_scope_id", "") or "")
        span_refs = _collect_required_refs(
            project_id=pid,
            item_scope_id=scope,
            input_hashes=getattr(span, "input_item_hashes", ()),
            output_hashes=getattr(span, "output_item_hashes", ()),
            tool_hashes=getattr(span, "tool_def_hashes", ()),
            envelope=env,
        )
        if span_refs:
            envelopes[idx] = env
            refs.extend(span_refs)

    resolved = fetch_trajectory_items_tuple_rows(query_fn, refs) if refs else {}
    out = list(spans)
    for idx, span in enumerate(out):
        updates: dict[str, Any] = {}
        env = envelopes.get(idx)
        if env is not None:
            pid = str(span.project_id or "")
            scope = str(span.item_scope_id or "")
            hydrated_in, hydrated_out = _hydrate_envelope_fields(
                env,
                project_id=pid,
                item_scope_id=scope,
                input_hashes=span.input_item_hashes,
                output_hashes=span.output_item_hashes,
                tool_hashes=span.tool_def_hashes,
                resolved=resolved,
            )
            if env.get("in") is not None and hydrated_in is None:
                logger.warning(
                    "trajectory_hydration_missing_items side=in project_id=%s span_id=%s scope_id=%s",
                    pid,
                    getattr(span, "id", ""),
                    scope,
                )
            elif hydrated_in is not None:
                updates["input"] = hydrated_in
            if env.get("out") is not None and hydrated_out is None:
                logger.warning(
                    "trajectory_hydration_missing_items side=out project_id=%s span_id=%s scope_id=%s",
                    pid,
                    getattr(span, "id", ""),
                    scope,
                )
            elif hydrated_out is not None:
                updates["output"] = hydrated_out

        raw_extra = getattr(span, "attributes_extra", None)
        if isinstance(raw_extra, str) and _SENTINEL_MARKER in raw_extra:
            eff_in = updates.get("input", getattr(span, "input", None))
            eff_out = updates.get("output", getattr(span, "output", None))
            restored_extra = restore_promoted_in_attributes_extra(
                raw_extra,
                span_input=eff_in,
                span_output=eff_out,
            )
            if restored_extra != raw_extra:
                updates["attributes_extra"] = restored_extra

        if updates:
            out[idx] = replace(span, **updates)

    return out
