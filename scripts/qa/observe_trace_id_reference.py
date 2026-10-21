"""Independent FINAL-based first-page ID/order reference for scalar trace filters.

No application query builder, selector, catalog, rollup, or candidate IDs are
used to choose the reference population. This is NOT a full-row metric oracle.
Unsupported shapes and an insufficient finite root prefix stay unverified.
"""

import replay_observe_filters as replay
from datetime import datetime, timedelta, timezone
import math
import re


def typed_membership_values(config):
    """Validate explicit picker provenance without coercion or type inference.

    The picker may use one UI filter type for several physical scalar types.
    Provenance, not that UI type or a value's spelling, chooses each Map. This
    oracle intentionally rejects omitted/unknown per-operand tags and JSON.
    """
    supplied = [
        config[name]
        for name in ("attribute_value_types", "attributeValueTypes")
        if name in config
    ]
    if not supplied:
        return None
    if len(supplied) == 2 and supplied[0] != supplied[1]:
        raise replay.ReplayError("REFERENCE_STORAGE_PROVENANCE_CONFLICT")
    if config.get("filter_op") not in {"in", "not_in"}:
        raise replay.ReplayError("REFERENCE_STORAGE_PROVENANCE_OPERATOR")
    if config.get("filter_type") not in {"text", "string", "number", "boolean"}:
        raise replay.ReplayError("REFERENCE_UNSUPPORTED_TYPE")
    values, types = config.get("filter_value"), supplied[0]
    if (
        not isinstance(values, list)
        or not values
        or not isinstance(types, list)
        or len(values) != len(types)
    ):
        raise replay.ReplayError("REFERENCE_STORAGE_PROVENANCE_ALIGNMENT")
    grouped = {}
    for value, storage in zip(values, types, strict=True):
        if not isinstance(storage, str) or storage not in {
            "string",
            "number",
            "boolean",
        }:
            raise replay.ReplayError("REFERENCE_STORAGE_PROVENANCE_TYPE")
        if storage == "string":
            valid = isinstance(value, str) and bool(value)
        elif storage == "boolean":
            valid = isinstance(value, bool)
        else:
            try:
                valid = (
                    isinstance(value, (int, float))
                    and not isinstance(value, bool)
                    and math.isfinite(value)
                )
            except OverflowError:
                valid = False
        if not valid:
            raise replay.ReplayError("REFERENCE_VALUE_TYPE_MISMATCH")
        grouped.setdefault(storage, []).append(value)
    return grouped


def positive_long_text_leaf(filters):
    """Only the QA long-string bucket, never empty-default or mixed-map proofs."""
    leaves = [
        f for f in filters if f["filter_config"].get("col_type") == "SPAN_ATTRIBUTE"
    ]
    if len(leaves) != 1:
        return None
    leaf = leaves[0]
    cfg = leaf["filter_config"]
    if typed_membership_values(cfg) is not None:
        # This optional string-only shortcut is not a mixed-Map proof. Typed
        # leaves remain supported by the independent FINAL prefix route below.
        return None
    op, value = cfg.get("filter_op"), cfg.get("filter_value")
    if cfg.get("filter_type") not in {"text", "string"} or op not in {
        "equals",
        "in",
        "contains",
    }:
        return None
    values = value if op == "in" else [value]
    if (
        not isinstance(values, list)
        or not values
        or not all(isinstance(v, str) and len(v.encode("utf-8")) > 128 for v in values)
    ):
        return None
    # Nonempty text cannot match the absent Map key's empty-string default,
    # even if the classifier's tied-version presence/value argMaxs disagree.
    return leaf, op, tuple(values)


def literal_like_pattern(value):
    """Bind a literal substring, independently of any application SQL helper."""
    return (
        "%" + value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    )


def text_digit_ngram_patterns(needles):
    """Necessary ASCII-only anchors for the deployed lower()/4-gram expression.

    Unicode case conversion cannot change ASCII digits. Do not use letters,
    Unicode decimal digits, wildcards, or an anchor from only some IN choices.
    Concatenation across Map values may add false positives, never negatives.
    """
    if not needles:
        return None
    patterns = []
    for needle in needles:
        runs = re.findall(r"[0-9]{4,}", needle)
        if not runs:
            return None
        # Longest run, first occurrence on ties; no value is embedded in SQL.
        patterns.append(literal_like_pattern(max(runs, key=len)))
    return tuple(dict.fromkeys(patterns))


