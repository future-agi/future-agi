"""Trace/session list rollups for traces whose usage and content sit on children.

OpenRouter Broadcast sends a root span with no content or usage, a generation
span (LLM) with the prompt, completion, tokens and cost, and one or more
Provider Attempt spans. The R2 real-user test (#3610) found the Observe trace
list, session list and session history reading only the root, so they showed
"-" and $0 for these traces. The rows here are the ones R2 stored from real
OpenRouter payloads, plus the traceAI regression traces from the same run,
whose roots carry content and must render exactly as before.

The statements run on a real ClickHouse: the CI ClickHouse 25.3 sidecar when
one is configured, else an embedded chdb session when chdb is importable,
else the module skips. Nothing here opens a port of its own.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from clickhouse_driver.util.escape import escape_params

from conftest import (
    _ch_test_native_port,
    _ch_test_owned_database,
    _open_ch_test_native_client,
)
from tracer.services.clickhouse.query_builders.trace_detail import (
    compute_trace_summary_and_graph,
)
from tracer.services.clickhouse.v2.query_builders.observe_usage import (
    ObserveTraceListQueryBuilderV2,
)
from tracer.services.clickhouse.v2.query_builders.session_detail import (
    build_session_detail_aggregate_query,
    build_session_detail_traces_query,
)
from tracer.services.clickhouse.v2.query_builders.session_list import (
    SessionListQueryBuilderV2,
)
from tracer.services.clickhouse.v2.query_builders.trace_list import (
    TRACE_ROLLUP_COLUMNS,
    TraceListQueryBuilderV2,
    apply_trace_rollup,
)

pytestmark = pytest.mark.integration

FIXTURE = json.loads(
    (
        Path(__file__).parent / "fixtures" / "openrouter_broadcast_r2_spans.json"
    ).read_text()
)
TRACES = {trace["case"]: trace for trace in FIXTURE["traces"]}
WINDOW_START = datetime(2026, 10, 25, tzinfo=UTC)
WINDOW_END = datetime(2026, 10, 27, tzinfo=UTC)

# CH25 `spans` (v2/schema/002_spans_v2.sql), reduced to the columns these
# reads touch; the types, materialized lengths and keys are the deployed ones.
_SPANS_DDL = """
CREATE TABLE spans
(
    project_id UUID,
    observation_type LowCardinality(String),
    service_name LowCardinality(String) DEFAULT '',
    start_time DateTime64(6, 'UTC'),
    trace_id String,
    id String,
    parent_span_id String DEFAULT '',
    name String DEFAULT '',
    trace_name String MATERIALIZED name,
    end_time Nullable(DateTime64(6, 'UTC')),
    latency_ms Int32 DEFAULT 0,
    project_version_id Nullable(UUID),
    end_user_id Nullable(UUID),
    trace_session_id Nullable(UUID),
    status LowCardinality(String) DEFAULT '',
    model LowCardinality(String) DEFAULT '',
    provider LowCardinality(String) DEFAULT '',
    prompt_tokens Int32 DEFAULT 0,
    completion_tokens Int32 DEFAULT 0,
    total_tokens Int32 DEFAULT 0,
    cost Float64 DEFAULT 0,
    attrs_string Map(LowCardinality(String), String),
    attrs_number Map(LowCardinality(String), Float64),
    attrs_bool Map(LowCardinality(String), UInt8),
    attributes_extra String DEFAULT '{}',
    metadata String DEFAULT '{}',
    input String DEFAULT '',
    output String DEFAULT '',
    input_length UInt32 MATERIALIZED lengthUTF8(input),
    output_length UInt32 MATERIALIZED lengthUTF8(output),
    is_deleted UInt8 DEFAULT 0,
    _version UInt64,
    INDEX idx_trace_id trace_id TYPE bloom_filter(0.001) GRANULARITY 1,
    INDEX idx_trace_session_id trace_session_id TYPE bloom_filter(0.001) GRANULARITY 1
)
ENGINE = ReplacingMergeTree(_version, is_deleted)
PARTITION BY toDate(start_time)
PRIMARY KEY (project_id, observation_type, service_name, toStartOfHour(start_time))
ORDER BY (project_id, observation_type, service_name, toStartOfHour(start_time), trace_id, id)
"""
_TRACES_DDL = """
CREATE TABLE traces
(
    id UUID,
    project_id UUID,
    tags String DEFAULT '[]',
    is_deleted UInt8 DEFAULT 0,
    _version UInt64
)
ENGINE = ReplacingMergeTree(_version, is_deleted)
ORDER BY (project_id, id)
"""
_REMAP_DDL = """
CREATE TABLE trace_session_id_remap
(
    old_id UUID,
    new_id UUID,
    version DateTime64(6, 'UTC') DEFAULT now64(6, 'UTC')
)
ENGINE = ReplacingMergeTree(version)
ORDER BY old_id
"""
_SPAN_COLUMNS = (
    "project_id",
    "observation_type",
    "service_name",
    "start_time",
    "trace_id",
    "id",
    "parent_span_id",
    "name",
    "end_time",
    "trace_session_id",
    "status",
    "model",
    "provider",
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "cost",
    "input",
    "output",
    "is_deleted",
    "_version",
)
_UTC_CONTEXT = SimpleNamespace(server_info=SimpleNamespace(get_timezone=lambda: "UTC"))


@pytest.fixture(autouse=True, scope="session")
def _drop_legacy_ch_spans_mvs():
    yield


@pytest.fixture(autouse=True, scope="session")
def _ensure_test_score_tenant_column():
    yield


class _LiveClickHouse:
    def __init__(self, client):
        self._client = client

    def execute(self, sql, params=None):
        rows, columns = self._client.execute(
            sql, params or None, with_column_types=True
        )
        names = [name for name, _type in columns]
        return [dict(zip(names, row, strict=True)) for row in rows]


class _EmbeddedClickHouse:
    """chdb with the driver's own literal escaping; never a socket."""

    def __init__(self, session):
        self._session = session

    def execute(self, sql, params=None):
        if params:
            sql = sql % escape_params(params, _UTC_CONTEXT)
        result = str(self._session.query(sql, "JSONEachRow"))
        return [json.loads(line) for line in result.splitlines() if line]


