"""Users monetary coverage: compatibility, exact CH rows and bounded CSV.

CH tests run only against the owned test database on the test VM. No model calls,
SQL rewriting or settings overrides stand in for executing production builders.
"""

import csv
import io
from datetime import UTC, datetime, timedelta
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest

from conftest import (
    _ch_test_native_client,
    _ch_test_owned_database,
    _open_ch_test_http_client,
)
from tracer.serializers.trace import UsersTableRowSerializer
from tracer.services.clickhouse.query_builders.user_list import UserListQueryBuilder
from tracer.services.clickhouse.v2.query_builders.user_list import (
    UserListQueryBuilderV2,
)
from tracer.services.users_list_manager import UserCursorRead, UsersListManager
from tracer.tests.test_openrouter_broadcast_list_rollups_ch25 import (
    _REMAP_DDL,
    _SPANS_DDL,
)

START = datetime(2026, 10, 20, 12, tzinfo=UTC)
UNKNOWN = {"fi.cost.kind": "unknown"}
KNOWN = {"fi.cost.kind": "provider_reported"}
NONE = {**UNKNOWN, "fi.usage.contribution": "none", "fi.usage.scope": "turn"}
_DATE_FILTER = {
    "column_id": "start_time",
    "filter_config": {
        "col_type": "SYSTEM_METRIC",
        "filter_type": "datetime",
        "filter_op": "between",
        "filter_value": [START.isoformat(), (START + timedelta(days=3)).isoformat()],
    },
}
_END_USERS_DDL = """
CREATE TABLE end_users (
    project_id UUID, end_user_id UUID, organization_id UUID,
    user_id String, user_id_type LowCardinality(Nullable(String)),
    user_id_hash String DEFAULT '',
    first_seen DateTime64(6, 'UTC'), version UInt64, is_deleted UInt8 DEFAULT 0
) ENGINE = ReplacingMergeTree(version) ORDER BY (project_id, end_user_id)
"""


def _coverage(status, subtotal, known, unknown):
    return {
        "status": status,
        "known_subtotal": subtotal,
        "known_fact_count": known,
        "unknown_fact_count": unknown,
    }


def _manager(project, *, filters=()):
    return UsersListManager(
        organization_id=str(uuid4()),
        allowed_project_ids=[str(project)],
        project_names={str(project): "Scoped project"},
        requested_columns=[],
        filters=list(filters),
    )


def _cursor(rows):
    return UserCursorRead(
        payload={
            "table": rows,
            "query_exact": True,
            "query_complete": True,
            "ordering_exact": True,
            "has_more": False,
        },
        window_start=START,
        window_end=START + timedelta(days=3),
        checkpoint_order=None,
        seen_rows=len(rows),
        has_more=False,
        unseen_row_proven=False,
    )


def _filter(op, value=0, column="total_cost"):
    return {
        "column_id": column,
        "filter_config": {
            "col_type": "SYSTEM_METRIC",
            "filter_type": "number",
            "filter_op": op,
            "filter_value": value,
        },
    }


@pytest.mark.unit
@pytest.mark.parametrize(
    "known,unknown,subtotal,status",
    [
        (1, 0, 1.25, "complete"),
        (1, 0, 0, "complete"),
        (1, 1, 1.25, "partial"),
        (0, 1, 0, "unknown"),
        (1, 1, 0, "partial"),
        (0, 0, 0, "complete"),
    ],
)
def test_format_rows_keeps_numeric_compatibility_and_typed_companion(
    known, unknown, subtotal, status
):
    row = UserListQueryBuilder.format_rows(
        [
            {
                "user_id": "customer",
                "total_cost": subtotal,
                "known_cost_fact_count": known,
                "unknown_cost_fact_count": unknown,
            }
        ]
    )["table"][0]
    assert row["total_cost"] == subtotal
    assert isinstance(row["total_cost"], (float, int))
    assert row["cost_coverage"] == _coverage(status, subtotal, known, unknown)
    serializer = UsersTableRowSerializer(data=row)
    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data["cost_coverage"] == row["cost_coverage"]


