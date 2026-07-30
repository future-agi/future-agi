#!/usr/bin/env python3
"""
validate_migration — 3-layer post-backfill parity validation.

Layer A (counts):
    For every (project_id, day) bucket in PG, count active (non-soft-deleted)
    rows. Compare to CH `spans FINAL` count for the same bucket. Any delta is
    a bucket-level disagreement that operators MUST triage.

Layer B (sampled deep-equal):
    Draw N random span ids from PG. For each, fetch the PG row, run it
    through the adapter to produce an "expected" CH row, fetch the actual
    CH row, and field-by-field compare. Every difference is surfaced —
    this is the strongest check because it pins both the adapter AND the
    round-trip through ClickHouse.

Layer C (top-20 query parity):
    Run a representative set of analytical queries against both PG and the
    new CH `spans` table; compare result shapes and values. This catches
    semantic regressions (e.g. an index/projection that returns subtly
    different aggregations than the PG truth).

Output: a JSON report. Pass --report=FILE to write to disk; default is
stdout. Exit codes:
    0 — all layers pass
    1 — usage error (bad flags, missing inputs)
    2 — at least one layer failed
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import clickhouse_connect
import psycopg
import structlog
from psycopg.rows import dict_row

# Reuse the adapter so Layer B's "expected" matches what the backfill actually wrote.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from adapter import (                                     # noqa: E402
    CH_INSERT_COLUMNS,
    adapt,
    row_to_tuple,
)


# ─── Logging ──────────────────────────────────────────────────────────────────
structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.processors.JSONRenderer(),
    ],
)
log = structlog.get_logger("validate")


# ─── PG / CH client helpers ───────────────────────────────────────────────────
def _pg_conn(args) -> psycopg.Connection:
    conn = psycopg.connect(
        host=args.pg_host, port=args.pg_port, user=args.pg_user,
        password=args.pg_pass, dbname=args.pg_db,
        autocommit=True, row_factory=dict_row,
    )
    conn.execute("SET TIME ZONE 'UTC'")
    return conn


def _ch_client(args) -> clickhouse_connect.driver.Client:
    return clickhouse_connect.get_client(
        host=args.ch_host, port=args.ch_http_port,
        username=args.ch_user, password=args.ch_pass,
        database=args.ch_db, send_receive_timeout=300,
    )


# ─── Layer A — count parity ──────────────────────────────────────────────────
def layer_a_counts(pg, ch, *, projects: list[str] | None,
                   since: datetime | None, until: datetime | None,
                   max_diff_examples: int = 50) -> dict[str, Any]:
    pf, params = "", {}
    if projects:
        pf = "AND s.project_id = ANY(%(projects)s)"
        params["projects"] = projects
    tf = ""
    if since:
        tf += " AND s.start_time >= %(since)s"
        params["since"] = since
    if until:
        tf += " AND s.start_time <  %(until)s"
        params["until"] = until

    log.info("layer_a_pg_query")
    with pg.cursor() as cur:
        cur.execute(f"""
            SELECT s.project_id::text AS project_id,
                   date_trunc('day', s.start_time) AS day,
                   count(*) AS n
            FROM tracer_observation_span s
            WHERE s.deleted = false AND s.start_time IS NOT NULL {pf} {tf}
            GROUP BY 1, 2
            ORDER BY 2, 1
        """, params)
        pg_buckets: dict[tuple[str, datetime], int] = {}
        for r in cur.fetchall():
            day = r["day"]
            if day.tzinfo is None:
                day = day.replace(tzinfo=timezone.utc)
            else:
                day = day.astimezone(timezone.utc)
            pg_buckets[(r["project_id"], day)] = int(r["n"])

    log.info("layer_a_ch_query", buckets_pg=len(pg_buckets))
    # CH side: matching project + day window. To avoid pulling every bucket if
    # the user specified projects, do the same WHERE.
    ch_filter_parts = ["is_deleted = 0"]
    ch_params: dict[str, Any] = {}
    if projects:
        ch_filter_parts.append("project_id IN (" + ",".join(
            f"toUUID('{p}')" for p in projects) + ")")
    if since:
        ch_filter_parts.append("start_time >= %(since)s")
        ch_params["since"] = since
    if until:
        ch_filter_parts.append("start_time <  %(until)s")
        ch_params["until"] = until
    ch_where = " AND ".join(ch_filter_parts)

    # NOTE: Use EXPLICIT GROUP BY expressions. The ClickHouse analyzer in 25.x
    # has two related quirks that produce empty results:
    #   - `GROUP BY project_id, day` (aliased) — alias `project_id` collides with
    #     the table column; CH groups by the raw UUID, the SELECT produces toString,
    #     and the groupset is empty.
    #   - `GROUP BY 1, 2` (positional) — empty when WHERE references a column
    #     whose `toString()` appears in the SELECT.
    # Reproduced both in DECISIONS #020. Explicit `GROUP BY toString(project_id), toStartOfDay(start_time)` works.
    rows = ch.query(f"""
        SELECT toString(project_id) AS project_id_str,
               toStartOfDay(start_time) AS day,
               count() AS n
        FROM spans FINAL
        WHERE {ch_where}
        GROUP BY toString(project_id), toStartOfDay(start_time)
        ORDER BY toStartOfDay(start_time), toString(project_id)
    """, parameters=ch_params).result_rows
    ch_buckets: dict[tuple[str, datetime], int] = {}
    for row in rows:
        # CH's SELECT name aliases (AS project_id, AS day) don't propagate to
        # the result_rows tuple ordering when GROUP BY is on the aliased name —
        # so unpack by position from the SELECT list ordering above.
        pid, day, n = row[0], row[1], row[2]
        if day.tzinfo is None:
            day = day.replace(tzinfo=timezone.utc)
        ch_buckets[(pid, day)] = int(n)

    # Diff
    all_keys = set(pg_buckets) | set(ch_buckets)
    diffs: list[dict[str, Any]] = []
    for k in sorted(all_keys, key=lambda kk: (kk[1], kk[0])):
        pn = pg_buckets.get(k, 0)
        cn = ch_buckets.get(k, 0)
        if pn != cn:
            diffs.append({
                "project_id": k[0],
                "day": k[1].isoformat(),
                "pg": pn, "ch": cn, "delta": cn - pn,
            })

    status = "pass" if not diffs else "fail"
    return {
        "status": status,
        "buckets_checked": len(all_keys),
        "buckets_matched": len(all_keys) - len(diffs),
        "buckets_diff": len(diffs),
        "diff_examples": diffs[:max_diff_examples],
    }


# ─── Layer B — sampled deep-equal ────────────────────────────────────────────
def _sample_pg_ids(pg, *, sample_size: int, projects: list[str] | None,
                   since: datetime | None, until: datetime | None) -> list[str]:
    pf, params = "", {"n": sample_size}
    if projects:
        pf = "AND s.project_id = ANY(%(projects)s)"
        params["projects"] = projects
    tf = ""
    if since:
        tf += " AND s.start_time >= %(since)s"
        params["since"] = since
    if until:
        tf += " AND s.start_time <  %(until)s"
        params["until"] = until
    with pg.cursor() as cur:
        # ORDER BY random() is acceptable at the scale of "draw 1k from a
        # few hundred million" — PG uses a sort-by-random which is O(N log N)
        # but only materializes the random column for filtered rows. If this
        # becomes too slow at prod scale, swap to TABLESAMPLE BERNOULLI.
        cur.execute(f"""
            SELECT s.id FROM tracer_observation_span s
            WHERE s.deleted = false AND s.start_time IS NOT NULL {pf} {tf}
            ORDER BY random()
            LIMIT %(n)s
        """, params)
        return [r["id"] for r in cur.fetchall()]


def _normalize_for_compare(value: Any) -> Any:
    """Make field values comparable between expected-from-adapter and actual-from-CH.

    Adjustments:
      • datetimes → UTC ISO8601 string truncated to microseconds (CH rounds, PG can have ns)
      • UUIDs → str (CH returns uuid.UUID, adapter outputs str)
      • floats → rounded to 1e-9 (tolerate ULP)
      • None and "" are NOT collapsed — they're meaningful different shapes
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        v = value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return v.isoformat(timespec="microseconds")
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, float):
        if math.isnan(value):
            return "NaN"
        if math.isinf(value):
            return "Inf" if value > 0 else "-Inf"
        return round(value, 9)
    return value


