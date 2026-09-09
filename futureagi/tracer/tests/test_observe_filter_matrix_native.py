"""Offline catalog-name capability/routing probes, not live execution proof.

Unsupported Users system filters must fail at public serializer boundaries;
supported leaves must reach typed membership. No field aliases are invented.
"""

import json
from datetime import timedelta

import pytest
from rest_framework.exceptions import ValidationError

from tracer.serializers.trace import UsersQuerySerializer
from tracer.services.clickhouse import exact_graph_reads as graph
from tracer.services.clickhouse.query_builders.base import BaseQueryBuilder
from tracer.services.clickhouse.query_builders.filters import EvalFilterMetadata
from tracer.services.clickhouse.v2.query_builders.user_list import (
    UserListQueryBuilderV2,
)
from tracer.tests.test_observe_filter_matrix import (
    DAYS,
    END,
    INVENTORY,
    PROJECT,
    _drop_legacy_ch_spans_mvs,  # noqa: F401 -- disable integration DDL
    _ensure_test_score_tenant_column,  # noqa: F401 -- disable integration DDL
    _offline_only,  # noqa: F401 -- enforce the same no-network fixture
    _public_filters,
    _render,
)

pytestmark = pytest.mark.unit
NATIVE_CASES = tuple(
    (surface, "SYSTEM_METRIC", item["name"], item["value_type"])
    for surface, items in INVENTORY["system_attributes"].items()
    for item in items
) + tuple(
    (surface, "EVAL_METRIC", item["name"], item["value_type"])
    for surface in ("traces", "sessions", "users")
    for item in INVENTORY["eval_configurations"]
)


def _compile(surface, filters, days):
    start = END - timedelta(days=days)
    if surface == "traces":
        sql, params = graph._compile_membership_filter(
            project_id=PROJECT,
            filters=filters,
            observe_type="trace",
            annotation_label_ids=(),
        )
    elif surface == "sessions":
        sql, params = graph._session_aggregate_source_sql(
            project_id=PROJECT,
            filters=filters,
            start_date=start,
            end_date=END,
            include_trace_ids=False,
            anchor_by_session_start=True,
        )
    else:
        sql, params, _ = graph._user_aggregate_source_sql(
            project_id=PROJECT,
            filters=filters,
            start_date=start,
            end_date=END,
            include_trace_ids=False,
            candidate_trace_ids_param="matrix_candidate_trace_ids",
        )
        params["matrix_candidate_trace_ids"] = ("matrix-trace",)
    return sql, {
        "project_id": PROJECT,
        "start_date": start,
        "end_date": END,
        "snapshot_start_date": start,
        "snapshot_end_date": END,
        **params,
    }


@pytest.mark.parametrize("days", DAYS)
@pytest.mark.parametrize(
    "surface,col_type,name,kind",
    NATIVE_CASES,
    ids=["-".join(case) for case in NATIVE_CASES],
)
def test_catalog_key_reaches_typed_membership_or_explicit_boundary_rejection(
    surface, col_type, name, kind, days, monkeypatch
):
    kind = "text" if kind == "string" else kind
    operation, value = {
        "text": ("equals", "matrix-value"),
        "number": ("greater_than", 0.01),
        "boolean": ("equals", True),
        "array": ("contains", ["matrix-tag"]),
        "datetime": ("greater_than", (END - timedelta(days=days)).isoformat()),
    }[kind]
    time_filter = {
        "column_id": "created_at",
        "filter_config": {
            "col_type": "SYSTEM_METRIC",
            "filter_type": "datetime",
            "filter_op": "between",
            "filter_value": [(END - timedelta(days=days)).isoformat(), END.isoformat()],
        },
    }
    leaf = {
        # UsersView's static User ID field sends user_id. The property registry
        # explicitly permits that wire spelling for system_attribute:users:user.
        "column_id": "user_id"
        if surface == "users" and name == "user" and col_type == "SYSTEM_METRIC"
        else name,
        "property_id": (
            f"eval_config:{name}"
            if col_type == "EVAL_METRIC"
            else f"system_attribute:{'all' if name in {'dataset', 'eval_source'} else surface}:{name}"
        ),
        "filter_config": {
            "col_type": col_type,
            "filter_type": kind,
            "filter_op": operation,
            "filter_value": value,
        },
    }
    if (
        surface == "users"
        and col_type == "SYSTEM_METRIC"
        and name
        in {
            "dataset",
            "eval_source",
            "active_users",
            "avg_cost_per_user",
            "avg_traces_per_user",
        }
    ):
        # 5 unsupported system filters x 3 windows are rejection coverage,
        # not successful SQL/filter execution. Metrics in req_data_config are
        # still valid; neither list nor graph may silently compile these leaves.
        for boundary in ("user_list", "user_graph_source"):
            with pytest.raises(ValidationError) as rejected:
                _public_filters([time_filter, leaf], boundary)
            assert rejected.value.get_codes() == {
                "filters": ["unsupported_users_filter"]
            }
            assert name in str(rejected.value.detail["filters"])
        workspace = UsersQuerySerializer(
            data={"filters": json.dumps([time_filter, leaf])}
        )
        assert not workspace.is_valid()
        assert workspace.errors["filters"][0].code == "unsupported_users_filter"
        assert name in str(workspace.errors["filters"])
        return
    filters = _public_filters(
        [time_filter, leaf],
        {
            "traces": "trace_graph_compiler",
            "sessions": "session_graph_source",
            "users": "user_graph_source",
        }[surface],
    )
    if col_type == "EVAL_METRIC":
        # Catalog identity/type coverage only. Real project ownership, template
        # resolution and PostgreSQL availability remain integration gaps.
        monkeypatch.setattr(
            "tracer.services.clickhouse.query_builders.filters.resolve_eval_filter_metadata",
            lambda eval_id, project_ids: EvalFilterMetadata((eval_id,), "SCORE"),
        )
    if name == "start_time" and col_type == "SYSTEM_METRIC":
        # Membership compilers deliberately omit root date predicates; their
        # caller owns intersection with the request window at microsecond grain.
        assert BaseQueryBuilder.parse_time_range(filters) == (
            END - timedelta(days=days) + timedelta(microseconds=1),
            END,
        )
        return
    if surface == "users" and col_type == "SYSTEM_METRIC":
        assert (
            leaf["column_id"] in UserListQueryBuilderV2.OUTPUT_FILTER_MAP
            or UserListQueryBuilderV2._is_date_filter(filters[1])
            or UserListQueryBuilderV2._is_relation_filter(filters[1])
        ), (
            "Catalog system key needs a public API mapping; do not silently treat it as a raw attribute"
        )
    sql, params = _compile(surface, filters, days)
    baseline, baseline_params = _compile(surface, filters[:1], days)
    assert _render(sql, params) != _render(baseline, baseline_params), (
        "Catalog leaf was silently dropped"
    )
    assert "SAMPLE " not in sql.upper()
    if col_type == "EVAL_METRIC":
        assert name in _render(sql, params), "EVAL configuration identity was dropped"