def _live_clickhouse_named() -> bool:
    """Whether a test ClickHouse is named, by the conftest's own resolution."""
    try:
        _ch_test_native_port()
    except pytest.skip.Exception:
        return False
    return True


@pytest.fixture
def ch(tmp_path):
    # A named test ClickHouse is the only backend when one is configured (CI's
    # sidecar); chdb stands in only when none is, so a misconfigured CI run
    # skips loudly instead of quietly switching engines.
    if _live_clickhouse_named():
        with _ch_test_owned_database("test_openrouter_rollups_") as database:
            client = _open_ch_test_native_client(
                database=database, owned_database=database
            )
            try:
                yield _LiveClickHouse(client)
            finally:
                client.disconnect()
        return
    chdb_session = pytest.importorskip("chdb.session")
    session = chdb_session.Session(str(tmp_path / "chdb"))
    session.query("CREATE DATABASE IF NOT EXISTS rollups")
    session.query("USE rollups")
    session.query("SET output_format_json_quote_64bit_integers = 0")
    try:
        yield _EmbeddedClickHouse(session)
    finally:
        session.close()


def _ts(value: str) -> datetime:
    return datetime.fromisoformat(value).replace(tzinfo=UTC)


def _span_rows(project_id, trace, **overrides):
    rows = []
    for span in trace["spans"]:
        start = _ts(span["start_time"])
        row = {
            "project_id": project_id,
            "observation_type": span["observation_type"],
            "service_name": "openrouter" if trace["shape"] == "openrouter" else "r2",
            "start_time": start,
            "trace_id": trace["trace_id"],
            "id": span["id"],
            "parent_span_id": span["parent_span_id"],
            "name": span["name"],
            "end_time": start + timedelta(milliseconds=span["end_time_offset_ms"]),
            "trace_session_id": span["trace_session_id"],
            "status": span["status"],
            "model": span["model"],
            "provider": span["provider"],
            "prompt_tokens": span["prompt_tokens"],
            "completion_tokens": span["completion_tokens"],
            "total_tokens": span["total_tokens"],
            "cost": span["cost"],
            "input": span["input"],
            "output": span["output"],
            "is_deleted": 0,
            "_version": 1,
        }
        row.update(overrides.get(span["id"], {}))
        rows.append(row)
    return rows