def _diff_rows(expected: dict[str, Any], actual: dict[str, Any]) -> list[dict[str, Any]]:
    diffs = []
    for col in CH_INSERT_COLUMNS:
        if col in _JSON_COLUMNS:
            # JSON columns: expected is a Python dict (adapter output already
            # JSON-stringified), actual is a JSON string from toJSONString().
            # Parse both, apply CH 25.x typed-JSON write-side transforms
            # (dot-flatten + leaf-stringify), then compare STRUCTURALLY.
            #
            # Why structural and not byte-equal: CH's typed JSON (max_dynamic_paths=0)
            # applies a handful of un-documented normalizations that can't be
            # reproduced perfectly Python-side at deep nesting (object-key ordering
            # inside arrays, decimal-string canonicalization, empty-array vs null
            # collapsing). Byte-equal failed on ~45% of 121 KB nested payloads even
            # though every leaf was semantically identical. Structural-equal
            # (same keyset at every depth + same string-coerced leaf values)
            # catches real regressions while ignoring CH's stable in-storage
            # canonicalization. Any leaf-value or keyset divergence still fails.
            e_raw = expected.get(col)
            a_raw = actual.get(col)
            try:
                e_dict = json.loads(e_raw) if isinstance(e_raw, str) else (e_raw or {})
            except json.JSONDecodeError:
                e_dict = {}
            try:
                a_dict = json.loads(a_raw) if isinstance(a_raw, str) else (a_raw or {})
            except json.JSONDecodeError:
                a_dict = {}
            # Apply the SAME normalization to both sides — CH's typed-JSON
            # canonicalization is observable on the readback path too (e.g.
            # if a leaf was somehow inserted as the float `1.0` from a
            # synthetic insert, CH will hand it back as `"1"`).
            e_norm = _stringify_leaves(_flatten_dotted_keys(e_dict))
            a_norm = _stringify_leaves(_flatten_dotted_keys(a_dict))
            if e_norm is _NULL_LEAF: e_norm = {}
            if a_norm is _NULL_LEAF: a_norm = {}
            jd = _json_structural_diff(e_norm, a_norm, prefix="$", limit=10)
            if jd:
                diffs.append({"field": col,
                              "diff_count": len(jd),
                              "diff_paths": jd})
            continue

        e = _normalize_for_compare(expected.get(col))
        a = _normalize_for_compare(actual.get(col))
        # For map types, sort keys before compare (CH returns map ordering by hash).
        if isinstance(e, dict) and isinstance(a, dict):
            if dict(sorted(e.items())) == dict(sorted(a.items())):
                continue
        if e != a:
            diffs.append({"field": col, "expected": _short(e), "actual": _short(a)})
    return diffs


