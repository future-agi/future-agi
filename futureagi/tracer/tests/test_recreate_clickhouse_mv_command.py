"""
Unit tests for the ``recreate_clickhouse_mv`` management command (PR3).

These tests exercise the command's flow against a mocked CH client — they
don't need a real ClickHouse running. The DDL string is the only thing
the command actually compares; we monkeypatch ``client.execute`` to feed
a controlled deployed-DDL string back to the command.
"""

from __future__ import annotations

from datetime import datetime, timezone
from io import StringIO
from unittest.mock import MagicMock

import pytest
from django.core.management import CommandError, call_command


def _fake_client(deployed_ddl: str | None):
    """Build a mock CH client that returns ``deployed_ddl`` for SHOW CREATE."""
    client = MagicMock()

    def execute(query, params=None, **kwargs):
        q = query.strip()
        if q.startswith("SHOW CREATE TABLE"):
            return [(deployed_ddl,)] if deployed_ddl is not None else []
        if q.startswith("SELECT now"):
            return [(datetime(2026, 5, 7, 0, 0, 0, tzinfo=timezone.utc),)]
        return []

    client.execute = execute
    return client


@pytest.mark.unit
class TestRecreateClickhouseMVCommand:
    def test_unknown_mv_raises(self, monkeypatch):
        """An unknown MV name should fail with a clear CommandError."""
        from tracer.services.clickhouse import client as ch_client

        monkeypatch.setattr(ch_client, "get_clickhouse_client", lambda: _fake_client(""))

        with pytest.raises(CommandError, match="not in MV_RECREATE_MANIFEST"):
            call_command("recreate_clickhouse_mv", "definitely_not_a_real_mv")

    def test_dry_run_no_changes_when_already_canonical(self, monkeypatch):
        """If deployed DDL matches canonical, dry-run reports no work needed."""
        from tracer.services.clickhouse import client as ch_client
        from tracer.services.clickhouse.schema import EVAL_METRICS_HOURLY_MV

        # The canonical DDL itself, returned as the deployed DDL → matches.
        client = _fake_client(EVAL_METRICS_HOURLY_MV.strip())
        monkeypatch.setattr(ch_client, "get_clickhouse_client", lambda: client)

        out = StringIO()
        call_command(
            "recreate_clickhouse_mv",
            "eval_metrics_hourly_mv",
            "--dry-run",
            stdout=out,
        )
        # Command short-circuits before the dry-run plan is even printed
        # because the DDLs are equivalent.
        assert "already matches canonical" in out.getvalue()

    def test_dry_run_prints_plan_when_diverged(self, monkeypatch):
        """If deployed DDL diverges, dry-run shows the plan but doesn't mutate."""
        from tracer.services.clickhouse import client as ch_client

        # An obviously-stale deployed DDL (no target_type filter)
        stale_ddl = (
            "CREATE MATERIALIZED VIEW eval_metrics_hourly_mv "
            "TO eval_metrics_hourly AS SELECT count() FROM tracer_eval_logger "
            "WHERE _peerdb_is_deleted = 0"
        )
        client = _fake_client(stale_ddl)
        monkeypatch.setattr(ch_client, "get_clickhouse_client", lambda: client)

        out = StringIO()
        call_command(
            "recreate_clickhouse_mv",
            "eval_metrics_hourly_mv",
            "--dry-run",
            stdout=out,
        )
        out_text = out.getvalue()

        # Plan banner + diff section both appear
        assert "Recreation plan: eval_metrics_hourly_mv" in out_text
        assert "Source table:" in out_text
        assert "Target table:" in out_text
        assert "Canonical DDL" in out_text
        assert "Deployed DDL" in out_text
        # And we explicitly said "no changes made"
        assert "Dry-run" in out_text and "no changes made" in out_text

    def test_diff_only_short_circuits(self, monkeypatch):
        """``--diff-only`` prints the diff and exits without confirming."""
        from tracer.services.clickhouse import client as ch_client

        client = _fake_client("CREATE MATERIALIZED VIEW eval_metrics_hourly_mv ...")
        monkeypatch.setattr(ch_client, "get_clickhouse_client", lambda: client)

        out = StringIO()
        call_command(
            "recreate_clickhouse_mv",
            "eval_metrics_hourly_mv",
            "--diff-only",
            stdout=out,
        )
        out_text = out.getvalue()
        assert "Canonical DDL" in out_text
        assert "Deployed DDL" in out_text
        # No "Recreation plan" banner — diff-only skips planning
        assert "Recreation plan:" not in out_text
