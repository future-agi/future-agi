"""Which rows an Instant eval run reads, and what each row is grouped by.

A run is asked about a view: its unit, its filters and its date window. The
server resolves that population itself, with the same ClickHouse list builders
as the view's grid, so a run is not limited to the ids a browser can collect.

- ``count_population`` is the grid's own count query.
- ``pick_rows`` takes the next ``n`` rows of the view in ``cityHash64(row id)``
  order. That order is a uniform random sample, the same every time, and it
  extends: the next pick continues after the largest hash already read.
- ``read_facets`` reads, for those rows, what the Ask page groups by (time,
  user, model, turns, duration, error, a short excerpt) in a few ClickHouse
  reads, and ``write_rows`` stores them once in ``tracer_instant_eval_row``.
  It reads the rows in the order their spans start, so each read covers a
  slice of the window; the pick hands it when each row's spans start.

``resolve_instant_rows`` is the eval-task resolver's hook: the first reconcile
of a run (and the one after "Read more") picks and stores the rows, and every
reconcile returns the run's rows in sample order.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from django.db import transaction

logger = structlog.get_logger(__name__)

EXCERPT_CHARS = 280
# Enough of a span's input to find its first user message.
_RAW_INPUT_CHARS = 6000
_FACET_BATCH = 5000
_WRITE_BATCH = 5000
# Users per label lookup: it binds their ids three times over, at some 40
# bytes each, and ClickHouse parses at most 256 KiB of a statement. A lookup
# past that fails, and comes back with no labels.
_LABEL_BATCH = 1000
SESSIONS_PER_USER = 5

# The eval task row type and the id column each grid's builder emits.
_BUILDER = {
    "traces": ("TRACE_LIST", "trace_id"),
    "spans": ("SPAN_LIST", "id"),
    "sessions": ("SESSION_LIST", "session_id"),
}

# Set in the task's filters by the start endpoint: the task reads the rows its
# run stored, not a filter query of its own.
RUN_MARKER = "instant_eval_run"

ROW_TYPE = {
    "traces": "traces",
    "spans": "spans",
    "sessions": "sessions",
    "users": "sessions",
}


class PopulationUnavailable(ValueError):
    """The view's filters can't be resolved on the server; the caller says so
    and the page hands over the rows it can see instead."""


@dataclass
class Population:
    count: int
    lower_bound: bool
    date_from: datetime | None
    date_to: datetime | None
    filters: list[dict]


def _frozen_filters(
    filters: list[dict], row_type: str
) -> tuple[list[dict], datetime, datetime]:
    """The view's filters with its window pinned to absolute times, so the
    run reads the same rows whenever its resolver runs."""
    from tracer.selectors.eval_tasks.row_resolver import _task_ui_filters
    from tracer.services.clickhouse.query_builders.base import BaseQueryBuilder

    try:
        ui_filters = _task_ui_filters(
            {"filters": list(filters or [])}, row_type=row_type
        )
        start, end = BaseQueryBuilder.parse_time_range(ui_filters)
    except (TypeError, ValueError) as exc:
        raise PopulationUnavailable(
            "These filters can't be read on the server."
        ) from exc
    # The builders read naive datetimes as UTC.
    start, end = (_aware(v) for v in (start, end))
    pinned = [
        item
        for item in ui_filters
        if not (
            (item.get("column_id") in ("created_at", "start_time"))
            and (item.get("filter_config") or {}).get("filter_op") == "between"
        )
    ]
    pinned.append(
        {
            "column_id": "created_at",
            "filter_config": {
                "filter_type": "datetime",
                "filter_op": "between",
                "filter_value": [start.isoformat(), end.isoformat()],
            },
        }
    )
    return pinned, start, end


def window_problem(filters: list[dict]) -> str | None:
    """Why a view's window can't be read, refused as the bounded lists
    refuse it, rather than read as the last 30 days: a range that ends before
    it starts, or a date that can't be read. None when it reads."""
    from tracer.services.clickhouse.query_builders.base import BaseQueryBuilder

    try:
        BaseQueryBuilder.parse_time_range(filters, strict=True)
    except ValueError as exc:
        if "before or equal to its end" in str(exc):
            return "The date range ends before it starts."
        return "The date range has a date that can't be read."
    return None


_USER_OPS = {"in", "equals", "not_in", "not_equals", "is_null", "is_not_null"}