def _canonical_json(obj: Any) -> str:
    """Deterministic JSON serialization for cross-engine comparison."""
    return json.dumps(obj, sort_keys=True, default=str)


def _json_structural_diff(e: Any, a: Any, *, prefix: str = "$",
                          limit: int = 10) -> list[dict[str, Any]]:
    """Walk two normalized JSON-like Python objects in parallel; return at most
    `limit` real divergences with their JSONPath. Treats element-wise equality
    of leaves (after _stringify_leaves) as the success criterion, and same
    keyset at every dict level as a structural requirement.

    Returns [] when structurally identical; non-empty when caller should fail.
    """
    out: list[dict[str, Any]] = []

    def walk(p: str, x: Any, y: Any) -> None:
        if len(out) >= limit:
            return
        if type(x) is not type(y):
            out.append({"path": p, "kind": "type_mismatch",
                        "exp": type(x).__name__, "act": type(y).__name__})
            return
        if isinstance(x, dict):
            kx, ky = set(x.keys()), set(y.keys())
            if kx != ky:
                missing = sorted(kx - ky)[:5]
                extra = sorted(ky - kx)[:5]
                out.append({"path": p, "kind": "key_mismatch",
                            "missing_in_actual": missing, "extra_in_actual": extra})
                return
            for k in sorted(kx):
                walk(f"{p}.{k}", x[k], y[k])
        elif isinstance(x, list):
            if len(x) != len(y):
                out.append({"path": p, "kind": "list_len",
                            "exp": len(x), "act": len(y)})
                return
            for i in range(len(x)):
                walk(f"{p}[{i}]", x[i], y[i])
        else:
            if x != y:
                out.append({"path": p, "kind": "value",
                            "exp": _short(x, 80), "act": _short(y, 80)})

    walk(prefix, e, a)
    return out


def _short(v: Any, limit: int = 200) -> Any:
    s = repr(v)
    return s if len(s) <= limit else s[:limit] + "…"


# Columns CH returns from `SELECT * FROM spans FINAL` may have a different
# materialization than the insert tuple (e.g. materialized columns appear).
# We compare on CH_INSERT_COLUMNS only — the columns we directly wrote.
#
# Important: `clickhouse-connect==0.8.18` cannot decode the typed JSON columns
# (`JSON(max_dynamic_paths=...)`) in result rows — it errors with "Unrecognized
# ClickHouse type base". We work around this by SELECT-ing those columns via
# `toJSONString(col)` which returns plain String, then parse JSON on the Python
# side for comparison. Same workaround the backfill uses (sends as JSON string).
#
# Columns parsed + structurally compared as JSON in _diff_rows:
_JSON_COLUMNS = {"attributes_extra", "resource_attrs", "metadata"}
# Of those, only these are real CH typed-JSON columns that clickhouse-connect
# can't decode and must be SELECT-ed via toJSONString(). `attributes_extra` is
# a plain String holding JSON text (schema 013) — wrapping it in toJSONString()
# DOUBLE-encodes it (`"{\"k\":...}"`), so json.loads() yields a str, not a dict,
# and every row falsely fails Layer B with type_mismatch. Select it raw.
_JSON_TYPED_COLUMNS = {"resource_attrs", "metadata"}


