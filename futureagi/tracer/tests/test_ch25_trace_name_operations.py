"""Operational contracts for row-local trace names and dictionary audits."""

from __future__ import annotations

from io import StringIO
from unittest.mock import Mock, patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from tracer.management.commands.ch25_backfill_traces import Command as BackfillCommand
from tracer.services.clickhouse import oss_cdc_bootstrap as cdc
from tracer.services.clickhouse import oss_native_bootstrap as native
from tracer.services.clickhouse.v2.apply_schema import split_statements
from tracer.services.clickhouse.v2.apply_schema_rewriter import rewrite_for_replicated


def test_schema_039_rewrites_to_replicated_and_uses_row_local_name():
    from pathlib import Path

    path = (
        Path(__file__).resolve().parents[1]
        / "services"
        / "clickhouse"
        / "v2"
        / "schema"
        / "039_trace_name_row_local.sql"
    )
    (statement,) = split_statements(path.read_text())
    assert "MODIFY COLUMN trace_name String" in statement
    assert "MATERIALIZED if(empty(parent_span_id), name, '')" in statement
    assert "MATERIALIZE COLUMN" not in statement
    assert "ON CLUSTER 'test_cluster'" in rewrite_for_replicated(
        statement, table_name="spans", cluster="test_cluster", zk_prefix="/ch/test"
    )


def test_fresh_oss_spans_does_not_read_dictionary():
    ddl = native.native_definitions("offline_native")["spans"]
    assert "MATERIALIZED if(empty(parent_span_id), name, '')" in ddl
    assert "trace_dict" not in ddl
    assert (
        cdc._SPAN_COLUMNS["trace_name"]
        == "String MATERIALIZED if(empty(parent_span_id), name, '')"
    )
    assert "trace_dict" in native.native_definitions("offline_native")


def test_backfill_parser_disallows_materialize_and_never_reloads_dictionary():
    parser = BackfillCommand().create_parser("manage.py", "ch25_backfill_traces")
    with pytest.raises(CommandError):
        parser.parse_args(["--materialize-spans"])
    with pytest.raises(CommandError):
        parser.parse_args(["--no-reload-dict"])
    client = Mock()
    client.query.return_value.result_rows = [(1,)]
    row = Mock()
    manager = Mock()
    manager.all.return_value.order_by.return_value = manager
    manager.count.return_value = 1
    manager.iterator.return_value = iter([row])
    with (
        patch("tracer.models.trace.Trace") as trace,
        patch.object(BackfillCommand, "_client", return_value=client),
        patch(
            "tracer.management.commands.ch25_backfill_traces._trace_to_row",
            return_value=["row"],
        ),
    ):
        trace.all_objects = manager
        call_command("ch25_backfill_traces", stdout=StringIO())
    client.command.assert_not_called()
    client.insert.assert_called_once()


def test_dictionary_health_failed_exits_2_and_not_loaded_is_healthy():
    from tracer.management.commands.ch25_dictionary_health import Command

    client = Mock()
    client.query.return_value.result_rows = [
        ("db", "failed_dict", "FAILED", 0, 0, 0, None, None, "could not load")
    ]
    with patch.object(Command, "_client", return_value=client):
        with pytest.raises(SystemExit) as exit_info:
            Command().handle(max_bytes=4 * 1024**3, trace_names=False, dry_run=False)
    assert exit_info.value.code == 2
    assert "system.dictionaries" in client.query.call_args.args[0]
    client.query.return_value.result_rows = []
    with patch.object(Command, "_client", return_value=client):
        assert (
            Command().handle(max_bytes=4 * 1024**3, trace_names=False, dry_run=False)
            == 0
        )


def test_dictionary_audit_dry_run_does_not_connect():
    from tracer.management.commands.ch25_dictionary_health import Command

    with patch.object(Command, "_client", side_effect=AssertionError("connected")):
        out = StringIO()
        command = Command(stdout=out)
        assert (
            command.handle(
                trace_names=True, dry_run=True, since="2026-08-01", until="2026-08-03"
            )
            == 0
        )
    sql = out.getvalue()
    assert "countIf(trace_name = '')" in sql
    assert (
        "if(trace_name != '', trace_name, if(empty(parent_span_id), name, ''))" in sql
    )
    assert "toDate(start_time)" in sql


def test_dictionary_audit_queries_one_day_at_a_time_readonly():
    from tracer.management.commands.ch25_dictionary_health import Command

    client = Mock()
    client.query.return_value.result_rows = []
    with patch.object(Command, "_client", return_value=client):
        assert (
            Command(stdout=StringIO()).handle(
                trace_names=True, dry_run=False, since="2026-08-01", until="2026-08-03"
            )
            == 0
        )
    assert client.query.call_count == 2
    first, second = client.query.call_args_list
    assert first.kwargs["parameters"] == {
        "day_start": "2026-08-01",
        "day_end": "2026-08-02",
    }
    assert second.kwargs["parameters"] == {
        "day_start": "2026-08-02",
        "day_end": "2026-08-03",
    }
    assert first.kwargs["settings"] == {
        "readonly": 1,
        "max_threads": 2,
        "max_execution_time": 30,
        "max_memory_usage": 1_000_000_000,
    }