@pytest.mark.unit
@pytest.mark.parametrize("subtotal", [0, 1.25])
def test_legacy_unqualified_row_still_parses_without_companion(subtotal):
    row = UserListQueryBuilder.format_rows([{"total_cost": subtotal}])["table"][0]
    assert row["total_cost"] == subtotal
    assert "cost_coverage" not in row
    serializer = UsersTableRowSerializer(data=row)
    assert serializer.is_valid(), serializer.errors
    assert "cost_coverage" not in serializer.validated_data


@pytest.mark.unit
@pytest.mark.parametrize("column", ["cost", "total_cost", "avg_cost"])
@pytest.mark.parametrize(
    "status,subtotal,known,unknown",
    [
        ("partial", 1.25, 1, 1),
        ("unknown", 0, 0, 1),
        ("partial", 0, 1, 1),
    ],
)
def test_manager_does_not_compare_unknown_prices_as_zero_or_subtotals(
    column, status, subtotal, known, unknown
):
    project = uuid4()
    row = {
        "end_user_id": str(uuid4()),
        "total_cost": subtotal,
        "cost_coverage": _coverage(status, subtotal, known, unknown),
    }
    for operation, expected, matches in [
        ("equals", 0, False),
        ("equals", subtotal, False),
        ("not_equals", 5, False),
        ("less_than", 10, False),
        ("in", [0, 1.25], False),
        ("not_in", [10], False),
        ("is_null", None, True),
        ("is_not_null", None, False),
    ]:
        assert (
            _manager(
                project, filters=[_filter(operation, expected, column)]
            )._row_matches_filters(row)
            is matches
        )


@pytest.mark.unit
@pytest.mark.parametrize("with_coverage", [False, True])
def test_real_zero_preserves_numeric_filter_behavior(with_coverage):
    row = {"total_cost": 0}
    if with_coverage:
        row["cost_coverage"] = _coverage("complete", 0, 1, 0)
    assert _manager(uuid4(), filters=[_filter("equals")])._row_matches_filters(row)
    assert not _manager(uuid4(), filters=[_filter("is_null")])._row_matches_filters(row)


@pytest.mark.unit
@pytest.mark.parametrize("build", ["build", "build_candidate_page_query"])
def test_sql_uses_one_winner_and_filters_counts_before_limit(build):
    builder = UserListQueryBuilderV2(
        organization_id=str(uuid4()),
        project_ids=[str(uuid4())],
        filters=[_DATE_FILTER, _filter("equals")],
        limit=20,
        offset=0,
        sort_params=[{"column_id": "total_cost", "direction": "asc"}],
    )
    sql, params = getattr(builder, build)()
    assert "argMax(tuple(spans.end_user_id, spans.start_time" in sql
    assert "tupleElement(latest_usage_state, 9) AS latest_coverage" in sql
    assert "argMax(cost, _version)" not in sql
    assert "sum(unknown_cost_fact_count) AS unknown_cost_fact_count" in sql
    assert "sum(known_cost_fact_count) AS known_cost_fact_count" in sql
    assert "isNotNull(complete_cost) AND (complete_cost =" in sql
    assert "ORDER BY complete_cost ASC NULLS LAST, end_user_id DESC" in sql
    assert sql.index("user_trace_usage AS") < sql.index("count() OVER() AS total_count")
    assert "LIMIT %(limit)s OFFSET %(offset)s" in sql
    assert params["limit"] == 20


@pytest.fixture(scope="module")
def users_cost_client():
    with _ch_test_owned_database("test_users_native_cost_") as database:
        client = _open_ch_test_http_client(database=database)
        try:
            assert client.query(
                "SELECT value FROM system.settings WHERE name = 'prefer_column_name_to_alias'"
            ).result_rows == [("0",)]
            for ddl in (
                _SPANS_DDL,
                _END_USERS_DDL,
                _REMAP_DDL,
                _REMAP_DDL.replace("trace_session_id_remap", "end_user_id_remap"),
            ):
                client.command(ddl)
            yield client
        finally:
            client.close()