def positive_numeric_leaf(filters):
    # membership_having validates the full flat AND before reference I/O.
    # Any mandatory positive leaf is a necessary raw superset, not a match proof.
    for leaf in filters:
        cfg = leaf["filter_config"]
        if cfg.get("col_type") != "SPAN_ATTRIBUTE":
            continue
        symbol = {
            "equals": "=",
            "greater_than": ">",
            "less_than": "<",
            "greater_than_or_equal": ">=",
            "less_than_or_equal": "<=",
        }.get(cfg["filter_op"])
        value = cfg.get("filter_value")
        if (
            cfg["filter_type"] == "number"
            and symbol is not None
            and not isinstance(value, bool)
            and isinstance(value, (int, float))
            and math.isfinite(value)
        ):
            return leaf, symbol, value
    return None


def positive_boolean_leaf(filters):
    for leaf in filters:
        cfg = leaf["filter_config"]
        if (
            cfg.get("col_type") == "SPAN_ATTRIBUTE"
            and cfg.get("filter_type") == "boolean"
            and cfg.get("filter_op") == "equals"
            and isinstance(cfg.get("filter_value"), bool)
            and not any(
                key in cfg for key in ("attribute_value_types", "attributeValueTypes")
            )
        ):
            return leaf, "=", cfg["filter_value"]
    return None


def span_coordinates(reader, params):
    """All immutable indexed coordinates, including old/deleted child versions."""
    prefixes = reader.execute_ch_query(
        """
        SELECT DISTINCT observation_type, service_name,
            toStartOfHour(start_time) AS span_hour, trace_id
        FROM spans
        PREWHERE project_id=%(project_id)s AND trace_id IN %(ref_trace_ids)s
        """,
        params,
        settings={},
    ).data
    if {str(p["trace_id"]) for p in prefixes} != set(params["ref_trace_ids"]):
        raise replay.ReplayError("REFERENCE_IDENTITY_DISCOVERY_INCONSISTENT")
    return tuple(
        (p["observation_type"], p["service_name"], p["span_hour"], p["trace_id"])
        for p in prefixes
    )


def complete_scalar_witness_reference(reader, filters, params, having, safe_final):
    """Independent exhaustive witness route for selective numeric/Boolean leaves.

    No candidate results, root prefix, date clipping, sampling or population
    LIMIT. The probe must finish in full; overflow/timeout only selects a
    different oracle, never proves absence. Raw versions are a superset;
    complete latest-state replay then decides membership AND root eligibility.
    """
    predicate = positive_numeric_leaf(filters)
    kind, column = "numeric", "attrs_number"
    if predicate is None:
        predicate = positive_boolean_leaf(filters)
        kind, column = "boolean", "attrs_bool"
    if predicate is None:
        return None
    leaf, symbol, value = predicate
    try:
        witnesses = reader.execute_ch_query(
            f"""
            SELECT DISTINCT trace_id FROM spans
            PREWHERE project_id=%(project_id)s
            WHERE mapContains({column}, %(ref_{kind}_key)s)
                AND {column}[%(ref_{kind}_key)s] {symbol} %(ref_{kind}_value)s
            """,
            {
                **params,
                f"ref_{kind}_key": leaf["column_id"],
                f"ref_{kind}_value": value,
            },
            settings={
                # Independent diagnostic guards, not application/SLO cutoffs.
                "max_execution_time": 2,
                "max_bytes_to_read": 1024**3,
                "max_result_rows": 1000,
                "max_rows_in_distinct": 1000,
                "distinct_overflow_mode": "throw",
                "result_overflow_mode": "throw",
            },
        ).data
    except Exception as exc:
        # No application error classifier/compiler dependency. Do not mask
        # syntax, permissions, scope, type errors or arbitrary exceptions.
        from clickhouse_driver.errors import ErrorCodes, ServerException

        if not isinstance(exc, ServerException) or exc.code not in {
            ErrorCodes.TIMEOUT_EXCEEDED,
            ErrorCodes.TOO_MANY_ROWS,
            ErrorCodes.TOO_MANY_BYTES,
            ErrorCodes.TOO_MANY_ROWS_OR_BYTES,
            ErrorCodes.MEMORY_LIMIT_EXCEEDED,
            ErrorCodes.SET_SIZE_LIMIT_EXCEEDED,
            ErrorCodes.LIMIT_EXCEEDED,
        }:
            raise
        return None
    ids = tuple(str(r["trace_id"]) for r in witnesses)
    if len(set(ids)) != len(ids):
        raise replay.ReplayError(f"REFERENCE_DUPLICATE_{kind.upper()}_WITNESS")
    evidence = {
        "population_exhausted": True,
        "raw_witness_population": len(ids),
        "reference_route": f"complete_global_{kind}_witness_then_FINAL",
    }
    if not ids:
        return [], {**evidence, "absence_proof": f"zero_raw_{kind}_witnesses_globally"}
    return final_witness_ids(reader, params, having, safe_final, ids), evidence


