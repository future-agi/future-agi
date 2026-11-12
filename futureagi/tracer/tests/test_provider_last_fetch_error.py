"""OBS-SW-110: a provider that rejects the API key leaves a record behind.

``fetch_logs_for_provider`` keeps the last 401/403 (status code and time) in
``poll_state["last_fetch_error"]`` so the empty call list can say the key was
rejected, and clears it after the next good fetch. The provider list
(GET /tracer/observability-provider/) returns that record as the read-only
``last_fetch_error`` field and nothing else from ``poll_state``. The ORM is
faked in memory, as in test_retell_poll_orchestration.py, and the serializer
reads unsaved rows, so no database is used.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
import requests
from structlog.testing import capture_logs

from tracer.models.observability_provider import ObservabilityProvider, ProviderChoices
from tracer.serializers.observability_provider import ObservabilityProviderSerializer
from tracer.utils import observability_provider as op

pytestmark = pytest.mark.unit

NOW = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)


@dataclass
class _Row:
    id: object
    provider: str
    poll_state: dict = field(default_factory=dict)
    last_fetched_at: datetime | None = None
    deleted: bool = False


class _ValuesList(list):
    def first(self):
        return self[0] if self else None


class _QuerySet:
    writes: list = []

    def __init__(self, rows):
        self.rows = rows

    def filter(self, **kwargs):
        return _QuerySet(
            [
                row
                for row in self.rows
                if all(getattr(row, key) == value for key, value in kwargs.items())
            ]
        )

    def values_list(self, field_name, flat=True):
        return _ValuesList(getattr(row, field_name) for row in self.rows)

    def update(self, **kwargs):
        _QuerySet.writes.append(kwargs)
        for row in self.rows:
            for key, value in kwargs.items():
                setattr(row, key, value)
        return len(self.rows)

    def get(self, **kwargs):
        rows = self.filter(**kwargs).rows
        if not rows:
            raise op.ObservabilityProvider.DoesNotExist
        return rows[0]


class _Manager:
    def __init__(self, table, *, hide_deleted):
        self._table = table
        self._hide_deleted = hide_deleted

    def _rows(self):
        rows = list(self._table.values())
        if self._hide_deleted:
            rows = [row for row in rows if not row.deleted]
        return rows

    def filter(self, **kwargs):
        return _QuerySet(self._rows()).filter(**kwargs)

    def get(self, **kwargs):
        return _QuerySet(self._rows()).get(**kwargs)


@pytest.fixture
def table(monkeypatch):
    rows: dict[object, _Row] = {}
    monkeypatch.setattr(
        op.ObservabilityProvider, "all_objects", _Manager(rows, hide_deleted=False)
    )
    monkeypatch.setattr(
        op.ObservabilityProvider, "objects", _Manager(rows, hide_deleted=True)
    )
    monkeypatch.setattr(op.timezone, "now", lambda: NOW)
    monkeypatch.setattr(_QuerySet, "writes", [])
    return rows


def _add(table, provider, poll_state=None):
    pid = uuid.uuid4()
    table[pid] = _Row(id=pid, provider=provider, poll_state=dict(poll_state or {}))
    return pid


def _http_error(status_code):
    exc = requests.HTTPError("provider said no")
    exc.response = SimpleNamespace(status_code=status_code)
    return exc


def _fetch(pid):
    return op.fetch_logs_for_provider(
        pid, scheduled=True, start_time=None, end_time=None
    )


@pytest.mark.parametrize("status_code", [401, 403])
def test_a_rejected_key_records_the_last_fetch_error(table, monkeypatch, status_code):
    pid = _add(table, ProviderChoices.VAPI)

    def rejected(provider, **kwargs):
        raise _http_error(status_code)

    monkeypatch.setattr(op, "_poll_other_provider", rejected)

    with capture_logs() as cap:
        assert _fetch(pid) is None

    assert table[pid].poll_state == {
        "last_fetch_error": {"status_code": status_code, "at": NOW.isoformat()}
    }
    # The existing auth event stays the only log line.
    assert [entry["event"] for entry in cap] == ["authentication_failed_for_provider"]


def test_a_retell_rejection_keeps_the_state_the_run_already_wrote(table, monkeypatch):
    pid = _add(table, ProviderChoices.RETELL, {"retell": {"bootstrapped": True}})

    def rejected_after_a_checkpoint(provider, **kwargs):
        # The run saved its place before the provider refused the next page,
        # so the loaded provider.poll_state is already out of date.
        op._write_retell_state(
            provider.id, {"bootstrapped": True, "window": {"key": "page-2"}}
        )
        raise _http_error(401)

    monkeypatch.setattr(op, "_poll_retell_provider", rejected_after_a_checkpoint)

    assert _fetch(pid) is None
    assert table[pid].poll_state == {
        "retell": {"bootstrapped": True, "window": {"key": "page-2"}},
        "last_fetch_error": {"status_code": 401, "at": NOW.isoformat()},
    }


def test_a_good_fetch_clears_the_last_fetch_error(table, monkeypatch):
    pid = _add(
        table,
        ProviderChoices.VAPI,
        {
            "last_fetch_error": {
                "status_code": 401,
                "at": "2026-11-09T11:50:00+00:00",
            },
            "other": "kept",
        },
    )
    monkeypatch.setattr(
        op, "_poll_other_provider", lambda provider, **kwargs: op.StoreOutcome(2, 0, 0)
    )

    assert _fetch(pid) == op.StoreOutcome(2, 0, 0)
    assert table[pid].poll_state == {"other": "kept"}


def test_other_http_errors_do_not_say_the_key_was_rejected(table, monkeypatch):
    pid = _add(table, ProviderChoices.BLAND)

    def server_error(provider, **kwargs):
        raise _http_error(500)

    monkeypatch.setattr(op, "_poll_other_provider", server_error)

    assert _fetch(pid) is None
    assert table[pid].poll_state == {}
    assert _QuerySet.writes == []


def test_a_good_fetch_with_nothing_recorded_writes_nothing(table, monkeypatch):
    pid = _add(table, ProviderChoices.VAPI, {"other": "kept"})
    monkeypatch.setattr(
        op, "_poll_other_provider", lambda provider, **kwargs: op.StoreOutcome(1, 0, 0)
    )

    assert _fetch(pid) == op.StoreOutcome(1, 0, 0)
    assert table[pid].poll_state == {"other": "kept"}
    assert _QuerySet.writes == []


# --------------------------------------------------------------------------
# GET /tracer/observability-provider/: what the empty call list reads
# --------------------------------------------------------------------------

PROVIDER_LIST_FIELDS = {
    "id",
    "project",
    "provider",
    "enabled",
    "organization",
    "workspace",
    "metadata",
    "created_at",
    "updated_at",
    "last_fetch_error",
}


def _serialized(poll_state):
    row = ObservabilityProvider(provider=ProviderChoices.VAPI, poll_state=poll_state)
    return ObservabilityProviderSerializer(row).data


def test_the_provider_list_returns_the_recorded_fetch_error_and_no_other_poll_state():
    # The key is the one the poller writes, so the two sides cannot drift.
    data = _serialized(
        {
            op.LAST_FETCH_ERROR_KEY: {
                "status_code": 401,
                "at": "2026-11-09T12:00:00+00:00",
            },
            "retell": {"bootstrapped": True, "window": {"key": "page-2"}},
        }
    )

    assert data["last_fetch_error"] == {
        "status_code": 401,
        "at": "2026-11-09T12:00:00+00:00",
    }
    # Retell cursors and backoff stay internal: no poll_state, no extra key.
    assert set(data) == PROVIDER_LIST_FIELDS


def test_the_fetch_error_shows_only_its_status_code_and_time():
    data = _serialized(
        {
            "last_fetch_error": {
                "status_code": 403,
                "at": "2026-11-09T12:00:00+00:00",
                "body": "upstream response text",
            }
        }
    )

    assert data["last_fetch_error"] == {
        "status_code": 403,
        "at": "2026-11-09T12:00:00+00:00",
    }


@pytest.mark.parametrize(
    "poll_state",
    [
        {},
        {"retell": {"bootstrapped": True}},
        {"last_fetch_error": None},
        {"last_fetch_error": "401"},
        [],
    ],
    ids=["empty", "retell-only", "null", "not-a-dict", "poll-state-not-a-dict"],
)
def test_no_recorded_fetch_error_reads_as_null(poll_state):
    assert _serialized(poll_state)["last_fetch_error"] is None


def test_the_fetch_error_is_read_only_and_poll_state_stays_out():
    fields = ObservabilityProviderSerializer().fields

    assert fields["last_fetch_error"].read_only is True
    assert "poll_state" not in fields
