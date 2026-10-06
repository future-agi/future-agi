"""Independent scalar-span ID/order oracle, not an HTTP or full-metrics proof.

An independently ordered scoped FINAL population, never application candidates,
compiler, rollups, sampling, or a history cutoff. Adjacent UTC intervals retain
every boundary-hour version; exact time/deletion/properties follow FINAL.
The shared independent scalar predicate runs on ONE physical winner, with
NOT(presence) for a missing typed key. Separate spans cannot supply separate
leaves of the same conjunction. FINAL's six-part replacement key supplies the
one-row contract; no per-span GROUP BY or global count window is necessary.

Caller-owned readers must throw on incomplete/aborted reads. The finite settings
below are reference diagnostic guards only, NOT application policy or SLOs.
Errors propagate as UNVERIFIED, never as an empty/exhausted reference. No client,
Django, ORM, production connection, DDL, or writes are initialized here.
"""

from datetime import datetime, timezone
import math
from time import monotonic
from uuid import UUID

from observe_annotation_reference import span_identity
from observe_trace_id_reference import (
    membership_having,
    positive_numeric_leaf,
    typed_membership_values,
    unix_microseconds,
)
from replay_observe_filters import ReplayError


CONTRACT = "CH25_six_part_identity_winner_order.v2"
SAFE_FINAL_SETTINGS = {
    "optimize_move_to_prewhere": 0,
    "optimize_move_to_prewhere_if_final": 0,
    "do_not_merge_across_partitions_select_final": 0,
    "use_skip_indexes_if_final": 0,
    "enable_optimize_predicate_expression_to_final_subquery": 0,
    "query_plan_merge_expressions": 0,
    # A failed diagnostic must throw, not return a partial prefix.
    "max_execution_time": 60,
    "timeout_overflow_mode": "throw",
    "max_threads": 2,
    "max_bytes_to_read": 8 * 1024**3,
    "read_overflow_mode": "throw",
    "max_memory_usage": 4 * 1024**3,
    "max_result_rows": 101,
    "result_overflow_mode": "throw",
    # A truncated DISTINCT/IN Set would silently omit immutable prefixes.
    "distinct_overflow_mode": "throw",
    "set_overflow_mode": "throw",
}
_HOUR_US = 3_600_000_000
_DAY_US = 24 * _HOUR_US
MAX_REFERENCE_QUERIES = 512


def _raw_prefix_proof(leaves, exact_predicate):
    """Independent necessary raw proof for the preserved daily strategy.

    Negative values require a union of selected typed domains, never their
    intersection. Absence is not inferred from raw versions. All-positive
    conjunctions additionally have the winning physical row as a value witness.
    """
    columns = {
        "text": "attrs_string",
        "string": "attrs_string",
        "number": "attrs_number",
        "boolean": "attrs_bool",
    }
    positive_ops = {
        "equals",
        "in",
        "contains",
        "starts_with",
        "ends_with",
        "between",
        "greater_than",
        "greater_than_or_equal",
        "less_than",
        "less_than_or_equal",
        "is_not_null",
    }
    presence = []
    all_positive = True
    for index, item in enumerate(leaves):
        cfg = item["filter_config"]
        op = cfg["filter_op"]
        all_positive = all_positive and op in positive_ops
        if op == "is_null":
            continue
        typed = typed_membership_values(cfg)
        domains = (
            [
                columns[storage]
                for storage in ("string", "number", "boolean")
                if storage in typed
            ]
            if typed is not None
            else [columns[cfg["filter_type"]]]
        )
        branches = [f"has({column}.keys, %(ref_key_{index})s)" for column in domains]
        presence.append(f"({' OR '.join(branches)})")
    if not presence:
        return "", "", "unpruned_absence_only"
    return (
        " AND ".join(presence),
        exact_predicate if all_positive else "",
        "positive_same_row" if all_positive else "necessary_typed_presence",
    )


