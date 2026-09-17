"""Independent PG Score membership plus FINAL trace-root ID/order oracle.

No candidate IDs, filter compiler, selector or catalog predicates determine
the reference population. Only flat annotation conjunctions are covered here.
PG and CH are not in one transaction: the separate CDC parity check is required.
"""

import json
from collections import defaultdict
from datetime import datetime, timezone

import replay_observe_filters as replay
from observe_trace_id_reference import span_coordinates, unix_microseconds


def score_matches(row, leaf):
    cfg = leaf["filter_config"]
    kind, op = cfg["filter_type"], cfg["filter_op"]
    expected = cfg.get("filter_value")
    values = expected if isinstance(expected, list) else [expected]
    payload = row["value"]
    if isinstance(payload, str):
        payload = json.loads(payload)
    if op in ("is_null", "is_not_null"):
        return True
    if kind == "annotator":
        if op in ("not_equals", "not_in"):
            # These exclude the selected annotator at entity scope, not one
            # Score at a time. Keep unsupported until separately validated.
            raise replay.ReplayError("REFERENCE_NEGATIVE_ANNOTATOR_UNVERIFIED")
        actual = str(row.get("annotator_id") or "")
        present = bool(actual)
    elif kind in ("text", "string"):
        actual = payload.get("text", "")
        present = isinstance(actual, str) and bool(actual)
        actual = actual.lower() if present else ""
        values = [str(value).lower() for value in values]
    elif kind == "number":
        actual = payload.get("rating", payload.get("value"))
        present = isinstance(actual, (float, int)) and not isinstance(actual, bool)
        if not present:
            # Don't bless CH JSONExtractFloat's missing/invalid-to-zero behavior.
            raise replay.ReplayError("REFERENCE_ANNOTATION_NUMERIC_STORAGE_UNSUPPORTED")
    elif kind in ("thumbs", "boolean"):
        actual = payload.get("value")
        tokens = {
            "up": "up",
            "down": "down",
            "thumbs up": "up",
            "thumbs down": "down",
            "thumbs_up": "up",
            "thumbs_down": "down",
            "true": "up",
            "false": "down",
        }
        values = [tokens.get(str(value).lower(), str(value)) for value in values]
        present = actual in ("up", "down")
    elif kind in ("categorical", "array"):
        actual = payload.get("selected", [])
        if not isinstance(actual, list):
            raise replay.ReplayError("REFERENCE_CATEGORICAL_STORAGE_UNSUPPORTED")
        # Legacy saved categorical thumbs selections may use display labels.
        legacy_thumbs = {
            "thumbs up": "up",
            "thumbs_up": "up",
            "thumbs down": "down",
            "thumbs_down": "down",
        }
        matched = any(
            value in actual
            or (
                isinstance(value, str)
                and value.strip().lower() in legacy_thumbs
                and payload.get("value") == legacy_thumbs[value.strip().lower()]
            )
            for value in values
        )
        if op in ("equals", "in", "contains"):
            return matched
        if op in ("not_equals", "not_in", "not_contains"):
            # A live annotation with none of these choices, not absence of
            # the label. Multiple Score rows retain existential value semantics.
            return not matched
        raise replay.ReplayError("REFERENCE_ANNOTATION_OPERATOR_UNSUPPORTED")
    else:
        raise replay.ReplayError("REFERENCE_ANNOTATION_TYPE_UNSUPPORTED")
    if not present:
        return False
    if op in ("equals", "in"):
        return actual in values
    if op in ("not_equals", "not_in"):
        return actual not in values
    if op in ("contains", "not_contains", "starts_with", "ends_with") and kind in (
        "text",
        "string",
    ):
        match = (
            values[0] in actual
            if "contains" in op
            else actual.startswith(values[0])
            if op == "starts_with"
            else actual.endswith(values[0])
        )
        return not match if op == "not_contains" else match
    if kind == "number":
        if op == "greater_than":
            return actual > values[0]
        if op == "greater_than_or_equal":
            return actual >= values[0]
        if op == "less_than":
            return actual < values[0]
        if op == "less_than_or_equal":
            return actual <= values[0]
        if op in ("between", "not_between"):
            match = values[0] <= actual <= values[1]
            return not match if op == "not_between" else match
    raise replay.ReplayError("REFERENCE_ANNOTATION_OPERATOR_UNSUPPORTED")


