"""Session membership must use the same immediate usage as page hydration.

These bounded real-ClickHouse regressions cover the three failure families from
native-cost qualification: count bindings, root-only usage and CDC column names.
The v1 leg uses the repository's actual CDC DDL, not CH25 columns grafted onto it.
The v2 leg exercises the current session-view builder and its normal SQL rewrite.
"""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from conftest import _ch_test_native_client, _ch_test_owned_database
from tracer.services.clickhouse.application_read_policy import (
    application_read_settings,
)
from tracer.services.clickhouse.query_builders.session_list import (
    SessionListQueryBuilder,
)
from tracer.services.clickhouse.schema import SPANS_TABLE, _to_single_node_engine
from tracer.services.clickhouse.v2.query_builders.session_list import (
    SessionListQueryBuilderV2,
)
from tracer.tests.test_openrouter_broadcast_list_rollups_ch25 import (  # noqa: F401 -- isolate root fixture mutators  # noqa: F401
    _REMAP_DDL,
    _SPANS_DDL,
    _drop_legacy_ch_spans_mvs,
    _ensure_test_score_tenant_column,
)

pytestmark = pytest.mark.integration
START = datetime(2026, 10, 20, 12, tzinfo=UTC)
# Literal oracle. Costs are binary-exact so equality probes don't need rounding.
TRUTH = {
    "zero": (0.0, 12),
    "legacy-zero": (0.0, 12),
    "unknown": (None, 12),
    "mixed": (None, 24),
    "known": (0.5, 24),
    "activity": (0.25, 12),
    "component-unknown": (None, 12),
    "replaced": (0.25, 12),
    "deleted": (0.0, 0),
    "nonadditive": (0.25, 12),
    "nonadditive-with-values": (0.25, 12),
    "summary": (0.25, 12),
    "shared": (0.5, 24),
    "remapped": (0.25, 12),
    "moved": (0.0, 0),
}


def time_filter():
    return {
        "column_id": "created_at",
        "filter_config": {
            "filter_type": "datetime",
            "filter_op": "between",
            "filter_value": [
                START.isoformat(),
                (START + timedelta(days=1)).isoformat(),
            ],
        },
    }


def cost_filter(operation):
    return {
        "column_id": "total_cost",
        "filter_config": {
            "filter_type": "number",
            "filter_op": operation,
            "filter_value": 0,
        },
    }