def final_witness_ids(reader, params, having, safe_final, ids):
    """Replay all six-part physical identities, then root eligibility/order."""
    bound = {**params, "ref_trace_ids": ids}
    bound["ref_span_prefixes"] = span_coordinates(reader, bound)
    roots = reader.execute_ch_query(
        f"""
        SELECT trace_id, maxIf(start_time,
            (parent_span_id IS NULL OR parent_span_id='')
            AND start_time>=fromUnixTimestamp64Micro(%(ref_start_us)s)
            AND start_time<fromUnixTimestamp64Micro(%(ref_end_us)s)) AS root_order_time
        FROM spans FINAL
        PREWHERE project_id=%(project_id)s
            AND (observation_type, service_name, toStartOfHour(start_time), trace_id)
                IN %(ref_span_prefixes)s
        WHERE is_deleted=0
        GROUP BY trace_id
        HAVING countIf((parent_span_id IS NULL OR parent_span_id='')
            AND start_time>=fromUnixTimestamp64Micro(%(ref_start_us)s)
            AND start_time<fromUnixTimestamp64Micro(%(ref_end_us)s))>0
            AND ({having})
        ORDER BY root_order_time DESC, trace_id DESC
        LIMIT %(ref_target_rows)s
        """,
        bound,
        settings=safe_final,
    ).data
    return [str(r["trace_id"]) for r in roots]


def complete_text_witness_reference(reader, filters, params, having, safe_final):
    """An independent complete raw superset for one positive long-text leaf.

    Roots alone are date-scoped; every child's physical history participates.
    An unavailable safe digit anchor or a bounded probe failure selects the
    existing prefix oracle, never an empty result. FINAL remains authoritative.
    """
    selected = positive_long_text_leaf(filters)
    if selected is None:
        return None
    leaf, op, needles = selected
    patterns = text_digit_ngram_patterns(needles)
    if patterns is None:
        return None
    bound = {**params, "ref_text_key": leaf["column_id"]}
    value = "lowerUTF8(attrs_string[%(ref_text_key)s])"
    if op == "in":
        bound["ref_text_values"] = list(needles)
        match = f"has(arrayMap(x -> lowerUTF8(x), %(ref_text_values)s), {value})"
    else:
        bound["ref_text_value"] = needles[0]
        match = (
            f"{value} = lowerUTF8(%(ref_text_value)s)"
            if op == "equals"
            else f"positionUTF8({value}, lowerUTF8(%(ref_text_value)s)) > 0"
        )
    anchors = []
    for index, pattern in enumerate(patterns):
        key = f"ref_text_digit_pattern_{index}"
        bound[key] = pattern
        anchors.append(
            "arrayStringConcat(arrayMap(x -> lower(x), mapValues(attrs_string))) "
            f"LIKE %({key})s"
        )
    try:
        witnesses = reader.execute_ch_query(
            f"""
            SELECT DISTINCT trace_id FROM spans
            PREWHERE project_id=%(project_id)s
                AND trace_id IN (
                    SELECT trace_id FROM spans
                    PREWHERE project_id=%(project_id)s
                        AND start_time>=fromUnixTimestamp64Micro(%(ref_start_us)s)
                        AND start_time<fromUnixTimestamp64Micro(%(ref_end_us)s)
                    WHERE parent_span_id IS NULL OR parent_span_id=''
                )
            WHERE ({" OR ".join(anchors)})
                AND mapContains(attrs_string, %(ref_text_key)s)
                AND ({match})
            """,
            bound,
            settings={
                "max_execution_time": 2,
                "max_bytes_to_read": 1024**3,
                "max_result_rows": 1000,
                "max_rows_in_distinct": 1000,
                "read_overflow_mode": "throw",
                "distinct_overflow_mode": "throw",
                "result_overflow_mode": "throw",
                "timeout_overflow_mode": "throw",
            },
        ).data
    except Exception as exc:
        from clickhouse_driver.errors import ErrorCodes, ServerException

        if not isinstance(exc, ServerException) or exc.code not in {
            ErrorCodes.TIMEOUT_EXCEEDED,
            ErrorCodes.TOO_MANY_ROWS,
            ErrorCodes.TOO_MANY_BYTES,
            ErrorCodes.TOO_MANY_ROWS_OR_BYTES,
            ErrorCodes.MEMORY_LIMIT_EXCEEDED,
            ErrorCodes.SET_SIZE_LIMIT_EXCEEDED,
            ErrorCodes.LIMIT_EXCEEDED,
        }:
            raise
        return None
    if not isinstance(witnesses, list) or len(witnesses) > 1000:
        raise replay.ReplayError("REFERENCE_TEXT_WITNESS_RESULT_INVALID")
    ids = tuple(str(r["trace_id"]) for r in witnesses)
    if len(set(ids)) != len(ids):
        raise replay.ReplayError("REFERENCE_DUPLICATE_TEXT_WITNESS")
    evidence = {
        "population_exhausted": True,
        "raw_witness_population": len(ids),
        "reference_route": "complete_root_scoped_text_witness_then_FINAL",
        "digit_anchor_count": len(patterns),
    }
    if not ids:
        return [], {
            **evidence,
            "absence_proof": "zero_raw_text_witnesses_in_complete_root_population",
        }
    return final_witness_ids(reader, params, having, safe_final, ids), evidence


