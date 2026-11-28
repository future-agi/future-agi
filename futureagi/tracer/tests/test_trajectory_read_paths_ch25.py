"""Read paths over the trajectory store: the pair-exact items query (RD-5)
and the missing-content policy on each surface (RD-4).

The live tests use a private ClickHouse 25.3 database with the full v2
schema and the Go contract fixtures. A span whose items were never written
is a copy of a fixture span under a project that has no items.
"""

from __future__ import annotations

import json
import os
import uuid
from contextlib import ExitStack
from datetime import UTC, datetime
from inspect import unwrap
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from django.test import override_settings

from conftest import (
    _ch_test_apply_v2_schema,
    _ch_test_native_client,
    _ch_test_owned_database,
    _open_ch_test_http_client,
)
from tracer.services.clickhouse.v2 import (
    span_reader,
    trace_detail_reads,
    trajectory_hydration,
)
from tracer.services.clickhouse.v2.trajectory_hydration import (
    CONTENT_INLINE,
    CONTENT_MISSING,
    CONTENT_STATE_KEY,
    CONTENT_UNAVAILABLE_KEY,
    CSV_UNAVAILABLE_PREFIX,
    ITEMS_QUERY_LOG_COMMENT,
    TrajectoryHydrationUnavailable,
    TrajectoryReadState,
    analytics_read_state_probe,
    build_trajectory_items_query,
    client_read_state_probe,
    content_unavailable_sides,
    fetch_trajectory_items_dict_rows,
    fetch_trajectory_items_tuple_rows,
    hydrate_span_dicts,
    mark_csv_unavailable_previews,
    pinned_read_state,
)

CONTRACT_DIR = Path(__file__).parent / "fixtures" / "trajectory_contract"
OLD = "2026-11-01 00:00:00.000000"


def _jsonl(name: str) -> list[dict]:
    with (CONTRACT_DIR / name).open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


PAYLOADS = _jsonl("payloads.jsonl")
SPANS = {row["id"]: row for row in _jsonl("spans.jsonl")}
PROJECT_ID = PAYLOADS[0]["project_id"]
# A case whose stored preview is shorter than the content, so a preview can
# never pass for the full input.
CASE = next(
    p
    for p in PAYLOADS
    if p["input_extracted"] and len(SPANS[p["span_id"]]["input"]) < len(p["input"])
)


@pytest.fixture(autouse=True)
def _fresh_state_cache():
    trajectory_hydration.reset_read_state_cache()
    yield
    trajectory_hydration.reset_read_state_cache()


class _NativeAnalytics:
    """The analytics executor shape, on a native client, probing its own
    database."""

    def __init__(self, client, database):
        self._client = client
        self.queries: list[tuple[str, dict]] = []
        self.trajectory_read_probe = analytics_read_state_probe(
            self, endpoint=f"native:{database}"
        )

    def execute_ch_query(self, query, params, *, timeout_ms, settings):
        self.queries.append((query, dict(settings or {})))
        rows, columns = self._client.execute(
            query,
            params,
            settings={**settings, "max_execution_time": max(timeout_ms / 1000, 5.0)},
            with_column_types=True,
        )
        names = [name for name, _type in columns]
        return SimpleNamespace(
            data=[dict(zip(names, row, strict=True)) for row in rows], complete=True
        )


@pytest.fixture(scope="module")
def ch():
    with ExitStack() as stack:
        try:
            database = stack.enter_context(_ch_test_owned_database("test_traj_paths_"))
            _ch_test_apply_v2_schema(database)
            http = _open_ch_test_http_client(database=database)
            stack.callback(http.close)
            native = stack.enter_context(_ch_test_native_client(database=database))
        except pytest.skip.Exception as exc:
            if os.environ.get("GITHUB_ACTIONS") == "true":
                pytest.fail(f"the read-path tests must run in CI: {exc}", pytrace=False)
            raise
        for table, name in (
            ("trajectory_items", "items.jsonl"),
            ("spans", "spans.jsonl"),
        ):
            http.raw_insert(
                table,
                insert_block=(CONTRACT_DIR / name).read_bytes(),
                fmt="JSONEachRow",
            )
        assert native.execute("SELECT version()")[0][0].startswith("25.3.")
        yield database, http, native


