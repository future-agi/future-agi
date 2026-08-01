"""Resource bounds for request-facing span/trace tenant-scope lookups."""

from tracer.services.clickhouse.v2.span_reader import CHSpanReader


class _Result:
    def __init__(self, rows=()):
        self.result_rows = list(rows)


class _RecordingClient:
    def __init__(self, rows=()):
        self.rows = rows
        self.calls = []

    def query(self, sql, parameters=None, settings=None):
        self.calls.append(
            {
                "sql": sql,
                "parameters": parameters or {},
                "settings": settings or {},
            }
        )
        return _Result(self.rows)


def _reader(client):
    reader = CHSpanReader.__new__(CHSpanReader)
    reader._client = client
    return reader


def test_span_scope_projects_prune_before_soft_id_and_merge_read_budget():
    client = _RecordingClient(
        [("span-1", "project-1", "trace-1", "session-1")],
    )
    result = _reader(client).scope_by_ids(
        ["span-1"],
        project_ids=["project-1"],
        settings={"max_execution_time": 0.75, "max_rows_to_read": 1_000_000},
    )

    call = client.calls[0]
    assert "FROM spans FINAL PREWHERE project_id IN %(project_ids)s" in call["sql"]
    assert "WHERE id IN %(ids)s" in call["sql"]
    assert "AS project_id_str" in call["sql"]
    assert "AS project_id," not in call["sql"]
    assert call["parameters"]["project_ids"] == ("project-1",)
    assert call["settings"]["use_skip_indexes_if_final"] == 1
    assert call["settings"]["max_execution_time"] == 0.75
    assert result["span-1"].project_id == "project-1"
    assert result["span-1"].trace_id == "trace-1"
    assert result["span-1"].trace_session_id == "session-1"


def test_span_scope_explicit_empty_project_scope_fails_closed_without_query():
    client = _RecordingClient()

    result = _reader(client).scope_by_ids(["span-1"], project_ids=[])

    assert result == {}
    assert client.calls == []


def test_trace_project_lookup_uses_compact_traces_store_and_project_prefix():
    client = _RecordingClient(
        [("trace-1", "project-1")],
    )
    result = _reader(client).trace_projects_by_ids(
        ["trace-1"],
        project_ids=["project-1"],
        settings={"max_execution_time": 0.75},
    )

    call = client.calls[0]
    assert "FROM traces FINAL" in call["sql"]
    assert "FROM spans" not in call["sql"]
    assert "PREWHERE project_id IN %(project_ids)s" in call["sql"]
    assert "WHERE id IN %(trace_ids)s" in call["sql"]
    assert call["settings"]["use_skip_indexes_if_final"] == 1
    assert call["settings"]["max_execution_time"] == 0.75
    assert result == {"trace-1": "project-1"}


def test_trace_project_lookup_empty_project_scope_fails_closed():
    client = _RecordingClient()

    result = _reader(client).trace_projects_by_ids(
        ["trace-1"],
        project_ids=[],
    )

    assert result == {}
    assert client.calls == []


def test_span_get_merges_request_budget_with_final_skip_index_setting():
    client = _RecordingClient()

    _reader(client).get(
        "span-1",
        project_id="project-1",
        settings={"max_execution_time": 0.75, "max_bytes_to_read": 268_435_456},
    )

    call = client.calls[0]
    assert "FROM spans FINAL PREWHERE project_id = %(pid)s" in call["sql"]
    assert "WHERE id = %(span_id)s" in call["sql"]
    assert call["settings"]["use_skip_indexes_if_final"] == 1
    assert call["settings"]["max_execution_time"] == 0.75
    assert call["settings"]["max_bytes_to_read"] == 268_435_456