def unix_microseconds(value):
    return (value - datetime(1970, 1, 1, tzinfo=timezone.utc)) // timedelta(
        microseconds=1
    )


def numeric_absence_proof(reader, filters, params):
    """Zero raw witnesses prove absence; positive counts never prove a match.

    This independent all-population count has no candidate IDs, pagination or
    application SQL/compiler dependency. Old versions/tombstones may add false
    positives but cannot remove a live match. Time restricts ROOTS only.
    """
    predicate = positive_numeric_leaf(filters)
    if predicate is None:
        return False
    leaf, symbol, value = predicate
    result = reader.execute_ch_query(
        f"""
        SELECT count() AS raw_witness_count
        FROM spans
        PREWHERE project_id=%(project_id)s
            AND trace_id IN (
                SELECT trace_id FROM spans
                PREWHERE project_id=%(project_id)s
                    AND start_time>=fromUnixTimestamp64Micro(%(ref_start_us)s)
                    AND start_time<fromUnixTimestamp64Micro(%(ref_end_us)s)
                WHERE parent_span_id IS NULL OR parent_span_id=''
            )
        WHERE mapContains(attrs_number, %(ref_absence_key)s)
            AND attrs_number[%(ref_absence_key)s] {symbol} %(ref_absence_value)s
        """,
        {**params, "ref_absence_key": leaf["column_id"], "ref_absence_value": value},
        settings={},
    ).data
    if len(result) != 1 or "raw_witness_count" not in result[0]:
        raise replay.ReplayError("REFERENCE_ABSENCE_COUNT_INVALID")
    return result[0]["raw_witness_count"] == 0


def boolean_absence_proof(reader, filters, params):
    """An independent zero raw count proves a mandatory Boolean leaf absent.

    Scan the whole authorized project, including every child, old version and
    tombstone. No requested-time, root, candidate-ID or finite-prefix selection
    may narrow this proof. Presence is mandatory: missing Map keys are not False.
    A positive count is inconclusive and must use the normal latest-state oracle.
    The caller validates the complete flat AND before choosing this shortcut.
    """
    predicate = positive_boolean_leaf(filters)
    if predicate is None:
        return False
    leaf, _, value = predicate
    result = reader.execute_ch_query(
        """
        SELECT count() AS raw_witness_count
        FROM spans
        PREWHERE project_id=%(project_id)s
        WHERE mapContains(attrs_bool, %(ref_absence_key)s)
            AND attrs_bool[%(ref_absence_key)s] = %(ref_absence_value)s
        """,
        {
            "project_id": params["project_id"],
            "ref_absence_key": leaf["column_id"],
            "ref_absence_value": value,
        },
        settings={},
    ).data
    count = result[0].get("raw_witness_count") if len(result) == 1 else None
    if type(count) is not int or count < 0:
        raise replay.ReplayError("REFERENCE_ABSENCE_COUNT_INVALID")
    return count == 0