def _seed_user(
    client, project, organization, user, label="customer", *, user_type=None
):
    client.insert(
        "end_users",
        [[project, user, organization, label, user_type, START, 1]],
        column_names=[
            "project_id",
            "end_user_id",
            "organization_id",
            "user_id",
            "user_id_type",
            "first_seen",
            "version",
        ],
    )


def _seed_spans(client, project, user, specs, *, trace=None):
    trace = trace or str(uuid4())
    rows = []
    for index, overrides in enumerate(specs):
        rows.append(
            {
                "project_id": project,
                "end_user_id": user,
                "trace_id": trace,
                "id": f"request-{index}",
                "parent_span_id": "",
                "name": "request",
                "observation_type": "llm",
                "service_name": "native-test",
                "start_time": START,
                "end_time": START + timedelta(seconds=1),
                "cost": 0.0,
                "total_tokens": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "attrs_string": KNOWN,
                "is_deleted": 0,
                "_version": 1,
                **overrides,
            }
        )
    columns = list(rows[0])
    client.insert(
        "spans", [[row[key] for key in columns] for row in rows], column_names=columns
    )


def _read(
    client, project, organization, *, method="build_candidate_page_query", **kwargs
):
    builder = UserListQueryBuilderV2(
        organization_id=str(organization),
        project_ids=[str(project)],
        filters=[_DATE_FILTER, *kwargs.pop("filters", [])],
        limit=20,
        offset=0,
        **kwargs,
    )
    sql, params = getattr(builder, method)()
    raw = list(client.query(sql, parameters=params).named_results())
    return builder.format_rows(raw), raw