def annotation_entity_ids(rows, leaf, resolve_span_ids):
    """Evaluate one label over exhaustive, already project-scoped Score rows.

    Value comparisons require a qualifying Score. A negative annotator is
    different: any Score for the label AND no Score by an excluded annotator.
    Do not complement each Score independently when multiple people annotated
    the same entity. Span-only Scores are resolved before that set difference.
    """

    def entities(selected):
        ids = {str(row["trace_id"]) for row in selected if row.get("trace_id")}
        spans = {
            row["span_id"]
            for row in selected
            if not row.get("trace_id") and row.get("span_id")
        }
        if spans:
            ids.update(str(value) for value in resolve_span_ids(spans))
        return ids

    return annotation_relation_entities(rows, leaf, entities)


def annotation_relation_entities(rows, leaf, entities):
    """Set evaluation shared by independent trace and span identity oracles."""
    cfg = leaf["filter_config"]
    if cfg["filter_type"] == "annotator" and cfg["filter_op"] in (
        "not_equals",
        "not_in",
    ):
        positive = {**leaf, "filter_config": {**cfg, "filter_op": "in"}}
        excluded = entities([row for row in rows if score_matches(row, positive)])
        return entities(rows) - excluded
    return entities([row for row in rows if score_matches(row, leaf)])


def reference_ids(reader, case, scope, filters, document):
    if document.get("annotation_scores_complete") is not True:
        raise replay.ReplayError("EXHAUSTIVE_ANNOTATION_SOURCE_REQUIRED")
    leaves = []
    for leaf in filters:
        cfg = leaf["filter_config"]
        if (
            cfg.get("col_type") == "SYSTEM_METRIC"
            and leaf["column_id"] == "created_at"
            and cfg["filter_op"] == "between"
        ):
            continue
        if cfg.get("col_type") != "ANNOTATION":
            raise replay.ReplayError("REFERENCE_MIXED_PROPERTY_FAMILIES_UNSUPPORTED")
        leaves.append(leaf)
    if not leaves:
        raise replay.ReplayError("REFERENCE_ANNOTATION_REQUIRED")
    params = {
        "project_id": scope["project_id"],
        "ref_start_us": unix_microseconds(replay.utc(case["window"]["start"])),
        "ref_end_us": unix_microseconds(replay.utc(case["window"]["end"])),
        "ref_target_rows": case["request"]["target_rows"],
    }
    safe_final = {
        "optimize_move_to_prewhere": 0,
        "optimize_move_to_prewhere_if_final": 0,
        "do_not_merge_across_partitions_select_final": 0,
        "use_skip_indexes_if_final": 0,
        "enable_optimize_predicate_expression_to_final_subquery": 0,
        "query_plan_merge_expressions": 0,
    }
    positive, negative = None, set()
    for leaf in leaves:
        label = leaf["column_id"].split("**", 1)[0]
        rows = [
            row
            for row in document["annotation_scores_snapshot"]
            if row["project_id"] == scope["project_id"]
            and row["label_id"] == label
            and not row["deleted"]
        ]

        def resolve_span_ids(span_ids):
            resolved = reader.execute_ch_query(
                """SELECT DISTINCT trace_id FROM spans FINAL
                WHERE project_id=%(project_id)s AND id IN %(ref_span_ids)s AND is_deleted=0""",
                {**params, "ref_span_ids": tuple(sorted(span_ids))},
                settings=safe_final,
            ).data
            return [str(row["trace_id"]) for row in resolved]

        ids = annotation_entity_ids(rows, leaf, resolve_span_ids)
        if leaf["filter_config"]["filter_op"] == "is_null":
            negative.update(ids)
        else:
            positive = ids if positive is None else positive.intersection(ids)
    evidence = {
        "reference_route": "independent_PG_Score_membership_then_FINAL_roots",
        "source_metadata_sha256": replay.digest(document),
        "population_exhausted": True,
    }
    if positive is not None:
        positive.difference_update(negative)
        if not positive:
            return [], {
                **evidence,
                "absence_proof": "complete_PG_Score_membership_empty",
            }
        # Every coordinate is discovered from independent PG trace IDs, never
        # from the candidate page. Missing historical traces are not returned.
        existing = reader.execute_ch_query(
            """SELECT DISTINCT trace_id FROM spans
            PREWHERE project_id=%(project_id)s AND trace_id IN %(ref_trace_ids)s""",
            {**params, "ref_trace_ids": tuple(sorted(positive))},
        ).data
        params["ref_trace_ids"] = tuple(str(row["trace_id"]) for row in existing)
        if not params["ref_trace_ids"]:
            return [], {
                **evidence,
                "absence_proof": "no_spans_for_source_annotation_traces",
            }
        params["ref_span_prefixes"] = span_coordinates(reader, params)
        selection = "AND (observation_type, service_name, toStartOfHour(start_time), trace_id) IN %(ref_span_prefixes)s"
    else:
        selection = ""
    exclude = ""
    if negative:
        params["ref_exclude_ids"] = tuple(sorted(negative))
        exclude = "AND trace_id NOT IN %(ref_exclude_ids)s"
    rows = reader.execute_ch_query(
        f"""SELECT trace_id, max(start_time) AS root_order_time
        FROM spans FINAL PREWHERE project_id=%(project_id)s {selection}
        WHERE is_deleted=0 AND (parent_span_id IS NULL OR parent_span_id='')
            AND start_time >= fromUnixTimestamp64Micro(%(ref_start_us)s)
            AND start_time < fromUnixTimestamp64Micro(%(ref_end_us)s) {exclude}
        GROUP BY trace_id ORDER BY root_order_time DESC, trace_id DESC
        LIMIT %(ref_target_rows)s""",
        params,
        settings=safe_final,
    ).data
    return [str(row["trace_id"]) for row in rows], evidence