def _hourly_population_eligible(leaves):
    """Shape-only routing after independent scalar validation, never values/IDs."""
    return bool(leaves) and all(
        leaf["filter_config"]["filter_op"] in {"equals", "in"}
        and leaf["filter_config"]["filter_type"]
        in {"text", "string", "number", "boolean"}
        for leaf in leaves
    )


def _utc(value):
    try:
        if isinstance(value, str):
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if not isinstance(value, datetime) or value.utcoffset() is None:
            raise ValueError
        return value.astimezone(timezone.utc)
    except (ValueError, TypeError, AttributeError):
        raise ReplayError("SPAN_REFERENCE_EXPLICIT_TIMEZONE_REQUIRED") from None


def _uuid(value):
    try:
        parsed = UUID(str(value))
        if parsed.int == 0:
            raise ValueError
        return str(parsed)
    except (ValueError, TypeError, AttributeError):
        raise ReplayError("SPAN_REFERENCE_INVALID_PROJECT") from None


def _scope_projects(scope):
    """scope is an explicit authorized inventory, not client-supplied filters.

    Existing project QA callers supply project_id. Workspace callers must
    supply project_ids AND authorized_project_ids; never infer an org scope.
    """
    if scope.get("project_ids") is None:
        projects = (_uuid(scope.get("project_id")),)
    else:
        raw = scope["project_ids"]
        if not isinstance(raw, (list, tuple)) or not raw or len(raw) > 100:
            raise ReplayError("SPAN_REFERENCE_EXPLICIT_PROJECT_SCOPE_REQUIRED")
        projects = tuple(_uuid(value) for value in raw)
        if scope.get("project_id") is not None and projects != (
            _uuid(scope["project_id"]),
        ):
            raise ReplayError("SPAN_REFERENCE_CONFLICTING_PROJECT_SCOPE")
        if "authorized_project_ids" not in scope:
            raise ReplayError("SPAN_REFERENCE_EXPLICIT_AUTHORIZATION_REQUIRED")
    if len(set(projects)) != len(projects):
        raise ReplayError("SPAN_REFERENCE_DUPLICATE_PROJECT")
    if "authorized_project_ids" in scope:
        authorized = scope["authorized_project_ids"]
        if not isinstance(authorized, (list, tuple)) or not authorized:
            raise ReplayError("SPAN_REFERENCE_EXPLICIT_AUTHORIZATION_REQUIRED")
        if not set(projects) <= {_uuid(value) for value in authorized}:
            raise ReplayError("SPAN_REFERENCE_PROJECT_NOT_AUTHORIZED")
    return projects