CASES = [
    pytest.param([{"cost": 1.25}], _coverage("complete", 1.25, 1, 0), id="known"),
    pytest.param([{}], _coverage("complete", 0, 1, 0), id="genuine-zero"),
    pytest.param(
        [{"cost": 1.25}, {"attrs_string": UNKNOWN}],
        _coverage("partial", 1.25, 1, 1),
        id="mixed",
    ),
    pytest.param(
        [{"attrs_string": UNKNOWN}], _coverage("unknown", 0, 0, 1), id="all-unknown"
    ),
    pytest.param(
        [{}, {"attrs_string": UNKNOWN}],
        _coverage("partial", 0, 1, 1),
        id="zero-plus-unknown",
    ),
    pytest.param(
        [{"cost": 1.25, "attrs_string": {}}],
        _coverage("complete", 1.25, 1, 0),
        id="legacy-known",
    ),
    pytest.param(
        [{"attrs_string": {}}], _coverage("complete", 0, 1, 0), id="legacy-zero"
    ),
    pytest.param(
        [{"observation_type": "tool", "attrs_string": UNKNOWN}],
        _coverage("complete", 0, 0, 0),
        id="tokenless-tool",
    ),
    pytest.param(
        [{"observation_type": "agent", "prompt_tokens": 100, "attrs_string": UNKNOWN}],
        _coverage("unknown", 0, 0, 1),
        id="prompt-only",
    ),
    pytest.param(
        [
            {
                "observation_type": "chain",
                "completion_tokens": 20,
                "attrs_string": UNKNOWN,
            }
        ],
        _coverage("unknown", 0, 0, 1),
        id="completion-only",
    ),
    pytest.param(
        [
            {
                "observation_type": "tool",
                "attrs_string": {**UNKNOWN, "fi.usage.scope": "turn"},
            }
        ],
        _coverage("unknown", 0, 0, 1),
        id="tokenless-turn",
    ),
    pytest.param(
        [{"observation_type": "tool", "attrs_string": KNOWN}],
        _coverage("complete", 0, 1, 0),
        id="tokenless-reported-zero",
    ),
    pytest.param(
        [
            {
                "observation_type": "agent",
                "cost": 17,
                "prompt_tokens": 100,
                "attrs_string": NONE,
            }
        ],
        _coverage("complete", 0, 0, 0),
        id="nonadditive-summary",
    ),
    pytest.param(
        [
            {
                "id": "parent",
                "name": "pydantic_ai.agent.run",
                "observation_type": "agent",
                "cost": 1.25,
                "total_tokens": 10,
            },
            {
                "id": "child",
                "parent_span_id": "parent",
                "cost": 1.25,
                "total_tokens": 10,
            },
        ],
        _coverage("complete", 1.25, 1, 0),
        id="repeated-pydantic-summary",
    ),
    pytest.param(
        [{"cost": 1.25}, {"cost": 1.25}],
        _coverage("complete", 2.5, 2, 0),
        id="equal-valued-independent-requests",
    ),
    pytest.param(
        [{"name": "pydantic_ai.agent.run", "observation_type": "agent"}],
        _coverage("complete", 0, 1, 0),
        id="summary-only-known-zero",
    ),
    pytest.param(
        [
            {
                "id": "parent",
                "name": "pydantic_ai.agent.run",
                "observation_type": "agent",
                "attrs_string": {**UNKNOWN, "fi.usage.scope": "turn"},
            },
            {"parent_span_id": "parent"},
        ],
        _coverage("complete", 0, 1, 0),
        id="unknown-wrapper-known-zero-child",
    ),
    pytest.param(
        [
            {
                "id": "parent",
                "name": "pydantic_ai.agent.run",
                "observation_type": "agent",
            },
            {
                "id": "parent",
                "name": "pydantic_ai.agent.run",
                "observation_type": "agent",
                "service_name": "another-physical-key",
                "attrs_string": {},
            },
            {"parent_span_id": "parent"},
        ],
        _coverage("complete", 0, 1, 0),
        id="same-wrapper-id-different-known-flags",
    ),
    pytest.param(
        [
            {
                "id": "parent",
                "name": "pydantic_ai.agent.run",
                "observation_type": "agent",
            },
            {"id": "parent", "service_name": "independent-physical-key"},
            {"parent_span_id": "parent"},
        ],
        # Preserve the existing fact count: only the wrapper is subtracted.
        # Counting on billable_usage_spans would also lose the colliding LLM.
        _coverage("complete", 0, 2, 0),
        id="known-fact-id-collides-with-proven-wrapper",
    ),
    pytest.param(
        [
            {"id": "shared-id", "cost": 1.25},
            {
                "id": "shared-id",
                "service_name": "independent-physical-key",
                "cost": 1.25,
            },
        ],
        _coverage("complete", 2.5, 2, 0),
        id="same-id-independent-physical-keys",
    ),
]


@pytest.mark.integration
@pytest.mark.parametrize("method", ["build_candidate_page_query", "build"])
@pytest.mark.parametrize("specs,expected", CASES)
def test_actual_user_rows_and_scoped_csv(users_cost_client, method, specs, expected):
    client = users_cost_client
    project, organization, user, outside = uuid4(), uuid4(), uuid4(), uuid4()
    _seed_user(client, project, organization, user)
    _seed_spans(client, project, user, specs)
    # Same logical label and even same UUID in another project may not alter the scope.
    _seed_user(client, outside, organization, user)
    _seed_spans(client, outside, user, [{"cost": 999, "attrs_string": UNKNOWN}])
    payload, raw = _read(client, project, organization, method=method)
    assert payload["total_count"] == 1
    (row,) = payload["table"]
    assert row["cost_coverage"] == expected
    assert row["total_cost"] == expected["known_subtotal"]
    assert row["project_id"] == str(project)
    assert raw[0]["complete_cost"] == (
        row["total_cost"] if expected["status"] == "complete" else None
    )
    manager = _manager(project)
    # Production hydration carries the exact qualified candidate without a second scan.
    hydrated = manager._read_exact_candidate_rows(
        candidate_ids=[str(user)],
        frozen_filters=[_DATE_FILTER],
        window_start=START,
        window_end=START + timedelta(days=3),
        deadline=None,
        candidate_rows=payload["table"],
        enrich_rows=False,
    )
    manager._label_project_scope(hydrated)
    output = list(
        csv.reader(
            io.StringIO("".join(manager.iter_export_csv(cursor_read=_cursor(hydrated))))
        )
    )
    header, cells = output
    assert header[9] == "Total Cost ($)" and header[16] == "Project"
    assert header[17:] == [
        "Cost coverage",
        "Known cost subtotal ($)",
        "Known cost fact count",
        "Unknown cost fact count",
    ]
    assert cells[16] == "Scoped project"
    assert cells[9] == (
        str(row["total_cost"]) if expected["status"] == "complete" else ""
    )
    assert cells[17] == expected["status"]
    assert float(cells[18]) == expected["known_subtotal"]
    assert [int(value) for value in cells[19:]] == [
        expected["known_fact_count"],
        expected["unknown_fact_count"],
    ]