def _ch_select_expr_for(col: str) -> str:
    if col in _JSON_TYPED_COLUMNS:
        return f"toJSONString({col}) AS {col}"
    return col


_CH_SELECT_COLUMNS_SQL = ", ".join(_ch_select_expr_for(c) for c in CH_INSERT_COLUMNS)


_NULL_LEAF = object()                                              # sentinel to drop


# CH 25.x typed JSON auto-detects ISO-8601 strings and stores them as
# DateTime64. On readback the string is `YYYY-MM-DD HH:MM:SS.ffffff` (no
# trailing Z, microseconds padded). Normalize both sides to a canonical form
# so semantic equality holds. Regex covers the two shapes observed in real
# customer payloads:
#   ISO:           "2025-10-08T17:51:11.479Z" / "2025-10-08T17:51:11.479+00:00"
#   CH-readback:   "2025-10-08 17:51:11.479000000"
import re
_ISO_DT = re.compile(
    r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?$"
)


_HUMAN_DT = re.compile(r"^[A-Z][a-z]{2} \d{1,2}, \d{4}(?: UTC)?$")


def _canonical_dt_string(s: str) -> str | None:
    """Return canonical 'YYYY-MM-DD HH:MM:SS.ffffff' for any ISO-8601 looking
    string OR human-readable 'Mon DD, YYYY' shape; else None. Bridges:
      • customer ISO         "2025-10-08T17:51:11.479Z"
      • CH-readback          "2025-10-08 17:51:11.479000000"
      • customer human       "Oct 16, 2025 UTC"  (CH parses → stores as Date)
    """
    if _HUMAN_DT.match(s):
        try:
            v = s.replace(" UTC", "")
            dt = datetime.strptime(v, "%b %d, %Y").replace(tzinfo=timezone.utc)
            return dt.strftime("%Y-%m-%d %H:%M:%S") + ".000000"
        except ValueError:
            return None
    if not _ISO_DT.match(s):
        return None
    try:
        # Python's fromisoformat handles 'YYYY-MM-DDTHH:MM:SS', also space sep
        # and timezone offsets in 3.11+. Strip trailing Z (UTC).
        v = s.replace("Z", "+00:00") if s.endswith("Z") else s
        # Replace '+0000' (no colon) → '+00:00' for fromisoformat strictness
        if v[-5:] in ("+0000", "-0000"):
            v = v[:-5] + v[-5] + v[-4:-2] + ":" + v[-2:]
        dt = datetime.fromisoformat(v)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = dt.astimezone(timezone.utc)
        # Truncate / pad to 6-digit microseconds and drop tz suffix — matches
        # CH's readback shape exactly. CH may emit 9-digit nanos; normalize to 6.
        return dt.strftime("%Y-%m-%d %H:%M:%S") + f".{dt.microsecond:06d}"
    except ValueError:
        return None


def _stringify_leaves(v: Any) -> Any:
    """ClickHouse `JSON(max_dynamic_paths=0)` stores all leaf values as strings —
    typed sub-columns are disabled to prevent sub-column explosion on customer
    payloads (PLAN_V2 cost analysis). So `[1, 2]` round-trips as `["1", "2"]`
    and `42` as `"42"`. Apply the same normalization to the adapter side before
    comparing the two JSON blobs, otherwise every overflow value disagrees on type.

    Additional CH-side transforms we mirror here (observed via golden-file diff
    against live spans):
      • `42.0` (Python float that's integer-valued) → stored as `"42"` in CH JSON
        (CH normalizes trailing `.0`). Mirror with `str(int(v))` when finite.
      • `null` leaves and `{}`/`[]` empty containers are dropped by CH's typed
        JSON. Caller-side drop via `_drop_null_leaves` after stringification.
    """
    if v is None:
        return _NULL_LEAF
    if isinstance(v, bool):
        return "true" if v else "false"                            # match JSON serialization
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        if math.isnan(v) or math.isinf(v):
            return repr(v)
        # CH normalizes integer-valued floats: `1.0` → `"1"`, but `1.5` stays `"1.5"`.
        if v.is_integer():
            return str(int(v))
        return str(v)
    if isinstance(v, list):
        out_l = []
        for x in v:
            y = _stringify_leaves(x)
            if y is _NULL_LEAF:
                continue                                           # drop nulls inside arrays
            out_l.append(y)
        return out_l
    if isinstance(v, dict):
        out_d: dict[str, Any] = {}
        for k, x in v.items():
            y = _stringify_leaves(x)
            if y is _NULL_LEAF:
                continue                                           # drop null-valued keys
            if isinstance(y, (dict, list)) and len(y) == 0:
                continue                                           # drop empty containers
            out_d[k] = y
        return out_d
    # Strings: pass through, but canonicalize ISO-8601 / CH-readback
    # DateTime forms so the two storage shapes compare equal.
    if isinstance(v, str):
        # Also handle CH's 9-digit-nanosecond readback by truncating to 6.
        if len(v) >= 21 and " " in v and "." in v:                 # quick prefilter
            try:
                # CH readback: '2025-10-08 17:51:11.479000000'
                head, _, frac = v.partition(".")
                if frac and frac.isdigit() and len(frac) > 6:
                    v_norm = f"{head}.{frac[:6]}"
                    return v_norm                                  # canonical CH shape
            except Exception:
                pass
        canon = _canonical_dt_string(v)
        if canon is not None:
            return canon
        return v
    return v                                                       # other types pass through


