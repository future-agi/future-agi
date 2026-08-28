from __future__ import annotations

from datetime import UTC, datetime
from io import StringIO

import pytest
from django.core.management.base import CommandError

from tracer.management.commands.backfill_dashboard_root_spans import (
    Command,
    _fingerprint_query,
    _insert_query,
    _Window,
    _windows,
)

_PROJECT_ID = "00000000-0000-0000-0000-000000000001"
_START = datetime(2025, 1, 1, tzinfo=UTC)
_END = datetime(2025, 1, 1, 2, tzinfo=UTC)


class _Result:
    def __init__(self, row):
        self.result_rows = [row]


class _BackfillClient:
    def __init__(self):
        self.inserted = False
        self.queries = []
        self.commands = []
        self.closed = False

    def query(self, sql, settings):
        self.queries.append((sql, settings))
        if "FROM spans FINAL" in sql:
            return _Result((2, 99, 101))
        return _Result((2, 99, 101) if self.inserted else (0, 0, 0))

    def command(self, sql, settings):
        self.commands.append((sql, settings))
        self.inserted = True

    def close(self):
        self.closed = True


@pytest.mark.unit
def test_backfill_windows_are_bounded_and_exclusive():
    windows = _windows(_START, _END, 1)

    assert windows == [
        _Window(_START, datetime(2025, 1, 1, 1, tzinfo=UTC)),
        _Window(datetime(2025, 1, 1, 1, tzinfo=UTC), _END),
    ]


@pytest.mark.unit
def test_backfill_sql_is_additive_scoped_and_content_verified():
    window = _Window(_START, _END)
    insert_sql = _insert_query(window, (_PROJECT_ID,))
    fingerprint_sql = _fingerprint_query("spans", window, (_PROJECT_ID,))
    executable = f"{insert_sql}\n{fingerprint_sql}".upper()

    assert "INSERT INTO DASHBOARD_ROOT_SPANS" in executable
    assert "FROM SPANS FINAL" in executable
    assert "PROJECT_ID IN (TOUUID(" in executable
    assert "PARENT_SPAN_ID = ''" in executable
    assert "CITYHASH64" in executable
    assert "SIPHASH64" in executable
    assert "ATTRS_STRING" in executable
    assert "ATTRS_NUMBER" in executable
    assert "ATTRS_BOOL" in executable
    assert "ALTER " not in executable
    assert "DELETE " not in executable
    assert "DROP " not in executable
    assert "TRUNCATE " not in executable


@pytest.mark.unit
def test_backfill_is_dry_run_by_default(monkeypatch):
    monkeypatch.setattr(
        Command,
        "_client",
        lambda _self: pytest.fail("dry-run must not connect"),
    )

    Command(stdout=StringIO()).handle(
        start=_START.isoformat(),
        end=_END.isoformat(),
        project_id=[_PROJECT_ID],
        all_projects=False,
        batch_hours=1,
        execute=False,
    )


@pytest.mark.unit
def test_backfill_execute_inserts_then_verifies(monkeypatch):
    client = _BackfillClient()
    monkeypatch.setattr(Command, "_client", lambda _self: client)

    Command(stdout=StringIO()).handle(
        start=_START.isoformat(),
        end=datetime(2025, 1, 1, 1, tzinfo=UTC).isoformat(),
        project_id=[_PROJECT_ID],
        all_projects=False,
        batch_hours=1,
        execute=True,
    )

    assert len(client.commands) == 1
    assert client.commands[0][0].startswith("INSERT INTO dashboard_root_spans")
    assert len(client.queries) == 3
    assert client.closed is True


@pytest.mark.unit
def test_backfill_fingerprint_compares_both_independent_hashes():
    client = _BackfillClient()

    assert Command._fingerprint(
        client,
        "spans",
        _Window(_START, _END),
        (_PROJECT_ID,),
        {},
    ) == (2, 99, 101)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("project_ids", "all_projects"),
    (([], False), ([_PROJECT_ID], True)),
)
def test_backfill_requires_one_explicit_scope(project_ids, all_projects):
    with pytest.raises(CommandError, match="choose either"):
        Command(stdout=StringIO()).handle(
            start=_START.isoformat(),
            end=_END.isoformat(),
            project_id=project_ids,
            all_projects=all_projects,
            batch_hours=1,
            execute=False,
        )