@pytest.mark.integration
@pytest.mark.parametrize(
    "old,new,deleted,expected",
    [
        (UNKNOWN, KNOWN, 0, _coverage("complete", 1.25, 2, 0)),
        (KNOWN, UNKNOWN, 0, _coverage("partial", 1.25, 1, 1)),
        (KNOWN, UNKNOWN, 1, _coverage("complete", 1.25, 1, 0)),
    ],
)
def test_latest_winner_tombstone_and_partition_merge(
    users_cost_client, old, new, deleted, expected
):
    client = users_cost_client
    project, organization, user = uuid4(), uuid4(), uuid4()
    _seed_user(client, project, organization, user)
    _seed_spans(client, project, user, [{"cost": 1.25}])
    # Separate date partition and physical insert, then a newer replacement.
    trace = str(uuid4())
    changed = {"id": "changing", "start_time": START + timedelta(days=1)}
    _seed_spans(client, project, user, [{**changed, "attrs_string": old}], trace=trace)
    _seed_spans(
        client,
        project,
        user,
        [{**changed, "attrs_string": new, "_version": 2, "is_deleted": deleted}],
        trace=trace,
    )
    payload, _ = _read(
        client,
        project,
        organization,
        include_num_sessions=True,
        candidate_end_user_ids=[str(user)],
    )
    assert payload["table"][0]["cost_coverage"] == expected


@pytest.mark.integration
@pytest.mark.parametrize("direction", ["asc", "desc"])
def test_complete_price_filter_count_and_sort_before_paging(
    users_cost_client, direction
):
    client = users_cost_client
    project, organization = uuid4(), uuid4()
    cases = {
        "zero": [{}],
        "known": [{"cost": 1.25}],
        "unknown": [{"attrs_string": UNKNOWN}],
        "partial": [{}, {"attrs_string": UNKNOWN}],
    }
    user_ids = {
        "zero": uuid4(),
        "known": uuid4(),
        # The existing tiebreaker is native UUID DESC, not toString(UUID).
        # ClickHouse compares the second half first: these deliberately
        # disagree with lexical order, making the regression deterministic.
        "unknown": UUID("ffffffff-ffff-4fff-8000-000000000001"),
        "partial": UUID("00000000-0000-4000-bfff-ffffffffffff"),
    }
    for label, specs in cases.items():
        user = user_ids[label]
        _seed_user(client, project, organization, user, label)
        _seed_spans(client, project, user, specs)
    payload, _ = _read(client, project, organization, filters=[_filter("equals")])
    assert payload["total_count"] == 1
    assert [row["user_id"] for row in payload["table"]] == ["zero"]
    nulls, _ = _read(client, project, organization, filters=[_filter("is_null", None)])
    assert nulls["total_count"] == 2
    assert {row["user_id"] for row in nulls["table"]} == {"partial", "unknown"}
    sorted_rows, _ = _read(
        client,
        project,
        organization,
        sort_params=[{"column_id": "total_cost", "direction": direction}],
    )
    assert [row["user_id"] for row in sorted_rows["table"][:2]] == (
        ["zero", "known"] if direction == "asc" else ["known", "zero"]
    )
    incomplete = sorted_rows["table"][2:]
    assert {row["user_id"] for row in incomplete} == {"partial", "unknown"}
    assert [row["end_user_id"] for row in incomplete] == [
        str(user_ids["partial"]),
        str(user_ids["unknown"]),
    ]


