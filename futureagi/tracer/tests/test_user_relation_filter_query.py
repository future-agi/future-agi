"""User-grain contracts for finite eval and annotation relation filters."""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from tracer.services.clickhouse.query_builders.filters import (
    ClickHouseFilterBuilder,
    EvalFilterMetadata,
)
from tracer.services.clickhouse.query_builders.user_list import UserListQueryBuilder


def _relation_filter(
    column_id: str,
    *,
    col_type: str,
    filter_type: str,
    filter_op: str,
    filter_value: Any = None,
) -> dict[str, Any]:
    return {
        "column_id": column_id,
        "filter_config": {
            "filter_type": filter_type,
            "filter_op": filter_op,
            "filter_value": filter_value,
            "col_type": col_type,
        },
    }


def _builder() -> tuple[UserListQueryBuilder, str, str, str]:
    project_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    alias_id = str(uuid.uuid4())
    builder = UserListQueryBuilder(
        organization_id=str(uuid.uuid4()),
        project_ids=[project_id],
        candidate_end_user_ids=[user_id],
        candidate_scan_end_user_ids=[user_id, alias_id],
        candidate_end_user_id_map={user_id: user_id, alias_id: user_id},
    )
    return builder, project_id, user_id, alias_id


def test_positive_relation_filters_aggregate_independently_at_user_grain():
    """An eval on span A and annotation on span B must satisfy both filters."""

    builder, _project_id, user_id, alias_id = _builder()
    eval_id = str(uuid.uuid4())
    eval_config_id = str(uuid.uuid4())
    annotation_id = str(uuid.uuid4())

    sql, params = builder.build_relation_filter_user_query(
        [
            _relation_filter(
                eval_id,
                col_type="EVAL_METRIC",
                filter_type="boolean",
                filter_op="equals",
                filter_value="Passed",
            ),
            _relation_filter(
                annotation_id,
                col_type="ANNOTATION",
                filter_type="number",
                filter_op="greater_than",
                filter_value=3,
            ),
        ],
        eval_filter_metadata={
            eval_id: EvalFilterMetadata((eval_config_id,), "PASS_FAIL")
        },
    )

    assert "GROUP BY resolved_end_user_id" in sql
    assert sql.count("max(toUInt8(") == 2
    assert "relation_requirement_0 = 1" in sql
    assert "relation_requirement_1 = 1" in sql
    assert "relation_candidate_span_entities AS" in sql
    assert params["candidate_end_user_ids"] == (user_id,)
    assert params["candidate_scan_end_user_ids"] == (user_id, alias_id)


def test_relation_probes_are_pruned_to_finite_candidate_span_entities():
    builder, _project_id, _user_id, _alias_id = _builder()
    eval_id = str(uuid.uuid4())
    eval_config_id = str(uuid.uuid4())
    annotation_id = str(uuid.uuid4())

    sql, _params = builder.build_relation_filter_user_query(
        [
            _relation_filter(
                eval_id,
                col_type="EVAL_METRIC",
                filter_type="number",
                filter_op="greater_than",
                filter_value=50,
            ),
            _relation_filter(
                annotation_id,
                col_type="ANNOTATION",
                filter_type="number",
                filter_op="equals",
                filter_value=4,
            ),
        ],
        eval_filter_metadata={eval_id: EvalFilterMetadata((eval_config_id,), "SCORE")},
    )

    finite_entity_probe = (
        "IN (SELECT toString(trace_id), toString(id) FROM "
        "relation_candidate_span_entities)"
    )
    assert (
        "(toString(eval_scan.trace_id), "
        "toString(eval_scan.observation_span_id)) " + finite_entity_probe
    ) in sql
    assert (
        "toString(s.observation_span_id) IN (SELECT toString(id) FROM "
        "relation_candidate_span_entities)"
    ) in sql
    assert sql.count(finite_entity_probe) >= 2
    assert sql.index(finite_entity_probe) < sql.index("LIMIT 1 BY eval_scan.id")