def _flatten_dotted_keys(d: Any) -> Any:
    """CH 25.x typed JSON columns auto-flatten `{"a.b": 1}` to `{"a": {"b": 1}}`.
    Apply the same transform to the adapter's output so the parity comparison
    is apples-to-apples.

    Conflicts (e.g. both `{"a": 1}` and `{"a.b": 2}` present) prefer the dotted
    form's nested merge (matches CH behavior — last-write-wins per path segment).
    """
    if not isinstance(d, dict):
        return d
    out: dict[str, Any] = {}
    for k, v in d.items():
        v2 = _flatten_dotted_keys(v) if isinstance(v, dict) else v
        if isinstance(k, str) and "." in k:
            parts = k.split(".")
            cur = out
            for p in parts[:-1]:
                if p not in cur or not isinstance(cur[p], dict):
                    cur[p] = {}
                cur = cur[p]
            cur[parts[-1]] = v2
        else:
            if k in out and isinstance(out[k], dict) and isinstance(v2, dict):
                out[k] = {**out[k], **v2}                          # merge dotted-then-named conflicts
            else:
                out[k] = v2
    return out


def layer_b_deep_equal(pg, ch, *, sample_size: int, projects: list[str] | None,
                       since: datetime | None, until: datetime | None,
                       max_diff_examples: int = 25,
                       max_diff_fields_per_row: int = 10) -> dict[str, Any]:
    log.info("layer_b_sampling_ids", sample_size=sample_size)
    ids = _sample_pg_ids(pg, sample_size=sample_size, projects=projects,
                        since=since, until=until)
    if not ids:
        return {"status": "skip", "sampled": 0, "reason": "no rows matched sampling filter"}

    log.info("layer_b_loaded_ids", count=len(ids))

    # Fetch PG rows for these ids (full row with all columns the adapter expects)
    with pg.cursor() as cur:
        cur.execute("""
            SELECT s.id, s.project_id, s.project_version_id, s.trace_id, s.parent_span_id,
                   s.name, s.observation_type, s.operation_name,
                   s.start_time, s.end_time,
                   s.input, s.output,
                   s.model, s.model_parameters, s.latency_ms,
                   s.org_id, s.org_user_id,
                   s.prompt_tokens, s.completion_tokens, s.total_tokens, s.response_time,
                   s.eval_id, s.cost,
                   s.status, s.status_message,
                   s.tags, s.metadata, s.span_events, s.provider,
                   s.input_images, s.eval_input, s.eval_attributes,
                   s.custom_eval_config_id, s.eval_status,
                   s.end_user_id, s.prompt_version_id, s.prompt_label_id,
                   s.span_attributes, s.resource_attributes, s.semconv_source,
                   s.created_at, s.updated_at, s.deleted,
                   t.session_id AS trace_session_id
            FROM tracer_observation_span s
            LEFT JOIN tracer_trace t ON t.id = s.trace_id
            WHERE s.id = ANY(%(ids)s)
        """, {"ids": ids})
        pg_rows = {r["id"]: r for r in cur.fetchall()}

    # Fetch CH rows for the same ids. We chunk the IN list so each FINAL scan
    # operates over a bounded set — under local memory pressure (8 GiB cap),
    # FINAL across hundreds of typed-JSON rows in one shot blows past CH's
    # per-query budget and the daemon closes the HTTP socket mid-response.
    # Per-chunk FINAL keeps RAM linear in chunk size; semantics identical.
    ch_rows: dict[str, dict[str, Any]] = {}
    CHUNK = 25
    for i in range(0, len(ids), CHUNK):
        chunk = ids[i : i + CHUNK]
        rows = ch.query(
            f"SELECT {_CH_SELECT_COLUMNS_SQL} FROM spans FINAL "
            f"WHERE id IN %(ids)s SETTINGS use_skip_indexes_if_final = 1",
            parameters={"ids": tuple(chunk)},
        ).result_rows
        for row_tuple in rows:
            d = dict(zip(CH_INSERT_COLUMNS, row_tuple))
            ch_rows[d["id"]] = d

    diffs: list[dict[str, Any]] = []
    missing_in_ch: list[str] = []
    matched = 0
    for sid in ids:
        pg_row = pg_rows.get(sid)
        if pg_row is None:
            continue                                                # was deleted concurrently; skip
        ch_row = ch_rows.get(sid)
        if ch_row is None:
            missing_in_ch.append(sid)
            continue
        expected_tuple = row_to_tuple(adapt(pg_row))
        expected = dict(zip(CH_INSERT_COLUMNS, expected_tuple))
        row_diffs = _diff_rows(expected, ch_row)
        if row_diffs:
            diffs.append({"span_id": sid, "diffs": row_diffs[:max_diff_fields_per_row]})
        else:
            matched += 1

    status = "pass" if not (diffs or missing_in_ch) else "fail"
    return {
        "status": status,
        "sampled": len(ids),
        "matched": matched,
        "diff_rows": len(diffs),
        "missing_in_ch": len(missing_in_ch),
        "missing_examples": missing_in_ch[:max_diff_examples],
        "diff_examples": diffs[:max_diff_examples],
    }