def _insert(ch, rows):
    def literal(column, value):
        if isinstance(value, datetime):
            micros = int(value.timestamp() * 1_000_000)
            return f"fromUnixTimestamp64Micro({micros}, 'UTC')"
        return str(escape_params({"v": value}, _UTC_CONTEXT)["v"])

    values = ", ".join(
        "(" + ", ".join(literal(col, row[col]) for col in _SPAN_COLUMNS) + ")"
        for row in rows
    )
    ch.execute(f"INSERT INTO spans ({', '.join(_SPAN_COLUMNS)}) VALUES {values}")


@pytest.fixture
def stored(ch):
    for ddl in (_SPANS_DDL, _TRACES_DDL, _REMAP_DDL):
        ch.execute(ddl)
    ch.execute("SYSTEM STOP MERGES spans")
    project_id = str(uuid4())
    for trace in FIXTURE["traces"]:
        _insert(ch, _span_rows(project_id, trace))
    return SimpleNamespace(ch=ch, project_id=project_id)


def _time_filter():
    return {
        "column_id": "created_at",
        "filter_config": {
            "col_type": "SYSTEM_METRIC",
            "filter_type": "datetime",
            "filter_op": "between",
            "filter_value": [WINDOW_START.isoformat(), WINDOW_END.isoformat()],
        },
    }


def _root(trace):
    return next(span for span in trace["spans"] if not span["parent_span_id"])


def _root_identity(project_id, trace, *, version=1):
    root = _root(trace)
    start = _ts(root["start_time"])
    start_us = int(start.timestamp() * 1_000_000)
    return (
        project_id,
        trace["trace_id"],
        root["id"],
        start_us,
        root["observation_type"],
        "openrouter" if trace["shape"] == "openrouter" else "r2",
        start_us // 3_600_000_000 * 3_600_000_000,
        version,
    )


def _content_rows(stored, cases, *, include_trace_rollup=True):
    builder = TraceListQueryBuilderV2(
        project_id=stored.project_id, filters=[_time_filter()]
    )
    traces = [TRACES[case] for case in cases]
    sql, params = builder.build_content_query(
        [trace["trace_id"] for trace in traces],
        root_identities=[_root_identity(stored.project_id, t) for t in traces],
        include_trace_rollup=include_trace_rollup,
    )
    return {row["trace_id"]: row for row in stored.ch.execute(sql, params)}


def _list_entry(trace, content):
    """The list row the view builds: root page fields, then content, then rollup."""
    root = _root(trace)
    row = {
        "cost": root["cost"],
        "total_tokens": root["total_tokens"],
        "prompt_tokens": root["prompt_tokens"],
        "completion_tokens": root["completion_tokens"],
        "model": root["model"],
        "provider": root["provider"],
        "input": content["input"],
        "output": content["output"],
    }
    apply_trace_rollup(row, content)
    return row


def _gen(trace):
    return next(span for span in trace["spans"] if span["observation_type"] == "llm")


def test_openrouter_trace_list_rows_read_the_generation_span(stored):
    cases = ["OR1", "OR3b", "OR4-t2", "OR5"]
    content = _content_rows(stored, cases)
    for case in cases:
        trace = TRACES[case]
        gen = _gen(trace)
        entry = _list_entry(trace, content[trace["trace_id"]])
        assert entry["input"] == gen["input"] and entry["input"], case
        assert entry["output"] == gen["output"] and entry["output"], case
        assert entry["model"] == gen["model"] and entry["provider"] == gen["provider"]
        assert entry["total_tokens"] == gen["total_tokens"]
        assert (entry["prompt_tokens"], entry["completion_tokens"]) == (
            gen["prompt_tokens"],
            gen["completion_tokens"],
        )
        # The root and every Provider Attempt carry cost 0, so the sum is the
        # generation's own cost, never a double count.
        assert entry["cost"] == pytest.approx(gen["cost"])
    # OR1 has two Provider Attempt spans (one ERROR); they add nothing.
    assert content[TRACES["OR1"]["trace_id"]]["trace_rollup_cost"] == pytest.approx(
        3.054e-05
    )


def test_privacy_mode_trace_keeps_empty_content_but_shows_usage(stored):
    trace = TRACES["OR7"]
    entry = _list_entry(trace, _content_rows(stored, ["OR7"])[trace["trace_id"]])
    assert (entry["input"], entry["output"]) == ("", "")
    assert entry["total_tokens"] == 50
    assert entry["cost"] == pytest.approx(2.1e-05)
    assert entry["model"] == "openai/gpt-4o-mini"