def _session_user_filters(project_id: str, filters: list[dict]) -> list[dict]:
    """A User ID filter as the Sessions grid reads it: its view resolves each
    label to the users' ids first (the session builder can't read the label),
    a positive set of no one matching nothing and a negative one restricting
    nothing."""
    from tracer.services.clickhouse.query_builders.base import NIL_UUID
    from tracer.services.clickhouse.v2.end_user_dict_reader import (
        resolve_end_user_ids_by_user_id,
    )
    from tracer.services.user_filter_capabilities import is_native_user_id_filter
    from tracer.utils.filters import FilterEngine

    out = []
    resolved: dict[str, list[str]] = {}
    for item in filters:
        if not is_native_user_id_filter(item):
            out.append(item)
            continue
        _, config = FilterEngine._normalize_filter_params(item)
        op = config.get("filter_op") or "in"
        if op not in _USER_OPS:
            raise PopulationUnavailable(
                "The Sessions view can't filter users that way."
            )
        leaf = {"col_type": "SYSTEM_METRIC", "filter_type": "text", "filter_op": op}
        if op not in ("is_null", "is_not_null"):
            values = config.get("filter_value")
            values = values if isinstance(values, list) else [values]
            values = [str(v) for v in values if v not in (None, "")]
            if not values:
                continue
            ids: list[str] = []
            for value in dict.fromkeys(values):
                if value not in resolved:
                    resolved[value] = resolve_end_user_ids_by_user_id(
                        value, project_id=str(project_id)
                    )
                ids.extend(resolved[value])
            negated = op in ("not_in", "not_equals")
            if not ids:
                if negated:
                    continue
                ids = [NIL_UUID]
            leaf["filter_op"] = "not_in" if negated else "in"
            leaf["filter_value"] = list(dict.fromkeys(ids))
        out.append({"column_id": "end_user_id", "filter_config": leaf})
    return out


def _builder(project_id: str, row_type: str, ui_filters: list[dict]):
    from tracer.services.clickhouse.v2.dispatch import get_v2_class

    query_type, id_col = _BUILDER[row_type]
    if row_type == "sessions":
        ui_filters = _session_user_filters(project_id, ui_filters)
    return (
        get_v2_class(query_type)(project_id=str(project_id), filters=ui_filters),
        id_col,
    )


def _ch(sql: str, params: dict) -> list[dict]:
    from tracer.services.clickhouse.v2.query_service import V2AnalyticsQueryService

    return (
        V2AnalyticsQueryService()
        .execute_ch_query(sql, params, timeout_ms=None, settings={})
        .data
    )


def count_population(
    project_id: str, unit: str, filters: list[dict], *, search: str | None = None
) -> Population:
    """How many rows the view holds, from the grid's own count query (for
    users, the Users list's: ``instant_eval_users``, with its search text)."""
    if unit == "users":
        from tracer.services.instant_eval_users import count_users

        return count_users(project_id, filters, search=search)
    row_type = ROW_TYPE[unit]
    pinned, start, end = _frozen_filters(filters, row_type)
    try:
        builder, _ = _builder(project_id, row_type, pinned)
        # The grids build their page first; that binds the window the count
        # query reads.
        builder.build()
        sql, params = builder.build_count_query()
    except PopulationUnavailable:
        raise
    except ValueError as exc:
        raise PopulationUnavailable(
            "These filters can't be counted on the server."
        ) from exc
    rows = _ch(sql, params)
    total = int((rows[0].get("total") if rows else 0) or 0)
    return Population(total, False, start, end, pinned)


