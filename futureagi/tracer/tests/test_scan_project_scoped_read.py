"""Project-scoped span reads for the trace-scanner + feed pipelines.

The spans table's primary-key prefix starts with ``project_id``; ``trace_id`` is
below it in the sorting key and absent from the primary key. A ``trace_id IN``
read with no ``project_id`` therefore cannot prune parts, so a FINAL merge spans
the whole table and blows the ClickHouse memory limit. These tests pin the
project scoping onto the reads that hold a project_id, so a revert that drops the
filter fails loudly here rather than as an out-of-memory crash in production.
"""

from unittest.mock import patch

from tracer.queries.feed import _project_cluster_inputs_corpus
from tracer.queries.scan_clustering import get_trace_input_data
from tracer.queries.trace_scanner import fetch_trace_data
from tracer.services.clickhouse.v2.span_reader import CHSpanReader


class _RecordingClient:
    """Captures the SQL + params of the last query; returns no rows."""

    def __init__(self):
        self.sql = None
        self.params = None
        self.settings = None

    def query(self, sql, parameters=None, settings=None):
        self.sql = sql
        self.params = parameters or {}
        self.settings = settings or {}

        class _Result:
            result_rows = []

        return _Result()


def _reader_with(client) -> CHSpanReader:
    # Bypass __init__ (which opens a real CH connection) and inject the fake.
    reader = CHSpanReader.__new__(CHSpanReader)
    reader._client = client
    return reader


class _FakeReaderCM:
    """Context-manager reader that records the project_id it was called with."""

    def __init__(self, sink: dict, method: str):
        self._sink = sink
        self._method = method

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def _record(self, trace_ids, project_id):
        self._sink["trace_ids"] = list(trace_ids)
        self._sink["project_id"] = project_id
        return []

    def list_by_trace_ids(self, trace_ids, *, project_id=None, include_heavy=True):
        assert self._method == "list_by_trace_ids"
        self._sink["include_heavy"] = include_heavy
        return self._record(trace_ids, project_id)

    def roots_by_trace_ids(self, trace_ids, *, project_id=None, **_):
        assert self._method == "roots_by_trace_ids"
        return self._record(trace_ids, project_id)


# ─── reader level: the WHERE predicate ────────────────────────────────────────
def test_list_by_trace_ids_adds_project_predicate_when_scoped():
    client = _RecordingClient()
    _reader_with(client).list_by_trace_ids(["t1", "t2"], project_id="proj-9")
    assert "project_id = %(pid)s" in client.sql  # prunes on the PK prefix
    assert client.params.get("pid") == "proj-9"


def test_list_by_trace_ids_unscoped_by_default():
    client = _RecordingClient()
    _reader_with(client).list_by_trace_ids(["t1"])
    assert "project_id = %(pid)s" not in client.sql
    assert "pid" not in client.params


# ─── reader level: heavy (default) vs lean projection ─────────────────────────
def test_list_by_trace_ids_heavy_by_default():
    # Default must stay byte-identical for the ~13 general callers.
    client = _RecordingClient()
    _reader_with(client).list_by_trace_ids(["t1"])
    assert "attributes_extra" in client.sql  # fat column read in full


def test_list_by_trace_ids_lean_stubs_fat_columns():
    client = _RecordingClient()
    _reader_with(client).list_by_trace_ids(["t1"], include_heavy=False)
    # the fat columns that dominate FINAL memory are stubbed, not read
    assert "attributes_extra" not in client.sql
    assert "span_events" not in client.sql
    assert "resource_attrs" not in client.sql


# ─── FINAL skip-index safety: setting on, is_deleted predicate off ────────────
def test_list_by_trace_ids_enables_skip_indexes_under_final():
    # Without this, CH 25.3 does an unpruned full-table FINAL merge -> OOM.
    client = _RecordingClient()
    _reader_with(client).list_by_trace_ids(["t1"], project_id="p")
    assert client.settings.get("use_skip_indexes_if_final") == 1


def test_list_by_trace_ids_omits_is_deleted_predicate():
    # An is_deleted=0 predicate arms the is_deleted minmax skip index under the
    # setting (deleted-row resurrection); the engine drops deleted rows under FINAL
    # anyway. A revert that re-adds it fails here.
    client = _RecordingClient()
    _reader_with(client).list_by_trace_ids(["t1"])
    assert "is_deleted = 0" not in client.sql  # the WHERE predicate, not the column


def test_read_columns_do_not_shadow_key_columns():
    # `toString(project_id) AS project_id` shadows the key column and disables PK
    # pruning on WHERE project_id; aliases must carry a distinct name.
    from tracer.services.clickhouse.v2.span_reader import _SELECT_SQL

    assert "toString(project_id) AS project_id_str" in _SELECT_SQL
    assert "toString(project_id) AS project_id," not in _SELECT_SQL
    assert "toString(org_id) AS org_id," not in _SELECT_SQL


# ─── scanner fetch path forwards project_id ───────────────────────────────────
def test_fetch_trace_data_scopes_read_to_project():
    sink: dict = {}
    reader = _FakeReaderCM(sink, "list_by_trace_ids")
    with patch("tracer.services.clickhouse.v2.get_reader", return_value=reader):
        result = fetch_trace_data(["t1", "t2"], "proj-42")
    assert sink["project_id"] == "proj-42"  # FAILS if fetch drops the scope
    assert sink["include_heavy"] is False  # scanner reads lean (fat cols stubbed)
    assert result == []


# ─── the two lean roots swaps (embed step + feed corpus) stay scoped ──────────
# These read only each trace's first root input.value, so they were switched from
# the heavy all-spans list_by_trace_ids to the lean roots_by_trace_ids — the
# second + third instances of the same unprunable read. Pin the scope so a revert
# to the heavy/unscoped read fails here, not as an OOM in prod.
def test_get_trace_input_data_scopes_embed_read():
    sink: dict = {}
    reader = _FakeReaderCM(sink, "roots_by_trace_ids")
    with (
        patch("tracer.queries.scan_clustering.get_reader", return_value=reader),
        patch("tracer.queries.scan_clustering.TraceScanResult") as tsr,
        patch("tracer.queries.scan_clustering.Trace") as trace_model,
    ):
        # one scanned trace so we reach the reader; no PG-input fallback rows
        tsr.objects.filter.return_value.values_list.return_value = [("t-1", True)]
        trace_model.objects.filter.return_value.values_list.return_value = []
        get_trace_input_data(["t-1"], "proj-embed")
    assert sink["project_id"] == "proj-embed"


def test_project_cluster_inputs_corpus_scopes_read():
    sink: dict = {}
    reader = _FakeReaderCM(sink, "roots_by_trace_ids")
    with (
        patch("tracer.queries.feed.get_reader", return_value=reader),
        patch("tracer.queries.feed.ErrorClusterTraces") as ect,
    ):
        ect.objects.filter.return_value.values_list.return_value = [("cluster-1", "t-1")]
        _project_cluster_inputs_corpus("proj-corpus")
    assert sink["project_id"] == "proj-corpus"
