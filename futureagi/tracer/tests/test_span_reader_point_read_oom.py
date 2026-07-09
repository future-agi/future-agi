"""Point / trace / id CH span reads must not OOM under FINAL (TH-6442).

TH-6515 (PR #1373) gave the multi-trace batch reads
(``list_by_trace_ids`` / ``roots_by_trace_ids``) the ``use_skip_indexes_if_final``
setting and dropped the redundant ``is_deleted = 0`` predicate, but left the
single-row / point-read siblings that back the annotation add + trace-detail
render paths. Each does ``SELECT <fat cols incl attributes_extra> FROM spans
FINAL WHERE <id | trace_id | parent_span_id> = X`` — pruning only via a bloom
skip index, which CH 25.3 disables under FINAL by default. Without the setting
the read does a full in-order merge over every part and OOMs (code 241) on a
wide (voice) span whose ``attributes_extra`` carries a whole raw log.

A 9 GiB OOM can't be reproduced in test-CH, so — mirroring
``test_scan_project_scoped_read.py`` — these pin the two levers that prevent the
unpruned merge: the skip-index setting is on, and the ``is_deleted = 0``
predicate (which, alongside the setting, arms the ``is_deleted`` minmax-index
resurrection bug — the 2-arg ReplacingMergeTree drops deleted rows under FINAL
anyway) is off. A revert of either fails here rather than as a prod OOM.
"""

from tracer.services.clickhouse.v2.span_reader import CHSpanReader


class _RecordingClient:
    """Captures the SQL + settings of the last query; returns no rows."""

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
    # Bypass __init__ (opens a real CH connection) and inject the fake.
    reader = CHSpanReader.__new__(CHSpanReader)
    reader._client = client
    return reader


def _assert_final_oom_safe(client, key_predicate):
    # skip indexes re-enabled under FINAL (else a full-table merge -> OOM)
    assert client.settings.get("use_skip_indexes_if_final") == 1
    # the is_deleted=0 predicate is dropped (redundant under FINAL; keeping it
    # alongside the setting arms the is_deleted minmax resurrection bug)
    assert "is_deleted = 0" not in client.sql
    # still prunes on the stable bloom-indexed column
    assert key_predicate in client.sql


def test_get_is_final_oom_safe():
    client = _RecordingClient()
    _reader_with(client).get("span-1")
    _assert_final_oom_safe(client, "id = %(span_id)s")


def test_list_by_trace_is_final_oom_safe():
    client = _RecordingClient()
    _reader_with(client).list_by_trace("trace-1")
    _assert_final_oom_safe(client, "trace_id = %(trace_id)s")


def test_first_span_by_type_is_final_oom_safe():
    client = _RecordingClient()
    _reader_with(client).first_span_by_type("trace-1", "llm")
    _assert_final_oom_safe(client, "trace_id = %(trace_id)s")


def test_list_by_parent_is_final_oom_safe():
    client = _RecordingClient()
    _reader_with(client).list_by_parent("parent-1")
    _assert_final_oom_safe(client, "parent_span_id = %(parent)s")


def test_list_by_ids_is_final_oom_safe():
    client = _RecordingClient()
    _reader_with(client).list_by_ids(["s1", "s2"])
    _assert_final_oom_safe(client, "id IN %(ids)s")