def test_traceai_roots_with_content_render_as_before(stored):
    cases = ["traceai-r4-agent-run", "traceai-r4-own-cost-llm"]
    content = _content_rows(stored, cases)

    agent = TRACES["traceai-r4-agent-run"]
    entry = _list_entry(agent, content[agent["trace_id"]])
    # The agent root's own content wins over its earlier LLM children.
    assert (entry["input"], entry["output"]) == (
        "r4 regression run",
        "echo: ping 1 via gpt-4o",
    )
    # Usage is the trace total, as the trace detail view shows it.
    assert entry["total_tokens"] == 36
    assert entry["cost"] == pytest.approx(5.85e-06 + 9.75e-05)
    assert entry["model"] == "gpt-4o-mini" and entry["provider"] == "openai"

    single = TRACES["traceai-r4-own-cost-llm"]
    entry = _list_entry(single, content[single["trace_id"]])
    root = _root(single)
    assert entry == {
        "cost": pytest.approx(root["cost"]),
        "total_tokens": root["total_tokens"],
        "prompt_tokens": root["prompt_tokens"],
        "completion_tokens": root["completion_tokens"],
        "model": root["model"],
        "provider": root["provider"],
        "input": root["input"],
        "output": root["output"],
    }


def test_rollup_reads_latest_live_versions_only(stored):
    trace = TRACES["OR4-t1"]
    root, gen, attempt = trace["spans"]
    rows = _span_rows(
        stored.project_id,
        trace,
        **{
            # A newer root version with content takes over the row as before.
            root["id"]: {"input": "edited root input", "output": "", "_version": 2},
            # A newer generation version with no content and no usage.
            gen["id"]: {
                "input": "",
                "output": "",
                "total_tokens": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "cost": 0.0,
                "_version": 2,
            },
            # A tombstoned attempt that once carried cost.
            attempt["id"]: {"cost": 9.0, "is_deleted": 1, "_version": 2},
        },
    )
    _insert(stored.ch, rows)
    builder = TraceListQueryBuilderV2(
        project_id=stored.project_id, filters=[_time_filter()]
    )
    sql, params = builder.build_content_query(
        [trace["trace_id"]],
        root_identities=[_root_identity(stored.project_id, trace, version=2)],
        include_trace_rollup=True,
    )
    (content,) = stored.ch.execute(sql, params)
    assert content["input"] == "edited root input"
    assert content["trace_rollup_cost"] == 0
    assert content["trace_rollup_total_tokens"] == 0
    assert content["first_llm_input"] == "" and content["first_llm_output"] == ""


def test_content_replay_without_rollup_is_unchanged(stored):
    content = _content_rows(stored, ["OR1"], include_trace_rollup=False)
    (row,) = content.values()
    assert not set(TRACE_ROLLUP_COLUMNS) & set(row)
    assert (row["input"], row["output"]) == ("", "")


def _session_page(stored, session_ids):
    builder = SessionListQueryBuilderV2(
        project_id=stored.project_id,
        filters=[_time_filter()],
        page_number=0,
        page_size=25,
    )
    sql, params = builder.build_page_hydration_query(session_ids)
    rows = stored.ch.execute(sql, params)
    return {str(row["session_id"]): row for row in rows}


def test_openrouter_session_row_sums_members_and_reads_generation_messages(stored):
    travel = [TRACES[f"OR4-t{turn}"] for turn in (1, 2, 3)]
    session_id = _root(travel[0])["trace_session_id"]
    row = _session_page(stored, [session_id])[session_id]
    assert int(row["traces_count"]) == 3
    assert row["total_cost"] == pytest.approx(sum(_gen(t)["cost"] for t in travel))
    assert int(row["total_tokens"]) == sum(_gen(t)["total_tokens"] for t in travel)
    assert row["first_message"] == _gen(travel[0])["input"]
    assert row["last_message"] == _gen(travel[2])["input"]


def test_traceai_session_row_keeps_root_messages(stored):
    agent = TRACES["traceai-r4-agent-run"]
    root = _root(agent)
    session_id = root["trace_session_id"]
    row = _session_page(stored, [session_id])[session_id]
    assert row["first_message"] == root["input"]
    assert row["last_message"] == root["input"]
    # Only the root carries this session's id in R2's run, so it alone is a
    # member: the same total the session detail shows.
    assert row["total_cost"] == 0 and int(row["total_tokens"]) == 0