def pick_rows(
    project_id: str,
    unit: str,
    filters: list[dict],
    *,
    n: int,
    after: int | None,
    extents: dict[str, Extent | None] | None = None,
    window: tuple[datetime | None, datetime | None] = (None, None),
    search: str | None = None,
) -> list[tuple[str, int]]:
    """The next ``n`` rows of the view in hash order, as (row id, hash).

    Given ``extents``, the same statement also fills it with each row's
    ``Extent`` in the facet window of the run's ``window``, or None for a row
    with no spans there: what ``read_facets`` batches and bounds its reads
    by. The rows are picked again inside it, rather than bound as ids. Users
    are the Users list's (``instant_eval_users``), with its search text."""
    if n <= 0:
        return []
    if unit == "users":
        from tracer.services.instant_eval_users import pick_users

        return pick_users(project_id, filters, n=n, after=after, search=search)
    row_type = ROW_TYPE[unit]
    builder, id_col = _builder(project_id, row_type, filters)
    inner, params = builder.build_id_query()
    params = dict(params)
    params["ie_n"] = int(n)
    where = ""
    if after is not None:
        params["ie_after"] = int(after)
        where = "WHERE h > %(ie_after)s"
    sql = (
        "SELECT rid, h FROM ("
        f"SELECT toString({id_col}) AS rid, cityHash64(toString({id_col})) AS h "
        f"FROM ({inner})) {where} ORDER BY h, rid LIMIT %(ie_n)s"
    )
    if extents is None:
        return [(str(r["rid"]), int(r["h"])) for r in _ch(sql, params) if r.get("rid")]
    span_window, window_params = _facet_window(*window, prefix="ie_")
    params.update(window_params, ie_pid=str(project_id))
    spans = _extent_sql(
        row_type, f"(SELECT rid FROM ({sql}))", span_window, pid="ie_pid"
    )
    rows = _ch(
        "SELECT p.rid AS rid, p.h AS h, e.rid AS spanned, e.t0 AS t0, e.t1 AS t1 "
        f"FROM ({sql}) AS p LEFT JOIN ({spans}) AS e ON p.rid = e.rid "
        "ORDER BY h, rid",
        params,
    )
    picked = []
    for r in rows:
        if not r.get("rid"):
            continue
        rid = str(r["rid"])
        picked.append((rid, int(r["h"])))
        extents[rid] = (
            Extent(_naive_utc(r["t0"]), _naive_utc(r["t1"]))
            if r.get("spanned")
            else None
        )
    return picked


def row_hashes(ids: Iterable[str]) -> dict[str, int]:
    """cityHash64 of hand-over ids, so they are read in the same order a sample
    would read them."""
    ids = [str(i) for i in ids if i]
    if not ids:
        return {}
    rows = _ch(
        "SELECT x AS rid, cityHash64(x) AS h FROM (SELECT arrayJoin(%(ids)s) AS x)",
        {"ids": ids},
    )
    return {str(r["rid"]): int(r["h"]) for r in rows}


# Rows written at once past which the planner is told about them straight
# away: a run's rows are a new task id its statistics don't know, and an
# estimate of one row turns the summary's joins into nested loops.
ANALYZE_AFTER = 1000


def analyze(table: str) -> None:
    from django.db import connection

    with connection.cursor() as cur:
        cur.execute(f"ANALYZE {table}")


def _chunks(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i : i + size]


_USER_CONTENT = re.compile(
    r'"role"\s*:\s*"(?:user|human)"[^{}]*?"content"\s*:\s*"((?:[^"\\]|\\.)*)"', re.S
)


def message_text(value) -> str:
    """Readable text from a span input/output, which may be a chat array: its
    last user message, a text field, or the value itself (the same object,
    when nothing in it is more readable). Insights reads spans the same way."""
    if value is None:
        return ""
    text = value if isinstance(value, str) else json.dumps(value)
    stripped = text.strip()
    if stripped[:1] in "[{":
        try:
            parsed = json.loads(stripped)
        except ValueError:
            return text
        if isinstance(parsed, dict):
            for key in ("content", "text", "message", "output", "input", "query"):
                if isinstance(parsed.get(key), str):
                    return parsed[key]
            messages = parsed.get("messages")
            parsed = messages if isinstance(messages, list) else parsed
        if isinstance(parsed, list):
            for message in reversed(parsed):
                if isinstance(message, dict) and message.get("role") in (
                    "user",
                    "human",
                ):
                    content = message.get("content")
                    return content if isinstance(content, str) else json.dumps(content)
    return text


def _user_labels(end_user_ids) -> dict[str, str]:
    """Each user's label from the end-user dictionary, for the users that
    have one; none when the dictionary can't be read."""
    from tracer.services.clickhouse.v2.end_user_dict_reader import resolve_user_ids

    try:
        return {k: v for k, v in resolve_user_ids(end_user_ids).items() if v}
    except Exception:
        logger.warning("instant_eval_user_labels_unreadable", exc_info=True)
        return {}