def _reader(http, database):
    reader = span_reader.CHSpanReader.__new__(span_reader.CHSpanReader)
    reader._client = http
    reader._read_state_probe = client_read_state_probe(http, endpoint=database)
    return reader


def _orphan_copy(http, *, created_at: str | None) -> tuple[str, dict]:
    """A copy of the CASE span under a new project that has no items: its
    pointers reference items that were never written."""
    project_id = str(uuid.uuid4())
    row = {**SPANS[CASE["span_id"]], "project_id": project_id}
    if created_at is not None:
        row["created_at"] = created_at
    http.raw_insert(
        "spans",
        insert_block=(json.dumps(row) + "\n").encode("utf-8"),
        fmt="JSONEachRow",
    )
    return project_id, row


def _seed_items(http, project_id: str, cells: list[tuple[str, str, str]]) -> None:
    http.insert(
        "trajectory_items",
        [
            [uuid.UUID(project_id), scope, item_hash, content]
            for scope, item_hash, content in cells
        ],
        column_names=["project_id", "scope_id", "item_hash", "content"],
    )


S1, S2 = "11111111-aaaa-4aaa-8aaa-111111111111", "22222222-bbbb-4bbb-8bbb-222222222222"
H1, H2 = "1" * 32, "2" * 32
CROSS = [(S1, H1, "s1h1"), (S1, H2, "s1h2"), (S2, H1, "s2h1"), (S2, H2, "s2h2")]


# --------------------------------------------------------------------------- #
# RD-5: pair-exact items query on both executors
# --------------------------------------------------------------------------- #
@pytest.mark.integration
def test_items_query_returns_only_requested_pairs_clickhouse_connect(ch):
    _database, http, _native = ch
    project_id = str(uuid.uuid4())
    _seed_items(http, project_id, CROSS)
    wanted = [(project_id, S1, H1), (project_id, S2, H2)]

    def query(sql, params):
        return http.query(
            sql,
            parameters=params,
            settings=trajectory_hydration.items_query_settings(params),
        ).result_rows

    resolved = fetch_trajectory_items_tuple_rows(query, wanted)
    assert resolved == {
        (project_id, S1, H1): "s1h1",
        (project_id, S2, H2): "s2h2",
    }
    # The statement itself returns two rows, not the four of scopes x hashes.
    sql, params = build_trajectory_items_query(
        project_id=project_id, pairs=[(S1, H1), (S2, H2)]
    )
    assert len(query(sql, params)) == 2
    # One pair binds as a one-member set.
    sql, params = build_trajectory_items_query(project_id=project_id, pairs=[(S2, H1)])
    assert [tuple(r[:1]) + (r[2],) for r in query(sql, params)] == [(S2, "s2h1")]


@pytest.mark.integration
def test_items_query_returns_only_requested_pairs_analytics_driver(ch):
    database, http, native = ch
    project_id = str(uuid.uuid4())
    _seed_items(http, project_id, CROSS)
    analytics = _NativeAnalytics(native, database)

    def query(sql, params):
        return analytics.execute_ch_query(
            sql, params, timeout_ms=10_000, settings={"max_threads": 1}
        ).data

    resolved = fetch_trajectory_items_dict_rows(
        query, [(project_id, S1, H2), (project_id, S2, H1)]
    )
    assert resolved == {
        (project_id, S1, H2): "s1h2",
        (project_id, S2, H1): "s2h1",
    }
    sql, params = build_trajectory_items_query(project_id=project_id, pairs=[(S1, H2)])
    assert [r["content"] for r in query(sql, params)] == ["s1h2"]