def test_session_page_mixes_both_shapes_without_cross_talk(stored):
    or_session = _root(TRACES["OR4-t1"])["trace_session_id"]
    single_session = _root(TRACES["OR3b"])["trace_session_id"]
    agent_session = _root(TRACES["traceai-r4-agent-run"])["trace_session_id"]
    page = _session_page(stored, [or_session, single_session, agent_session])
    assert set(page) == {or_session, single_session, agent_session}
    assert page[single_session]["first_message"] == _gen(TRACES["OR3b"])["input"]
    assert page[agent_session]["first_message"] == "r4 regression run"


def test_session_history_bubbles_read_the_generation_span(stored):
    travel = [TRACES[f"OR4-t{turn}"] for turn in (1, 2, 3)]
    session_id = _root(travel[0])["trace_session_id"]
    rows = stored.ch.execute(
        *build_session_detail_traces_query(
            stored.project_id, (session_id,), limit=10, offset=0
        )
    )
    assert [(row["input"], row["output"]) for row in rows] == [
        (_gen(t)["input"], _gen(t)["output"]) for t in travel
    ]


def test_session_history_keeps_a_root_with_content(stored):
    root = _root(TRACES["traceai-r4-agent-run"])
    rows = stored.ch.execute(
        *build_session_detail_traces_query(
            stored.project_id, (root["trace_session_id"],), limit=10, offset=0
        )
    )
    assert [(row["input"], row["output"]) for row in rows] == [
        (root["input"], root["output"])
    ]


def _usage_trace(stored, shape, *, cost=5.85e-6, tokens=18):
    """R3 r6's 11+7 Pydantic shape, plus independent-call controls.

    Source: r3-3611-20261026/vm-results/out/ch-dump-r6.json at
    1b2e14baf31a7b9304dbc40e8003621e4d86c921. This is recorded/local-fake
    instrumentation, not a live OpenRouter Broadcast export.
    """
    import copy

    trace = copy.deepcopy(TRACES["OR4-t1"])
    trace["trace_id"] = str(uuid4())
    trace["shape"] = "traceai"
    session = str(uuid4())
    for index, span in enumerate(trace["spans"]):
        span.update(
            id=f"{index + 1:016x}",
            parent_span_id="" if index == 0 else f"{index:016x}",
            name="pydantic_ai.agent.run" if index < 2 else "ChatCompletion",
            observation_type="unknown" if index < 2 else "llm",
            trace_session_id=session,
            total_tokens=tokens,
            prompt_tokens=tokens - 7,
            completion_tokens=7,
            cost=cost,
            input="" if index < 2 else "generation input",
            output="" if index < 2 else "generation output",
        )
    if shape in ("siblings", "independent-root"):
        trace["spans"][0].update(
            name="root content", input="root wins", output="root output"
        )
        for span in trace["spans"][1:]:
            span.update(
                parent_span_id=trace["spans"][0]["id"],
                observation_type="llm",
                name="ChatCompletion",
            )
        if shape == "siblings":
            trace["spans"][0].update(
                cost=0, total_tokens=0, prompt_tokens=0, completion_tokens=0
            )
        else:
            trace["spans"][0]["observation_type"] = "llm"
            trace["spans"] = trace["spans"][:2]
    elif shape == "parent-only":
        for span in trace["spans"][1:]:
            span.update(cost=0, total_tokens=0, prompt_tokens=0, completion_tokens=0)
    _insert(stored.ch, _span_rows(stored.project_id, trace))
    return trace