def membership_having(filters, *, scalar_span=False):
    """Independent scalar semantics at trace grain, after physical FINAL.

    Each ordinary leaf requires a latest-live span satisfying that leaf. This
    also applies to negative value comparisons: a trace can have both an equal
    and a different value on different spans. Missing keys do not satisfy a
    negative value comparison. Only is_null is absence across the whole trace.
    These rules are not interchangeable with NOT EXISTS(positive), nor with
    requiring all leaves to occur on the same span.

    scalar_span=True renders the same independent predicates for ONE physical
    winner after FINAL, without an aggregate. Map presence is non-nullable;
    absence becomes NOT(presence). Value negatives retain their presence guard.
    The default trace-grain SQL and parameter bindings remain unchanged.
    """

    def render(predicate, *, absent=False):
        if scalar_span:
            return f"NOT ({predicate})" if absent else f"({predicate})"
        return f"countIf({predicate}) {'= 0' if absent else '> 0'}"

    predicates, params = [], {}
    for index, item in enumerate(filters):
        cfg = item["filter_config"]
        if (
            cfg.get("col_type") == "SYSTEM_METRIC"
            and item["column_id"] == "created_at"
            and cfg["filter_op"] == "between"
        ):
            continue
        if cfg.get("col_type") != "SPAN_ATTRIBUTE":
            raise replay.ReplayError("REFERENCE_UNSUPPORTED_FILTER")
        typed_values = typed_membership_values(cfg)
        kind, op = cfg["filter_type"], cfg["filter_op"]
        table_map = {
            "text": "attrs_string",
            "string": "attrs_string",
            "number": "attrs_number",
            "boolean": "attrs_bool",
        }.get(kind)
        if table_map is None:
            raise replay.ReplayError("REFERENCE_UNSUPPORTED_TYPE")
        key, val = f"ref_key_{index}", f"ref_value_{index}"
        params[key] = item["column_id"]
        if typed_values is not None:
            presence, positives = [], []
            for storage, column in (
                ("string", "attrs_string"),
                ("number", "attrs_number"),
                ("boolean", "attrs_bool"),
            ):
                if storage not in typed_values:
                    continue
                bound = f"{val}_{storage}"
                values = typed_values[storage]
                # The typed wire contract lowercases selected strings in
                # Python, while ClickHouse lowerUTF8 reads the stored value.
                # Keep numeric strings in their string branch; never cast them.
                params[bound] = (
                    [v.lower() for v in values] if storage == "string" else values
                )
                exists = f"mapContains({column}, %({key})s)"
                lhs = f"{column}[%({key})s]"
                if storage == "string":
                    lhs = f"lowerUTF8({lhs})"
                presence.append(exists)
                positives.append(f"({exists} AND has(%({bound})s, {lhs}))")
            positive = " OR ".join(positives)
            match = (
                f"({' OR '.join(presence)}) AND NOT ({positive})"
                if op == "not_in"
                else f"({positive})"
            )
            # NOT_IN negates all selected typed positives on ONE latest span;
            # a different span may still satisfy this leaf at trace grain.
            predicates.append(render(match))
            continue
        present = f"mapContains({table_map}, %({key})s)"
        value = f"{table_map}[%({key})s]"
        if op in ("is_null", "is_not_null"):
            predicates.append(render(present, absent=op == "is_null"))
            continue
        raw = cfg.get("filter_value")
        multiple = op in {"in", "not_in", "between", "not_between"}
        if multiple:
            if not isinstance(raw, list) or not raw:
                raise replay.ReplayError("REFERENCE_VALUE_TYPE_MISMATCH")
            if op in {"between", "not_between"} and len(raw) != 2:
                raise replay.ReplayError("REFERENCE_VALUE_TYPE_MISMATCH")
            operands = raw
        else:
            operands = [raw]
        for operand in operands:
            if kind in {"text", "string"}:
                valid = isinstance(operand, str) and bool(operand)
            elif kind == "boolean":
                valid = isinstance(operand, bool)
            else:
                valid = (
                    isinstance(operand, (int, float))
                    and not isinstance(operand, bool)
                    and math.isfinite(operand)
                )
            if not valid:
                raise replay.ReplayError("REFERENCE_VALUE_TYPE_MISMATCH")
        params[val] = raw
        text_kind = kind in {"text", "string"}
        lhs = f"lowerUTF8({value})" if text_kind else value
        rhs = f"lowerUTF8(%({val})s)" if text_kind else f"%({val})s"
        if op in {"in", "not_in"}:
            choices = (
                f"arrayMap(x -> lowerUTF8(x), %({val})s)" if text_kind else f"%({val})s"
            )
            match = f"has({choices}, {lhs})"
            if op == "not_in":
                match = f"NOT ({match})"
        elif op in {"equals", "not_equals"}:
            match = f"{lhs} {'=' if op == 'equals' else '!='} {rhs}"
        elif op in {"contains", "not_contains", "starts_with", "ends_with"}:
            if not text_kind:
                raise replay.ReplayError("REFERENCE_UNSUPPORTED_OPERATOR")
            if op in {"contains", "not_contains"}:
                match = f"position({lhs}, {rhs}) {'> 0' if op == 'contains' else '= 0'}"
            else:
                fn = "startsWith" if op == "starts_with" else "endsWith"
                match = f"{fn}({lhs}, {rhs})"
        elif op in {"between", "not_between"}:
            # Ordering/range semantics use the original scalar, not lowercase
            # text. Bounds remain in requested order; never silently sort them.
            params[f"{val}_low"], params[f"{val}_high"] = operands
            params.pop(val)
            match = f"{value} BETWEEN %({val}_low)s AND %({val}_high)s"
            if op == "not_between":
                match = f"NOT ({match})"
        else:
            symbol = {
                "greater_than": ">",
                "less_than": "<",
                "greater_than_or_equal": ">=",
                "less_than_or_equal": "<=",
            }.get(op)
            if symbol is None:
                raise replay.ReplayError("REFERENCE_UNSUPPORTED_OPERATOR")
            match = f"{value} {symbol} %({val})s"
        predicates.append(render(f"{present} AND ({match})"))
    if not predicates:
        raise replay.ReplayError("REFERENCE_ATTRIBUTE_REQUIRED")
    return " AND ".join(predicates), params