@pytest.mark.integration
@override_settings(FI_TRAJECTORY_STORE_READ_ENABLED=None)
def test_span_reader_items_query_carries_timeout_and_row_cap(ch):
    database, http, native = ch
    reader = _reader(http, database)
    (span,) = reader.list_by_trace(CASE["trace_id"], project_id=PROJECT_ID)
    assert span.input == CASE["input"]
    refs = len(
        {
            *list(span.input_item_hashes),
            *list(span.output_item_hashes),
            *list(span.tool_def_hashes),
        }
    )
    native.execute("SYSTEM FLUSH LOGS")
    rows = native.execute(
        "SELECT Settings['max_result_rows'], Settings['max_execution_time'], "
        "Settings['result_overflow_mode'], Settings['max_threads'] "
        "FROM system.query_log "
        "WHERE type = 'QueryFinish' AND log_comment = %(comment)s "
        "AND current_database = %(db)s AND position(query, %(pid)s) > 0 "
        "ORDER BY event_time_microseconds",
        {"comment": ITEMS_QUERY_LOG_COMMENT, "db": database, "pid": PROJECT_ID},
    )
    assert rows, "the items query is tagged for system.query_log"
    max_rows, max_time, overflow, threads = rows[-1]
    # A span's refs may repeat a hash (and include the p: system prompt): the
    # cap is the distinct pairs + 1, never more than refs + 1.
    assert 2 <= int(max_rows) <= refs + 1
    assert float(max_time) == 10
    assert overflow == "throw"
    assert threads == "1"


# --------------------------------------------------------------------------- #
# RD-4: missing items on each surface
# --------------------------------------------------------------------------- #
@pytest.mark.integration
@override_settings(FI_TRAJECTORY_STORE_READ_ENABLED=None)
def test_missing_item_trace_detail_returns_content_unavailable(ch):
    database, http, native = ch
    project_id, _row = _orphan_copy(http, created_at=OLD)
    detail = trace_detail_reads.read_trace_detail(
        analytics=_NativeAnalytics(native, database),
        project_ids=[project_id],
        trace_id=CASE["trace_id"],
        include_annotations=False,
    )
    (span,) = detail.spans
    # The preview is kept for display and marked; it is not the content.
    assert span["input"] != CASE["input"]
    assert span[CONTENT_STATE_KEY]["input"] == CONTENT_MISSING
    assert "input" in content_unavailable_sides(span)
    attrs = json.loads(span["span_attributes"] or "{}")
    assert not any(k.startswith("__fi_promoted_") for k in attrs)
    assert "input.value" not in attrs  # never rebuilt from the preview


@pytest.mark.integration
@override_settings(FI_TRAJECTORY_STORE_READ_ENABLED=None)
def test_missing_item_eval_loader_raises_span_content_unavailable(ch):
    from tracer.services.clickhouse.v2.eval_loader import (
        SpanContentUnavailable,
        _construct_from_chspan,
    )

    database, http, _native = ch
    project_id, _row = _orphan_copy(http, created_at=OLD)
    span = _reader(http, database).get(CASE["span_id"], project_id=project_id)
    assert span.content_state[0] == CONTENT_MISSING
    with pytest.raises(SpanContentUnavailable) as exc:
        _construct_from_chspan(span)
    assert "input" in exc.value.sides


@pytest.mark.integration
@override_settings(FI_TRAJECTORY_STORE_READ_ENABLED=None)
def test_missing_item_on_fresh_span_is_retryable_unavailable_live(ch):
    """A span written just now whose items this replica does not show yet is
    retried, never recorded as lost content."""
    database, http, _native = ch
    project_id, _row = _orphan_copy(http, created_at=None)  # created_at = now
    with pytest.raises(TrajectoryHydrationUnavailable) as exc:
        _reader(http, database).get(CASE["span_id"], project_id=project_id)
    assert exc.value.code == "items_not_yet_visible"


def _run(analytics, sql, params):
    return analytics.execute_ch_query(sql, params, timeout_ms=10_000, settings={}).data


