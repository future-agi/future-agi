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
      reconstructs the exact original JSON payloads transparently.

      Legacy ``payload_encoding == 'inline'`` spans trigger ZERO queries
      against ``trajectory_items``.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import replace
from typing import Any

PROJECT_SCOPE_ID = "__project__"
PROJECT_HASH_PREFIX = "p:"

ENCODING_INLINE = "inline"
ENCODING_ITEMS_V1 = "items_v1"

SENTINEL_PROMOTED_INPUT_KEY = "__fi_promoted_input_key"
SENTINEL_PROMOTED_INPUT_JSON = "__fi_promoted_input_json"
SENTINEL_PROMOTED_OUTPUT_KEY = "__fi_promoted_output_key"
SENTINEL_PROMOTED_OUTPUT_JSON = "__fi_promoted_output_json"

_SENTINEL_MARKER = "__fi_promoted_"

TRAJECTORY_SPAN_COLUMNS: tuple[str, ...] = (
    "input_item_hashes",
    "output_item_hashes",
    "tool_def_hashes",
    "item_scope_id",
    "payload_encoding",
    "payload_envelope",
)


def _normalize_hash(value: Any) -> str:
    if isinstance(value, (bytes, bytearray)):
        return value.decode("ascii", errors="ignore").rstrip("\x00")
    return str(value).rstrip("\x00")


def _resolve_scope_and_hash(prefixed_hash: str, default_scope: str) -> tuple[str, str]:
    if prefixed_hash.startswith(PROJECT_HASH_PREFIX):
        return PROJECT_SCOPE_ID, prefixed_hash[len(PROJECT_HASH_PREFIX) :]
    return default_scope, prefixed_hash


def _collect_required_refs(
    *,
    project_id: str,
    item_scope_id: str,
    input_hashes: Sequence[str] | None,
    output_hashes: Sequence[str] | None,
    tool_hashes: Sequence[str] | None,
    envelope: Mapping[str, Any] | None,
) -> list[tuple[str, str, str]]:
    """Return all ``(project_id, scope_id, raw_hash)`` tuples referenced by one span."""
    if not project_id:
        return []
    refs: list[tuple[str, str, str]] = []
    for h in input_hashes or ():
        if h:
            scope, raw = _resolve_scope_and_hash(str(h), item_scope_id)
            if scope and raw:
                refs.append((project_id, scope, raw))
    for h in output_hashes or ():
        if h:
            scope, raw = _resolve_scope_and_hash(str(h), item_scope_id)
            if scope and raw:
                refs.append((project_id, scope, raw))
    for h in tool_hashes or ():
        if h:
            scope, raw = _resolve_scope_and_hash(str(h), item_scope_id)
            if scope and raw:
                refs.append((project_id, scope, raw))
    if isinstance(envelope, Mapping):
        for side in ("in", "out"):
            shape = envelope.get(side)
            if isinstance(shape, Mapping):
                sh = shape.get("sh")
                if isinstance(sh, str) and sh:
                    scope, raw = _resolve_scope_and_hash(sh, item_scope_id)
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
    """Reconstruct a span's original JSON input/output from its envelope shape and hashes.

    Returns ``None`` if any referenced item is missing from ``trajectory_items``
    so callers safely preserve the inline preview instead of corrupting output.
    """
    if not isinstance(shape, Mapping):
        return None

    fmt = shape.get("f")
    if fmt == "msg_array":
        if not hashes:
            return None
        items: list[Any] = []
        for h in hashes:
            content = resolve(str(h))
            if content is None:
                return None
            items.append(_parse_json_item(content))
        return json.dumps(items, ensure_ascii=False, separators=(",", ":"))

    if fmt == "obj_messages":
        wrapper = shape.get("w")
        obj: dict[str, Any] = dict(wrapper) if isinstance(wrapper, Mapping) else {}

        sk = shape.get("sk")
        sh = shape.get("sh")
        if isinstance(sk, str) and sk and isinstance(sh, str) and sh:
            sys_content = resolve(sh)
            if sys_content is None:
                return None
            obj[sk] = _parse_json_item(sys_content)

        tk = shape.get("tk")
        if isinstance(tk, str) and tk:
            tools: list[Any] = []
            for h in tool_hashes or ():
                content = resolve(str(h))
                if content is None:
                    return None
                tools.append(_parse_json_item(content))
            obj[tk] = tools

        mk = shape.get("mk")
        if isinstance(mk, str) and mk:
            msgs: list[Any] = []
            for h in hashes or ():
                content = resolve(str(h))
                if content is None:
                    return None
                msgs.append(_parse_json_item(content))
            obj[mk] = msgs

        return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))

    return None