def build_span_reference_query(case, scope, filters, *, raw_prefix=True):
    """Independent ordered page plus lookahead; LIMIT only AFTER membership.

    page_number selects an independent numbered reference page, not a public
    signed-cursor replay. Opaque cursors/custom sorts are intentionally rejected.
    """
    if case.get("surface") not in {"spans", "task_spans", "eval_spans"}:
        raise ReplayError("SPAN_REFERENCE_SURFACE_NOT_SUPPORTED")
    projects = _scope_projects(scope)
    start, end = (_utc(case["window"][key]) for key in ("start", "end"))
    if start >= end:
        raise ReplayError("SPAN_REFERENCE_INVALID_WINDOW")
    request = case["request"]
    target = request["target_rows"]
    wire = request.get("params", {})
    page = wire.get("page_number", 0)
    if type(target) is not int or not 1 <= target <= 100:
        raise ReplayError("SPAN_REFERENCE_INVALID_PAGE_SIZE")
    if type(page) is not int or not 0 <= page <= 10000:
        raise ReplayError("SPAN_REFERENCE_INVALID_PAGE_NUMBER")
    if wire.get("cursor") or any(
        key in wire for key in ("sort", "sort_by", "order_by", "sort_order")
    ):
        raise ReplayError("SPAN_REFERENCE_CURSOR_OR_CUSTOM_ORDER_UNSUPPORTED")
    if "page_size" in wire and wire["page_size"] != target:
        raise ReplayError("SPAN_REFERENCE_PAGE_SIZE_MISMATCH")
    if wire.get("project_id") is not None and projects != (_uuid(wire["project_id"]),):
        raise ReplayError("SPAN_REFERENCE_REQUEST_SCOPE_MISMATCH")
    if not isinstance(filters, (list, tuple)):
        raise ReplayError("SPAN_REFERENCE_FLAT_AND_REQUIRED")
    leaves, date_count = [], 0
    for leaf in filters:
        if (
            not isinstance(leaf, dict)
            or set(leaf) - {"column_id", "filter_config"}
            or not isinstance(leaf.get("column_id"), str)
            or not leaf["column_id"]
            or not isinstance(leaf.get("filter_config"), dict)
        ):
            raise ReplayError("SPAN_REFERENCE_FLAT_AND_REQUIRED")
        cfg = leaf["filter_config"]
        if set(cfg) - {
            "col_type",
            "filter_type",
            "filter_op",
            "filter_value",
            "attribute_value_types",
            "attributeValueTypes",
        }:
            raise ReplayError("SPAN_REFERENCE_UNSUPPORTED_FILTER_CONFIG")
        if cfg.get("col_type") == "SYSTEM_METRIC" and leaf["column_id"] in {
            "created_at",
            "start_time",
        }:
            values = cfg.get("filter_value")
            if (
                cfg.get("filter_type") != "datetime"
                or cfg.get("filter_op") != "between"
                or not isinstance(values, (list, tuple))
                or len(values) != 2
                or tuple(_utc(value) for value in values) != (start, end)
            ):
                raise ReplayError("SPAN_REFERENCE_WINDOW_FILTER_MISMATCH")
            date_count += 1
            if date_count > 1:
                raise ReplayError("SPAN_REFERENCE_MULTIPLE_WINDOWS_UNSUPPORTED")
        elif cfg.get("col_type") == "SPAN_ATTRIBUTE":
            leaves.append(leaf)
        else:
            raise ReplayError("SPAN_REFERENCE_UNSUPPORTED_FILTER")
    if not 1 <= len(leaves) <= 10:
        raise ReplayError("SPAN_REFERENCE_REQUIRES_ONE_TO_TEN_SCALARS")
    predicate, values = membership_having(leaves, scalar_span=True)
    hourly = _hourly_population_eligible(leaves)
    raw_presence, raw_value, prefix_proof = (
        ("", "", "unpruned_full_hour_FINAL")
        if hourly or not raw_prefix
        else _raw_prefix_proof(leaves, predicate)
    )
    params = {
        "ref_project_ids": projects,
        "ref_start_us": unix_microseconds(start),
        "ref_end_us": unix_microseconds(end),
        "ref_scan_start_us": unix_microseconds(start),
        "ref_scan_end_us": unix_microseconds(end),
        "ref_has_before": 0,
        "ref_before_order": (unix_microseconds(end), "", "", "", "", ""),
        "ref_limit": target + 1,
        "ref_offset": page * target,
        "ref_hourly_discovery": hourly,
        "ref_prefix_proof": prefix_proof,
        **values,
    }
    raw_cte, prefix_scope = "", ""
    if raw_presence:
        raw_cte = f"""raw_witness_prefixes AS (
        SELECT DISTINCT project_id, observation_type, service_name,
               toStartOfHour(start_time) AS start_hour
        FROM spans
        PREWHERE project_id IN %(ref_project_ids)s
          AND toStartOfHour(start_time) >= toStartOfHour(fromUnixTimestamp64Micro(%(ref_scan_start_us)s, 'UTC'))
          AND toStartOfHour(start_time) <= toStartOfHour(fromUnixTimestamp64Micro(%(ref_scan_end_us)s - 1, 'UTC'))
          AND ({raw_presence})
        {f"WHERE ({raw_value})" if raw_value else ""}
    ), """
        prefix_scope = """
          AND (project_id, observation_type, service_name, toStartOfHour(start_time)) IN (
              SELECT project_id, observation_type, service_name, start_hour
              FROM raw_witness_prefixes
          )"""
    sql = f"""
    WITH {raw_cte}physical_winners AS (
        SELECT project_id, observation_type, service_name, trace_id, id,
               start_time, is_deleted, _version, attrs_string, attrs_number, attrs_bool
        FROM spans FINAL
        PREWHERE project_id IN %(ref_project_ids)s
          AND toStartOfHour(start_time) >= toStartOfHour(fromUnixTimestamp64Micro(%(ref_scan_start_us)s, 'UTC'))
          AND toStartOfHour(start_time) <= toStartOfHour(fromUnixTimestamp64Micro(%(ref_scan_end_us)s - 1, 'UTC'))
          {prefix_scope}
    ), matching_spans AS (
        SELECT project_id, trace_id, id, observation_type, service_name,
               start_time, _version
        FROM physical_winners
        WHERE is_deleted = 0
          AND start_time >= fromUnixTimestamp64Micro(%(ref_start_us)s, 'UTC')
          AND start_time < fromUnixTimestamp64Micro(%(ref_end_us)s, 'UTC')
          AND start_time >= fromUnixTimestamp64Micro(%(ref_scan_start_us)s, 'UTC')
          AND start_time < fromUnixTimestamp64Micro(%(ref_scan_end_us)s, 'UTC')
          AND (%(ref_has_before)s = 0 OR
               (toUnixTimestamp64Micro(start_time), id, trace_id, toString(project_id), observation_type, service_name)
                   < %(ref_before_order)s)
          AND ({predicate})
    )
    SELECT toString(project_id) AS project_id, trace_id, id, observation_type,
           service_name, start_time, _version,
           toUnixTimestamp64Micro(toDateTime64(toStartOfHour(start_time), 6, 'UTC')) AS physical_hour_us
    FROM matching_spans
    ORDER BY start_time DESC, id DESC, trace_id DESC, project_id DESC,
             observation_type DESC, service_name DESC
    LIMIT %(ref_limit)s OFFSET %(ref_offset)s
    """
    return sql, params