def _csv_hydrate(analytics, rows, *, project_id, surface):
    """The CSV branch of the trace/span list views: hydrate, then mark."""
    hydrate_span_dicts(
        rows,
        lambda sql, params: list(_run(analytics, sql, params) or []),
        project_id_fallback=project_id,
        attr_key="attributes_extra",
        state=TrajectoryReadState.READY,
        surface=surface,
    )
    mark_csv_unavailable_previews(rows)
    return rows


@pytest.mark.integration
@override_settings(FI_TRAJECTORY_STORE_READ_ENABLED=None)
def test_missing_item_csv_cell_marked(ch):
    """The span CSV's real content query: an old span with a lost item gets a
    marked preview cell; a span written just now (replica lag) is retried,
    never written as a preview. Before, the query did not project
    created_at, so lag became a permanent marked cell."""
    from tracer.services.clickhouse.v2.query_builders.span_list import (
        SpanListQueryBuilderV2,
    )

    database, http, native = ch
    analytics = _NativeAnalytics(native, database)
    with pinned_read_state(TrajectoryReadState.READY):
        for created_at in (OLD, None):
            project_id, row = _orphan_copy(http, created_at=created_at)
            sql, params = SpanListQueryBuilderV2(
                project_id=project_id
            ).build_content_query([row["id"]], include_trajectory=True)
            rows = _run(analytics, sql, params)
            assert len(rows) == 1 and "created_at" in rows[0]
            if created_at is None:
                with pytest.raises(TrajectoryHydrationUnavailable) as exc:
                    _csv_hydrate(
                        analytics, rows, project_id=project_id, surface="span_csv"
                    )
                assert exc.value.code == "items_not_yet_visible"
                continue
            (cell,) = _csv_hydrate(
                analytics, rows, project_id=project_id, surface="span_csv"
            )
            assert cell["input"].startswith(CSV_UNAVAILABLE_PREFIX)
            assert cell["input"] != CASE["input"]


@pytest.mark.integration
@override_settings(FI_TRAJECTORY_STORE_READ_ENABLED=None)
def test_fresh_missing_item_is_retryable_on_session_detail_and_projections(ch):
    """Session detail and the reader's column projections (list_by_ids,
    list_root_spans_by_trace_ids) project created_at with the pointers, so a
    span written just now whose items are not visible is retried (503 /
    activity retry), and an old one is marked missing."""
    from tracer.services.clickhouse.v2.query_builders.session_detail import (
        build_session_detail_traces_query,
    )

    database, http, native = ch
    analytics = _NativeAnalytics(native, database)
    reader = _reader(http, database)
    for created_at in (OLD, None):
        project_id, row = _orphan_copy(http, created_at=created_at)
        fresh = created_at is None
        with pinned_read_state(TrajectoryReadState.READY):
            sql, params = build_session_detail_traces_query(
                project_id, [row["trace_session_id"]], limit=10, offset=0
            )
            traces = _run(analytics, sql, params)
            assert len(traces) == 1 and "created_at" in traces[0]

            def hydrate_session(rows=traces, pid=project_id):
                return hydrate_span_dicts(
                    rows,
                    lambda s, p: list(_run(analytics, s, p) or []),
                    project_id_fallback=pid,
                    state=TrajectoryReadState.READY,
                    surface="session_detail",
                )

            if fresh:
                with pytest.raises(TrajectoryHydrationUnavailable):
                    hydrate_session()
            else:
                hydrate_session()
                assert traces[0][CONTENT_STATE_KEY]["input"] == CONTENT_MISSING

        calls = (
            lambda pid=project_id, sid=row["id"]: reader.list_by_ids(
                [sid], project_id=pid, columns=["id", "input"]
            ),
            lambda pid=project_id, tid=row["trace_id"]: list(
                reader.list_root_spans_by_trace_ids(
                    [tid], project_id=pid, columns=["trace_id", "input"]
                ).values()
            ),
        )
        for call in calls:
            if fresh:
                with pytest.raises(TrajectoryHydrationUnavailable) as exc:
                    call()
                assert exc.value.code == "items_not_yet_visible"
            else:
                (projected,) = call()
                assert projected[CONTENT_UNAVAILABLE_KEY] == ["input"]
                assert "created_at" not in projected