def root_prefix(reader, params, safe_final):
    """Independent newest-first FINAL population; no arbitrary date clipping.

    Entire adjacent intervals are exhausted before advancing. The read cap can
    stop the diagnostic but cannot silently shorten its original request.
    """
    roots, seen = [], set()
    scan_end = params["ref_end"]
    while scan_end > params["ref_start"] and len(roots) < params["ref_root_limit"]:
        scan_start = max(params["ref_start"], scan_end - timedelta(days=1))
        local = {
            **params,
            "ref_scan_start": scan_start,
            "ref_scan_end": scan_end,
            "ref_scan_start_us": unix_microseconds(scan_start),
            "ref_scan_end_us": unix_microseconds(scan_end),
            "ref_remaining": params["ref_root_limit"] - len(roots),
            "ref_seen_ids": tuple(sorted(seen)),
        }
        seen_predicate = "AND trace_id NOT IN %(ref_seen_ids)s" if seen else ""
        rows = reader.execute_ch_query(
            f"""
        SELECT trace_id, max(start_time) AS root_order_time
        FROM spans FINAL
        PREWHERE project_id=%(project_id)s
            AND toStartOfHour(start_time)>=toStartOfHour(toDateTime64(%(ref_scan_start)s, 6, 'UTC'))
            AND toStartOfHour(start_time)<=toStartOfHour(toDateTime64(%(ref_scan_end)s, 6, 'UTC'))
            {seen_predicate}
        WHERE is_deleted=0 AND (parent_span_id IS NULL OR parent_span_id='')
            AND start_time>=fromUnixTimestamp64Micro(%(ref_scan_start_us)s)
            AND start_time<fromUnixTimestamp64Micro(%(ref_scan_end_us)s)
        GROUP BY trace_id ORDER BY root_order_time DESC, trace_id DESC
        LIMIT %(ref_remaining)s
    """,
            local,
            settings=safe_final,
        ).data
        row_ids = [str(r["trace_id"]) for r in rows]
        if len(set(row_ids)) != len(row_ids) or seen.intersection(row_ids):
            raise replay.ReplayError("REFERENCE_DUPLICATE_ROOT")
        roots.extend(rows)
        seen.update(row_ids)
        scan_end = scan_start
    return roots