def _parse_envelope(raw: Any) -> dict[str, Any] | None:
    if not raw:
        return None
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def build_trajectory_items_query(
    *, project_id: str, scope_ids: Iterable[str], item_hashes: Iterable[str]
) -> tuple[str, dict[str, Any]]:
    """Build a single PK-pruned query against ``trajectory_items``."""
    scopes = tuple(dict.fromkeys(str(s) for s in scope_ids if s))
    hashes = tuple(dict.fromkeys(str(h) for h in item_hashes if h))
    if not project_id or not scopes or not hashes:
        return "", {}
    sql = """
        SELECT
            scope_id,
            item_hash,
            argMax(content, first_seen_at) AS content
        FROM trajectory_items
        PREWHERE project_id = %(traj_project_id)s
          AND scope_id IN %(traj_scope_ids)s
        WHERE item_hash IN %(traj_item_hashes)s
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
    by_project: dict[str, tuple[set[str], set[str]]] = {}
    for project_id, scope_id, item_hash in refs:
        scopes, hashes = by_project.setdefault(project_id, (set(), set()))
        scopes.add(scope_id)
        hashes.add(item_hash)

    resolved: dict[tuple[str, str, str], str] = {}
    for project_id, (scopes, hashes) in by_project.items():
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
    by_project: dict[str, tuple[set[str], set[str]]] = {}
    for project_id, scope_id, item_hash in refs:
        scopes, hashes = by_project.setdefault(project_id, (set(), set()))
        scopes.add(scope_id)
        hashes.add(item_hash)

    resolved: dict[tuple[str, str, str], str] = {}
    for project_id, (scopes, hashes) in by_project.items():
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
    """Pop Phase 0 compaction sentinels and re-inject original promoted attributes."""
    in_key = attrs.pop(SENTINEL_PROMOTED_INPUT_KEY, None)
    in_was_json = bool(attrs.pop(SENTINEL_PROMOTED_INPUT_JSON, False))
    out_key = attrs.pop(SENTINEL_PROMOTED_OUTPUT_KEY, None)
    out_was_json = bool(attrs.pop(SENTINEL_PROMOTED_OUTPUT_JSON, False))

    if isinstance(in_key, str) and in_key and in_key not in attrs and span_input is not None:
        attrs[in_key] = _parse_json_item(span_input) if in_was_json else span_input

    if isinstance(out_key, str) and out_key and out_key not in attrs and span_output is not None:
        attrs[out_key] = _parse_json_item(span_output) if out_was_json else span_output

    return attrs


def restore_promoted_in_attributes_extra(
    raw_extra: Any,
    *,
    span_input: str | None = None,
    span_output: str | None = None,
) -> Any:
    """Restore Phase 0 promoted attributes inside a raw ``attributes_extra`` JSON string or dict.

    Fast-paths in O(1) when no sentinel marker is present so legacy spans incur
    zero JSON parsing overhead.
    """
    if isinstance(raw_extra, dict):
        if not any(
            k in raw_extra
            for k in (SENTINEL_PROMOTED_INPUT_KEY, SENTINEL_PROMOTED_OUTPUT_KEY)
        ):
            return raw_extra
        copy = dict(raw_extra)
        restore_promoted_attributes(copy, span_input=span_input, span_output=span_output)
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
        restore_promoted_attributes(parsed, span_input=span_input, span_output=span_output)
        return json.dumps(parsed, ensure_ascii=False, separators=(",", ":"))

    return raw_extra


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

            def _resolve(prefixed_hash: str, _pid: str = pid, _scope: str = scope) -> str | None:
                s, raw = _resolve_scope_and_hash(prefixed_hash, _scope)
                return resolved.get((_pid, s, raw))

            in_shape = env.get("in")
            if in_shape is not None:
                hydrated_in = reconstruct_payload(
                    in_shape,
                    row.get("input_item_hashes"),
                    row.get("tool_def_hashes"),
                    _resolve,
                )
                if hydrated_in is not None:
                    row["input"] = hydrated_in

            out_shape = env.get("out")
            if out_shape is not None:
                hydrated_out = reconstruct_payload(
                    out_shape,
                    row.get("output_item_hashes"),
                    None,
                    _resolve,
                )
                if hydrated_out is not None:
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

    if not refs:
        return spans

    resolved = fetch_trajectory_items_tuple_rows(query_fn, refs)
    out = list(spans)
    for idx, env in envelopes.items():
        span = out[idx]
        pid = str(span.project_id or "")
        scope = str(span.item_scope_id or "")

        def _resolve(prefixed_hash: str, _pid: str = pid, _scope: str = scope) -> str | None:
            s, raw = _resolve_scope_and_hash(prefixed_hash, _scope)
            return resolved.get((_pid, s, raw))

        updates: dict[str, Any] = {}
        in_shape = env.get("in")
        if in_shape is not None:
            hydrated_in = reconstruct_payload(
                in_shape,
                span.input_item_hashes,
                span.tool_def_hashes,
                _resolve,
            )
            if hydrated_in is not None:
                updates["input"] = hydrated_in

        out_shape = env.get("out")
        if out_shape is not None:
            hydrated_out = reconstruct_payload(
                out_shape,
                span.output_item_hashes,
                None,
                _resolve,
            )
            if hydrated_out is not None:
                updates["output"] = hydrated_out

        if updates:
            out[idx] = replace(span, **updates)

    return out