@pytest.mark.parametrize("col_type", ["EVAL_METRIC", "ANNOTATION"])
@pytest.mark.parametrize(
    ("filter_op", "required_value"),
    [("is_null", 0), ("is_not_null", 1)],
)
def test_relation_null_operators_use_user_wide_presence(
    col_type: str,
    filter_op: str,
    required_value: int,
):
    """One unrelated span cannot satisfy a user-wide relation null check."""

    builder, _project_id, _user_id, _alias_id = _builder()
    relation_id = str(uuid.uuid4())
    metadata = (
        {relation_id: EvalFilterMetadata((str(uuid.uuid4()),), "SCORE")}
        if col_type == "EVAL_METRIC"
        else None
    )

    sql, _params = builder.build_relation_filter_user_query(
        [
            _relation_filter(
                relation_id,
                col_type=col_type,
                filter_type="number",
                filter_op=filter_op,
            )
        ],
        eval_filter_metadata=metadata,
    )

    assert sql.count("max(toUInt8(") == 1
    assert f"relation_requirement_0 = {required_value}" in sql
    assert "GROUP BY resolved_end_user_id" in sql
    assert "tuple(trace_id, id) NOT IN" not in sql


@pytest.mark.parametrize(
    ("filter_item", "metadata", "matching_fragment", "forbidden_fragment"),
    [
        (
            _relation_filter(
                "00000000-0000-0000-0000-000000000101",
                col_type="EVAL_METRIC",
                filter_type="number",
                filter_op="not_in",
                filter_value=[30, 60],
            ),
            {
                "00000000-0000-0000-0000-000000000101": EvalFilterMetadata(
                    ("00000000-0000-0000-0000-000000000201",),
                    "SCORE",
                )
            },
            "output_float IN %(",
            "output_float NOT IN %(",
        ),
        (
            _relation_filter(
                "00000000-0000-0000-0000-000000000102",
                col_type="ANNOTATION",
                filter_type="number",
                filter_op="not_equals",
                filter_value=4,
            ),
            None,
            "JSONExtractFloat(s.value, 'value')) = %(",
            "JSONExtractFloat(s.value, 'value')) != %(",
        ),
    ],
)
def test_negative_relation_operators_require_presence_and_reject_any_forbidden_value(
    filter_item: dict[str, Any],
    metadata: dict[str, EvalFilterMetadata] | None,
    matching_fragment: str,
    forbidden_fragment: str,
):
    """Negative filters are presence AND no positive-value witness per user."""

    builder, _project_id, _user_id, _alias_id = _builder()

    sql, _params = builder.build_relation_filter_user_query(
        [filter_item],
        eval_filter_metadata=metadata,
    )

    assert sql.count("max(toUInt8(") == 2
    assert "relation_requirement_0 = 1" in sql
    assert "relation_requirement_1 = 0" in sql
    assert matching_fragment in sql
    assert forbidden_fragment not in sql
    assert "GROUP BY resolved_end_user_id" in sql


def test_relation_filter_params_are_scoped_per_user_requirement():
    builder, _project_id, _user_id, _alias_id = _builder()
    first_label = str(uuid.uuid4())
    second_label = str(uuid.uuid4())

    _sql, params = builder.build_relation_filter_user_query(
        [
            _relation_filter(
                first_label,
                col_type="ANNOTATION",
                filter_type="text",
                filter_op="not_equals",
                filter_value="bad",
            ),
            _relation_filter(
                second_label,
                col_type="ANNOTATION",
                filter_type="text",
                filter_op="equals",
                filter_value="good",
            ),
        ]
    )

    scoped_relation_params = {
        name: value for name, value in params.items() if name.startswith("relation_")
    }
    assert len(scoped_relation_params) == len(set(scoped_relation_params))
    assert first_label in scoped_relation_params.values()
    assert second_label in scoped_relation_params.values()
    assert "bad" in scoped_relation_params.values()
    assert "good" in scoped_relation_params.values()


def test_candidate_entity_table_requires_a_builder_owned_identifier():
    with pytest.raises(ValueError, match="candidate_entities_table"):
        ClickHouseFilterBuilder(
            candidate_entities_table="candidate_spans; DROP TABLE spans"
        )