@pytest.mark.parametrize(
    "shape,total",
    [("nested", 18), ("siblings", 36), ("independent-root", 36), ("parent-only", 18)],
)
def test_usage_accounting_preserves_independent_calls_and_parent_only_usage(
    stored, shape, total
):
    trace = _usage_trace(stored, shape)
    builder = ObserveTraceListQueryBuilderV2(
        project_id=stored.project_id, filters=[_time_filter()]
    )
    (content,) = stored.ch.execute(
        *builder.build_content_query(
            [trace["trace_id"]],
            root_identities=[_root_identity(stored.project_id, trace)],
            include_trace_rollup=True,
        )
    )
    assert content["trace_rollup_total_tokens"] == total
    assert content["trace_rollup_cost"] == pytest.approx(
        5.85e-6 * total / 18, abs=1e-18
    )
    session = trace["spans"][0]["trace_session_id"]
    assert _session_page(stored, [session])[session]["total_tokens"] == total
    (detail,) = stored.ch.execute(
        *build_session_detail_aggregate_query(stored.project_id, (session,))
    )
    assert detail["total_tokens"] == total
    assert detail["total_cost"] == pytest.approx(
        content["trace_rollup_cost"], abs=1e-18
    )
    (history,) = stored.ch.execute(
        *build_session_detail_traces_query(
            stored.project_id, (session,), limit=10, offset=0
        )
    )
    assert history["total_tokens"] == total
    entries = {
        span["id"]: {"observation_span": span, "children": []}
        for span in trace["spans"]
    }
    roots = []
    for span in trace["spans"]:
        if span["parent_span_id"]:
            entries[span["parent_span_id"]]["children"].append(entries[span["id"]])
        else:
            roots.append(entries[span["id"]])
    summary, graph = compute_trace_summary_and_graph(roots)
    assert summary["total_tokens"] == total
    assert summary["total_cost"] == pytest.approx(
        content["trace_rollup_cost"], abs=1e-18
    )
    assert len(graph["nodes"]) == len(trace["spans"])
    if shape in ("siblings", "independent-root"):
        entry = _list_entry(trace, content)
        assert (entry["input"], entry["output"]) == ("root wins", "root output")


def test_observe_usage_predicate_uses_canonical_totals_not_empty_roots(stored):
    eligible = [
        _usage_trace(stored, "siblings", tokens=18 + index) for index in range(13)
    ]
    excluded = _usage_trace(stored, "nested")
    identities = [trace["trace_id"] for trace in [*eligible, excluded]]
    usage_filter = {
        "column_id": "total_tokens",
        "filter_config": {
            "col_type": "SYSTEM_METRIC",
            "filter_type": "number",
            "filter_op": "greater_than",
            "filter_value": 20,
        },
    }
    builder = ObserveTraceListQueryBuilderV2(
        project_id=stored.project_id, filters=[_time_filter(), usage_filter]
    )
    rows = stored.ch.execute(*builder.build_filter_match_query(identities))
    assert {row["trace_id"] for row in rows} == {
        trace["trace_id"] for trace in eligible
    }
    # The real list uses the identity-only classifier, whose single-project
    # outer projection has no project_id. Execute that path, not just the full
    # classifier, to catch invalid membership references at the UI boundary.
    seed_rows = [
        {
            "trace_id": trace["trace_id"],
            "root_span_id": _root(trace)["id"],
            "start_time": _ts(_root(trace)["start_time"]),
        }
        for trace in [*eligible, excluded]
    ]
    identity_rows = stored.ch.execute(
        *builder.build_filter_identity_match_query_from_seed_rows(seed_rows)
    )
    assert {row["trace_id"] for row in identity_rows} == {
        trace["trace_id"] for trace in eligible
    }
    # Legacy/non-Observe callers keep root usage semantics, not this opt-in.
    legacy = TraceListQueryBuilderV2(
        project_id=stored.project_id, filters=[_time_filter(), usage_filter]
    )
    assert stored.ch.execute(*legacy.build_filter_match_query(identities)) == []