_POPULATION_SQL = """
    SELECT maxOrNull(toUnixTimestamp64Micro(
        toDateTime64(toStartOfHour(start_time), 6, 'UTC')
    )) AS newest_raw_hour_us
    FROM spans
    PREWHERE project_id IN %(ref_project_ids)s
      AND toStartOfHour(start_time) >= fromUnixTimestamp64Micro(%(ref_population_start_us)s, 'UTC')
      AND toStartOfHour(start_time) < fromUnixTimestamp64Micro(%(ref_population_end_us)s, 'UTC')
"""


def _population_hour(result, params):
    """Accept only a complete aggregate over the selected raw-hour proof domain.

    Stale versions/tombstones may cause unnecessary FINAL, never a match.
    A numeric-qualified NULL proves no witness, not physical emptiness.
    """
    try:
        rows = result.data
        if (
            getattr(result, "complete", True) is not True
            or not isinstance(rows, list)
            or len(rows) != 1
            or not isinstance(rows[0], dict)
        ):
            raise ValueError
        hour = rows[0]["newest_raw_hour_us"]
        if hour is not None and (
            type(hour) is not int
            or hour % _HOUR_US
            or not params["ref_population_start_us"]
            <= hour
            < params["ref_population_end_us"]
        ):
            raise ValueError
    except (AttributeError, KeyError, TypeError, ValueError):
        raise ReplayError("SPAN_REFERENCE_POPULATION_INVALID_OR_INCOMPLETE") from None
    return hour