# ─── Layer C — top-20 query parity ───────────────────────────────────────────
# Each entry: a query name, a PG SQL string, an equivalent CH SQL string, and
# a normalizer that turns the result rows into a canonical comparable form.
#
# These are the queries the product dashboard + eval surfaces actually run.
# When we change something that affects their semantics, we want a failure
# here — that's the point.

@dataclass
class ParityQuery:
    name: str
    # SQL templates take {scope} placeholders. The validator substitutes
    # WHERE-clause fragments produced from --project-id / --since / --until
    # so this layer respects the same scope filters as Layers A and B.
    pg_sql: str
    ch_sql: str
    pg_params: dict[str, Any] = field(default_factory=dict)
    ch_params: dict[str, Any] = field(default_factory=dict)
    tolerance: float = 1e-6


# NOTE: every GROUP BY uses EXPLICIT expressions, never positional indices.
# A ClickHouse analyzer quirk (see DECISIONS #020) makes `GROUP BY 1` return
# empty results when WHERE references a column whose `toString()` is in the
# SELECT — `GROUP BY toString(project_id)` works, `GROUP BY 1` silently doesn't.
_DEFAULT_QUERIES: list[ParityQuery] = [
    ParityQuery(
        # NOTE: SELECT alias `project_id_str` avoids colliding with the underlying
        # table column. CH's analyzer rejects `GROUP BY toString(project_id)` when
        # there's an `AS project_id` alias in the SELECT because it treats the
        # alias as referencing the underlying column. See DECISIONS #020.
        name="span_count_by_project",
        pg_sql="""SELECT project_id::text AS project_id_str, count(*) AS n
                  FROM tracer_observation_span s
                  WHERE s.deleted = false AND s.start_time IS NOT NULL {pg_scope}
                  GROUP BY project_id::text ORDER BY project_id::text""",
        ch_sql="""SELECT toString(project_id) AS project_id_str, count() AS n
                  FROM spans FINAL WHERE is_deleted = 0 {ch_scope}
                  GROUP BY toString(project_id) ORDER BY toString(project_id)""",
    ),
    ParityQuery(
        name="span_count_by_observation_type",
        pg_sql="""SELECT observation_type, count(*) AS n
                  FROM tracer_observation_span s
                  WHERE s.deleted = false AND s.start_time IS NOT NULL {pg_scope}
                  GROUP BY observation_type ORDER BY observation_type""",
        ch_sql="""SELECT observation_type, count() AS n
                  FROM spans FINAL WHERE is_deleted = 0 {ch_scope}
                  GROUP BY observation_type ORDER BY observation_type""",
    ),
    ParityQuery(
        # CH's `model` column is MATERIALIZED from attributes (gen_ai.request.model
        # → llm.model_name → llm.model fallback chain). PG side must derive the
        # same way for apples-to-apples comparison — falling back to span_attributes
        # JSONB when PG's column is NULL. Otherwise CH appears to have ~10× more
        # gpt-4o spans than PG only because the model column on PG is sparse.
        name="top_10_models_by_span_count",
        pg_sql="""SELECT model_derived, n FROM (
                    SELECT coalesce(
                             NULLIF(model, ''),
                             s.span_attributes->>'gen_ai.request.model',
                             s.span_attributes->>'llm.model_name',
                             s.span_attributes->>'llm.model',
                             ''
                           ) AS model_derived, count(*) AS n
                    FROM tracer_observation_span s
                    WHERE s.deleted = false AND s.start_time IS NOT NULL {pg_scope}
                    GROUP BY 1
                  ) x WHERE model_derived != ''
                  ORDER BY n DESC, model_derived LIMIT 10""",
        ch_sql="""SELECT model AS model_derived, count() AS n FROM spans FINAL
                  WHERE is_deleted = 0 AND model != '' {ch_scope}
                  GROUP BY model ORDER BY count() DESC, model LIMIT 10""",
    ),
    ParityQuery(
        name="total_tokens_per_project",
        pg_sql="""SELECT project_id::text AS project_id_str,
                         coalesce(sum(total_tokens), 0) AS tokens
                  FROM tracer_observation_span s
                  WHERE s.deleted = false AND s.start_time IS NOT NULL {pg_scope}
                  GROUP BY project_id::text ORDER BY project_id::text""",
        ch_sql="""SELECT toString(project_id) AS project_id_str, sum(total_tokens) AS tokens
                  FROM spans FINAL WHERE is_deleted = 0 {ch_scope}
                  GROUP BY toString(project_id) ORDER BY toString(project_id)""",
    ),
    ParityQuery(
        name="trace_session_attachment_count",
        pg_sql="""SELECT count(*) AS n
                  FROM tracer_observation_span s
                  LEFT JOIN tracer_trace t ON t.id = s.trace_id
                  WHERE s.deleted = false AND s.start_time IS NOT NULL
                    AND t.session_id IS NOT NULL {pg_scope}""",
        ch_sql="""SELECT count() AS n FROM spans FINAL
                  WHERE is_deleted = 0 AND trace_session_id IS NOT NULL {ch_scope}""",
    ),
]


