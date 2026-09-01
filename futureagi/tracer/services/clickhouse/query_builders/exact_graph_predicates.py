"""Single-scan predicates for exact raw-span graph aggregation.

ClickHouse 25.3 expands a common-table expression at every reference.  A
trace filter expressed as ``trace_id IN (SELECT ... FROM spans)`` therefore
opens another physical ``spans`` read and can observe a different parts
snapshot.  Exact graphs instead compile every supported filter to a predicate
on the *current* span row.  The time-series query turns those predicates into
independent per-trace flags after one raw ``spans`` read collapses physical
versions to a scalar-only latest-row tuple.

Relational filters are expressed against their authoritative eval, score, or
end-user relation while correlating to the current span row.  They never add a
second ``spans`` lookup: score rows use tagged trace/span entity keys and
end-user rows expand the curated remap directly.  A genuinely unsupported
shape still fails closed. Scalar, array, map, and legacy-JSON span attributes
all stay on the single-scan path.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from typing import Any

from tracer.services.clickhouse.query_builders.base import BaseQueryBuilder
from tracer.services.clickhouse.query_builders.filters import (
    build_literal_text_predicate,
    normalize_filter_op,
)
from tracer.services.clickhouse.query_builders.latest_filter_predicates import (
    UnsupportedFilterShapeError,
    compile_span_attribute_row_predicate,
)
from tracer.utils.filter_operators import normalize_span_attribute_filter_type

_OUTER_SOURCE_SENTINEL = "graph_outer_span_rows"
_RELATIONAL_SQL_PATTERN = re.compile(r"\b(?:SELECT|FROM|JOIN)\b", re.IGNORECASE)
_SPANS_SOURCE_PATTERN = re.compile(
    r"\b(?:FROM|JOIN)\s+(?:[A-Za-z_][A-Za-z0-9_]*\.)?spans\b",
    re.IGNORECASE,
)
_ZERO_UUID = "00000000-0000-0000-0000-000000000000"


@dataclass(frozen=True)
class ExactGraphRowPredicatePlan:
    """Bound row predicates evaluated by one physical ``spans`` read."""

    predicates: tuple[str, ...]
    # The legacy exact trace graph searched one day on either side for
    # ordinary scalar/system witnesses, but kept structured JSON array/map
    # witnesses inside the requested output window. Keep that distinction
    # explicit so the one-scan query can preserve both contracts.
    output_window_only: tuple[bool, ...]
    # Most filters require an any-sibling witness (window max = 1).  Absence
    # filters such as ``has_eval = false`` instead require that no sibling has
    # the positive relation witness (window max = 0).  Keeping the positive
    # predicate and changing only the required flag avoids the classic
    # ``any(NOT predicate)`` bug on traces with mixed children.
    required_matches: tuple[bool, ...]
    # Boolean requirements over the positive row predicates.  The outer tuple
    # is an AND; every inner tuple is an OR of ``(predicate index, required)``
    # conditions.  Most filters therefore contribute one singleton group.
    # ``has_annotation=false`` with configured labels is the important
    # exception: a row/trace is incomplete when *any* required label is
    # missing, so its missing-label checks belong to one OR group.
    match_condition_groups: tuple[tuple[tuple[int, bool], ...], ...]
    # Datetime complements constrain rows that contribute to the aggregate;
    # they are not trace-membership witnesses.  Applying ``not_between`` as a
    # window flag would select a trace because one sibling was outside the
    # excluded range and then incorrectly aggregate the excluded sibling too.
    contribution_predicates: tuple[str, ...]
    params: dict[str, Any]


def _filter_parts(item: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
    if not isinstance(item, dict):
        raise UnsupportedFilterShapeError("filter must be an object")
    column_id = item.get("column_id") or item.get("columnId")
    config_key = "filter_config" if "filter_config" in item else "filterConfig"
    config = item.get(config_key) or {}
    if not isinstance(column_id, str) or not column_id or not isinstance(config, dict):
        raise UnsupportedFilterShapeError("filter key and config are required")
    return column_id, config_key, config


def _namespace_params(
    predicate: str,
    params: dict[str, Any],
    *,
    filter_index: int | str,
) -> tuple[str, dict[str, Any]]:
    """Give independently-compiled predicates collision-free bind names."""

    namespaced: dict[str, Any] = {}
    # Replace only complete clickhouse-driver placeholders.  Sorting by length
    # is defensive for names such as ``attr_1`` and ``attr_10``.
    for old_name in sorted(params, key=len, reverse=True):
        new_name = f"graph_filter_{filter_index}_{old_name}"
        predicate = predicate.replace(f"%({old_name})s", f"%({new_name})s")
        namespaced[new_name] = params[old_name]
    return predicate, namespaced


def _is_root_only_system_metric(builder: Any, column_id: str) -> bool:
    mapped_column = builder.SYSTEM_METRIC_MAP.get(column_id)
    return column_id in builder.ROOT_ONLY_SYSTEM_METRICS or (
        column_id != "span_name"
        and mapped_column is not None
        and mapped_column in builder.ROOT_ONLY_SYSTEM_METRICS
    )


def _local_param(params: dict[str, Any], prefix: str, value: Any) -> str:
    """Bind one relation value under a deterministic local name."""

    index = 1
    name = f"{prefix}_{index}"
    while name in params:
        index += 1
        name = f"{prefix}_{index}"
    params[name] = value
    return name


def _parse_boolean_filter(column_id: str, value: Any, operator: str | None) -> bool:
    if normalize_filter_op(operator) != "equals":
        raise UnsupportedFilterShapeError(
            f"{column_id} supports only the equals operation"
        )
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in {"true", "false"}:
        return value.strip().lower() == "true"
    raise UnsupportedFilterShapeError(f"{column_id} requires a boolean value")


def _validated_uuid(value: Any, *, field: str) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (TypeError, ValueError) as exc:
        raise UnsupportedFilterShapeError(f"{field} must be a UUID") from exc


def _score_row_entity_keys(observe_type: str) -> str:
    trace_key = "concat('trace:', toString(trace_id))"
    if observe_type == "span":
        trace_key = (
            f"if(parent_span_id IS NULL OR parent_span_id = '', {trace_key}, '')"
        )
    return (
        "arrayFilter(graph_key -> graph_key != '', ["
        f"{trace_key}, concat('span:', toString(id))])"
    )


def _score_relation_entity_keys(observe_type: str) -> str:
    """Return one authoritative Score identity for the requested view.

    New span annotations can carry both ``trace_id`` and
    ``observation_span_id``.  Emitting both keys made a child annotation match
    the root row in a span graph.  Span mode therefore prefers the observation
    key and falls back to a trace key only for a genuinely trace-level score.
    Trace mode does the inverse, while retaining the historic span-only
    fallback that the any-sibling trace fold promotes to its owning trace.
    """

    valid_trace = f"NOT isNull(s.trace_id) AND s.trace_id != toUUID('{_ZERO_UUID}')"
    has_span = "notEmpty(ifNull(s.observation_span_id, ''))"
    trace_key = "concat('trace:', toString(s.trace_id))"
    span_key = "concat('span:', toString(s.observation_span_id))"
    if observe_type == "span":
        keys = (
            f"if({has_span}, {span_key}, ''), "
            f"if(NOT ({has_span}) AND ({valid_trace}), {trace_key}, '')"
        )
    else:
        keys = (
            f"if({valid_trace}, {trace_key}, ''), "
            f"if(NOT ({valid_trace}) AND ({has_span}), {span_key}, '')"
        )
    return f"arrayFilter(graph_key -> graph_key != '', [{keys}])"


def _score_entity_membership_predicate(
    *,
    project_id: str,
    observe_type: str,
    score_condition: str = "",
    group_having: str = "",
) -> tuple[str, dict[str, Any]]:
    """Match current span rows to one project-scoped Score relation read.

    A Score can identify either a trace or only an observation span.  Encoding
    both identities as tagged strings lets one ``model_hub_score FINAL`` read
    serve both cases.  In span mode a trace-level score maps only to the root,
    matching the list contract; trace mode's any-sibling window flag promotes
    an observation score to its trace without another spans lookup.
    """

    params: dict[str, Any] = {
        "relation_project_id": _validated_uuid(project_id, field="project_id")
    }
    condition_sql = f" AND ({score_condition})" if score_condition else ""
    grouping_sql = (
        f" GROUP BY graph_relation_entity_key HAVING {group_having}"
        if group_having
        else ""
    )
    relation = (
        "SELECT graph_relation_entity_key "
        "FROM model_hub_score AS s FINAL "
        f"ARRAY JOIN {_score_relation_entity_keys(observe_type)} "
        "AS graph_relation_entity_key "
        "WHERE s.tracer_project_id = toUUID(%(relation_project_id)s) "
        "AND s.deleted = false "
        "AND s._peerdb_is_deleted = 0"
        f"{condition_sql}{grouping_sql}"
    )
    predicate = (
        "arrayExists(graph_row_entity_key -> graph_row_entity_key IN ("
        f"{relation}), {_score_row_entity_keys(observe_type)})"
    )
    return predicate, params


def _annotation_value_condition(
    *,
    filter_type: str,
    filter_op: str | None,
    filter_value: Any,
    params: dict[str, Any],
) -> str:
    """Compile the value portion of one annotation relation filter."""

    normalized_type = str(filter_type or "").strip().lower()
    normalized_op = normalize_filter_op(filter_op)
    value_expr = "s.value"

    if normalized_type == "number":
        number_expr = (
            "if(JSONHas(s.value, 'rating'), "
            "JSONExtractFloat(s.value, 'rating'), "
            "JSONExtractFloat(s.value, 'value'))"
        )
        if normalized_op in {"between", "not_between"}:
            if not isinstance(filter_value, (list, tuple)) or len(filter_value) != 2:
                raise UnsupportedFilterShapeError(
                    f"annotation {normalized_op} requires two values"
                )
            try:
                lower = float(filter_value[0])
                upper = float(filter_value[1])
            except (TypeError, ValueError) as exc:
                raise UnsupportedFilterShapeError(
                    "annotation number filter requires numeric values"
                ) from exc
            lower_param = _local_param(params, "annotation_lower", lower)
            upper_param = _local_param(params, "annotation_upper", upper)
            sql_op = "NOT BETWEEN" if normalized_op == "not_between" else "BETWEEN"
            return f"{number_expr} {sql_op} %({lower_param})s AND %({upper_param})s"
        if normalized_op in {"in", "not_in"}:
            raw_values = (
                filter_value
                if isinstance(filter_value, (list, tuple))
                else [filter_value]
            )
            try:
                values = tuple(float(value) for value in raw_values)
            except (TypeError, ValueError) as exc:
                raise UnsupportedFilterShapeError(
                    "annotation number filter requires numeric values"
                ) from exc
            if not values:
                return "1 = 1" if normalized_op == "not_in" else "0 = 1"
            param = _local_param(params, "annotation_numbers", values)
            sql_op = "NOT IN" if normalized_op == "not_in" else "IN"
            return f"{number_expr} {sql_op} %({param})s"
        comparison = {
            "equals": "=",
            "not_equals": "!=",
            "greater_than": ">",
            "greater_than_or_equal": ">=",
            "less_than": "<",
            "less_than_or_equal": "<=",
        }.get(normalized_op)
        if comparison is None:
            raise UnsupportedFilterShapeError(
                f"unsupported annotation number operation: {normalized_op!r}"
            )
        try:
            value = float(filter_value)
        except (TypeError, ValueError) as exc:
            raise UnsupportedFilterShapeError(
                "annotation number filter requires a numeric value"
            ) from exc
        param = _local_param(params, "annotation_number", value)
        return f"{number_expr} {comparison} %({param})s"

    if normalized_type in {"boolean", "thumbs"}:
        raw_values = (
            filter_value if isinstance(filter_value, (list, tuple)) else [filter_value]
        )
        token_map = {
            "true": "up",
            "false": "down",
            "thumbs up": "up",
            "thumbs down": "down",
            "thumbs_up": "up",
            "thumbs_down": "down",
            "up": "up",
            "down": "down",
        }
        tokens: list[str] = []
        for value in raw_values:
            if isinstance(value, bool):
                token = "up" if value else "down"
            else:
                token = token_map.get(str(value).strip().lower())
            if token is not None and token not in tokens:
                tokens.append(token)
        if not tokens:
            raise UnsupportedFilterShapeError("annotation thumbs value is invalid")
        param = _local_param(params, "annotation_thumbs", tuple(tokens))
        sql_op = "NOT IN" if normalized_op in {"not_equals", "not_in"} else "IN"
        if normalized_op not in {"equals", "not_equals", "in", "not_in"}:
            raise UnsupportedFilterShapeError(
                f"unsupported annotation thumbs operation: {normalized_op!r}"
            )
        return f"JSONExtractString({value_expr}, 'value') {sql_op} %({param})s"

    if normalized_type == "text":
        text_expr = f"JSONExtractString({value_expr}, 'text')"
        if normalized_op in {"contains", "not_contains", "starts_with", "ends_with"}:
            param = _local_param(params, "annotation_text", str(filter_value))
            literal = build_literal_text_predicate(
                text_expr,
                param,
                normalized_op,
                case_insensitive=True,
            )
            return f"{text_expr} != '' AND {literal}"
        if normalized_op in {"equals", "not_equals"}:
            param = _local_param(params, "annotation_text", str(filter_value).lower())
            comparison = "!=" if normalized_op == "not_equals" else "="
            return (
                f"{text_expr} != '' AND lowerUTF8(toString({text_expr})) "
                f"{comparison} %({param})s"
            )
        if normalized_op in {"in", "not_in"}:
            raw_values = (
                filter_value
                if isinstance(filter_value, (list, tuple))
                else [filter_value]
            )
            values = tuple(
                str(value).lower() for value in raw_values if value not in (None, "")
            )
            if not values:
                return "1 = 1" if normalized_op == "not_in" else "0 = 1"
            param = _local_param(params, "annotation_texts", values)
            sql_op = "NOT IN" if normalized_op == "not_in" else "IN"
            return (
                f"{text_expr} != '' AND lowerUTF8(toString({text_expr})) "
                f"{sql_op} %({param})s"
            )
        raise UnsupportedFilterShapeError(
            f"unsupported annotation text operation: {normalized_op!r}"
        )

    if normalized_type in {"array", "categorical"}:
        values = (
            list(filter_value)
            if isinstance(filter_value, (list, tuple))
            else [filter_value]
        )
        if not values:
            return (
                "1 = 1"
                if normalized_op in {"not_equals", "not_in", "not_contains"}
                else "0 = 1"
            )
        selected_expr = "JSONExtract(s.value, 'selected', 'Array(String)')"
        conditions: list[str] = []
        legacy_thumbs = {
            "thumbs up": "up",
            "thumbs down": "down",
            "thumbs_up": "up",
            "thumbs_down": "down",
        }
        for value in values:
            param = _local_param(params, "annotation_choice", value)
            condition = f"has({selected_expr}, %({param})s)"
            thumb = (
                legacy_thumbs.get(value.strip().lower())
                if isinstance(value, str)
                else None
            )
            if thumb is not None:
                thumb_param = _local_param(params, "annotation_thumb", thumb)
                condition = (
                    f"({condition} OR JSONExtractString(s.value, 'value') "
                    f"= %({thumb_param})s)"
                )
            conditions.append(condition)
        combined = "(" + " OR ".join(conditions) + ")"
        if normalized_op in {"not_equals", "not_in", "not_contains"}:
            return f"NOT {combined}"
        if normalized_op not in {"equals", "in", "contains"}:
            raise UnsupportedFilterShapeError(
                f"unsupported annotation categorical operation: {normalized_op!r}"
            )
        return combined

    raise UnsupportedFilterShapeError(
        f"unsupported annotation filter type: {normalized_type!r}"
    )


def _compile_annotation_filter(
    *,
    column_id: str,
    config: dict[str, Any],
    project_id: str,
    observe_type: str,
    annotation_label_ids: tuple[str, ...] | None = None,
) -> list[tuple[str, bool, dict[str, Any]]]:
    """Compile one annotation leaf to one project-scoped Score scan."""

    filter_type = str(config.get("filter_type") or config.get("filterType") or "")
    filter_op = normalize_filter_op(config.get("filter_op") or config.get("filterOp"))
    filter_value = config.get("filter_value", config.get("filterValue"))
    params: dict[str, Any] = {}

    if column_id == "has_annotation":
        required = _parse_boolean_filter(column_id, filter_value, filter_op)
        if annotation_label_ids is not None:
            if not annotation_label_ids:
                # Completeness across an authoritative empty label set is
                # vacuously true.  Keep this as a normal graph predicate so
                # the existing required-match algebra yields all rows for
                # ``true`` and no rows for ``false`` without touching Score.
                return [("1 = 1", required, {})]
            requirements: list[tuple[str, bool, dict[str, Any]]] = []
            for label_id in annotation_label_ids:
                label_params = {
                    "annotation_label_1": _validated_uuid(
                        label_id,
                        field="annotation label_id",
                    )
                }
                predicate, relation_params = _score_entity_membership_predicate(
                    project_id=project_id,
                    observe_type=observe_type,
                    score_condition=("s.label_id = toUUID(%(annotation_label_1)s)"),
                )
                requirements.append(
                    (predicate, required, {**label_params, **relation_params})
                )
            return requirements
        predicate, relation_params = _score_entity_membership_predicate(
            project_id=project_id,
            observe_type=observe_type,
        )
        return [(predicate, required, relation_params)]

    if column_id == "my_annotations":
        required = _parse_boolean_filter(column_id, filter_value, filter_op)
        user_id = config.get("user_id") or config.get("userId")
        if not user_id:
            return [("0 = 1", True, {})]
        user_param = _local_param(
            params,
            "annotation_user",
            _validated_uuid(user_id, field="annotation user_id"),
        )
        predicate, relation_params = _score_entity_membership_predicate(
            project_id=project_id,
            observe_type=observe_type,
            score_condition=f"s.annotator_id = toUUID(%({user_param})s)",
        )
        return [(predicate, required, {**params, **relation_params})]

    if column_id == "annotator":
        if filter_op in {"is_null", "is_not_null"}:
            condition = "" if filter_op == "is_null" else "isNotNull(s.annotator_id)"
            predicate, relation_params = _score_entity_membership_predicate(
                project_id=project_id,
                observe_type=observe_type,
                score_condition=condition,
            )
            return [(predicate, filter_op == "is_not_null", relation_params)]
        raw_values = (
            filter_value if isinstance(filter_value, (list, tuple)) else [filter_value]
        )
        values = tuple(
            _validated_uuid(value, field="annotator")
            for value in raw_values
            if value not in (None, "")
        )
        if not values:
            raise UnsupportedFilterShapeError("annotator filter requires a value")
        annotator_param = _local_param(params, "annotators", values)
        matching = f"s.annotator_id IN %({annotator_param})s"
        if filter_op in {"not_equals", "not_in"}:
            any_predicate, any_relation_params = _score_entity_membership_predicate(
                project_id=project_id,
                observe_type=observe_type,
            )
            matching_predicate, matching_relation_params = (
                _score_entity_membership_predicate(
                    project_id=project_id,
                    observe_type=observe_type,
                    score_condition=matching,
                )
            )
            # Global negative annotator means: the entity has at least one
            # annotation, but no sibling/entity annotation is by a selected
            # user.  A single ``any(non-matching score)`` witness is wrong for
            # traces containing both matching and non-matching annotations.
            return [
                (any_predicate, True, any_relation_params),
                (
                    matching_predicate,
                    False,
                    {**params, **matching_relation_params},
                ),
            ]
        elif filter_op in {"equals", "in"}:
            predicate, relation_params = _score_entity_membership_predicate(
                project_id=project_id,
                observe_type=observe_type,
                score_condition=matching,
            )
        else:
            raise UnsupportedFilterShapeError(
                f"unsupported annotator operation: {filter_op!r}"
            )
        return [(predicate, True, {**params, **relation_params})]

    annotation_label_id = column_id.split("**", 1)[0]
    label_param = _local_param(
        params,
        "annotation_label",
        _validated_uuid(annotation_label_id, field="annotation label"),
    )
    label_condition = f"s.label_id = toUUID(%({label_param})s)"
    if filter_op in {"is_null", "is_not_null"}:
        predicate, relation_params = _score_entity_membership_predicate(
            project_id=project_id,
            observe_type=observe_type,
            score_condition=label_condition,
        )
        return [
            (
                predicate,
                filter_op == "is_not_null",
                {**params, **relation_params},
            )
        ]

    if filter_type.strip().lower() == "annotator":
        raw_values = (
            filter_value if isinstance(filter_value, (list, tuple)) else [filter_value]
        )
        values = tuple(
            _validated_uuid(value, field="annotation annotator")
            for value in raw_values
            if value not in (None, "")
        )
        if not values:
            raise UnsupportedFilterShapeError(
                "annotation annotator filter requires a value"
            )
        annotator_param = _local_param(params, "annotation_annotators", values)
        matching = f"s.annotator_id IN %({annotator_param})s"
        if filter_op in {"not_equals", "not_in"}:
            label_predicate, label_relation_params = _score_entity_membership_predicate(
                project_id=project_id,
                observe_type=observe_type,
                score_condition=label_condition,
            )
            matching_predicate, matching_relation_params = (
                _score_entity_membership_predicate(
                    project_id=project_id,
                    observe_type=observe_type,
                    score_condition=f"{label_condition} AND {matching}",
                )
            )
            return [
                (
                    label_predicate,
                    True,
                    {**params, **label_relation_params},
                ),
                (
                    matching_predicate,
                    False,
                    {**params, **matching_relation_params},
                ),
            ]
        elif filter_op in {"equals", "in"}:
            predicate, relation_params = _score_entity_membership_predicate(
                project_id=project_id,
                observe_type=observe_type,
                score_condition=f"{label_condition} AND {matching}",
            )
        else:
            raise UnsupportedFilterShapeError(
                f"unsupported annotation annotator operation: {filter_op!r}"
            )
        return [(predicate, True, {**params, **relation_params})]

    value_condition = _annotation_value_condition(
        filter_type=filter_type,
        filter_op=filter_op,
        filter_value=filter_value,
        params=params,
    )
    predicate, relation_params = _score_entity_membership_predicate(
        project_id=project_id,
        observe_type=observe_type,
        score_condition=f"{label_condition} AND ({value_condition})",
    )
    return [(predicate, True, {**params, **relation_params})]


def _compile_has_eval_filter(
    *,
    config: dict[str, Any],
    project_id: str,
    observe_type: str,
) -> tuple[str, bool, dict[str, Any]]:
    """Compile ``has_eval`` without a project-correlation spans subquery."""

    from tracer.models.custom_eval_config import CustomEvalConfig
    from tracer.services.clickhouse.eval_logger_table import (
        eval_logger_live_state_columns,
        eval_logger_source,
        eval_logger_version_column,
    )

    required = _parse_boolean_filter(
        "has_eval",
        config.get("filter_value", config.get("filterValue")),
        config.get("filter_op") or config.get("filterOp"),
    )
    try:
        config_ids = tuple(
            str(config_id)
            for config_id in CustomEvalConfig.objects.filter(
                project_id=project_id,
                deleted=False,
            ).values_list("id", flat=True)
        )
    except Exception as exc:
        raise UnsupportedFilterShapeError(
            "could not resolve project-scoped eval configurations"
        ) from exc
    if not config_ids:
        return "0 = 1", required, {}

    table, _ = eval_logger_source()
    version_column = eval_logger_version_column(table)
    live_columns = eval_logger_live_state_columns(table)
    _, live_predicate = eval_logger_source(
        "latest_eval",
        include_cdc_tombstone_guard=True,
        table=table,
    )
    params = {"project_eval_config_ids": config_ids}
    projection = ", ".join(f"eval_scan.{column}" for column in live_columns)
    if observe_type == "trace":
        outer_identity = "trace_id"
        selected_identity = "toString(latest_eval.trace_id)"
        inner_identity = (
            "NOT isNull(eval_scan.trace_id) AND eval_scan.trace_id != "
            f"toUUID('{_ZERO_UUID}')"
        )
    else:
        outer_identity = "tuple(trace_id, id)"
        selected_identity = (
            "tuple(toString(latest_eval.trace_id), "
            "toString(latest_eval.observation_span_id))"
        )
        inner_identity = (
            "NOT isNull(eval_scan.trace_id) AND eval_scan.trace_id != "
            f"toUUID('{_ZERO_UUID}') AND "
            "notEmpty(toString(eval_scan.observation_span_id))"
        )
    predicate = (
        f"{outer_identity} IN ("
        f"SELECT {selected_identity} FROM ("
        "SELECT eval_scan.id, eval_scan.trace_id, "
        f"eval_scan.observation_span_id, {projection} "
        f"FROM {table} AS eval_scan "
        "WHERE eval_scan.custom_eval_config_id "
        "IN %(project_eval_config_ids)s "
        f"AND {inner_identity} "
        f"ORDER BY eval_scan.{version_column} DESC "
        "LIMIT 1 BY eval_scan.id"
        ") AS latest_eval "
        f"WHERE {live_predicate})"
    )
    return predicate, required, params


def _compile_end_user_filter(
    *,
    column_id: str,
    config: dict[str, Any],
    project_id: str,
) -> tuple[str, bool, dict[str, Any]]:
    """Resolve curated and remapped end-user ids with no second spans read."""

    from tracer.services.clickhouse.v2.query_builders.filters import (
        ClickHouseFilterBuilderV2,
    )

    dimension_column = {
        "user": "user_id",
        "user_id": "user_id",
        "user_id_type": "user_id_type",
    }[column_id]
    filter_type = config.get("filter_type") or config.get("filterType") or "text"
    filter_op = normalize_filter_op(config.get("filter_op") or config.get("filterOp"))
    filter_value = config.get("filter_value", config.get("filterValue"))
    if filter_op in {"is_null", "is_not_null"}:
        has_end_user = (
            f"NOT isNull(end_user_id) AND end_user_id != toUUID('{_ZERO_UUID}')"
        )
        return has_end_user, filter_op == "is_not_null", {}

    negative_to_positive = {
        "not_equals": "equals",
        "not_in": "in",
        "not_contains": "contains",
    }
    positive_op = negative_to_positive.get(filter_op, filter_op)
    builder = ClickHouseFilterBuilderV2(
        table=_OUTER_SOURCE_SENTINEL,
        project_id=project_id,
        query_mode=ClickHouseFilterBuilderV2.QUERY_MODE_SPAN,
        score_date_scope=False,
        span_date_scope=False,
    )
    dimension_condition = builder._build_column_condition(  # noqa: SLF001
        dimension_column,
        str(filter_type),
        positive_op,
        filter_value,
    )
    if not dimension_condition:
        raise UnsupportedFilterShapeError(
            f"end-user filter {column_id!r} did not produce a predicate"
        )
    params: dict[str, Any] = {
        **builder._params,  # noqa: SLF001
        "relation_project_id": _validated_uuid(project_id, field="project_id"),
    }
    membership = (
        "end_user_id IN ("
        "SELECT graph_relation_end_user_id FROM ("
        "SELECT arrayJoin(arrayFilter(graph_end_user_key -> "
        f"graph_end_user_key != toUUID('{_ZERO_UUID}'), "
        "arrayConcat([eu.end_user_id], groupUniqArray(remap.old_id), "
        "groupUniqArray(remap.new_id)))) AS graph_relation_end_user_id "
        "FROM end_users AS eu FINAL "
        "LEFT JOIN ("
        "SELECT old_id, new_id, "
        "argMin(old_id, toString(old_id)) OVER (PARTITION BY new_id) "
        "AS survivor_id FROM end_user_id_remap FINAL"
        ") AS remap ON eu.end_user_id = remap.survivor_id "
        "WHERE eu.project_id = toUUID(%(relation_project_id)s) "
        "AND eu.is_deleted = 0 "
        f"AND ({dimension_condition}) GROUP BY eu.end_user_id))"
    )
    if filter_op in negative_to_positive:
        # Required=false makes trace mode compute ``max(membership) = 0`` and
        # span mode apply ``membership = 0``.  Negating the row predicate first
        # would admit a trace merely because one sibling had a different user.
        return membership, False, params
    return membership, True, params


def compile_exact_graph_row_predicates(
    filters: list[dict[str, Any]],
    *,
    project_id: str,
    observe_type: str,
    annotation_label_ids: list[str] | tuple[str, ...] | None = None,
) -> ExactGraphRowPredicatePlan:
    """Compile graph filters without adding another physical ``spans`` read.

    In trace mode every returned predicate is an independent any-sibling
    requirement.  :class:`TimeSeriesQueryBuilder` computes one window flag per
    predicate, so ``attribute_a = x AND attribute_b = y`` may be witnessed by
    two different child spans while all children of the matched trace still
    contribute to the graph.  In span mode the predicates are conjoined on the
    contributing row itself.
    """

    normalized_observe_type = str(observe_type or "trace").strip().lower()
    if normalized_observe_type not in {"trace", "span"}:
        raise UnsupportedFilterShapeError("observe_type must be trace or span")

    # Lazy imports avoid the base-filter <-> v2-filter module cycle.
    from tracer.services.clickhouse.v2.query_builders.filters import (
        ClickHouseFilterBuilderV2,
        rewrite_v1_sql_to_v2,
    )

    predicates: list[str] = []
    output_window_only: list[bool] = []
    required_matches: list[bool] = []
    match_condition_groups: list[tuple[tuple[int, bool], ...]] = []
    contribution_predicates: list[str] = []
    bound_params: dict[str, Any] = {}
    for filter_index, original_item in enumerate(filters or []):
        column_id, config_key, config = _filter_parts(original_item)
        filter_type = str(config.get("filter_type") or config.get("filterType") or "")
        if column_id in {"created_at", "start_time"} and filter_type in {
            "datetime",
            "date",
            "timestamp",
        }:
            continue

        item = original_item
        col_type = config.get("col_type") or config.get("colType")
        raw_value = config.get("filter_value", config.get("filterValue"))
        structured_attribute = False
        relation_requirements: list[tuple[str, bool, dict[str, Any]]] | None = None
        normalized_col_type = str(col_type or "").strip().upper()
        if column_id == "has_eval":
            relation_predicate, required, relation_params = _compile_has_eval_filter(
                config=config,
                project_id=project_id,
                observe_type=normalized_observe_type,
            )
            relation_requirements = [(relation_predicate, required, relation_params)]
        elif (
            column_id in {"has_annotation", "my_annotations", "annotator"}
            or normalized_col_type == "ANNOTATION"
        ):
            relation_requirements = _compile_annotation_filter(
                column_id=column_id,
                config=config,
                project_id=project_id,
                observe_type=normalized_observe_type,
                annotation_label_ids=(
                    None
                    if annotation_label_ids is None
                    else tuple(annotation_label_ids)
                ),
            )
        elif column_id in {"user", "user_id", "user_id_type"}:
            relation_predicate, required, relation_params = _compile_end_user_filter(
                column_id=column_id,
                config=config,
                project_id=project_id,
            )
            relation_requirements = [(relation_predicate, required, relation_params)]

        if relation_requirements is not None:
            requirement_predicate_indexes: list[int] = []
            for requirement_index, (
                predicate,
                required,
                predicate_params,
            ) in enumerate(relation_requirements):
                if _OUTER_SOURCE_SENTINEL in predicate or _SPANS_SOURCE_PATTERN.search(
                    predicate
                ):
                    raise UnsupportedFilterShapeError(
                        f"graph filter {column_id!r} requires another spans read"
                    )
                predicate, predicate_params = _namespace_params(
                    predicate,
                    dict(predicate_params),
                    filter_index=(
                        filter_index
                        if requirement_index == 0
                        else f"{filter_index}_requirement_{requirement_index}"
                    ),
                )
                duplicate_params = set(bound_params).intersection(predicate_params)
                if duplicate_params:  # pragma: no cover - namespace invariant
                    raise AssertionError(
                        f"duplicate graph bind params: {duplicate_params}"
                    )
                bound_params.update(predicate_params)
                requirement_predicate_indexes.append(len(predicates))
                predicates.append(predicate)
                output_window_only.append(False)
                required_matches.append(required)
            if (
                column_id == "has_annotation"
                and annotation_label_ids
                and requirement_predicate_indexes
            ):
                wants_complete = _parse_boolean_filter(
                    column_id,
                    raw_value,
                    config.get("filter_op") or config.get("filterOp"),
                )
                if wants_complete:
                    match_condition_groups.extend(
                        ((predicate_index, True),)
                        for predicate_index in requirement_predicate_indexes
                    )
                else:
                    match_condition_groups.append(
                        tuple(
                            (predicate_index, False)
                            for predicate_index in requirement_predicate_indexes
                        )
                    )
            else:
                match_condition_groups.extend(
                    ((predicate_index, required_matches[predicate_index]),)
                    for predicate_index in requirement_predicate_indexes
                )
            continue

        if col_type == ClickHouseFilterBuilderV2.SPAN_ATTRIBUTE:
            normalized_type = normalize_span_attribute_filter_type(
                filter_type,
                raw_value,
            )
            if normalized_type != filter_type:
                normalized_config = dict(config)
                normalized_config[
                    "filter_type" if "filter_type" in config else "filterType"
                ] = normalized_type
                item = {**original_item, config_key: normalized_config}
                config = normalized_config
            structured_attribute = normalized_type in {"array", "map"}

        if structured_attribute:
            predicate, predicate_params = compile_span_attribute_row_predicate(
                item,
                index=filter_index,
            )
            predicate = rewrite_v1_sql_to_v2(predicate)
            builder = None
        else:
            # Span mode is intentional even for a trace graph: it yields a
            # predicate on the current row.  The caller supplies trace-level
            # any-sibling semantics with window flags, without an IN-subquery.
            builder = ClickHouseFilterBuilderV2(
                table=_OUTER_SOURCE_SENTINEL,
                project_id=project_id,
                query_mode=(
                    ClickHouseFilterBuilderV2.QUERY_MODE_TRACE
                    if normalized_col_type == "EVAL_METRIC"
                    and normalized_observe_type == "trace"
                    else ClickHouseFilterBuilderV2.QUERY_MODE_SPAN
                ),
                score_date_scope=False,
                span_date_scope=False,
            )
            predicate, predicate_params = builder.translate([item])

        if not predicate:
            raise UnsupportedFilterShapeError(
                f"graph filter {column_id!r} did not produce a predicate"
            )
        if _OUTER_SOURCE_SENTINEL in predicate or _SPANS_SOURCE_PATTERN.search(
            predicate
        ):
            raise UnsupportedFilterShapeError(
                f"graph filter {column_id!r} requires another spans read"
            )
        if (
            _RELATIONAL_SQL_PATTERN.search(predicate)
            and normalized_col_type != "EVAL_METRIC"
        ):
            raise UnsupportedFilterShapeError(
                f"graph filter {column_id!r} produced an unsupported relation"
            )

        if (
            normalized_observe_type == "trace"
            and builder is not None
            and normalized_col_type in {"SYSTEM_METRIC", "TRACE_END_USER"}
            and _is_root_only_system_metric(builder, column_id)
        ):
            predicate = (
                f"(parent_span_id IS NULL OR parent_span_id = '') AND ({predicate})"
            )

        predicate, predicate_params = _namespace_params(
            predicate,
            dict(predicate_params),
            filter_index=filter_index,
        )
        duplicate_params = set(bound_params).intersection(predicate_params)
        if duplicate_params:  # pragma: no cover - namespace invariant
            raise AssertionError(f"duplicate graph bind params: {duplicate_params}")
        bound_params.update(predicate_params)
        predicates.append(predicate)
        output_window_only.append(structured_attribute)
        required_matches.append(True)
        match_condition_groups.append(((len(predicates) - 1, True),))

    datetime_predicate, datetime_params = (
        BaseQueryBuilder.bounded_datetime_exclusion_sql(
            filters or [],
            column="start_time",
            param_prefix="graph_datetime",
        )
    )
    if datetime_predicate:
        duplicate_params = set(bound_params).intersection(datetime_params)
        if duplicate_params:  # pragma: no cover - namespace invariant
            raise AssertionError(f"duplicate graph bind params: {duplicate_params}")
        bound_params.update(datetime_params)
        contribution_predicates.append(datetime_predicate)

    return ExactGraphRowPredicatePlan(
        predicates=tuple(predicates),
        output_window_only=tuple(output_window_only),
        required_matches=tuple(required_matches),
        match_condition_groups=tuple(match_condition_groups),
        contribution_predicates=tuple(contribution_predicates),
        params=bound_params,
    )


__all__ = ["ExactGraphRowPredicatePlan", "compile_exact_graph_row_predicates"]
