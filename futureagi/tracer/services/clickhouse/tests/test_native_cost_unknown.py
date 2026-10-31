"""Unknown native prices must not become verified zero at the read boundary."""

from types import SimpleNamespace

from tracer.services.clickhouse.v2.span_reader import (
    _DATA_KEYS,
    CHSpanReader,
    _row_to_chspan,
)


class CostClient:
    def __init__(self, row):
        self.row = row
        self.sql = ""

    def query(self, sql, **kwargs):
        self.sql = sql
        return SimpleNamespace(result_rows=[self.row])


def test_native_cost_unknown_at_span_read_boundary():
    fields = dict.fromkeys(_DATA_KEYS)
    fields.update(cost=0.0, attrs_string={"fi.cost.kind": "unknown"})
    assert _row_to_chspan(tuple(fields[k] for k in _DATA_KEYS)).cost is None
    fields["attrs_string"] = {"fi.cost.kind": "provider_reported"}
    assert _row_to_chspan(tuple(fields[k] for k in _DATA_KEYS)).cost == 0.0


def test_native_cost_unknown_in_trace_and_session_aggregates():
    cases = (
        ("trace_aggregate", ("trace",), (2, 100, 30, 130, None, None)),
        ("session_aggregate", ("session",), (2, 100, 30, 130, None, None, None)),
        ("aggregate_by_trace_ids", (["trace"],), (2, 100, 30, 130, None)),
    )
    for method, args, row in cases:
        client = CostClient(row)
        reader = CHSpanReader.__new__(CHSpanReader)
        reader._client = client
        result = getattr(reader, method)(*args)
        assert result["cost"] is None, method
        assert "fi.cost.kind" in client.sql and "countIf" in client.sql, method


def test_native_cost_unknown_mixed_trace_is_not_partial_total():
    client = CostClient(("trace", 2, 100, 30, 130, None, None, None, 40))
    reader = CHSpanReader.__new__(CHSpanReader)
    reader._client = client
    result = reader.per_trace_aggregate(["trace"])["trace"]
    assert result["cost"] is None
    assert "fi.cost.kind" in client.sql and "countIf" in client.sql
