"""Wire-routing and migration/backfill contracts; run on the cloud test host."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from unittest.mock import Mock
from uuid import uuid4

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from tracer.management.commands import ch25_backfill_skill_trace_links as backfill
from tracer.services.clickhouse.v2.adapter import CH_INSERT_COLUMNS, adapt, row_to_tuple

VERSION = "11111111-1111-4111-8111-111111111111"
SECOND_VERSION = "22222222-2222-4222-8222-222222222222"


def _span(attributes, **overrides):
    row = {
        "project_id": str(uuid4()),
        "trace_id": str(uuid4()),
        "id": "0123456789abcdef",
        "name": "skill.load",
        "start_time": datetime(2026, 11, 1, tzinfo=UTC),
        "span_attributes": attributes,
        **overrides,
    }
    return adapt(row)


@pytest.mark.parametrize("cached,root_file", [(True, False), (False, True)])
@pytest.mark.parametrize("encoded", [False, True])
def test_skill_attributes_keep_types_and_flat_dotted_array_keys(
    cached, root_file, encoded
):
    attributes = {
        "fi.skill.version_id": VERSION,
        "fi.skill.version": 3,
        "fi.skill.requested_version": 3,
        "fi.skill.load.cached": cached,
        "fi.skill.load.root_file": root_file,
        "fi.skills.version_ids": [VERSION, SECOND_VERSION],
        "fi.skills.ids": [VERSION],
        "fi.skills.refs": ["policy@v3"],
        "fi.skills.count": 2,
        "fi.skills.truncated": False,
        "fi.context.linked_skill_version_ids": [SECOND_VERSION],
    }
    row = _span(json.dumps(attributes) if encoded else attributes)
    assert row.attrs_string == {"fi.skill.version_id": VERSION}
    assert row.attrs_number == {
        "fi.skill.version": 3.0,
        "fi.skill.requested_version": 3.0,
        "fi.skills.count": 2.0,
    }
    assert row.attrs_bool == {
        "fi.skill.load.cached": int(cached),
        "fi.skill.load.root_file": int(root_file),
        "fi.skills.truncated": 0,
    }
    overflow = json.loads(row.attributes_extra)
    for key in (
        "fi.skills.version_ids",
        "fi.skills.ids",
        "fi.skills.refs",
        "fi.context.linked_skill_version_ids",
    ):
        assert overflow[key] == attributes[key]
        assert key not in row.attrs_string
        assert key not in row.attrs_number
        assert key not in row.attrs_bool
    assert "fi" not in overflow
    wire = dict(zip(CH_INSERT_COLUMNS, row_to_tuple(row), strict=True))
    assert isinstance(wire["attributes_extra"], str)
    assert json.loads(wire["attributes_extra"])["fi.skills.version_ids"] == [
        VERSION,
        SECOND_VERSION,
    ]
    assert "skill_version_ids" not in wire  # The server owns the materialized column.


def test_failed_load_keeps_scope_without_fabricating_verified_identity():
    attrs = {
        "fi.skill.name": "policy",
        "fi.skill.requested_label": "production",
        "fi.skill.load.via": "sdk.read_file",
        "error.type": "integrity",
        "gen_ai.agent.name": "reviewer",
        "fi.context.id": VERSION,
        "fi.context.version_id": SECOND_VERSION,
    }
    row = _span(attrs, status="ERROR")
    assert row.status == "ERROR"
    assert row.attrs_string == attrs
    for key in ("fi.skill.id", "fi.skill.version_id", "fi.skill.digest"):
        assert key not in row.attrs_string


def test_adapter_tombstone_retains_full_load_row():
    attrs = {"fi.skill.version_id": VERSION, "fi.skill.name": "policy"}
    row = _span(attrs, deleted=True)
    wire = dict(zip(CH_INSERT_COLUMNS, row_to_tuple(row), strict=True))
    assert wire["is_deleted"] == 1
    assert wire["name"] == "skill.load"
    assert wire["attrs_string"] == attrs
    assert wire["start_time"] == datetime(2026, 11, 1, tzinfo=UTC)


def test_schema_is_auto_discovered_and_does_not_backfill_during_boot():
    from tracer.services.clickhouse.v2.apply_schema import discover_files

    files = discover_files(backfill.SCHEMA_PATH.parent, None)
    assert any(f.path.name == backfill.SCHEMA_PATH.name for f in files)
    sql = backfill.SCHEMA_PATH.read_text(encoding="utf-8")
    executable = "\n".join(
        line for line in sql.splitlines() if not line.lstrip().startswith("--")
    )
    assert (
        re.search(r"\b(MATERIALIZE|POPULATE|OPTIMIZE|INSERT)\b", executable, re.I)
        is None
    )
    assert executable.count("ENGINE = ReplacingMergeTree(_version, is_deleted)") == 2
    assert (
        "JSONExtract(attributes_extra, 'fi.skills.version_ids', 'Array(String)')"
        in executable
    )
    assert "idx_skill_version_ids skill_version_ids" in executable
    assert "PROJECTION by_trace" in executable


def test_backfill_uses_both_frozen_projections_and_keeps_tombstones():
    projections = backfill.load_projections()
    assert tuple(projections) == backfill.TARGETS
    for table, projection in projections.items():
        sql = backfill.backfill_sql(table, projection, project_scoped=True)
        assert f"INSERT INTO {table}\n{projection}\n" in sql
        assert "is_deleted, _version" in sql
        assert "is_deleted = 0" not in sql
        assert "FINAL" not in sql
        assert "start_time >= %(start)s AND start_time < %(end)s" in sql
        assert "AND project_id = %(project_id)s" in sql
    assert "status = 'ERROR'" in projections["skill_load_failures"]
    assert (
        "attrs_string['fi.skill.version_id'] = ''" in projections["skill_load_failures"]
    )
    assert (
        "attrs_string['fi.skill.version_id'] != ''" in projections["skill_trace_links"]
    )


@pytest.mark.parametrize(
    "start,end",
    [
        ("bad", "2026-11-02"),
        ("2026-11-02", "2026-11-01"),
        ("2026-11-01", "2026-11-01"),
        ("2026-10-01", "2026-11-02"),
    ],
)
def test_backfill_rejects_invalid_or_unbounded_windows_before_connecting(
    monkeypatch, start, end
):
    connect = Mock()
    monkeypatch.setattr(backfill.Command, "_client", connect)
    with pytest.raises(CommandError):
        call_command("ch25_backfill_skill_trace_links", start_date=start, end_date=end)
    connect.assert_not_called()


def test_backfill_validates_project_before_connecting(monkeypatch):
    connect = Mock()
    monkeypatch.setattr(backfill.Command, "_client", connect)
    with pytest.raises(CommandError, match="UUID"):
        call_command(
            "ch25_backfill_skill_trace_links",
            start_date="2026-11-01",
            end_date="2026-11-02",
            project_id="not-a-project",
        )
    connect.assert_not_called()


def test_backfill_dry_run_never_connects(monkeypatch):
    connect = Mock()
    monkeypatch.setattr(backfill.Command, "_client", connect)
    call_command(
        "ch25_backfill_skill_trace_links",
        start_date="2026-11-01",
        end_date="2026-11-02",
        dry_run=True,
    )
    connect.assert_not_called()


def test_backfill_runs_two_tables_per_day_and_closes_client(monkeypatch):
    client = Mock()
    monkeypatch.setattr(backfill.Command, "_client", lambda self: client)
    call_command(
        "ch25_backfill_skill_trace_links",
        start_date="2026-11-01",
        end_date="2026-11-03",
        project_id=VERSION,
    )
    assert client.command.call_count == 4
    calls = client.command.call_args_list
    assert calls[0].kwargs["parameters"] == {
        "start": datetime(2026, 11, 1, tzinfo=UTC),
        "end": datetime(2026, 11, 2, tzinfo=UTC),
        "project_id": VERSION,
    }
    assert calls[1].kwargs["parameters"] == calls[0].kwargs["parameters"]
    assert (
        calls[2].kwargs["parameters"]["start"] == calls[0].kwargs["parameters"]["end"]
    )
    assert all(c.kwargs["settings"] == backfill.QUERY_SETTINGS for c in calls)
    client.close.assert_called_once()


def test_backfill_stops_on_error_without_hiding_partial_completion(monkeypatch):
    client = Mock()
    client.command.side_effect = [None, RuntimeError("insert failed")]
    monkeypatch.setattr(backfill.Command, "_client", lambda self: client)
    with pytest.raises(RuntimeError, match="insert failed"):
        call_command(
            "ch25_backfill_skill_trace_links",
            start_date="2026-11-01",
            end_date="2026-11-03",
        )
    assert client.command.call_count == 2
    client.close.assert_called_once()