def _build_scope_fragments(projects: list[str] | None,
                           since: datetime | None,
                           until: datetime | None) -> tuple[str, dict, str, dict]:
    """Build matching WHERE-clause fragments for PG and CH that apply the
    --project-id / --since / --until scope filters used by Layers A and B.
    Returns (pg_fragment, pg_params, ch_fragment, ch_params).
    """
    pg_parts: list[str] = []
    ch_parts: list[str] = []
    pg_params: dict[str, Any] = {}
    ch_params: dict[str, Any] = {}
    if projects:
        pg_parts.append("AND s.project_id = ANY(%(_projects)s)")
        pg_params["_projects"] = projects
        ch_parts.append("AND project_id IN ("
                        + ",".join(f"toUUID('{p}')" for p in projects) + ")")
    if since:
        pg_parts.append("AND s.start_time >= %(_since)s")
        pg_params["_since"] = since
        ch_parts.append("AND start_time >= %(_since)s")
        ch_params["_since"] = since
    if until:
        pg_parts.append("AND s.start_time <  %(_until)s")
        pg_params["_until"] = until
        ch_parts.append("AND start_time <  %(_until)s")
        ch_params["_until"] = until
    return (" ".join(pg_parts), pg_params, " ".join(ch_parts), ch_params)


def _normalize_value(v: Any, tolerance: float) -> Any:
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    if isinstance(v, (int,)):
        return v
    if isinstance(v, float):
        if math.isnan(v):
            return "NaN"
        if math.isinf(v):
            return "Inf" if v > 0 else "-Inf"
        # Round to a multiple of `tolerance` so engine ULP doesn't cause false fails.
        if tolerance > 0:
            return round(v / tolerance) * tolerance
        return v
    if isinstance(v, datetime):
        v2 = v.astimezone(timezone.utc) if v.tzinfo else v.replace(tzinfo=timezone.utc)
        return v2.isoformat(timespec="microseconds")
    if isinstance(v, uuid.UUID):
        return str(v)
    return v


def _normalize_rows(rows: list[tuple], tolerance: float) -> list[tuple]:
    return [tuple(_normalize_value(c, tolerance) for c in row) for row in rows]


def layer_c_queries(pg, ch, *, queries: list[ParityQuery] | None = None,
                    projects: list[str] | None = None,
                    since: datetime | None = None, until: datetime | None = None,
                    max_row_examples: int = 20) -> dict[str, Any]:
    qs = queries or _DEFAULT_QUERIES
    pg_scope, pg_scope_params, ch_scope, ch_scope_params = _build_scope_fragments(
        projects, since, until)
    results: list[dict[str, Any]] = []
    all_pass = True
    for q in qs:
        pg_sql = q.pg_sql.format(pg_scope=pg_scope, ch_scope=ch_scope)
        ch_sql = q.ch_sql.format(pg_scope=pg_scope, ch_scope=ch_scope)
        with pg.cursor() as cur:
            cur.execute(pg_sql, {**q.pg_params, **pg_scope_params})
            pg_rows = [tuple(r.values()) for r in cur.fetchall()]
        ch_rows = ch.query(ch_sql, parameters={**q.ch_params, **ch_scope_params}).result_rows

        pg_norm = _normalize_rows(pg_rows, q.tolerance)
        ch_norm = _normalize_rows([tuple(r) for r in ch_rows], q.tolerance)

        match = pg_norm == ch_norm
        if not match:
            all_pass = False
        results.append({
            "name": q.name,
            "match": match,
            "pg_row_count": len(pg_rows),
            "ch_row_count": len(ch_rows),
            "pg_sample": pg_norm[:max_row_examples],
            "ch_sample": ch_norm[:max_row_examples],
        })

    return {
        "status": "pass" if all_pass else "fail",
        "queries_checked": len(qs),
        "queries_matched": sum(1 for r in results if r["match"]),
        "queries_mismatched": sum(1 for r in results if not r["match"]),
        "details": results,
    }


