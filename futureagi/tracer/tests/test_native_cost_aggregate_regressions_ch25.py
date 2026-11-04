"""Execute native-cost reader/hydration regressions on the owned CH25 sidecar.

No SQL extraction, alias rewrites, setting overrides or embedded-engine fallback:
these are full production method calls over synthetic spans. The existing
OpenRouter rollup fixture supplies only the reduced CH25 table definitions.
"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from conftest import _ch_test_owned_database, _open_ch_test_http_client
from tracer.services.clickhouse.v2.query_builders.session_list import (
    SessionListQueryBuilderV2,
)
from tracer.services.clickhouse.v2.span_reader import CHSpanReader
from tracer.tests.test_openrouter_broadcast_list_rollups_ch25 import (
    _REMAP_DDL,
    _SPANS_DDL,
    _TRACES_DDL,
)

pytestmark = pytest.mark.integration
START = datetime(2026, 10, 20, 12, tzinfo=UTC)
UNKNOWN = {"fi.cost.kind": "unknown"}
NONADDITIVE = {
    **UNKNOWN,
    "fi.usage.contribution": "none",
    "fi.usage.scope": "turn",
}
# Literal expectations, not computed with the production completeness predicate.
# Each tuple is (root overrides, optional child overrides, expected cost).
CASES = [
    pytest.param({}, {"cost": 0.2}, 0.3, id="all-known"),
    pytest.param({"cost": 0.0}, None, 0.0, id="explicit-zero"),
    pytest.param({"cost": 0.0, "attrs_string": {}}, None, 0.0, id="legacy-zero"),
    pytest.param({"cost": 0.0, "attrs_string": UNKNOWN}, None, None, id="all-unknown"),
    pytest.param({}, {"attrs_string": UNKNOWN}, None, id="mixed-unknown"),
    pytest.param(
        {},
        {"observation_type": "tool", "total_tokens": 0, "attrs_string": UNKNOWN},
        0.1,
        id="tokenless-tool",
    ),
    pytest.param(
        {},
        {"observation_type": "chain", "total_tokens": 0, "attrs_string": UNKNOWN},
        0.1,
        id="tokenless-chain",
    ),
    pytest.param(
        {},
        {"total_tokens": 0, "attrs_string": UNKNOWN},
        None,
        id="tokenless-llm",
    ),
    pytest.param(
        {},
        {
            "observation_type": "tool",
            "total_tokens": 0,
            "prompt_tokens": 100,
            "attrs_string": UNKNOWN,
        },
        None,
        id="prompt-only-tool",
    ),
    pytest.param(
        {},
        {
            "observation_type": "chain",
            "total_tokens": 0,
            "completion_tokens": 20,
            "attrs_string": UNKNOWN,
        },
        None,
        id="completion-only-chain",
    ),
    pytest.param(
        {},
        {
            "observation_type": "agent",
            "total_tokens": 0,
            "prompt_tokens": 100,
            "completion_tokens": 20,
            "attrs_string": UNKNOWN,
        },
        None,
        id="components-only-agent",
    ),
    pytest.param(
        {},
        {
            "observation_type": "agent",
            "total_tokens": 0,
            "prompt_tokens": 100,
            "completion_tokens": 20,
            "attrs_string": NONADDITIVE,
        },
        0.1,
        id="components-nonadditive-summary",
    ),
    pytest.param(
        {},
        {"observation_type": "tool", "total_tokens": 1, "attrs_string": UNKNOWN},
        None,
        id="tokenful-tool",
    ),
    pytest.param(
        {},
        {"observation_type": "chain", "total_tokens": 1, "attrs_string": UNKNOWN},
        None,
        id="tokenful-chain",
    ),
    pytest.param(
        {},
        {
            "observation_type": "tool",
            "total_tokens": 0,
            "attrs_string": {**UNKNOWN, "fi.usage.scope": "turn"},
        },
        None,
        id="tokenless-turn-tool",
    ),
    pytest.param(
        {},
        {
            "observation_type": "chain",
            "total_tokens": 0,
            "attrs_string": {**UNKNOWN, "fi.usage.scope": "turn"},
        },
        None,
        id="tokenless-turn-chain",
    ),
    pytest.param(
        {},
        {
            "total_tokens": 0,
            "attrs_string": {
                **UNKNOWN,
                "fi.usage.contribution": "future-additive-kind",
            },
        },
        None,
        id="unknown-future-contribution",
    ),
    pytest.param(
        {},
        {"attrs_string": NONADDITIVE},
        0.1,
        id="nonadditive-overrides-tokens-llm-turn",
    ),
    pytest.param(
        {
            "observation_type": "agent",
            "total_tokens": 0,
            "cost": 0.0,
            "attrs_string": NONADDITIVE,
        },
        None,
        0.0,
        id="summary-only",
    ),
]


@pytest.fixture(autouse=True, scope="session")
def _drop_legacy_ch_spans_mvs():
    yield


@pytest.fixture(autouse=True, scope="session")
def _ensure_test_score_tenant_column():
    yield


@pytest.fixture(scope="module")
def cost_client():
    with _ch_test_owned_database("test_native_cost_regressions_") as database:
        client = _open_ch_test_http_client(database=database)
        try:
            # Fail rather than hide the alias bug behind a non-default server.
            assert client.query(
                "SELECT value FROM system.settings "
                "WHERE name = 'prefer_column_name_to_alias'"
            ).result_rows == [("0",)]
            for ddl in (_SPANS_DDL, _REMAP_DDL, _TRACES_DDL):
                client.command(ddl)
            yield client
        finally:
            client.close()


@pytest.mark.parametrize("surface", ["trace", "hydration"])
@pytest.mark.parametrize("root_overrides,child_overrides,expected_cost", CASES)
def test_native_cost_aggregate_completeness(
    cost_client, surface, root_overrides, child_overrides, expected_cost
):
    project, session = uuid4(), uuid4()
    trace = str(uuid4())
    root = {
        "id": "root",
        "parent_span_id": "",
        "observation_type": "llm",
        "total_tokens": 120,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "cost": 0.1,
        "attrs_string": {"fi.cost.kind": "provider_reported"},
        **root_overrides,
    }
    spans = [root]
    if child_overrides is not None:
        spans.append(
            {
                "id": "child",
                "parent_span_id": "root",
                "observation_type": "llm",
                "total_tokens": 120,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "cost": 0.0,
                "attrs_string": {"fi.cost.kind": "provider_reported"},
                **child_overrides,
            }
        )
    columns = [
        "project_id",
        "trace_session_id",
        "trace_id",
        "start_time",
        "end_time",
        "name",
        "input",
        "_version",
        *root,
    ]
    rows = []
    for span in spans:
        row = {
            "project_id": project,
            "trace_session_id": session,
            "trace_id": trace,
            "start_time": START,
            "end_time": START + timedelta(seconds=1),
            "name": "synthetic.request",
            "input": "synthetic input",
            "_version": 1,
            **span,
        }
        rows.append([row[column] for column in columns])
    cost_client.insert("spans", rows, column_names=columns)
    expected_tokens = sum(span["total_tokens"] for span in spans)

    if surface == "trace":
        reader = CHSpanReader.__new__(CHSpanReader)
        reader._client = cost_client
        result = reader.trace_aggregate(trace)
        assert set(result) == {
            "span_count",
            "prompt_tokens",
            "completion_tokens",
            "total_tokens",
            "cost",
            "last_end",
        }
        assert result["span_count"] == len(spans)
        assert result["prompt_tokens"] == sum(span["prompt_tokens"] for span in spans)
        assert result["completion_tokens"] == sum(
            span["completion_tokens"] for span in spans
        )
        assert result["total_tokens"] == expected_tokens
        # DateTime64(6, 'UTC') is decoded as naive UTC by some supported drivers.
        last_end = result["last_end"]
        if last_end.tzinfo is None:
            last_end = last_end.replace(tzinfo=UTC)
        assert last_end == START + timedelta(seconds=1)
        actual_cost = result["cost"]
    else:
        # Session usage excludes explicit nonadditive summaries, even if a
        # stored legacy row still carries their copied numeric token values.
        expected_tokens = sum(
            span["total_tokens"]
            for span in spans
            if span["attrs_string"].get("fi.usage.contribution") != "none"
        )
        builder = SessionListQueryBuilderV2(
            project_id=str(project),
            filters=[
                {
                    "column_id": "created_at",
                    "filter_config": {
                        "col_type": "SYSTEM_METRIC",
                        "filter_type": "datetime",
                        "filter_op": "between",
                        "filter_value": [
                            START.isoformat(),
                            (START + timedelta(days=1)).isoformat(),
                        ],
                    },
                }
            ],
        )
        sql, params = builder.build_page_hydration_query([str(session)])
        result = list(cost_client.query(sql, parameters=params).named_results())
        assert len(result) == 1
        assert str(result[0]["session_id"]) == str(session)
        assert result[0]["total_tokens"] == expected_tokens
        assert result[0]["traces_count"] == 1
        actual_cost = result[0]["total_cost"]
        formatted = builder.format_sessions(result, list(result[0]))[0]
        if not any(
            span["attrs_string"].get("fi.usage.scope") == "turn"
            and span["attrs_string"].get("fi.usage.contribution") == "none"
            for span in spans
        ):
            assert "capture_coverage" not in formatted

    if expected_cost is None:
        assert actual_cost is None, "unknown work must not become zero or a partial sum"
    else:
        assert actual_cost == pytest.approx(expected_cost, rel=0, abs=1e-12)


@pytest.mark.parametrize("surface", ["session", "batch", "per_trace"])
@pytest.mark.parametrize("prompt,completion", [(100, 0), (0, 20), (100, 20)])
def test_component_only_unknown_cost_in_other_reader_aggregates(
    cost_client, surface, prompt, completion
):
    project, session, trace = uuid4(), uuid4(), str(uuid4())
    columns = [
        "project_id",
        "trace_session_id",
        "trace_id",
        "id",
        "parent_span_id",
        "start_time",
        "end_time",
        "observation_type",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "cost",
        "attrs_string",
        "_version",
    ]
    common = [project, session, trace]
    rows = [
        common
        + [
            "known",
            "",
            START,
            START + timedelta(seconds=1),
            "llm",
            0,
            0,
            120,
            0.25,
            {"fi.cost.kind": "provider_reported"},
            1,
        ],
        common
        + [
            "component-work",
            "known",
            START,
            START + timedelta(seconds=1),
            "agent",
            prompt,
            completion,
            0,
            0.0,
            UNKNOWN,
            1,
        ],
    ]
    cost_client.insert("spans", rows, column_names=columns)
    reader = CHSpanReader.__new__(CHSpanReader)
    reader._client = cost_client
    if surface == "session":
        result = reader.session_aggregate(str(session))
    elif surface == "batch":
        result = reader.aggregate_by_trace_ids([trace])
    else:
        result = reader.per_trace_aggregate([trace])[trace]
    assert result["cost"] is None
    assert result["total_tokens"] == 120
    assert result["prompt_tokens"] == prompt
    assert result["completion_tokens"] == completion


@pytest.mark.parametrize("source_usd", [None, 17.0])
@pytest.mark.parametrize("late_day", [0, 1])
def test_native_source_summary_parent_late_children_and_replay(
    cost_client, source_usd, late_day
):
    """Real latest-live session query, including project scope and tombstones."""
    from tracer.services.clickhouse.query_builders.trace_usage import (
        capture_coverage_from_counts,
    )
    from tracer.services.clickhouse.v2.query_builders.session_detail import (
        build_session_detail_aggregate_query,
    )

    project, session, trace = uuid4(), uuid4(), str(uuid4())
    root_id = "0000000000000001"
    raw_summary = {
        "fi.source.summary.gen_ai.usage.input_tokens": 42925.0,
        "fi.source.summary.gen_ai.usage.output_tokens": 146.0,
    }
    if source_usd is not None:
        raw_summary["fi.source.summary.gen_ai.cost.total"] = source_usd
    root = {
        "id": root_id,
        "parent_span_id": "",
        "name": "invoke_agent",
        "observation_type": "agent",
        "start_time": START,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "cost": 0.0,
        "attrs_string": {
            **NONADDITIVE,
            "fi.normalization.profile": "github-copilot-cli-1.0.98",
        },
        "attrs_number": raw_summary,
        "is_deleted": 0,
    }
    children = [
        {
            **root,
            "id": sid,
            "parent_span_id": root_id,
            "name": "chat",
            "observation_type": "llm",
            "start_time": START + timedelta(days=late_day),
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": prompt + completion,
            "attrs_string": UNKNOWN,
            "attrs_number": {},
        }
        for sid, prompt, completion in [
            ("0000000000000002", 14095, 61),
            ("0000000000000003", 14370, 52),
            ("0000000000000004", 14460, 33),
        ]
    ]
    hook = {
        **root,
        "id": "0000000000000005",
        "parent_span_id": root_id,
        "name": "execute_hook",
        "observation_type": "tool",
        "attrs_string": UNKNOWN,
        "attrs_number": {},
    }
    columns = [
        "project_id",
        "trace_session_id",
        "trace_id",
        "end_time",
        "_version",
        *root,
    ]

    def insert(spans, version, selected_project=project):
        rows = []
        for span in spans:
            values = {
                "project_id": selected_project,
                "trace_session_id": session,
                "trace_id": trace,
                "end_time": span["start_time"] + timedelta(seconds=1),
                "_version": version,
                **span,
            }
            rows.append([values[column] for column in columns])
        cost_client.insert("spans", rows, column_names=columns)

    def read_usage(
        expected_tokens,
        expected_count,
        expected_cost,
        root_version=1,
        source_present=True,
    ):
        sql, params = build_session_detail_aggregate_query(str(project), [str(session)])
        rows = list(cost_client.query(sql, parameters=params).named_results())
        assert len(rows) == 1
        row = rows[0]
        assert row["total_tokens"] == expected_tokens
        assert row["total_cost"] == expected_cost
        assert row["total_traces"] == 1
        expected_capture = (
            {
                "scope": "observed_requests",
                "source_summary_present": True,
                "request_count": expected_count,
                "coverage_unknown": True,
            }
            if source_present
            else None
        )
        assert row["observed_request_count"] == expected_count
        assert bool(row["source_summary_present"]) is source_present
        assert (
            capture_coverage_from_counts(
                source_summary_present=bool(row["source_summary_present"]),
                request_count=row["observed_request_count"],
            )
            == expected_capture
        )

        # The actual trace-list physical winner and shared session-list base,
        # not just detail reducers. Each full statement is bounded to this page.
        from tracer.services.clickhouse.v2.query_builders.trace_list import (
            TraceListQueryBuilderV2,
            apply_trace_rollup,
        )

        trace_builder = TraceListQueryBuilderV2(project_id=str(project), filters=[])
        start_us = int(START.timestamp() * 1_000_000)
        identity = (
            str(project),
            trace,
            root_id,
            start_us,
            "agent",
            "",
            start_us // 3_600_000_000 * 3_600_000_000,
            root_version,
        )
        trace_sql, trace_params = trace_builder.build_content_query(
            [trace],
            root_identities=[identity],
            include_trace_rollup=True,
        )
        trace_rows = list(
            cost_client.query(trace_sql, parameters=trace_params).named_results()
        )
        assert len(trace_rows) == 1
        trace_row = {"project_id": str(project), "trace_id": trace, "id": root_id}
        apply_trace_rollup(trace_row, trace_rows[0])
        assert trace_row.get("capture_coverage") == expected_capture
        assert trace_row["cost"] == expected_cost
        assert trace_row["total_tokens"] == expected_tokens
        assert trace_row["id"] == "0000000000000001"
        session_builder = SessionListQueryBuilderV2(
            project_id=str(project),
            filters=[
                {
                    "column_id": "created_at",
                    "filter_config": {
                        "col_type": "SYSTEM_METRIC",
                        "filter_type": "datetime",
                        "filter_op": "between",
                        "filter_value": [
                            START.isoformat(),
                            (START + timedelta(days=2)).isoformat(),
                        ],
                    },
                }
            ],
        )
        session_sql, session_params = session_builder.build_page_hydration_query(
            [str(session)]
        )
        session_rows = list(
            cost_client.query(session_sql, parameters=session_params).named_results()
        )
        assert len(session_rows) == 1
        session_row = session_builder.format_sessions(
            session_rows, list(session_rows[0])
        )[0]
        assert session_row["session_id"] == str(session)
        assert session_row["total_tokens"] == expected_tokens
        assert session_row["total_cost"] == expected_cost
        assert session_row["total_traces_count"] == 1
        assert session_row.get("capture_coverage") == expected_capture

    insert([root], 1)
    insert(children, 1, selected_project=uuid4())
    read_usage(0, 0, 0.0)
    insert([*children, hook], 1)
    read_usage(43071, 3, None)
    insert([root, *children, hook], 2)
    read_usage(43071, 3, None, root_version=2)
    stored = list(
        cost_client.query(
            "SELECT id, attrs_number FROM spans FINAL WHERE project_id = %(project)s "
            "AND trace_id = %(trace)s AND id = %(id)s AND is_deleted = 0",
            parameters={"project": str(project), "trace": trace, "id": root_id},
        ).named_results()
    )
    assert stored == [{"id": root_id, "attrs_number": raw_summary}]
    insert([{**children[0], "is_deleted": 1}], 3)
    read_usage(28915, 2, None, root_version=2)
    # The source witness follows the same latest physical winner; a historical
    # marker must not survive a newer unmarked root or a deleted source root.
    insert([{**root, "attrs_string": {}, "attrs_number": {}}], 4)
    sql, params = build_session_detail_aggregate_query(str(project), [str(session)])
    row = list(cost_client.query(sql, parameters=params).named_results())[0]
    assert not row["source_summary_present"]
    assert row["observed_request_count"] == 2
    read_usage(28915, 2, None, root_version=4, source_present=False)