@pytest.fixture(scope="module", params=["v1", "v2"])
def sessions(request):
    with _ch_test_owned_database("test_session_usage_") as database:
        with _ch_test_native_client(database=database) as client:
            client.execute(
                _to_single_node_engine(SPANS_TABLE)
                if request.param == "v1"
                else _SPANS_DDL
            )
            client.execute(_REMAP_DDL)
            client.execute("SYSTEM STOP MERGES spans")
            try:
                project = uuid4()
                session_ids = {
                    name: str(UUID(int=index + 1)) for index, name in enumerate(TRUTH)
                }
                rows = []

                def span(name, *, root=False, **changes):
                    # Distinct calls may be numerically identical; IDs, not values,
                    # identify a request. Roots carry no usage except the known
                    # Pydantic summary case.
                    return {
                        "project_id": project,
                        "trace_id": str(UUID(int=100 + list(TRUTH).index(name))),
                        "trace_session_id": UUID(session_ids[name]),
                        "id": "root" if root else "call",
                        "parent_span_id": "" if root else "root",
                        "start_time": START
                        + timedelta(minutes=list(TRUTH).index(name)),
                        "end_time": START
                        + timedelta(minutes=list(TRUTH).index(name), seconds=1),
                        "name": "root" if root else "request",
                        "observation_type": "agent" if root else "llm",
                        "prompt_tokens": 0 if root else 10,
                        "completion_tokens": 0 if root else 2,
                        "total_tokens": 0 if root else 12,
                        "cost": 0.0 if root else 0.25,
                        "attrs_string": {"fi.cost.kind": "provider_reported"},
                        "input": "synthetic input",
                        "_version": 1,
                        "is_deleted": 0,
                        **changes,
                    }

                unknown = {"fi.cost.kind": "unknown"}
                for name in TRUTH:
                    rows.append(span(name, root=True))
                    changes = {}
                    if name in {"zero", "legacy-zero", "unknown"}:
                        changes["cost"] = 0.0
                    if name in {"unknown", "replaced"}:
                        changes["attrs_string"] = unknown
                    if name == "unknown" and request.param == "v1":
                        changes["cost"] = None
                    if name == "legacy-zero":
                        changes["attrs_string"] = {}
                    if name == "summary":
                        rows[-1].update(
                            name="pydantic_ai.agent.run",
                            cost=0.25,
                            total_tokens=12,
                            prompt_tokens=10,
                            completion_tokens=2,
                        )
                    if name == "remapped":
                        old = UUID(session_ids[name])
                        new = UUID(int=999)
                        client.execute(
                            "INSERT INTO trace_session_id_remap (old_id, new_id) VALUES",
                            [(old, new)],
                        )
                        rows[-1]["trace_session_id"] = new
                        changes["trace_session_id"] = new
                    call = span(name, **changes)
                    rows.append(call)
                    if name in {"known", "mixed"}:
                        rows.append(
                            span(
                                name,
                                id="second",
                                attrs_string=unknown if name == "mixed" else {},
                            )
                        )
                    if name in {"activity", "component-unknown"}:
                        rows.append(
                            span(
                                name,
                                id="tool",
                                observation_type="tool",
                                cost=0.0,
                                total_tokens=0,
                                prompt_tokens=1 if name == "component-unknown" else 0,
                                completion_tokens=0,
                                attrs_string=unknown,
                            )
                        )
                    if name in {"nonadditive", "nonadditive-with-values"}:
                        rows.append(
                            span(
                                name,
                                id="summary",
                                cost=1.0 if name.endswith("values") else 0.0,
                                total_tokens=12 if name.endswith("values") else 0,
                                prompt_tokens=10 if name.endswith("values") else 0,
                                completion_tokens=2 if name.endswith("values") else 0,
                                attrs_string={
                                    **unknown,
                                    "fi.usage.contribution": "none",
                                    "fi.usage.scope": "turn",
                                },
                            )
                        )
                    if name == "replaced":
                        rows.extend(
                            [dict(call), {**call, "_version": 2, "attrs_string": {}}]
                        )
                    if name == "deleted":
                        rows.append({**call, "_version": 2, "is_deleted": 1})
                    if name == "moved":
                        rows.append({**call, "_version": 2, "trace_session_id": None})
                    if name == "shared":
                        second_trace = str(UUID(int=500))
                        rows.extend(
                            [
                                span(name, root=True, trace_id=second_trace),
                                span(name, trace_id=second_trace),
                            ]
                        )
                    # Same textual trace/span/session IDs in another tenant must
                    # neither add cost nor inject unknown coverage.
                    rows.append(
                        {
                            **call,
                            "project_id": UUID(int=9999),
                            "attrs_string": unknown,
                            "cost": 100.0,
                        }
                    )

                # Request windows are half-open. A billable unknown exactly at
                # the ceiling cannot poison the session's in-window usage.
                rows.append(
                    span(
                        "known",
                        id="after-window",
                        start_time=START + timedelta(days=1),
                        attrs_string=unknown,
                    )
                )
                # A root's historical membership must not resurrect a deleted
                # session, even though its orphan child is still live.
                removed_root = span(
                    "known",
                    root=True,
                    trace_id=str(UUID(int=700)),
                    trace_session_id=UUID(int=800),
                )
                rows.extend(
                    [
                        removed_root,
                        {**removed_root, "_version": 2, "is_deleted": 1},
                        span(
                            "known",
                            trace_id=str(UUID(int=700)),
                            trace_session_id=UUID(int=800),
                        ),
                    ]
                )
                for row in rows:
                    if request.param == "v1":
                        row = {**row, "created_at": START}
                        row["span_attr_str"] = row.pop("attrs_string")
                        row["_peerdb_version"] = row.pop("_version")
                        row["_peerdb_is_deleted"] = row.pop("is_deleted")
                    client.execute(
                        f"INSERT INTO spans ({', '.join(row)}) VALUES",
                        [tuple(row.values())],
                    )
                yield (
                    client,
                    project,
                    session_ids,
                    (
                        SessionListQueryBuilder
                        if request.param == "v1"
                        else SessionListQueryBuilderV2
                    ),
                )
            finally:
                client.execute("SYSTEM START MERGES spans")


def execute(client, query):
    sql, params = query
    rows, columns = client.execute(
        sql, params, with_column_types=True, settings=application_read_settings()
    )
    return [
        dict(zip((column[0] for column in columns), row, strict=True)) for row in rows
    ]


@pytest.mark.parametrize(
    "operation", ["equals", "is_null", "is_not_null", "greater_than"]
)
@pytest.mark.parametrize(
    "surface",
    [
        "candidate",
        "cursor",
        "count",
        "match",
        "aggregate_list",
        "aggregate_count",
        "ids",
    ],
)
def test_child_usage_membership_and_count(sessions, operation, surface):
    client, project, session_ids, builder_class = sessions
    expected_names = {
        "equals": {"zero", "legacy-zero", "deleted", "moved"},
        "is_null": {"unknown", "mixed", "component-unknown"},
        "is_not_null": set(TRUTH) - {"unknown", "mixed", "component-unknown"},
        "greater_than": {
            "known",
            "activity",
            "replaced",
            "nonadditive",
            "nonadditive-with-values",
            "summary",
            "shared",
            "remapped",
        },
    }[operation]
    builder = builder_class(
        project_id=str(project), filters=[time_filter(), cost_filter(operation)]
    )
    if surface == "cursor":
        builder.filters.append(
            {
                "column_id": "session_id",
                "filter_config": {
                    "filter_type": "string",
                    "filter_op": "in",
                    "filter_value": list(session_ids.values()),
                },
            }
        )
    query = {
        "candidate": builder.build_candidate_page_query,
        "cursor": builder.build_candidate_cursor_page_query,
        "count": builder.build_candidate_count_query,
        "match": lambda: builder.build_filter_match_query(list(session_ids.values())),
        "aggregate_list": builder.build,
        "aggregate_count": builder.build_count_query,
        "ids": builder.build_id_query,
    }[surface]()
    actual = execute(client, query)
    if surface in {"count", "aggregate_count"}:
        assert actual == [{"total": len(expected_names)}]
    else:
        assert {str(row["session_id"]) for row in actual} == {
            session_ids[name] for name in expected_names
        }
        assert len(actual) == len(expected_names)