def excerpt_of(raw: str | None) -> str:
    """The first readable line of a span's input: a chat input's user message,
    or the text itself."""
    raw = raw or ""
    text = message_text(raw)
    if text is raw and raw.lstrip()[:1] in "[{":
        # Cut off mid-JSON: find a user message the parser couldn't reach.
        match = _USER_CONTENT.search(raw)
        if match:
            try:
                text = json.loads(f'"{match.group(1)}"')
            except ValueError:
                text = match.group(1)
    text = " ".join(str(text or "").split())
    return text if len(text) <= EXCERPT_CHARS else text[: EXCERPT_CHARS - 1] + "…"


def _naive_utc(value):
    if isinstance(value, datetime) and value.tzinfo is not None:
        return value.astimezone(UTC).replace(tzinfo=None)
    return value


def _aware(value) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


# One ClickHouse read per batch of rows. Errors count on any span of the row:
# a failed tool call is a child span.
_FACET_SQL = {
    "sessions": (
        "SELECT toString(trace_session_id) AS rid, min(start_time) AS t, "
        "anyIf(end_user_id, end_user_id IS NOT NULL) AS uid, "
        "uniqExactIf(trace_id, parent_span_id = '' OR parent_span_id IS NULL) AS turns, "
        "dateDiff('millisecond', min(start_time), max(coalesce(end_time, start_time))) AS dur, "
        "max(status = 'ERROR') AS err, argMinIf(model, start_time, model != '') AS m, "
        "argMinIf(substring(if(attrs_string['input.value'] != '', attrs_string['input.value'], input), 1, %(raw)s), "
        "start_time, parent_span_id = '' OR parent_span_id IS NULL) AS raw, '' AS tid, '' AS kind, '' AS name, "
        # What a session read under several ids needs to be put together.
        "max(coalesce(end_time, start_time)) AS t_end, "
        "groupUniqArrayIf(trace_id, parent_span_id = '' OR parent_span_id IS NULL) AS traces, "
        "minIf(start_time, model != '') AS m_at, "
        "minIf(start_time, parent_span_id = '' OR parent_span_id IS NULL) AS raw_at "
        "FROM spans WHERE project_id = %(pid)s AND is_deleted = 0 "
        "AND toString(trace_session_id) IN %(ids)s {window} GROUP BY trace_session_id"
    ),
    "traces": (
        "SELECT trace_id AS rid, minIf(start_time, parent_span_id = '' OR parent_span_id IS NULL) AS t, "
        "anyIf(end_user_id, end_user_id IS NOT NULL) AS uid, toUInt64(0) AS turns, "
        "maxIf(latency_ms, parent_span_id = '' OR parent_span_id IS NULL) AS dur, "
        "max(status = 'ERROR') AS err, argMinIf(model, start_time, model != '') AS m, "
        "argMinIf(substring(if(attrs_string['input.value'] != '', attrs_string['input.value'], input), 1, %(raw)s), "
        "start_time, parent_span_id = '' OR parent_span_id IS NULL) AS raw, '' AS tid, '' AS kind, '' AS name "
        "FROM spans WHERE project_id = %(pid)s AND is_deleted = 0 "
        "AND trace_id IN %(ids)s {window} GROUP BY trace_id"
    ),
    "spans": (
        "SELECT id AS rid, any(start_time) AS t, any(end_user_id) AS uid, toUInt64(0) AS turns, "
        "any(latency_ms) AS dur, max(status = 'ERROR') AS err, any(model) AS m, "
        "any(substring(if(attrs_string['input.value'] != '', attrs_string['input.value'], input), 1, %(raw)s)) AS raw, "
        "any(trace_id) AS tid, any(observation_type) AS kind, any(name) AS name "
        "FROM spans WHERE project_id = %(pid)s AND is_deleted = 0 "
        "AND id IN %(ids)s {window} GROUP BY id"
    ),
}


# The column a row's spans are found by, as each facet read matches it.
_FACET_KEY = {
    "sessions": "toString(trace_session_id)",
    "traces": "trace_id",
    "spans": "id",
}
# A batch reads a minute either side of its rows' spans: bound parameters
# keep whole seconds.
_EDGE = timedelta(minutes=1)


@dataclass(frozen=True)
class Extent:
    """When a row's spans start inside the facet window: the first and the
    last of them."""

    first: datetime
    last: datetime