@pytest.mark.unit
@pytest.mark.parametrize(
    "coverage",
    [
        None,
        _coverage("complete", 0, 1, 0),
        _coverage("partial", 1.25, 1, 1),
        _coverage("unknown", 0, 0, 1),
    ],
)
def test_csv_is_bounded_and_legacy_cells_are_unchanged(coverage):
    row = {
        "user_id": "=formula",
        "total_cost": 0 if coverage is None else coverage["known_subtotal"],
    }
    if coverage is not None:
        row["cost_coverage"] = coverage
    # Export consumes the supplied finite page; it must not query or refill it.
    manager = _manager(uuid4())
    with patch.object(
        manager, "list_cursor_payload", side_effect=AssertionError("unexpected read")
    ):
        header, cells = list(
            csv.reader(
                io.StringIO(
                    "".join(manager.iter_export_csv(cursor_read=_cursor([row])))
                )
            )
        )
    assert cells[0] == "'=formula"
    assert len(header) == len(cells) == 21
    if coverage is None:
        assert cells[9] == "0"
        assert cells[17:] == ["", "", "", ""]
    else:
        assert cells[9] == (
            str(row["total_cost"]) if coverage["status"] == "complete" else ""
        )
        assert cells[17] == coverage["status"]


@pytest.mark.integration
@pytest.mark.parametrize("method", ["build_candidate_page_query", "build"])
def test_equal_version_usage_and_coverage_never_mix_winners(users_cost_client, method):
    client = users_cost_client
    project, organization, user, trace = uuid4(), uuid4(), uuid4(), str(uuid4())
    _seed_user(client, project, organization, user)
    _seed_spans(
        client,
        project,
        user,
        [{"id": "tie", "cost": 1.25, "total_tokens": 10}],
        trace=trace,
    )
    _seed_spans(
        client, project, user, [{"id": "tie", "attrs_string": UNKNOWN}], trace=trace
    )
    payload, _ = _read(client, project, organization, method=method)
    (row,) = payload["table"]
    assert (row["total_cost"], row["total_tokens"], row["cost_coverage"]) in [
        (1.25, 10, _coverage("complete", 1.25, 1, 0)),
        (0, 0, _coverage("unknown", 0, 0, 1)),
    ]


@pytest.mark.integration
@pytest.mark.parametrize("method", ["build_candidate_page_query", "build"])
@pytest.mark.parametrize("correction", ["cleared-owner", "outside-window"])
def test_newer_owner_and_timestamp_corrections_remove_old_unknown(
    users_cost_client, method, correction
):
    client = users_cost_client
    project, organization, user, trace = uuid4(), uuid4(), uuid4(), str(uuid4())
    _seed_user(client, project, organization, user)
    _seed_spans(client, project, user, [{"cost": 1.25}])
    changed = {
        "id": "corrected",
        "attrs_string": UNKNOWN,
        "start_time": START + timedelta(days=2, hours=23, minutes=50),
    }
    _seed_spans(client, project, user, [changed], trace=trace)
    # The window ends at START + 3 days (12:00); use the lower boundary for
    # an out-of-window correction within the SAME immutable hour instead.
    if correction == "outside-window":
        changed["start_time"] = START + timedelta(minutes=20)
        trace = str(uuid4())
        _seed_spans(client, project, user, [changed], trace=trace)
        # A separate precise date window below ends at 12:30.
        changed["start_time"] = START + timedelta(minutes=40)
    else:
        changed["end_user_id"] = None
    _seed_spans(client, project, user, [{**changed, "_version": 2}], trace=trace)
    builder = UserListQueryBuilderV2(
        organization_id=str(organization),
        project_ids=[str(project)],
        limit=20,
        offset=0,
        filters=[
            {
                **_DATE_FILTER,
                "filter_config": {
                    **_DATE_FILTER["filter_config"],
                    "filter_value": [
                        START.isoformat(),
                        (
                            START + timedelta(minutes=30)
                            if correction == "outside-window"
                            else START + timedelta(days=3)
                        ).isoformat(),
                    ],
                },
            }
        ],
    )
    sql, params = getattr(builder, method)()
    result = builder.format_rows(
        list(client.query(sql, parameters=params).named_results())
    )
    assert result["table"][0]["cost_coverage"] == _coverage("complete", 1.25, 1, 0)