def _validate_rows(result, params, seen, previous):
    """Validate each independent ordered prefix, including cross-query drift."""
    try:
        if getattr(result, "complete", True) is not True:
            raise ValueError
        rows = result.data
        if not isinstance(rows, list) or len(rows) > params["ref_limit"]:
            raise ValueError
        identities = []
        for row in rows:
            if (
                type(row["_version"]) is not int
                or not 0 <= row["_version"] < 2**64
                or any(
                    not isinstance(row[key], str)
                    for key in ("trace_id", "id", "observation_type", "service_name")
                )
                or not row["trace_id"]
                or not row["id"]
            ):
                raise ValueError
            identity = span_identity(row)
            physical = tuple(identity[:6])
            order = (
                identity[6],
                identity[2],
                identity[1],
                identity[0],
                identity[4],
                identity[5],
            )
            if (
                identity[0] not in params["ref_project_ids"]
                or physical in seen
                or row["physical_hour_us"] != identity[3]
                or identity[3] != identity[6] // _HOUR_US * _HOUR_US
                or not params["ref_start_us"] <= identity[6] < params["ref_end_us"]
                or not params["ref_scan_start_us"]
                <= identity[6]
                < params["ref_scan_end_us"]
                or (params["ref_has_before"] and order >= params["ref_before_order"])
                or (previous is not None and order >= previous)
            ):
                raise ValueError
            identities.append(identity)
            seen.add(physical)
            previous = order
    except (KeyError, TypeError, ValueError, ReplayError):
        raise ReplayError("SPAN_REFERENCE_RESULT_INVALID_OR_INCOMPLETE") from None
    return identities, previous


def _remaining_ms(reader, deadline):
    remaining = int((deadline - monotonic()) * 1000)
    outer_remaining = getattr(reader, "remaining_read_ms", None)
    if callable(outer_remaining):
        outer = outer_remaining()
        if type(outer) not in (int, float) or not math.isfinite(outer):
            raise ReplayError("SPAN_REFERENCE_INVALID_READER_DEADLINE")
        remaining = min(remaining, int(outer))
    if remaining <= 0:
        raise ReplayError("SPAN_REFERENCE_TOTAL_WALL_EXCEEDED")
    return remaining


def _read_bytes(result, reader, prior_calls):
    """Use explicit result progress or the runner's one-call progress ledger.

    Missing progress cannot silently grant a fresh 8GiB to the next statement.
    ReadOnlyExecutor exposes progress.bytes in calls[-1]; local fixture readers
    may expose read_bytes directly. No database/connection introspection.
    """
    amount = getattr(result, "read_bytes", None)
    calls = getattr(reader, "calls", None)
    if amount is None and prior_calls is not None and isinstance(calls, list):
        if len(calls) == prior_calls + 1 and isinstance(calls[-1], dict):
            amount = calls[-1].get("read_bytes")
    if type(amount) is not int or amount < 0:
        raise ReplayError("SPAN_REFERENCE_READ_PROGRESS_REQUIRED")
    return amount


