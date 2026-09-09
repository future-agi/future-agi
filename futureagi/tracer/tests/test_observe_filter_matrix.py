"""Offline filter qualification probes; deliberately NOT production qualification.

The adjacent inventory uses synthetic custom keys and evaluation IDs. Public
system/telemetry spellings remain to exercise alias and type collisions. Values
and witnesses are synthetic. Type-edge profiles supplement the fixture cases;
multi-key combinations are representative, not exhaustive.
SQL cases exercise serialization, binding, and builder composition, not a
ClickHouse interpreter. Users cases execute the real Python collector and
matcher over mocked latest-state responses. Known parity gaps are ordinary
failing assertions, not xfails. No databases, network, or production fixtures.
"""

from __future__ import annotations

import json
import re
import socket
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from clickhouse_driver.util.escape import escape_params

from tracer.serializers.filters import (
    FilterListField,
    ObserveGraphDataRequestSerializer,
)
from tracer.serializers.project import ProjectUsersAggregateGraphDataRequestSerializer
from tracer.serializers.trace import (
    TraceObserveListQuerySerializer,
    UsersQuerySerializer,
)
from tracer.serializers.trace_session import (
    TraceSessionGraphDataRequestSerializer,
    TraceSessionListQuerySerializer,
)
from tracer.services.clickhouse import exact_graph_reads as graph
from tracer.services.clickhouse.query_builders.latest_filter_predicates import (
    compile_trace_filter_plans,
    partition_span_filter_plans,
)
from tracer.services.clickhouse.read_budget import ReadDeadline
from tracer.services.clickhouse.v2.query_builders.filters import rewrite_v1_sql_to_v2
from tracer.services.clickhouse.v2.query_builders.session_list import (
    SessionListQueryBuilderV2,
)
from tracer.services.clickhouse.v2.query_builders.trace_list import (
    TraceListQueryBuilderV2,
)
from tracer.services.filter_attestation import graph_execution_filters
from tracer.services.users_list_manager import UsersListManager
from tracer.tests.test_trace_root_physical_replay import assert_coherent_classifier

pytestmark = pytest.mark.unit
PROJECT = "00000000-0000-4000-8000-000000000002"
ORG = "00000000-0000-4000-8000-000000000003"
USER = "00000000-0000-4000-8000-000000000004"
SESSION = "00000000-0000-4000-8000-000000000005"
END = datetime(2026, 9, 5, 7, 0, 0, 123456)
DAYS = (7, 30, 365)


@dataclass(frozen=True)
class Profile:
    name: str
    kind: str
    operation: str
    expected: object
    matching: tuple
    nonmatching: tuple
    selected_types: tuple | None = None


