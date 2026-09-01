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


def test_stream_query_normalizes_settings_and_rechunks_rows():
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
        "settings": {
            "max_execution_time": 9.5,
            "max_threads": 2,
            "max_memory_usage": 36 * 1024 * 1024 * 1024,
            "max_bytes_to_read": 36 * 1024 * 1024 * 1024,
            "max_result_rows": 1_000_000,
            "max_result_bytes": 512 * 1024 * 1024,
            "readonly": 2,
            "read_overflow_mode": "throw",
            "timeout_overflow_mode": "throw",
            "result_overflow_mode": "throw",
        },
    }


def test_stream_query_removes_row_read_limit_and_preserves_tighter_memory_caps():
    client = _RecordingClient([])
    reader = CHSpanReader.__new__(CHSpanReader)
    reader._client = client

    assert (
        list(
            reader.stream_query(
                "SELECT id FROM spans",
                settings={
                    "max_rows_to_read": 1,
                    "max_bytes_to_read": 2 * 1024 * 1024 * 1024,
                    "max_memory_usage": 2 * 1024 * 1024 * 1024,
                },
            )
        )
        == []
    )

    assert client.call["settings"] == {
        "max_execution_time": 9.5,
        "max_memory_usage": 2 * 1024 * 1024 * 1024,
        "max_bytes_to_read": 2 * 1024 * 1024 * 1024,
        "max_threads": 4,
        "max_result_rows": 1_000_000,
        "max_result_bytes": 512 * 1024 * 1024,
        "readonly": 2,
        "read_overflow_mode": "throw",
        "timeout_overflow_mode": "throw",
        "result_overflow_mode": "throw",
    }