@pytest.mark.integration
@override_settings(FI_TRAJECTORY_STORE_READ_ENABLED=None)
def test_same_version_items_v1_and_inline_twins_resolve_consistently(ch):
    """Two physical rows of one span with an equal _version, an items_v1 row
    and its inline copy: the child tree and Insights evidence take content
    and pointers from ONE row, so the span reads as its full content either
    way, never as the preview labelled inline."""
    from ee.agenthub.insights import evidence

    database, http, native = ch
    project_id = str(uuid.uuid4())
    items = [{**item, "project_id": project_id} for item in _jsonl("items.jsonl")]
    http.raw_insert(
        "trajectory_items",
        insert_block="".join(json.dumps(i) + "\n" for i in items).encode("utf-8"),
        fmt="JSONEachRow",
    )
    pointer = {**SPANS[CASE["span_id"]], "project_id": project_id, "created_at": OLD}
    inline = {
        **pointer,
        "input": CASE["input"],
        "output": CASE["output"],
        "input_item_hashes": [],
        "output_item_hashes": [],
        "tool_def_hashes": [],
        "item_scope_id": "",
        "payload_encoding": "inline",
        "payload_envelope": "",
        "attributes_extra": "{}",
    }
    # One part holding both twins, in each order, for two span ids.
    twins = [
        {**pointer, "id": "twin-pointer-first"},
        {**inline, "id": "twin-pointer-first"},
        {**inline, "id": "twin-inline-first"},
        {**pointer, "id": "twin-inline-first"},
    ]
    for block in (twins, list(reversed(twins))):
        http.raw_insert(
            "spans",
            insert_block="".join(json.dumps(r) + "\n" for r in block).encode("utf-8"),
            fmt="JSONEachRow",
        )

    reader = _reader(http, database)
    tree = reader.child_tree_spans_by_trace_ids(
        [CASE["trace_id"]], project_id=project_id
    )
    by_id = {d["id"]: d for d in tree[CASE["trace_id"]]}
    for span_id in ("twin-pointer-first", "twin-inline-first"):
        assert by_id[span_id]["input"] == CASE["input"], span_id
        assert content_unavailable_sides(by_id[span_id]) == []

    class _EvidenceReader:
        def rows(self, statement, *, max_rows):
            query, params = statement
            return _run(_NativeAnalytics(native, database), query, params)

    start = datetime.fromisoformat(pointer["start_time"]).replace(tzinfo=UTC)
    nodes = [
        evidence._Node(
            trace_id=CASE["trace_id"],
            span_id=span_id,
            start=start,
            parent_id="",
            kind="llm",
            status="OK",
            version=1,
            raw_chars=len(CASE["input"]) + len(CASE["output"]),
            order=i,
        )
        for i, span_id in enumerate(("twin-pointer-first", "twin-inline-first"))
    ]
    with pinned_read_state(TrajectoryReadState.READY):
        content = evidence._read_content(
            _EvidenceReader(),
            builder=evidence.EvidenceQueryBuilder(project_id=project_id),
            kept=nodes,
            full={id(n) for n in nodes},
            status_only=set(),
            partial={},
            limits=evidence.EvidenceLimits(),
        )
    assert len(content) == 2
    for row in content.values():
        assert row["input"] != pointer["input"], "a preview read as content"
        assert content_unavailable_sides(row) == []


