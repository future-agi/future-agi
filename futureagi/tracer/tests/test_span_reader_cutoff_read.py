"""A root read "as of" a cutoff must not return a row rewritten after it.

The investigator reads a trace at its attempt's cutoff. A provider call log is
re-exported under the same span id, so the cutoff predicates have to run after
the ``FINAL`` merge: before it, they would drop the later version and bring the
earlier one back. The SQL-shape tests pin that without ClickHouse; the last test
proves it against a real ReplacingMergeTree.
"""

import uuid
from datetime import UTC, datetime, timedelta, timezone

import pytest

from tracer.services.clickhouse.v2.span_reader import (
    _CUTOFF_SETTINGS,
    _CUTOFF_WHERE,
    _SORTING_KEY,
    CHSpanReader,
)

CUTOFF = datetime(2026, 7, 1, 10, 0, 0, 250000, tzinfo=UTC)


class _RecordingClient:
    def __init__(self):
        self.sql = self.parameters = self.settings = None

    def query(self, sql, parameters=None, settings=None):
        self.sql, self.parameters, self.settings = sql, parameters, settings

        class _Result:
            result_rows = []

        return _Result()


def _reader_with(client) -> CHSpanReader:
    reader = CHSpanReader.__new__(CHSpanReader)
    reader._client = client
    return reader


def test_cutoff_read_filters_after_the_final_merge():
    client = _RecordingClient()

    _reader_with(client).roots_by_trace_ids(["t1"], project_id="p1", cutoff=CUTOFF)

    assert "FROM spans FINAL WHERE" in client.sql
    assert _CUTOFF_WHERE in client.sql
    assert "PREWHERE" not in client.sql
    assert client.settings == _CUTOFF_SETTINGS
    assert client.settings["optimize_move_to_prewhere_if_final"] == 0
    assert "auto_minmax_index_created_at" in (
        client.settings["ignore_data_skipping_indices"]
    )
    # UTC text with microseconds, whatever zone the datetime carries.
    assert client.parameters["cutoff"] == "2026-07-01 10:00:00.250000"
    local = CUTOFF.astimezone(timezone(timedelta(hours=5, minutes=30)))
    _reader_with(client).roots_by_trace_ids(["t1"], cutoff=local)
    assert client.parameters["cutoff"] == "2026-07-01 10:00:00.250000"


def test_read_without_a_cutoff_is_unchanged():
    client = _RecordingClient()

    _reader_with(client).roots_by_trace_ids(["t1"], project_id="p1")

    assert "created_at" not in client.sql.split("WHERE", 1)[1]
    assert client.settings == {"use_skip_indexes_if_final": 1}


def test_cutoff_read_refuses_the_limit_by_dedup():
    with pytest.raises(ValueError):
        _reader_with(_RecordingClient()).roots_by_trace_ids(
            ["t1"], cutoff=CUTOFF, dedup_via_limit_by=True
        )


@pytest.mark.django_db
def test_row_rewritten_after_the_cutoff_has_no_eligible_version():
    """Merges stopped, one part per version: the engine has not collapsed them."""
    import clickhouse_connect

    from conftest import _require_safe_ch25_test_target
    from tracer.services.clickhouse.v2 import get_v2_config

    table = f"cutoff_probe_{uuid.uuid4().hex[:8]}"
    config = get_v2_config()
    _require_safe_ch25_test_target(host=config["host"], database=config["database"])
    ch = clickhouse_connect.get_client(
        host=config["host"],
        port=config["http_port"],
        username=config["user"],
        password=config["password"],
        database=config["database"],
    )
    try:
        ch.command(
            f"CREATE TABLE {table} ("
            "  project_id UUID, observation_type LowCardinality(String),"
            "  service_name LowCardinality(String), start_time DateTime64(6),"
            "  trace_id UUID, id String, name String,"
            "  created_at DateTime64(6, 'UTC'), updated_at DateTime64(6, 'UTC'),"
            "  is_deleted UInt8 DEFAULT 0, _version UInt64 DEFAULT 1,"
            "  INDEX auto_minmax_index_created_at created_at TYPE minmax GRANULARITY 1"
            ") ENGINE = ReplacingMergeTree(_version, is_deleted) "
            "PARTITION BY toDate(start_time) "
            f"ORDER BY ({_SORTING_KEY})"
        )
        try:
            ch.command(f"SYSTEM STOP MERGES {table}")
            before, after = "2026-07-01 09:59:00.000000", "2026-07-01 10:00:00.500000"
            rows = [
                # (id, name, created_at, updated_at, _version)
                ("unchanged", "unchanged-v1", before, before, 1),
                ("rewritten", "rewritten-v1", before, before, 1),
                # Same span id, exported again half a second after the cutoff.
                ("rewritten", "rewritten-v2", after, after, 2),
                ("updated", "updated-v1", before, before, 1),
                ("updated", "updated-v2", before, after, 2),
            ]
            for span_id, name, created, updated, version in rows:  # one part each
                ch.insert(
                    table,
                    [
                        [
                            "11111111-1111-1111-1111-111111111111",
                            "conversation",
                            "svc",
                            "2026-07-01 09:58:00",
                            "22222222-2222-2222-2222-222222222222",
                            span_id,
                            name,
                            created,
                            updated,
                            version,
                        ]
                    ],
                    column_names=[
                        "project_id",
                        "observation_type",
                        "service_name",
                        "start_time",
                        "trace_id",
                        "id",
                        "name",
                        "created_at",
                        "updated_at",
                        "_version",
                    ],
                )
            parts = ch.query(
                f"SELECT count() FROM system.parts WHERE table = '{table}' AND active"
            ).result_rows[0][0]
            assert parts > 1, "parts already merged — the probe would prove nothing"

            def names(where, settings):
                return sorted(
                    row[0]
                    for row in ch.query(
                        f"SELECT name FROM {table} FINAL WHERE {where}",
                        parameters={"cutoff": "2026-07-01 10:00:00.250000"},
                        settings=settings,
                    ).result_rows
                )

            assert names("1", {}) == ["rewritten-v2", "unchanged-v1", "updated-v2"]
            # Neither rewritten row comes back as its first version.
            assert names(_CUTOFF_WHERE, _CUTOFF_SETTINGS) == ["unchanged-v1"]
        finally:
            ch.command(f"DROP TABLE IF EXISTS {table}")
    finally:
        ch.close()