def span_identity(row):
    """Independent CH25 physical identity plus winner/order, never legacy fallback.

    The final two fields are mutable evidence, not replacement-key components.
    Including them catches a stale winning version/timestamp even when page IDs
    otherwise agree. This does not establish a cross-query transaction snapshot.
    """
    for field in (
        "project_id",
        "trace_id",
        "id",
        "observation_type",
        "service_name",
        "_version",
    ):
        if row.get(field) is None:
            raise replay.ReplayError("REFERENCE_COMPLETE_SPAN_IDENTITY_REQUIRED")
    timestamp = row.get("start_time")
    if isinstance(timestamp, str):
        try:
            timestamp = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        except ValueError as exc:
            raise replay.ReplayError("REFERENCE_SPAN_TIMESTAMP_INVALID") from exc
    if not isinstance(timestamp, datetime):
        raise replay.ReplayError("REFERENCE_SPAN_TIMESTAMP_INVALID")
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    timestamp = timestamp.astimezone(timezone.utc)
    version = row["_version"]
    if type(version) is not int or version < 0:
        raise replay.ReplayError("REFERENCE_SPAN_VERSION_INVALID")
    return [
        str(row["project_id"]),
        str(row["trace_id"]),
        str(row["id"]),
        unix_microseconds(timestamp.replace(minute=0, second=0, microsecond=0)),
        str(row["observation_type"]),
        str(row["service_name"]),
        unix_microseconds(timestamp),
        str(version),
    ]