def _facet_window(
    start: datetime | None, end: datetime | None, *, prefix: str = ""
) -> tuple[str, dict[str, Any]]:
    """The window a row's spans are read in: the run's window and a day either
    side, since sessions and traces run past its edges. Unbounded without a
    run window (hand-overs name rows from any time)."""
    if start is None or end is None:
        return "", {}
    return (
        f"AND start_time >= %({prefix}w_start)s AND start_time < %({prefix}w_end)s",
        {
            f"{prefix}w_start": _naive_utc(start) - timedelta(days=1),
            f"{prefix}w_end": _naive_utc(end) + timedelta(days=1),
        },
    )


def _extent_sql(row_type: str, ids: str, window: str, *, pid: str = "pid") -> str:
    """Each row's first and last span start, over the spans its facets read."""
    key = _FACET_KEY[row_type]
    return (
        f"SELECT {key} AS rid, min(start_time) AS t0, max(start_time) AS t1 "
        f"FROM spans WHERE project_id = %({pid})s AND is_deleted = 0 "
        f"AND {key} IN {ids} {window} GROUP BY rid"
    )


def _row_extents(
    project_id: str,
    row_type: str,
    ids: list[str],
    *,
    start: datetime | None = None,
    end: datetime | None = None,
) -> dict[str, Extent]:
    """Each row's ``Extent`` in the facet window, for rows the pick didn't
    read it for (hand-overs, a user's sessions). One narrow read per batch of
    rows; a row with no spans in the window is left out."""
    window, params = _facet_window(start, end)
    sql = _extent_sql(row_type, "%(ids)s", window)
    out: dict[str, Extent] = {}
    for chunk in _chunks(ids, _FACET_BATCH):
        for row in _ch(sql, {"pid": str(project_id), "ids": tuple(chunk), **params}):
            out[str(row["rid"])] = Extent(_naive_utc(row["t0"]), _naive_utc(row["t1"]))
    return out


def _resolve(row_type: str, ids: list[str]) -> tuple[dict, dict[str, str]]:
    """``({survivor: the ids asked that resolve to it}, {raw id: survivor})``:
    a session through the id remap (``CHSpanReader.resolve_ids``); a trace or
    a span is only itself."""
    if row_type != "sessions":
        return {i: [i] for i in ids}, {i: i for i in ids}
    from tracer.services.clickhouse.v2 import get_reader

    with get_reader() as reader:
        return reader.resolve_ids("trace_session_id_remap", ids)


def _user_survivors(user_ids: set[str]) -> dict[str, str]:
    """``{end user id: its survivor}`` for the ids a remap group holds."""
    if not user_ids:
        return {}
    from tracer.services.clickhouse.v2 import get_reader

    with get_reader() as reader:
        return reader.remap_groups("end_user_id_remap", user_ids)


def _ms(value: datetime) -> int:
    """Whole milliseconds since the epoch, as ClickHouse's ``dateDiff``
    counts them."""
    return (_naive_utc(value) - datetime(1970, 1, 1)) // timedelta(milliseconds=1)


def _merged(parts: list[dict]) -> dict:
    """One session's facet row from its rows under each id it was read by:
    its first start, user, model and message, every turn once, and the time
    from its first start to its last end."""
    if len(parts) == 1:
        return parts[0]
    parts = sorted(parts, key=lambda r: _naive_utc(r["t"]))
    rooted = [r for r in parts if r["traces"]]
    modelled = [r for r in parts if r["m"]]
    t_end = max(_naive_utc(r["t_end"]) for r in parts)
    return {
        **parts[0],
        "uid": next((r["uid"] for r in parts if r["uid"]), None),
        "turns": len({trace for r in parts for trace in r["traces"]}),
        "dur": _ms(t_end) - _ms(parts[0]["t"]),
        "err": max(r["err"] for r in parts),
        "m": (
            min(modelled, key=lambda r: (_naive_utc(r["m_at"]), r["m"]))["m"]
            if modelled
            else ""
        ),
        "raw": (
            min(rooted, key=lambda r: (_naive_utc(r["raw_at"]), r["raw"]))["raw"]
            if rooted
            else ""
        ),
    }