@pytest.mark.unit
def test_child_tree_and_evidence_read_one_packed_argmax(settings):
    """SQL shape of the equal-_version fix: with pointers, one argMax over a
    tuple and no per-column argMax of content or pointers; flag-off SQL
    keeps the per-column projections."""
    from ee.agenthub.insights import evidence

    sent: list[str] = []

    class _Client:
        def query(self, sql, parameters=None, settings=None):
            sent.append(sql)
            return SimpleNamespace(result_rows=[])

    reader = span_reader.CHSpanReader.__new__(span_reader.CHSpanReader)
    reader._client = _Client()
    for state in (TrajectoryReadState.NOT_READY, TrajectoryReadState.READY):
        with pinned_read_state(state):
            reader.child_tree_spans_by_trace_ids(["t"], project_id="p")
    off, on = sent
    assert "argMax(input, _version) AS input" in off
    assert "latest_child_row" not in off
    assert "AS latest_child_row" in on
    for col in ("input", "output", "attributes_extra", "payload_encoding"):
        assert f"argMax({col}, _version)" not in on

    assert "argMax(" not in evidence._CONTENT_HEAVY_TRAJ_INNER.replace(
        "argMax(\n                            tuple(", "", 1
    )
    assert "AS latest_content_tuple" in evidence._CONTENT_HEAVY_TRAJ_INNER
    for name in (
        "latest_input",
        "latest_output",
        "latest_attributes_extra",
        "latest_payload_encoding",
        "latest_created_at",
    ):
        assert f"AS {name}" in evidence._CONTENT_HEAVY_TRAJ_INNER
        assert f"AS {name}" in evidence._CONTENT_HEAVY_INNER or name not in (
            "latest_input",
            "latest_output",
            "latest_attributes_extra",
        )


@pytest.mark.unit
def test_missing_item_insights_evidence_fails_closed():
    from ee.agenthub.insights import evidence

    trace_id, span_id = "t-1", "s-1"
    shape = {"g": ["[", "]"], "r": "m", "il": 3, "x": "0" * 32}
    content_row = {
        "trace_id": trace_id,
        "id": span_id,
        "input": "preview",
        "input_chars": 7,
        "output": "",
        "output_chars": 0,
        "project_id": "p",
        "input_item_hashes": ["a" * 32],
        "output_item_hashes": [],
        "tool_def_hashes": [],
        "item_scope_id": "scope",
        "payload_encoding": "items_v1",
        "payload_envelope": json.dumps({"v": 2, "in": shape}),
        "created_at": datetime(2026, 11, 1, tzinfo=UTC),
    }

    class _Reader:
        def __init__(self, items):
            self.items = items

        def rows(self, statement, *, max_rows):
            query, _params = statement
            if "FROM trajectory_items" in query:
                if isinstance(self.items, Exception):
                    raise self.items
                return self.items
            return [dict(content_row)]

    node = evidence._Node(
        trace_id=trace_id,
        span_id=span_id,
        start=datetime(2026, 11, 1, tzinfo=UTC),
        parent_id="",
        kind="llm",
        status="OK",
        version=1,
        raw_chars=10,
        order=0,
    )
    args = {
        "builder": evidence.EvidenceQueryBuilder(
            project_id="11111111-1111-4111-8111-111111111111"
        ),
        "kept": [node],
        "full": {id(node)},
        "status_only": set(),
        "partial": {},
        "limits": evidence.EvidenceLimits(),
    }
    with pinned_read_state(TrajectoryReadState.READY):
        with pytest.raises(evidence.EvidenceUnavailable) as exc:
            evidence._read_content(_Reader([]), **args)
        assert exc.value.code == "trajectory_items_missing"
        with pytest.raises(evidence.EvidenceUnavailable) as exc:
            evidence._read_content(_Reader(ConnectionError("down")), **args)
        assert exc.value.code == "trajectory_hydration_unavailable"


def _span_detail_request():
    return SimpleNamespace(
        organization=SimpleNamespace(id="ORG1"),
        workspace=None,
        user=SimpleNamespace(organization=SimpleNamespace(id="ORG1")),
    )