@pytest.mark.parametrize("column", ["cost", "total_tokens"])
@pytest.mark.parametrize("direction", ["asc", "desc"])
def test_usage_sort_is_global_stable_and_exact_across_pages(stored, column, direction):
    isolated = SimpleNamespace(ch=stored.ch, project_id=str(uuid4()))
    traces = [
        _usage_trace(isolated, "siblings", tokens=18 + i // 2, cost=3e-8 * (1 + i // 2))
        for i in range(13)
    ]
    _usage_trace(isolated, "nested")
    filters = [
        _time_filter(),
        {
            "column_id": "total_tokens",
            "filter_config": {
                "col_type": "SYSTEM_METRIC",
                "filter_type": "number",
                "filter_op": "greater_than",
                "filter_value": 20,
            },
        },
    ]
    # Equal values deliberately tie: the stable secondary order is trace ID DESC.
    expected = sorted(traces, key=lambda trace: trace["trace_id"], reverse=True)
    expected.sort(
        key=lambda trace: sum(span[column] for span in trace["spans"]),
        reverse=direction == "desc",
    )
    pages = []
    for number in (0, 1):
        builder = ObserveTraceListQueryBuilderV2(
            project_id=isolated.project_id,
            filters=filters,
            page_number=number,
            page_size=10,
        )
        sort = [{"column_id": column, "direction": direction}]
        rows = stored.ch.execute(*builder.build_usage_sorted_query(sort))
        pages.extend(rows)
        assert len(rows) == (10 if number == 0 else 3)
        assert stored.ch.execute(
            *builder.build_usage_sorted_query(sort, count_only=True)
        ) == [{"total": 13}]
        # Sorted rows retain the physical identity needed by content hydration.
        builder.content_root_identities_for_rows(rows)
    assert [row["trace_id"] for row in pages] == [
        trace["trace_id"] for trace in expected
    ]
    assert len({row["trace_id"] for row in pages}) == 13


@pytest.mark.parametrize("cost", [3e-8, 2.37e-5, 0.0])
def test_session_usage_retains_subprecision_cost(stored, cost):
    trace = _usage_trace(stored, "nested", cost=cost)
    session = trace["spans"][0]["trace_session_id"]
    (detail,) = stored.ch.execute(
        *build_session_detail_aggregate_query(stored.project_id, (session,))
    )
    assert detail["total_cost"] == pytest.approx(cost, rel=1e-10, abs=1e-18)


@pytest.mark.parametrize(
    "operator,expected",
    [
        ("between", [0]),
        ("not_between", [1]),
        ("equals", [0]),
        ("not_equals", [1]),
        ("is_null", []),
        ("less_than_or_equal", [0]),
        ("greater_than", [1]),
    ],
)
def test_usage_sort_keeps_half_open_microsecond_time_predicates(
    stored, operator, expected
):
    isolated = SimpleNamespace(ch=stored.ch, project_id=str(uuid4()))
    traces = [_usage_trace(isolated, "nested") for _ in range(2)]
    start = _ts(_root(traces[0])["start_time"]) + timedelta(microseconds=123)
    for index, trace in enumerate(traces):
        rows = _span_rows(isolated.project_id, trace)
        for row in rows:
            row.update(start_time=start + timedelta(microseconds=index), _version=2)
        _insert(stored.ch, rows)
    value = start.isoformat()
    if operator in ("between", "not_between"):
        value = [value, (start + timedelta(microseconds=1)).isoformat()]
    filters = [
        _time_filter(),
        {
            "column_id": "created_at",
            "filter_config": {
                "col_type": "SYSTEM_METRIC",
                "filter_type": "datetime",
                "filter_op": operator,
                "filter_value": value,
            },
        },
    ]
    builder = ObserveTraceListQueryBuilderV2(
        project_id=isolated.project_id, filters=filters
    )
    sort = [{"column_id": "cost", "direction": "asc"}]
    rows = stored.ch.execute(*builder.build_usage_sorted_query(sort))
    assert {row["trace_id"] for row in rows} == {
        traces[index]["trace_id"] for index in expected
    }
    assert stored.ch.execute(
        *builder.build_usage_sorted_query(sort, count_only=True)
    ) == [{"total": len(expected)}]


def test_org_usage_filter_does_not_pool_identical_trace_ids_between_projects(stored):
    first = SimpleNamespace(ch=stored.ch, project_id=str(uuid4()))
    second = SimpleNamespace(ch=stored.ch, project_id=str(uuid4()))
    excluded = _usage_trace(first, "nested")
    included = _usage_trace(second, "siblings")
    included["trace_id"] = excluded["trace_id"]
    _insert(stored.ch, _span_rows(second.project_id, included))
    builder = ObserveTraceListQueryBuilderV2(
        project_ids=[first.project_id, second.project_id],
        filters=[
            _time_filter(),
            {
                "column_id": "total_tokens",
                "filter_config": {
                    "col_type": "SYSTEM_METRIC",
                    "filter_type": "number",
                    "filter_op": "greater_than",
                    "filter_value": 20,
                },
            },
        ],
    )
    rows = stored.ch.execute(*builder.build_filter_match_query([included["trace_id"]]))
    assert [(str(row["project_id"]), row["trace_id"]) for row in rows] == [
        (second.project_id, included["trace_id"])
    ]