def read_facets(
    project_id: str,
    row_type: str,
    ids: list[str],
    *,
    start: datetime | None = None,
    end: datetime | None = None,
    extents: dict[str, Extent | None] | None = None,
) -> dict[str, dict]:
    """What the Ask page shows and groups each row by. Rows with no spans in
    the project are left out, which is also how hand-over ids are checked.

    A session reads through the id remap: a straddler's or a consolidated
    group's spans make one row, under every session id asked for that
    resolves to the group, and every row's user is its survivor.

    More rows than one read takes are read in the order their spans start,
    and each read covers only its rows' spans (their ``extents``, from the
    pick, or read here first) rather than the whole window: a sample spread
    over a month reads each day about once. Every span a row reads starts
    inside its batch's bounds, so its facets are what a read of the whole
    window gives."""
    askers, aliases = _resolve(row_type, list(dict.fromkeys(str(i) for i in ids)))
    raw_ids = list(aliases)
    window, params_window = _facet_window(start, end)
    sql = _FACET_SQL[row_type].format(window=window)
    batches: list[tuple[list[str], dict[str, Any]]] = [(raw_ids, {})] if raw_ids else []
    if len(raw_ids) > _FACET_BATCH:
        # Spans are read by the ids they carry, so batches are of those ids.
        known = {i: e for i, e in (extents or {}).items() if i in aliases}
        missing = [i for i in raw_ids if i not in known]
        if missing:
            known.update(dict.fromkeys(missing))
            known.update(
                _row_extents(project_id, row_type, missing, start=start, end=end)
            )
        spanned = sorted(
            (i for i in raw_ids if known.get(i) is not None),
            key=lambda i: (known[i].first, i),
        )
        sql = _FACET_SQL[row_type].format(
            window=f"{window} AND start_time >= %(b_start)s AND start_time <= %(b_end)s"
        )
        batches = [
            (
                chunk,
                {
                    "b_start": min(known[i].first for i in chunk) - _EDGE,
                    "b_end": max(known[i].last for i in chunk) + _EDGE,
                },
            )
            for chunk in _chunks(spanned, _FACET_BATCH)
        ]
    parts: dict[str, list[dict]] = defaultdict(list)
    for chunk, bounds in batches:
        rows = _ch(
            sql,
            {
                "pid": str(project_id),
                "ids": tuple(chunk),
                "raw": _RAW_INPUT_CHARS,
                **params_window,
                **bounds,
            },
        )
        for row in rows:
            parts[aliases[str(row["rid"])]].append(row)
    out: dict[str, dict] = {}
    for survivor, rows in parts.items():
        row = _merged(rows)
        text = excerpt_of(row.get("raw"))
        if row_type == "spans":
            kind = str(row.get("kind") or "span").lower()
            text = (
                f"{kind} · {row.get('name') or ''}: {text}"
                if text
                else f"{kind} · {row.get('name') or ''}"
            )
        duration = row.get("dur")
        facets = {
            "time": _aware(row.get("t")),
            "end_user_id": str(row["uid"]) if row.get("uid") else None,
            "model": str(row.get("m") or "")[:255],
            "turns": (
                int(row["turns"])
                if row_type == "sessions" and row.get("turns")
                else None
            ),
            "duration_ms": (
                int(duration) if duration is not None and int(duration) >= 0 else None
            ),
            "error": bool(row.get("err")),
            "excerpt": text,
            "trace_id": str(row.get("tid") or "") or None,
        }
        for asker in askers[survivor]:
            out[asker] = dict(facets)
    survivors = _user_survivors(
        {f["end_user_id"] for f in out.values() if f["end_user_id"]}
    )
    for facets in out.values():
        user = facets["end_user_id"]
        facets["end_user_id"] = survivors.get(user, user) if user else user
    labels = {}
    users = list({f["end_user_id"] for f in out.values() if f["end_user_id"]})
    for chunk in _chunks(users, _LABEL_BATCH):
        labels.update(_user_labels(chunk))
    for facets in out.values():
        facets["user_label"] = str(labels.get(facets["end_user_id"] or "", "") or "")[
            :255
        ]
    return out


def write_rows(
    task_id, unit: str, ranked: list[tuple[str, int]], facets: dict[str, dict]
) -> int:
    """Store the rows a run reads with their facets, in sample order. A row
    already stored keeps its place (the unique index ignores it)."""
    from tracer.models.instant_eval import InstantEvalRow

    row_type = ROW_TYPE[unit]
    objs = [
        InstantEvalRow(
            task_id=task_id,
            row_id=row_id,
            unit=row_type,
            sample_rank=rank,
            time=f["time"],
            end_user_id=f["end_user_id"],
            user_label=f["user_label"],
            model=f["model"],
            turns=f["turns"],
            duration_ms=f["duration_ms"],
            error=f["error"],
            excerpt=f["excerpt"],
            trace_id=f["trace_id"] if row_type == "spans" else None,
        )
        for row_id, rank in ranked
        if (f := facets.get(row_id)) is not None
    ]
    for chunk in _chunks(objs, _WRITE_BATCH):
        InstantEvalRow.objects.bulk_create(chunk, ignore_conflicts=True)
    if len(objs) >= ANALYZE_AFTER:
        analyze("tracer_instant_eval_row")
    return len(objs)