def reference_ids(reader, case, scope, filters):
    if case["surface"] not in {"traces", "task_traces", "eval_traces"}:
        raise replay.ReplayError("REFERENCE_SURFACE_NOT_SUPPORTED")
    having, leaf_params = membership_having(filters)
    params = {
        "project_id": scope["project_id"],
        "ref_start": replay.utc(case["window"]["start"]),
        "ref_end": replay.utc(case["window"]["end"]),
        "ref_root_limit": 250,
        "ref_target_rows": case["request"]["target_rows"],
        **leaf_params,
    }
    params.update(
        ref_start_us=unix_microseconds(params["ref_start"]),
        ref_end_us=unix_microseconds(params["ref_end"]),
    )
    # PREWHERE contains only immutable replacement-key components, widened to
    # whole boundary hours. Mutable time/parent/tombstone filters run AFTER
    # FINAL; automatic predicate motion is explicitly disabled.
    safe_final = {
        "optimize_move_to_prewhere": 0,
        "optimize_move_to_prewhere_if_final": 0,
        "use_skip_indexes_if_final": 0,
        "enable_optimize_predicate_expression_to_final_subquery": 0,
        "query_plan_merge_expressions": 0,
    }
    complete_scalar = complete_scalar_witness_reference(
        reader, filters, params, having, safe_final
    )
    if complete_scalar is not None:
        return complete_scalar
    complete_text = complete_text_witness_reference(
        reader, filters, params, having, safe_final
    )
    if complete_text is not None:
        return complete_text
    if boolean_absence_proof(reader, filters, params):
        return [], {
            "population_exhausted": True,
            "absence_proof": "zero_raw_boolean_witnesses_in_entire_project",
        }
    if numeric_absence_proof(reader, filters, params):
        return [], {
            "population_exhausted": True,
            "absence_proof": "zero_raw_numeric_witnesses_in_complete_root_population",
        }
    roots = root_prefix(reader, params, safe_final)
    if not roots:
        return [], {"root_prefix": 0, "population_exhausted": True}
    target = case["request"]["target_rows"]
    ordered, examined = [], 0
    # Chunk the independently acquired ordered population, not the candidate
    # result. A broad UUID IN set defeats skip-index selectivity on this table.
    # Stop only after a proven ordered prefix fills the requested page.
    for offset in range(0, len(roots), 50):
        batch = roots[offset : offset + 50]
        params["ref_trace_ids"] = tuple(str(r["trace_id"]) for r in batch)
        # trace_id is in the full sorting/replacement key but NOT in the
        # deployed sparse primary index. Discover its complete indexed
        # coordinates using only narrow columns, across all child history.
        # No mutable/deletion/value/date predicate or population LIMIT here.
        params["ref_span_prefixes"] = span_coordinates(reader, params)
        matching = reader.execute_ch_query(
            f"""
        SELECT trace_id FROM spans FINAL
        PREWHERE project_id=%(project_id)s
            AND (observation_type, service_name, toStartOfHour(start_time), trace_id)
                IN %(ref_span_prefixes)s
        WHERE is_deleted=0
        GROUP BY trace_id HAVING {having}
    """,
            params,
            settings=safe_final,
        ).data
        ids = {str(r["trace_id"]) for r in matching}
        ordered.extend(str(r["trace_id"]) for r in batch if str(r["trace_id"]) in ids)
        examined += len(batch)
        if len(ordered) >= target:
            break
    if len(ordered) < target and len(roots) == params["ref_root_limit"]:
        raise replay.ReplayError("REFERENCE_PREFIX_INSUFFICIENT_NOT_EMPTY")
    return ordered[:target], {
        "root_prefix": len(roots),
        "roots_examined": examined,
        "population_exhausted": len(roots) < params["ref_root_limit"]
        and examined == len(roots)
        and len(ordered) <= target,
    }