def reference_span_ids(reader, case, scope, filters, document):
    """Independent Score membership over the complete FINAL span population.

    The source snapshot determines lookup IDs, not candidate rows. Trace-only
    annotations target roots; a span-backed Score targets its trace-local span.
    Guards throw if the independent source population cannot be exhausted.
    """
    if document.get("annotation_scores_complete") is not True:
        raise replay.ReplayError("EXHAUSTIVE_ANNOTATION_SOURCE_REQUIRED")
    leaves = []
    for leaf in filters:
        cfg = leaf["filter_config"]
        if (
            leaf["column_id"] == "created_at"
            and cfg.get("col_type") == "SYSTEM_METRIC"
            and cfg["filter_op"] == "between"
        ):
            continue
        if cfg.get("col_type") != "ANNOTATION":
            raise replay.ReplayError("REFERENCE_MIXED_PROPERTY_FAMILIES_UNSUPPORTED")
        leaves.append(leaf)
    if not leaves:
        raise replay.ReplayError("REFERENCE_ANNOTATION_REQUIRED")
    labels = {leaf["column_id"].split("**", 1)[0] for leaf in leaves}
    scores = [
        row
        for row in document["annotation_scores_snapshot"]
        if row["project_id"] == scope["project_id"]
        and not row["deleted"]
        and row["label_id"] in labels
    ]
    trace_ids = {
        str(row["trace_id"])
        for row in scores
        if row.get("trace_id") and not row.get("span_id")
    }
    span_ids = {row["span_id"] for row in scores if row.get("span_id")}
    settings = {
        "optimize_move_to_prewhere": 0,
        "optimize_move_to_prewhere_if_final": 0,
        "do_not_merge_across_partitions_select_final": 0,
        "use_skip_indexes_if_final": 0,
        "enable_optimize_predicate_expression_to_final_subquery": 0,
        "query_plan_merge_expressions": 0,
    }
    params = {
        "project_id": scope["project_id"],
        "ref_trace_ids": tuple(sorted(trace_ids)),
        "ref_span_ids": tuple(sorted(span_ids)),
    }
    physical = []
    for selection, enabled in (
        (
            "trace_id IN %(ref_trace_ids)s AND (parent_span_id IS NULL OR parent_span_id='')",
            trace_ids,
        ),
        ("id IN %(ref_span_ids)s", span_ids),
    ):
        if enabled:
            physical.extend(
                reader.execute_ch_query(
                    f"SELECT project_id,trace_id,id,start_time,parent_span_id,observation_type,service_name,_version FROM spans FINAL WHERE project_id=%(project_id)s AND is_deleted=0 AND ({selection})",
                    params,
                    settings=settings,
                ).data
            )
    roots, spans = defaultdict(set), defaultdict(set)
    for row in physical:
        identity = tuple(span_identity(row)[:6])
        if identity[0] != str(scope["project_id"]):
            raise replay.ReplayError("REFERENCE_SPAN_SCOPE_ESCAPED")
        spans[str(row["id"])].add(identity)
        if row["parent_span_id"] in (None, ""):
            roots[str(row["trace_id"])].add(identity)

    def entities(selected):
        result = set()
        for score in selected:
            if score.get("span_id"):
                # A populated trace id also fences a reused OTel span id.
                result.update(
                    identity
                    for identity in spans[score["span_id"]]
                    if not score.get("trace_id")
                    or identity[1] == str(score["trace_id"])
                )
            elif score.get("trace_id"):
                result.update(roots[str(score["trace_id"])])
        return result

    positive, negative = None, set()
    for leaf in leaves:
        rows = [
            row
            for row in scores
            if row["label_id"] == leaf["column_id"].split("**", 1)[0]
        ]
        pairs = annotation_relation_entities(rows, leaf, entities)
        if leaf["filter_config"]["filter_op"] == "is_null":
            negative.update(pairs)
        else:
            positive = pairs if positive is None else positive.intersection(pairs)
    params.update(
        ref_start_us=unix_microseconds(replay.utc(case["window"]["start"])),
        ref_end_us=unix_microseconds(replay.utc(case["window"]["end"])),
        ref_target_rows=case["request"]["target_rows"],
    )
    evidence = {
        "reference_route": "independent_PG_Score_physical_membership_then_FINAL_spans",
        "span_evidence_contract": "CH25_six_part_identity_winner_order.v2",
        "source_metadata_sha256": replay.digest(document),
        "population_exhausted": True,
    }
    if positive is not None and not positive:
        return [], evidence
    selection = ""
    coordinate = """(toString(project_id),trace_id,id,
        toUnixTimestamp64Micro(toDateTime64(toStartOfHour(start_time),6,'UTC')),
        observation_type,service_name)"""
    if positive is not None:
        params["ref_span_coordinates"] = tuple(sorted(positive))
        selection += f" AND {coordinate} IN %(ref_span_coordinates)s"
    if negative:
        params["ref_excluded_coordinates"] = tuple(sorted(negative))
        selection += f" AND {coordinate} NOT IN %(ref_excluded_coordinates)s"
    rows = reader.execute_ch_query(
        f"""SELECT DISTINCT project_id,trace_id,id,start_time,observation_type,service_name,_version FROM spans FINAL
        WHERE project_id=%(project_id)s AND is_deleted=0 {selection}
          AND start_time >= fromUnixTimestamp64Micro(%(ref_start_us)s)
          AND start_time < fromUnixTimestamp64Micro(%(ref_end_us)s)
        ORDER BY start_time DESC,id DESC,trace_id DESC,toString(project_id) DESC,observation_type DESC,service_name DESC
        LIMIT %(ref_target_rows)s""",
        params,
        settings=settings,
    ).data
    return [span_identity(row) for row in rows], evidence