def reference_span_ids(reader, case, scope, filters):
    """Prove OFFSET + target + lookahead from independently populated UTC hours.

    Raw project/hour discovery skips physically empty ranges, or ranges with
    no mandatory numeric witness when one is available. Every hit
    replays its whole hour through unpruned FINAL, including tombstones and
    versions outside the exact boundary timestamps. Only then may the scalar
    predicate and the oracle's OWN full ordering key select an ordered page.
    Never rediscover an hour with an unfinished keyset. This new route applies
    only to all-positive scalar equality/IN. Raw discovery starts in the newest
    remaining UTC day, widening to seven adjacent days only after seven whole
    proven-empty days. Every hit resets that width, even if FINAL has no matches.
    Other shapes retain independent
    raw-prefix daily reads, widening to weeks only after seven empty days.
    Neither route uses application candidate IDs or compiler metadata.
    Global OFFSET is consumed once in Python, not reapplied to every interval.

    One wall, byte and query budget covers every statement, including OFFSET
    traversal. Failure returns no prefix. Reader completeness/throw-on-overflow
    and byte progress are mandatory. Concurrent writes are not snapshot-frozen.
    """
    started = monotonic()
    deadline = started + SAFE_FINAL_SETTINGS["max_execution_time"]
    sql, params = build_span_reference_query(case, scope, filters)
    hourly = params["ref_hourly_discovery"]
    numeric = positive_numeric_leaf(filters) if hourly else None
    population_sql, population_values = _POPULATION_SQL, {}
    if numeric is not None:
        # Full AND/scope validation above precedes any I/O. A matching winner
        # is a raw witness in its immutable hour; stale hits still need FINAL.
        predicate, population_values = membership_having([numeric[0]], scalar_span=True)
        population_sql += f"\n WHERE ({predicate})"
    target = params["ref_limit"] - 1
    scan_end = params["ref_end_us"]
    scan_start = None
    width_days, consecutive_empty, interval_rows = 1, 0, 0
    population_width_days, raw_empty_days = 1, 0
    intervals_completed = 0
    skip_remaining = params["ref_offset"]
    identities, seen, previous = [], set(), None
    before = None
    read_bytes, query_count = 0, 0
    population_query_count = 0
    coverage = []

    def read_statement(query, local):
        nonlocal read_bytes, query_count
        remaining_ms = _remaining_ms(reader, deadline)
        if query_count >= MAX_REFERENCE_QUERIES:
            raise ReplayError("SPAN_REFERENCE_TOTAL_QUERY_BUDGET_EXCEEDED")
        remaining_bytes = SAFE_FINAL_SETTINGS["max_bytes_to_read"] - read_bytes
        if remaining_bytes <= 0:
            raise ReplayError("SPAN_REFERENCE_TOTAL_READ_BYTES_EXCEEDED")
        limits = {
            **SAFE_FINAL_SETTINGS,
            "max_execution_time": remaining_ms / 1000,
            "max_bytes_to_read": remaining_bytes,
        }
        calls = getattr(reader, "calls", None)
        prior_calls = len(calls) if isinstance(calls, list) else None
        query_count += 1
        result = reader.execute_ch_query(query, local, settings=limits)
        read_bytes += _read_bytes(result, reader, prior_calls)
        if read_bytes > SAFE_FINAL_SETTINGS["max_bytes_to_read"]:
            raise ReplayError("SPAN_REFERENCE_TOTAL_READ_BYTES_EXCEEDED")
        # Even a late successful NULL/empty/positive result cannot establish a
        # reference after its original caller/local deadline has expired.
        _remaining_ms(reader, deadline)
        return result

    while scan_end > params["ref_start_us"] and len(identities) < target + 1:
        if scan_start is None and hourly:
            newest_day = (scan_end - 1) // _DAY_US * _DAY_US
            probe_start = max(
                params["ref_start_us"],
                newest_day - (population_width_days - 1) * _DAY_US,
            )
            population_params = {
                **population_values,
                "ref_project_ids": params["ref_project_ids"],
                "ref_population_start_us": probe_start // _HOUR_US * _HOUR_US,
                "ref_population_end_us": ((scan_end - 1) // _HOUR_US + 1) * _HOUR_US,
            }
            result = read_statement(population_sql, population_params)
            population_query_count += 1
            hour = _population_hour(result, population_params)
            if hour is None:
                # A partial newest day (or remaining fraction after a hit) is
                # not a whole proven-empty day. Only completed UTC days qualify the
                # wider metadata interval; no failed/late read reaches here.
                if probe_start % _DAY_US == 0 and scan_end % _DAY_US == 0:
                    raw_empty_days += (scan_end - probe_start) // _DAY_US
                population_width_days = 7 if raw_empty_days >= 7 else 1
            else:
                # Even a stale/tombstoned or value-rejected hit is populated.
                # Do not treat classifier emptiness as raw absence evidence.
                population_width_days, raw_empty_days = 1, 0
            next_end = probe_start if hour is None else min(scan_end, hour + _HOUR_US)
            if next_end < scan_end:
                coverage.append(
                    {
                        "kind": "numeric_witness_empty"
                        if numeric
                        else "raw_population_empty",
                        "start_us": next_end,
                        "end_us": scan_end,
                        **({"numeric_witness_rows": 0} if numeric else {"rows": 0}),
                        "exhausted": True,
                    }
                )
            scan_end = next_end
            if hour is None:
                # NULL covers only this adjacent probe, not older history.
                continue
            scan_start = max(params["ref_start_us"], hour)
        elif scan_start is None:
            newest_day = (scan_end - 1) // _DAY_US * _DAY_US
            scan_start = max(
                params["ref_start_us"], newest_day - (width_days - 1) * _DAY_US
            )

        needed = skip_remaining + target + 1 - len(identities)
        local = {
            **params,
            "ref_scan_start_us": scan_start,
            "ref_scan_end_us": scan_end,
            "ref_limit": min(SAFE_FINAL_SETTINGS["max_result_rows"], needed),
            "ref_offset": 0,
            "ref_has_before": int(before is not None),
            "ref_before_order": before or params["ref_before_order"],
        }
        result = read_statement(sql, local)
        batch, previous = _validate_rows(result, local, seen, previous)
        interval_rows += len(batch)
        skipped = min(skip_remaining, len(batch))
        skip_remaining -= skipped
        identities.extend(batch[skipped:])
        interval_exhausted = len(batch) < local["ref_limit"]
        coverage.append(
            {
                "kind": "exact_hour_page" if hourly else "exact_daily_page",
                "start_us": scan_start,
                "end_us": scan_end,
                "rows": len(batch),
                "exhausted": interval_exhausted,
            }
        )
        if interval_exhausted:
            intervals_completed += 1
            if not hourly:
                consecutive_empty = consecutive_empty + 1 if interval_rows == 0 else 0
                width_days = 7 if consecutive_empty >= 7 else 1
            scan_end, scan_start = scan_start, None
            interval_rows, before = 0, None
        else:
            before = previous
    exhausted = scan_end <= params["ref_start_us"]
    _remaining_ms(reader, deadline)
    total = 0 if exhausted and not identities and params["ref_offset"] == 0 else None
    return identities[:target], {
        "reference_route": (
            "independent_raw_hour_discovery_unpruned_FINAL_scalar_span_prefix.v2"
            if hourly
            else "independent_adjacent_UTC_intervals_raw_prefix_FINAL_scalar_span_prefix.v1"
        ),
        "reference_prefix_proof": params["ref_prefix_proof"],
        **(
            {"reference_population_proof": "mandatory_numeric_witness"}
            if numeric
            else {}
        ),
        "span_evidence_contract": CONTRACT,
        "project_ids": list(params["ref_project_ids"]),
        "window_start_us": params["ref_start_us"],
        "window_end_us": params["ref_end_us"],
        "total_matches": total,
        "page_number": params["ref_offset"] // target,
        "has_more": len(identities) > target,
        "population_exhausted": exhausted,
        "ordered_prefix_complete": True,
        "complete_population_read": params["ref_offset"] == 0 and exhausted,
        "reference_query_count": query_count,
        "population_query_count": population_query_count,
        "reference_read_bytes": read_bytes,
        "reference_elapsed_ms": round((monotonic() - started) * 1000, 2),
        "intervals_completed": intervals_completed,
        "interval_coverage": coverage,
        "lookahead_identity": identities[target] if len(identities) > target else None,
        "same_transaction_snapshot": False,
        "http_e2e": False,
        "metrics_verified": False,
    }