# ─── CLI ──────────────────────────────────────────────────────────────────────
def _build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    # PG
    p.add_argument("--pg-host", default="127.0.0.1")
    p.add_argument("--pg-port", type=int, default=5432)
    p.add_argument("--pg-user", default=os.environ.get("PG_USER", "user"))
    p.add_argument("--pg-pass", default=os.environ.get("PG_PASSWORD", "password"))
    p.add_argument("--pg-db",   default=os.environ.get("PG_DB", "tfc"))
    # CH 25.3
    p.add_argument("--ch-host", default="127.0.0.1")
    p.add_argument("--ch-http-port", type=int, default=19001)
    p.add_argument("--ch-user", default="default")
    p.add_argument("--ch-pass", default=os.environ.get("CH_PASSWORD", ""))
    p.add_argument("--ch-db",   default="default")
    # Scope filters
    p.add_argument("--project-id", action="append", default=None,
                   help="Limit validation to this project (repeatable)")
    p.add_argument("--since", type=lambda s: datetime.fromisoformat(s).replace(tzinfo=timezone.utc),
                   help="Only spans where start_time >= this (ISO8601)")
    p.add_argument("--until", type=lambda s: datetime.fromisoformat(s).replace(tzinfo=timezone.utc),
                   help="Only spans where start_time < this (ISO8601)")
    # Layer selection
    p.add_argument("--all", action="store_true", help="Run all three layers")
    p.add_argument("--counts", action="store_true", help="Run Layer A only")
    p.add_argument("--deep", action="store_true", help="Run Layer B only")
    p.add_argument("--queries", action="store_true", help="Run Layer C only")
    p.add_argument("--sample-size", type=int, default=1000,
                   help="Layer B sample size (default 1000)")
    p.add_argument("--seed", type=int, default=None,
                   help="RNG seed for reproducible Layer B samples")
    # Output
    p.add_argument("--report", type=Path, default=None,
                   help="Write JSON report to this file (otherwise stdout)")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_argparser().parse_args(argv)
    if not (args.all or args.counts or args.deep or args.queries):
        log.error("must pass one of --all / --counts / --deep / --queries")
        return 1

    if args.seed is not None:
        random.seed(args.seed)

    pg = _pg_conn(args)
    ch = _ch_client(args)

    run_id = f"{datetime.now(timezone.utc):%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:8]}"
    report: dict[str, Any] = {
        "run_id": run_id,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "scope": {
            "projects": args.project_id or "all",
            "since": args.since.isoformat() if args.since else None,
            "until": args.until.isoformat() if args.until else None,
        },
        "layers": {},
    }
    t0 = time.time()

    overall_pass = True

    if args.all or args.counts:
        log.info("layer_a_start")
        ta = time.time()
        ra = layer_a_counts(pg, ch, projects=args.project_id,
                            since=args.since, until=args.until)
        ra["elapsed_sec"] = round(time.time() - ta, 2)
        report["layers"]["counts"] = ra
        if ra["status"] == "fail":
            overall_pass = False
        log.info("layer_a_done", **{k: v for k, v in ra.items() if k != "diff_examples"})

    if args.all or args.deep:
        log.info("layer_b_start")
        tb = time.time()
        rb = layer_b_deep_equal(pg, ch, sample_size=args.sample_size,
                                projects=args.project_id,
                                since=args.since, until=args.until)
        rb["elapsed_sec"] = round(time.time() - tb, 2)
        report["layers"]["deep_equal"] = rb
        if rb["status"] == "fail":
            overall_pass = False
        log.info("layer_b_done", **{k: v for k, v in rb.items()
                                    if k not in ("diff_examples", "missing_examples")})

    if args.all or args.queries:
        log.info("layer_c_start")
        tc = time.time()
        rc = layer_c_queries(pg, ch, projects=args.project_id,
                             since=args.since, until=args.until)
        rc["elapsed_sec"] = round(time.time() - tc, 2)
        report["layers"]["queries"] = rc
        if rc["status"] == "fail":
            overall_pass = False
        log.info("layer_c_done", status=rc["status"], matched=rc["queries_matched"],
                 mismatched=rc["queries_mismatched"])

    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    report["elapsed_sec"] = round(time.time() - t0, 2)
    report["overall_status"] = "pass" if overall_pass else "fail"

    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2, default=str))
        log.info("report_written", path=str(args.report), status=report["overall_status"])
    else:
        print(json.dumps(report, indent=2, default=str))

    return 0 if overall_pass else 2


if __name__ == "__main__":
    sys.exit(main())