# Independent witnesses/counterexamples: expected truth is not computed by
# copying the implementation. Each tuple denotes values on separate live spans.
PROFILES = (
    Profile(
        "short_text",
        "text",
        "in",
        ["matrix-company"],
        (("string", "matrix-company"),),
        (("string", "other"),),
        ("string",),
    ),
    Profile(
        "long_text",
        "text",
        "equals",
        "context:" + "x" * 248,
        (("string", "context:" + "x" * 248),),
        (("string", "context:other"),),
    ),
    Profile(
        "over_500_text",
        "text",
        "equals",
        "x" * 600,
        (("string", "x" * 600),),
        (("string", "other"),),
    ),
    Profile(
        "number_range",
        "number",
        "greater_than",
        0.01,
        (("number", 0.02),),
        (("number", 0.01), ("string", "0.02")),
    ),
    Profile(
        "number_zero",
        "number",
        "equals",
        0,
        (("number", 0),),
        (("boolean", False), ("string", "0")),
    ),
    Profile(
        "boolean_true",
        "boolean",
        "equals",
        True,
        (("boolean", True),),
        (("number", 1), ("string", "true")),
    ),
    Profile(
        "boolean_false",
        "boolean",
        "equals",
        False,
        (("boolean", False),),
        (("number", 0), ("string", "false")),
    ),
    Profile(
        "array_mixed",
        "array",
        "contains",
        ["vip", 42, True],
        (("json", [True, "other"]),),
        (("json", ["supervip", "42", 1]),),
    ),
    Profile(
        "json_subset",
        "json",
        "contains",
        {"tier": "vip"},
        (("json", {"tier": "vip", "extra": 2}),),
        (("json", {"tier": "supervip", "extra": 2}),),
    ),
    Profile(
        "json_equals",
        "json",
        "equals",
        {"tier": "vip", "active": True},
        (("json", {"active": True, "tier": "vip"}),),
        (("json", {"active": 1, "tier": "vip"}),),
    ),
    Profile("missing_text", "text", "is_null", None, (), (("string", "present"),)),
    Profile(
        "mixed_storage",
        "text",
        "in",
        ["1", 1, True],
        (("number", 1), ("string", "unrelated")),
        (("json", 1), ("string", "other"), ("boolean", False)),
        ("string", "number", "boolean"),
    ),
    Profile(
        "negative_text",
        "text",
        "not_in",
        ["blocked"],
        (("string", "other"),),
        (("string", "blocked"),),
    ),
    Profile(
        "negative_mixed",
        "text",
        "not_in",
        ["1", 1, True],
        (("number", 2), ("boolean", False)),
        (("number", 1),),
        ("string", "number", "boolean"),
    ),
    Profile(
        "negative_array",
        "array",
        "not_contains",
        ["blocked"],
        (("json", ["vip"]),),
        (("json", ["blocked"]),),
    ),
    Profile(
        "negative_json",
        "json",
        "not_contains",
        {"tier": "vip"},
        (("json", {"tier": "other", "extra": 2}),),
        (("json", {"tier": "vip", "extra": 2}),),
    ),
)
BY_NAME = {profile.name: profile for profile in PROFILES}


@dataclass(frozen=True)
class Case:
    name: str
    leaves: tuple[tuple[str, Profile], ...]


INVENTORY = json.loads(
    Path(__file__).with_name("test_observe_filter_matrix_inventory.json").read_text()
)
TYPE_PROFILES = {
    "string": "short_text",
    "number": "number_range",
    "boolean": "boolean_true",
    "array": "array_mixed",
}
INVENTORY_CASES = tuple(
    Case(
        f"inventory_{attribute['name']}_{kind}",
        ((attribute["name"], BY_NAME[TYPE_PROFILES[kind]]),),
    )
    for attribute in INVENTORY["attributes"]
    for kind in attribute["observed_types"]
)
MIXED_CASES = tuple(
    Case(
        f"inventory_{attribute['name']}_mixed",
        (
            (
                attribute["name"],
                Profile(
                    "inventory_mixed",
                    "text",
                    "in",
                    ["1", 1],
                    (("number", 1), ("string", "other")),
                    (("boolean", True), ("string", "other")),
                    ("string", "number"),
                ),
            ),
        ),
    )
    for attribute in INVENTORY["attributes"]
    if len(attribute["observed_types"]) > 1
)
INVENTORY_COMBINATIONS = tuple(
    Case(
        f"inventory_combo_{index:02d}_and_{count}",
        tuple(
            (item["name"], BY_NAME[TYPE_PROFILES[item["observed_types"][0]]])
            for offset in range(count)
            for item in (INVENTORY["attributes"][(index * 23 + offset * 19) % 242],)
        ),
    )
    for index, count in enumerate((2, 2, 2, 5, 5, 5, 10, 10, 10, 10))
)
# A catalog resolved_type=json can mean multiple scalar storage types. Never
# invent object storage for those keys; object semantics use explicit fixtures.
CASES = (
    INVENTORY_CASES
    + MIXED_CASES
    + INVENTORY_COMBINATIONS
    + tuple(
        Case(
            f"type_edge_{profile.name}",
            ((f"observe_matrix.edge_{index:03d}", profile),),
        )
        for index, profile in enumerate(PROFILES)
    )
    + tuple(
        Case(
            f"{name}_and_{count}",
            tuple(
                (
                    f"observe_matrix.combo_{index:02d}",
                    PROFILES[index % len(PROFILES)]
                    if name == "heterogeneous"
                    else BY_NAME[name],
                )
                for index in range(count)
            ),
        )
        for count in (2, 5, 10)
        for name in (*BY_NAME, "heterogeneous")
    )
)