def extend_run(run) -> int:
    """Pick and store rows until the run holds ``rows_requested`` of them (or
    the view runs out). Returns how many rows were added."""
    from tracer.models.instant_eval import InstantEvalRow
    from tracer.services import instant_eval_users

    task = run.eval_task
    stored = InstantEvalRow.objects.filter(task_id=task.id)
    users = run.unit == "users"
    # A user question counts the users it read; each brings its sessions.
    done = {
        str(v)
        for v in stored.values_list("end_user_id" if users else "row_id", flat=True)
        if v is not None
    }
    wanted = int(run.rows_requested or 0) - len(done)
    if wanted <= 0:
        return 0
    rank_base = stored.count()
    project_id = str(run.project_id)
    row_type = ROW_TYPE[run.unit]
    explicit = _explicit_ids(task)
    # Hand-overs name rows from any time; a view's rows sit in its window. A
    # user's sessions are read inside the run's window either way: one started
    # before runs kept it reads the 30 days before it started, as the lists do.
    start, end = (
        (run.date_from, run.date_to) if explicit is None or users else (None, None)
    )
    if users and (start is None or end is None):
        start, end = run.created_at - timedelta(days=30), run.created_at
    # Past one facet read, when each picked row's spans start: each read
    # then covers a slice of time.
    extents: dict[str, Extent | None] = {}
    if explicit is not None:
        hashes = row_hashes(explicit)
        picked = sorted(hashes.items(), key=lambda x: (x[1], x[0]))
        picked = [p for p in picked if p[0] not in done][:wanted]
    else:
        after = int(run.hash_cursor) if run.hash_cursor else None
        picked = pick_rows(
            project_id,
            run.unit,
            run.filters,
            n=wanted,
            after=after,
            extents=extents if wanted > _FACET_BATCH else None,
            window=(start, end),
            search=run.search,
        )
    if not picked:
        return 0
    cursor = max(h for _, h in picked)
    if users:
        # A user's sessions are read like a session question's: in the run's
        # window and a day either side.
        session_ids = instant_eval_users.user_sessions(
            project_id, [rid for rid, _ in picked], start, end
        )
        hashes = row_hashes(session_ids)
        ranked_ids = sorted(session_ids, key=lambda s: (hashes.get(s, 0), s))
    else:
        ranked_ids = [rid for rid, _ in picked]
    facets = read_facets(
        project_id, row_type, ranked_ids, start=start, end=end, extents=extents
    )
    ranked = [(rid, rank_base + i) for i, rid in enumerate(ranked_ids)]
    with transaction.atomic():
        added = write_rows(task.id, run.unit, ranked, facets)
        type(run).objects.filter(id=run.id).update(hash_cursor=str(cursor))
    logger.info(
        "instant_eval_rows_written",
        task_id=str(task.id),
        picked=len(picked),
        added=added,
    )
    return added


def _explicit_ids(task) -> list[str] | None:
    """Rows handed over by id (a selection, an Insights category), if any."""
    raw = (task.filters or {}).get("instant_ids")
    if not isinstance(raw, list):
        return None
    return list(dict.fromkeys(str(v) for v in raw if v not in (None, "")))


def resolve_instant_rows(task) -> tuple[str, ...] | None:
    """The eval-task resolver's hook: a task started by Instant eval reads the
    rows its run picked, in sample order. None for any other task."""
    filters = getattr(task, "filters", None)
    if not isinstance(filters, dict) or not filters.get(RUN_MARKER):
        return None
    from tracer.models.instant_eval import InstantEvalRow, InstantEvalRun

    run = (
        InstantEvalRun.objects.filter(eval_task_id=task.id)
        .select_related("eval_task")
        .first()
    )
    if run is None:
        return None
    extend_run(run)
    return tuple(
        InstantEvalRow.objects.filter(task_id=task.id)
        .order_by("sample_rank", "id")
        .values_list("row_id", flat=True)
    )