def _span_row(**extra):
    row = {
        "id": "S1",
        "trace_id": "T1",
        "project_id": "P1",
        "parent_span_id": None,
        "name": "llm",
        "observation_type": "llm",
        "start_time": datetime(2026, 1, 1, tzinfo=UTC),
        "end_time": datetime(2026, 1, 1, 0, 0, 1, tzinfo=UTC),
        "input": '"preview"',
        "output": '"out"',
        "model": "gpt-4",
        "latency_ms": 1,
        "prompt_tokens": 1,
        "completion_tokens": 1,
        "total_tokens": 2,
        "cost": 0.0,
        "status": "OK",
        "status_message": None,
        "tags": "[]",
        "span_events": "[]",
        "provider": "openai",
        "span_attributes": '{"k": "v"}',
        "project_version_id": None,
        "custom_eval_config_id": None,
        "trace_session_id": None,
        "metadata_json": "{}",
        "attrs_string": {},
        "attrs_number": {},
        "attrs_bool": {},
    }
    row.update(extra)
    return row


def _retrieve_span(read_span_detail):
    from tracer.models.observation_span import ObservationSpan
    from tracer.models.project import Project
    from tracer.views.observation_span import ObservationSpanView

    project_manager = MagicMock()
    project_manager.filter.return_value.values_list.return_value.__getitem__.return_value = [
        "P1"
    ]
    with (
        patch.object(Project, "no_workspace_objects", project_manager),
        patch("tracer.views.observation_span.V2AnalyticsQueryService"),
        patch(
            "tracer.services.clickhouse.v2.trace_detail_reads.read_span_detail",
            side_effect=read_span_detail,
        ),
        patch.object(
            ObservationSpan.objects,
            "get",
            side_effect=AssertionError("Postgres span lookup is forbidden"),
        ),
    ):
        return unwrap(ObservationSpanView.retrieve)(
            ObservationSpanView(), _span_detail_request(), pk="S1"
        )


@pytest.mark.unit
def test_views_return_503_on_hydration_failure():
    def failing(**_kwargs):
        raise TrajectoryHydrationUnavailable("items_query_failed")

    response = _retrieve_span(failing)
    assert response.status_code == 503
    assert "service_unavailable" in json.dumps(response.data, default=str)


@pytest.mark.unit
def test_session_detail_returns_503_on_hydration_failure():
    from tracer.views.trace_session import TraceSessionView

    view = TraceSessionView()
    view.kwargs = {"pk": "S1"}
    request = SimpleNamespace(validated_query_data={}, query_params={})
    with (
        patch.object(TraceSessionView, "get_queryset") as queryset,
        patch("tracer.views.trace_session.V2AnalyticsQueryService"),
        patch.object(
            TraceSessionView,
            "_retrieve_clickhouse",
            side_effect=TrajectoryHydrationUnavailable("items_not_yet_visible"),
        ),
    ):
        queryset.return_value.get.return_value = SimpleNamespace(
            project=SimpleNamespace(id="P1")
        )
        response = unwrap(TraceSessionView.retrieve)(view, request)
    assert response.status_code == 503
    assert "service_unavailable" in json.dumps(response.data, default=str)


@pytest.mark.unit
def test_span_detail_marks_content_unavailable_and_keeps_preview():
    row = _span_row(
        **{CONTENT_STATE_KEY: {"input": CONTENT_MISSING, "output": CONTENT_INLINE}}
    )
    detail = trace_detail_reads.TraceDetailRead(
        project_id="P1",
        spans=(row,),
        eval_config_ids=(),
        evals=(),
        annotations=(),
        query_count=3,
        elapsed_ms=0.0,
    )
    response = _retrieve_span(lambda **_kw: detail)
    assert response.status_code == 200
    span = response.data["result"]["observation_span"]
    assert span[CONTENT_UNAVAILABLE_KEY] == ["input"]
    assert span["input"] == "preview"

    inline = trace_detail_reads.TraceDetailRead(
        project_id="P1",
        spans=(_span_row(),),
        eval_config_ids=(),
        evals=(),
        annotations=(),
        query_count=3,
        elapsed_ms=0.0,
    )
    response = _retrieve_span(lambda **_kw: inline)
    # Inline rows: the response is unchanged (no new key).
    assert CONTENT_UNAVAILABLE_KEY not in response.data["result"]["observation_span"]