@pytest.mark.integration
def test_alias_membership_keeps_coverage_and_foreign_org_is_empty(users_cost_client):
    client = users_cost_client
    project, organization = uuid4(), uuid4()
    survivor, alias = sorted([uuid4(), uuid4()], key=str)
    _seed_user(client, project, organization, survivor, "canonical")
    _seed_user(client, project, organization, alias, "alias")
    _seed_spans(client, project, survivor, [{"cost": 1.25}])
    _seed_spans(client, project, alias, [{"attrs_string": UNKNOWN}])
    client.insert(
        "end_user_id_remap", [[survivor, alias]], column_names=["old_id", "new_id"]
    )
    payload, _ = _read(
        client,
        project,
        organization,
        candidate_end_user_ids=[str(survivor)],
        candidate_scan_end_user_ids=[str(survivor), str(alias)],
        candidate_end_user_id_map={
            str(survivor): str(survivor),
            str(alias): str(survivor),
        },
    )
    (row,) = payload["table"]
    assert row["end_user_id"] == str(survivor)
    assert row["user_id"] == "canonical"
    assert row["cost_coverage"] == _coverage("partial", 1.25, 1, 1)
    forbidden, _ = _read(client, project, uuid4())
    assert forbidden == {"table": [], "total_count": 0}


@pytest.mark.unit
@pytest.mark.parametrize(
    "count_field", ["known_cost_fact_count", "unknown_cost_fact_count"]
)
def test_half_present_internal_companion_is_not_treated_as_legacy(count_field):
    with pytest.raises(KeyError):
        UserListQueryBuilder.format_rows([{"total_cost": 0, count_field: 1}])


@pytest.mark.integration
@pytest.mark.parametrize(
    "limit,offset,max_rows",
    [
        (2, 0, None),
        (1, 1, None),
        (1, 2, None),
        (3, 0, None),
        (1, 10, None),
        (0, 0, None),
        (None, None, 2),
    ],
)
def test_bounded_compatibility_rows_keep_types_nulls_order_and_count(
    users_cost_client, limit, offset, max_rows
):
    client = users_cost_client
    project, organization = uuid4(), uuid4()
    for label, spec in (
        ("zero", {}),
        ("known", {"cost": 1.25}),
        ("unknown", {"attrs_string": UNKNOWN, "end_time": None}),
    ):
        user = uuid4()
        _seed_user(
            client,
            project,
            organization,
            user,
            label,
            user_type="email" if label == "known" else None,
        )
        _seed_spans(client, project, user, [spec])
    kwargs = {
        "organization_id": str(organization),
        "project_ids": [str(project)],
        "filters": [_DATE_FILTER],
        "sort_params": [{"column_id": "total_cost", "direction": "desc"}],
    }
    # With no limit the original relational path is retained, independently of
    # the bounded tuple round trip. Never derive the expected count from it.
    reference_sql, reference_params = UserListQueryBuilderV2(**kwargs).build()
    reference = client.query(reference_sql, parameters=reference_params)
    assert len(reference.result_rows) == 3
    sql, params = UserListQueryBuilderV2(
        **kwargs, limit=limit, offset=offset, max_rows=max_rows
    ).build()
    result = client.query(sql, parameters=params)
    paginated = limit is not None and offset is not None
    expected = (
        reference.result_rows[offset : offset + limit]
        if paginated
        else reference.result_rows[:max_rows]
    )
    assert result.result_rows == [
        (*row[:-1], 3 if paginated else 0) for row in expected
    ]
    # HTTP omits columns even for an independent SELECT WHERE 0 on this
    # transport. Retain the HTTP row oracle, but use native metadata for empty
    # results as well as populated rows; do not mistake headers for lost rows.
    with _ch_test_native_client(database=client.database) as native:
        reference_rows, reference_columns = native.execute(
            reference_sql, reference_params, with_column_types=True
        )
        rows, columns = native.execute(sql, params, with_column_types=True)
    assert columns[:-1] == reference_columns[:-1]
    assert columns[-1] == (
        "total_count",
        "UInt64" if paginated else reference_columns[-1][1],
    )
    assert dict(columns)["known_cost_fact_count"] == "Int64"
    expected_native = (
        reference_rows[offset : offset + limit]
        if paginated
        else reference_rows[:max_rows]
    )
    assert rows == [(*row[:-1], 3 if paginated else 0) for row in expected_native]
    named_rows = [
        dict(zip((name for name, _ in columns), row, strict=True)) for row in rows
    ]
    if paginated and ((limit, offset) == (1, 2) or limit == 3):
        unknown = next(row for row in named_rows if row["user_id"] == "unknown")
        assert unknown["complete_cost"] is None
        assert unknown["last_active"] is None
        assert unknown["user_id_type"] is None
    for row in named_rows:
        if row["user_id"] == "known":
            assert row["user_id_type"] == "email"


