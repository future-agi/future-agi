"""Contract tests for the generic ClickHouse streaming read helper."""

from tracer.services.clickhouse.v2.span_reader import CHSpanReader


class _BlockStream:
    def __init__(self, blocks):
        self._blocks = blocks

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def __iter__(self):
        return iter(self._blocks)


class _RecordingClient:
    def __init__(self, blocks):
        self._blocks = blocks
        self.call = None

    def query_row_block_stream(self, sql, *, parameters, settings):
        self.call = {
            "sql": sql,
            "parameters": parameters,
            "settings": settings,
        }
        return _BlockStream(self._blocks)


def test_stream_query_forwards_settings_and_rechunks_rows():
    client = _RecordingClient([[(1,), (2,), (3,)], [(4,)]])
    reader = CHSpanReader.__new__(CHSpanReader)
    reader._client = client

    batches = list(
        reader.stream_query(
            "SELECT id FROM spans WHERE project_id = %(project_id)s",
            {"project_id": "project-1"},
            batch_size=3,
            settings={"max_execution_time": 10, "max_threads": 2},
        )
    )

    assert batches == [["1", "2", "3"], ["4"]]
    assert client.call == {
        "sql": "SELECT id FROM spans WHERE project_id = %(project_id)s",
        "parameters": {"project_id": "project-1"},
        "settings": {"max_execution_time": 10, "max_threads": 2},
    }