def test_inventory_preserves_synthetic_name_type_coverage():
    assert len(INVENTORY["attributes"]) == 242
    assert len({item["name"] for item in INVENTORY["attributes"]}) == 242
    assert len(INVENTORY_CASES) == 260
    assert len(MIXED_CASES) == 18
    assert len(INVENTORY_COMBINATIONS) == 10
    assert all(
        len({key for key, _ in case.leaves}) == len(case.leaves)
        for case in INVENTORY_COMBINATIONS
    )
    for item in INVENTORY["attributes"]:
        assert set(item) == {"name", "observed_types", "resolved_type"}
    assert {
        surface: len(items) for surface, items in INVENTORY["system_attributes"].items()
    } == {"traces": 38, "sessions": 14, "users": 22}
    assert len(INVENTORY["eval_configurations"]) == 8
    assert all(
        item["name"].startswith("00000000-0000-4000-8000-")
        for item in INVENTORY["eval_configurations"]
    )
    for item in (
        *INVENTORY["eval_configurations"],
        *(item for items in INVENTORY["system_attributes"].values() for item in items),
    ):
        assert set(item) == {"name", "value_type"}


@pytest.fixture(autouse=True)
def _offline_only(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Offline matrix attempted a network connection")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)


@pytest.fixture(autouse=True, scope="session")
def _drop_legacy_ch_spans_mvs():
    """Override repository integration setup: this suite never touches CH."""
    yield


@pytest.fixture(autouse=True, scope="session")
def _ensure_test_score_tenant_column():
    """Override repository integration setup: no DDL in offline qualification."""
    yield


def _public_filters(filters, surface):
    """Use the endpoint's real serializer and its actual graph preprocessing.

    Views pass these scalar leaves through (annotation-principal binding is a
    no-op here). No catalog-to-wire alias is inferred from SQL builder maps.
    """
    list_serializers = {
        "trace_list": TraceObserveListQuerySerializer,
        "session_list": TraceSessionListQuerySerializer,
        "user_list": UsersQuerySerializer,
    }
    if surface in list_serializers:
        serializer = list_serializers[surface](
            data={"project_id": PROJECT, "filters": json.dumps(filters)}
        )
    else:
        graph_serializers = {
            "trace_graph_compiler": ObserveGraphDataRequestSerializer,
            "session_graph_source": TraceSessionGraphDataRequestSerializer,
            "user_graph_source": ProjectUsersAggregateGraphDataRequestSerializer,
        }
        serializer = graph_serializers[surface](
            data={
                "project_id": PROJECT,
                "filters": filters,
                "interval": "day",
                "req_data_config": {
                    "type": "SYSTEM_METRIC",
                    "id": "active_users"
                    if surface == "user_graph_source"
                    else "total_tokens",
                },
            }
        )
    serializer.is_valid(raise_exception=True)
    validated = serializer.validated_data["filters"]
    if surface in {"trace_graph_compiler", "session_graph_source"}:
        return graph_execution_filters(validated)
    return validated


def _filters(case, days, surface=None):
    start = END - timedelta(days=days)
    filters = [
        {
            "column_id": "created_at",
            "filter_config": {
                "col_type": "SYSTEM_METRIC",
                "filter_type": "datetime",
                "filter_op": "between",
                "filter_value": [start.isoformat(), END.isoformat()],
            },
        }
    ]
    for key, profile in case.leaves:
        config = {
            "col_type": "SPAN_ATTRIBUTE",
            "filter_type": profile.kind,
            "filter_op": profile.operation,
            "filter_value": profile.expected,
        }
        if profile.selected_types is not None:
            config["attribute_value_types"] = list(profile.selected_types)
        filters.append(
            {
                "column_id": key,
                "property_id": f"custom_attribute:{key}",
                "filter_config": config,
            }
        )
    return (
        _public_filters(filters, surface)
        if surface
        else FilterListField().run_validation(filters)
    )


def _builder(surface, filters):
    cls = (
        TraceListQueryBuilderV2
        if surface == "trace_list"
        else SessionListQueryBuilderV2
    )
    return cls(
        project_id=PROJECT,
        filters=filters,
        page_size=25,
        eval_config_ids=[],
        annotation_label_ids=[],
    )


def _render(sql, params):
    missing = set(re.findall(r"%\(([^)]+)\)s", sql)) - params.keys()
    assert not missing, f"Unbound SQL parameters: {missing}"
    context = SimpleNamespace(server_info=SimpleNamespace(get_timezone=lambda: "UTC"))
    return sql % escape_params(params, context)


def _normalized(sql):
    return " ".join(sql.split())


def _assert_keys_bound(sql, params, case, *, require_key_parameters=True):
    used = set(re.findall(r"%\(([^)]+)\)s", sql))
    rendered = _render(sql, params)
    for key, _ in case.leaves:
        if require_key_parameters:
            assert any(params[name] == key for name in used), (
                f"Dropped attribute: {key}"
            )
        else:
            # The legacy graph compiler quotes identifiers as SQL literals;
            # unlike latest-state plans, it does not bind every key separately.
            assert key in rendered, f"Dropped attribute: {key}"
    assert "SAMPLE " not in sql.upper()


@pytest.mark.parametrize("days", DAYS)
@pytest.mark.parametrize("case", CASES, ids=lambda case: case.name)
@pytest.mark.parametrize("surface", ("trace_list", "session_list"))
def test_list_sql_retains_every_and_leaf_and_exact_scope(surface, case, days):
    filters = _filters(case, days, surface)
    builder = _builder(surface, filters)
    if surface == "trace_list":
        plans = compile_trace_filter_plans(filters)
        sql, params = builder.build_filter_identity_match_query_from_seed_rows(
            [{"trace_id": "matrix-trace"}]
        )
        assert params["candidate_start_date"] == END - timedelta(days=days)
        assert params["candidate_end_date"] == END
        # Dates constrain roots, never the finite trace's all-time children.
        assert "candidate_witness_start_date_us" not in params
    else:
        plans, residual = partition_span_filter_plans(
            filters, group_attribute_nulls=True
        )
        assert not residual
        sql, params = builder.build_filter_match_query([SESSION])
        assert params["start_date"] == END - timedelta(days=days)
        assert params["end_date"] == END
    assert len(plans) == len(case.leaves)
    conjunction = rewrite_v1_sql_to_v2(
        " AND ".join(plan.grouped_match_predicate() for plan in plans)
    )
    assert _normalized(conjunction) in _normalized(sql)
    if surface == "trace_list":
        # CH25 chooses values and tombstones from one physical winner. Do not
        # require the old independent aggregate, or merely check for argMax.
        assert_coherent_classifier(sql)
        assert "_physical_winner.3 AS latest_is_deleted" in sql
        assert _normalized(
            "GROUP BY observation_type, service_name, "
            "toStartOfHour(start_time), trace_id, id"
        ) in _normalized(sql)
        # The sixth storage-key component is constant in this project scope.
        assert "PREWHERE project_id = %(project_id)s" in sql
        assert params["project_id"] == PROJECT
    else:
        assert "argMax(is_deleted, _version)" in sql
    assert "latest_is_deleted = 0" in sql
    assert not re.search(r"FROM\s+spans(?:\s+AS\s+\w+)?\s+FINAL\b", sql, re.I)
    _assert_keys_bound(sql, params, case)


@pytest.mark.parametrize("days", DAYS)
@pytest.mark.parametrize("case", CASES, ids=lambda case: case.name)
@pytest.mark.parametrize(
    "surface", ("trace_graph_compiler", "session_graph_source", "user_graph_source")
)
def test_graph_compilers_retain_every_leaf_and_bind(surface, case, days):
    """Compilation coverage only: parity assertions live in separate tests."""
    filters = _filters(case, days, surface)
    start = END - timedelta(days=days)
    if surface == "trace_graph_compiler":
        sql, params = graph._compile_membership_filter(
            project_id=PROJECT,
            filters=filters,
            observe_type="trace",
            annotation_label_ids=(),
        )
        # The caller, not the membership compiler, owns these root bounds.
        params = {
            "project_id": PROJECT,
            "start_date": start,
            "end_date": END,
            "snapshot_start_date": start,
            "snapshot_end_date": END,
            **params,
        }
    elif surface == "session_graph_source":
        sql, params = graph._session_aggregate_source_sql(
            project_id=PROJECT,
            filters=filters,
            start_date=start,
            end_date=END,
            include_trace_ids=False,
            anchor_by_session_start=True,
        )
        plan = graph._session_membership_plan(project_id=PROJECT, filters=filters)
        assert len(plan.scalar_predicates) == len(case.leaves)
        conjunction = " AND ".join(plan.scalar_group_predicates)
        assert _normalized(conjunction) in _normalized(sql)
        assert params["snapshot_start_date"] == start
        assert params["snapshot_end_date"] == END
        # Output partition dates are supplied by the outer graph caller.
        params.update(start_date=start, end_date=END)
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
        row_predicates, membership, _ = graph._user_membership_having(
            filters, project_id=PROJECT
        )
        # Independent witnesses are intersected at user grain. Requiring the
        # old whole-list row conjunction here would reintroduce same-span AND.
        assert all(_normalized(predicate) in _normalized(sql) for predicate in row_predicates)
        assert _normalized("HAVING " + membership) in _normalized(sql)
        assert params["snapshot_start_date"] == start
        assert params["snapshot_end_date"] == END
    _assert_keys_bound(sql, params, case, require_key_parameters=False)


def _user_match(case, days, *, changed_leaf=None, force_missing=False):
    filters = _filters(case, days, "user_list")
    manager = UsersListManager(
        organization_id=ORG,
        allowed_project_ids=[PROJECT],
        project_id=PROJECT,
        requested_columns=[],
        filters=filters,
    )
    assert set(manager.attribute_keys) == {key for key, _ in case.leaves}, (
        "Raw custom key collided with Users native-field routing"
    )
    row = {"end_user_id": USER}
    calls = []

    def execute(query, params, **kwargs):
        calls.append((query, params))
        assert params["attr_start_date"] == END - timedelta(days=days)
        assert params["attr_end_date"] == END
        assert params["eu_ids"] == (USER,)
        assert "argMax" in query and "latest_is_deleted = 0" in query
        assert not re.search(r"FROM\s+spans(?:\s+AS\s+\w+)?\s+FINAL\b", query, re.I)
        _render(query, params)
        data = []
        for index, (key, profile) in enumerate(case.leaves):
            if key not in params["requested_attribute_keys"]:
                continue
            values = (
                profile.matching
                if changed_leaf != index
                else (() if force_missing else profile.nonmatching)
            )
            if values:
                data.append(
                    {
                        "end_user_id": USER,
                        "attribute_key": key,
                        "attribute_typed_values": [
                            (kind, json.dumps(value)) for kind, value in values
                        ],
                    }
                )
        return SimpleNamespace(data=data)

    with patch(
        "tracer.services.users_list_manager.V2AnalyticsQueryService"
    ) as analytics:
        analytics.return_value.execute_ch_query.side_effect = execute
        attributes = manager._read_span_attributes(
            [row],
            ReadDeadline.start(10_000),
            start_date=END - timedelta(days=days),
            end_date=END,
        )
    manager._apply_span_attributes([row], attributes)
    assert calls
    queried_keys = {
        key for _, params in calls for key in params["requested_attribute_keys"]
    }
    assert queried_keys == {key for key, _ in case.leaves}
    return manager._row_matches_filters(row)


@pytest.mark.parametrize("days", DAYS)
@pytest.mark.parametrize("case", CASES, ids=lambda case: case.name)
def test_users_collector_and_semantics_with_single_leaf_counterexamples(case, days):
    assert _user_match(case, days), f"Lost matching user: {case.name}"
    # Every leaf must be necessary; testing just an all-match row cannot prove AND.
    for index, (key, _) in enumerate(case.leaves):
        assert not _user_match(case, days, changed_leaf=index), (
            f"Ignored AND leaf {key}: {case.name}"
        )


@pytest.mark.parametrize("days", DAYS)
@pytest.mark.parametrize(
    "profile_name",
    ("negative_text", "negative_mixed", "negative_array", "negative_json"),
)
def test_users_negative_missing_key_agrees_with_grouped_compiler(profile_name, days):
    case = Case(profile_name, (("observe_matrix.absent", BY_NAME[profile_name]),))
    plans = compile_trace_filter_plans(_filters(case, days))
    assert "exists_0" in plans[0].predicate
    assert not _user_match(case, days, changed_leaf=0, force_missing=True), (
        "Missing key cannot satisfy a typed negative requiring key presence"
    )


@pytest.mark.parametrize("days", DAYS)
@pytest.mark.parametrize("count", (2, 5, 10))
def test_session_list_and_graph_intersect_at_same_entity_grain(count, days):
    """Distinct traces can each supply a leaf of one session conjunction."""
    case = Case(
        "split_traces",
        tuple(
            (f"observe_matrix.split_{i}", BY_NAME["short_text"]) for i in range(count)
        ),
    )
    filters = _filters(case, days, "session_list")
    sql, _ = _builder("session_list", filters).build_filter_match_query([SESSION])
    graph_sql, _ = graph._session_aggregate_source_sql(
        project_id=PROJECT,
        filters=_filters(case, days, "session_graph_source"),
        start_date=END - timedelta(days=days),
        end_date=END,
        include_trace_ids=False,
        anchor_by_session_start=True,
    )

    def scalar_grains(query):
        return [
            grain
            for grain, having in re.findall(
                r"GROUP BY ([^\n]+)\n\s+HAVING ([^\n]+)", query
            )
            if "latest_attr" in having
        ]

    list_grains = scalar_grains(sql)
    graph_grains = scalar_grains(graph_sql)
    assert list_grains and graph_grains
    assert all("trace_id" not in grain for grain in list_grains), (
        f"List intersects at {list_grains}; graph at {graph_grains}"
    )


@pytest.mark.parametrize("days", DAYS)
def test_session_list_graph_null_parity_for_one_trace_with_present_and_missing_children(
    days,
):
    """Keep the shared absence policy at session grain.

    One session, one trace, one child with the key, one child without it:
    Both list and graph reject: one present child defeats entity absence.
    """
    case = Case("null_group", (("observe_matrix.absent", BY_NAME["missing_text"]),))
    filters = _filters(case, days, "session_list")
    list_sql, _ = _builder("session_list", filters).build_filter_match_query([SESSION])
    graph_sql, _ = graph._session_aggregate_source_sql(
        project_id=PROJECT,
        filters=_filters(case, days, "session_graph_source"),
        start_date=END - timedelta(days=days),
        end_date=END,
        include_trace_ids=False,
        anchor_by_session_start=True,
    )
    assert re.search(r"countIf\(latest_attr_exists_0[^\n]*\) = 0", list_sql)
    assert re.search(
        r"countIf\(latest_attr_exists_0[^\n]*\) = 0", graph_sql
    ), "Graph accepts some missing child; list requires no child with key"


@pytest.mark.parametrize("days", DAYS)
@pytest.mark.parametrize("count", (2, 5, 10))
def test_user_graph_retains_cross_span_and_membership(count, days):
    case = Case(
        "split_spans",
        tuple(
            (f"observe_matrix.split_{i}", BY_NAME["short_text"]) for i in range(count)
        ),
    )
    assert _user_match(case, days)
    sql, _, _ = graph._user_aggregate_source_sql(
        project_id=PROJECT,
        filters=_filters(case, days, "user_graph_source"),
        start_date=END - timedelta(days=days),
        end_date=END,
        include_trace_ids=False,
        candidate_trace_ids_param="matrix_candidate_trace_ids",
    )
    # A conjunction in candidate_user_spans requires one span to carry every
    # key. It cannot implement the collector's independent per-key witnesses.
    candidate = sql.split("candidate_user_spans AS (", 1)[1].split("\n    ),", 1)[0]
    assert "attrs_string[" not in candidate, (
        "Graph intersects custom leaves on one span before user grouping"
    )