@pytest.mark.integration
@pytest.mark.parametrize("include_num_sessions", [False, True])
def test_nonzero_session_state_survives_singleton_usage_projection(
    users_cost_client, include_num_sessions
):
    client = users_cost_client
    project, organization, user, session_a, session_b = (uuid4() for _ in range(5))
    _seed_user(client, project, organization, user)
    for index, session in enumerate((session_a, session_b, session_a, None)):
        _seed_spans(
            client,
            project,
            user,
            [{"trace_session_id": session, "cost": 1.25}],
            trace=f"session-state-{index}",
        )
    # The embedded aggregate is enabled only for an admitted finite user page.
    result, raw = _read(
        client,
        project,
        organization,
        candidate_end_user_ids=[str(user)],
        include_num_sessions=include_num_sessions,
    )
    row = result["table"][0]
    assert row["num_sessions"] == (2 if include_num_sessions else 0)
    assert row["num_traces"] == 4
    assert row["total_cost"] == 5.0
    assert row["cost_coverage"] == _coverage("complete", 5.0, 4, 0)
    assert raw[0]["num_sessions"] == row["num_sessions"]


@pytest.mark.integration
@pytest.mark.parametrize("present", [False, True])
def test_exact_user_compatibility_retains_empty_and_unknown_rows(
    users_cost_client, present
):
    client = users_cost_client
    project, organization, known, unknown = uuid4(), uuid4(), uuid4(), uuid4()
    _seed_user(client, project, organization, known, "known")
    _seed_spans(client, project, known, [{"cost": 1.25}])
    _seed_user(client, project, organization, unknown, "unknown")
    _seed_spans(client, project, unknown, [{"attrs_string": UNKNOWN, "end_time": None}])
    target = unknown if present else uuid4()
    builder = UserListQueryBuilderV2(
        organization_id=str(organization),
        project_ids=[str(project)],
        end_user_id=str(target),
        filters=[_DATE_FILTER],
    )
    sql, params = builder.build()
    rows = list(client.query(sql, parameters=params).named_results())
    if not present:
        assert rows == []
        return
    assert len(rows) == 1
    assert str(rows[0]["end_user_id"]) == str(unknown)
    assert rows[0]["complete_cost"] is None
    assert rows[0]["last_active"] is None
    assert rows[0]["total_count"] == 0
    result = builder.format_rows(rows)["table"][0]
    assert result["user_id"] == "unknown"
    assert result["cost_coverage"] == _coverage("unknown", 0, 0, 1)