@pytest.mark.parametrize("case", TRUTH)
def test_hydration_uses_identical_live_member_usage(sessions, case):
    client, project, session_ids, builder_class = sessions
    builder = builder_class(project_id=str(project), filters=[time_filter()])
    rows = execute(client, builder.build_page_hydration_query([session_ids[case]]))
    assert len(rows) == 1
    cost, tokens = TRUTH[case]
    assert rows[0]["total_cost"] == cost
    assert rows[0]["total_tokens"] == tokens
    assert rows[0]["traces_count"] == (2 if case == "shared" else 1)
    formatted = builder.format_sessions(rows, list(rows[0]))[0]
    if case in {"nonadditive", "nonadditive-with-values"}:
        # The native hour and CDC created-day implementations share this path.
        # The nonadditive LLM-shaped source is not a second observed request.
        assert formatted["capture_coverage"] == {
            "scope": "observed_requests",
            "source_summary_present": True,
            "request_count": 1,
            "coverage_unknown": True,
        }
    else:
        assert "capture_coverage" not in formatted


@pytest.mark.parametrize("direction", ["asc", "desc"])
def test_usage_sort_pages_are_complete_null_last(sessions, direction):
    client, project, session_ids, builder_class = sessions
    observed = []
    for page in range((len(TRUTH) + 2) // 3):
        builder = builder_class(
            project_id=str(project),
            filters=[time_filter()],
            page_size=3,
            page_number=page,
            sort_params=[{"column_id": "total_cost", "direction": direction}],
        )
        rows = execute(client, builder.build_candidate_page_query())
        assert all(row["total_count"] == len(TRUTH) for row in rows)
        observed.extend(str(row["session_id"]) for row in rows[:3])
    ordered_known = sorted(
        (name for name in TRUTH if TRUTH[name][0] is not None),
        key=lambda name: (TRUTH[name][0], session_ids[name]),
        reverse=direction == "desc",
    )
    ordered_unknown = sorted(
        (name for name in TRUTH if TRUTH[name][0] is None),
        key=lambda name: session_ids[name],
        reverse=direction == "desc",
    )
    assert observed == [session_ids[name] for name in ordered_known + ordered_unknown]
    assert len(set(observed)) == len(TRUTH)


@pytest.mark.parametrize(
    "surface", ["candidate", "count", "match", "aggregate_list", "aggregate_count"]
)
def test_child_token_filter_uses_the_usage_rollup(sessions, surface):
    client, project, session_ids, builder_class = sessions
    token_filter = {
        "column_id": "total_tokens",
        "filter_config": {
            "filter_type": "number",
            "filter_op": "greater_than",
            "filter_value": 12,
        },
    }
    builder = builder_class(
        project_id=str(project), filters=[time_filter(), token_filter]
    )
    query = {
        "candidate": builder.build_candidate_page_query,
        "count": builder.build_candidate_count_query,
        "match": lambda: builder.build_filter_match_query(list(session_ids.values())),
        "aggregate_list": builder.build,
        "aggregate_count": builder.build_count_query,
    }[surface]()
    rows = execute(client, query)
    if surface in {"count", "aggregate_count"}:
        assert rows == [{"total": 3}]
    else:
        assert {str(row["session_id"]) for row in rows} == {
            session_ids[name] for name in ("known", "mixed", "shared")
        }


def test_cost_cursor_continuation_has_no_root_only_holes(sessions):
    client, project, session_ids, builder_class = sessions
    builder = builder_class(
        project_id=str(project),
        page_size=3,
        filters=[
            time_filter(),
            cost_filter("greater_than"),
            {
                "column_id": "session_id",
                "filter_config": {
                    "filter_type": "string",
                    "filter_op": "in",
                    "filter_value": list(session_ids.values()),
                },
            },
        ],
    )
    expected = [
        session_ids[name]
        for name in reversed(TRUTH)
        if TRUTH[name][0] is not None and TRUTH[name][0] > 0
    ]
    observed = []
    cursor = {}
    for offset in range(0, len(expected), 3):
        rows = execute(client, builder.build_candidate_cursor_page_query(**cursor))
        assert rows[0]["remaining_count"] == len(expected) - offset
        page = rows[:3]
        observed.extend(str(row["session_id"]) for row in page)
        cursor = {
            "before_start_time": page[-1]["session_start"],
            "before_session_id": str(page[-1]["session_id"]),
        }
    assert observed == expected
