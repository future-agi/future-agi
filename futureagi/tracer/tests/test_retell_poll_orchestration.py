"""Behavioural tests for the Retell poll orchestrator (contract §7).

The ORM is faked in-memory (see ``_FakeManager``/``_FakeQuerySet`` below) so
these tests never touch a database; every case patches
``ObservabilityService.fetch_retell_page``, ``process_and_store_logs``,
``ObservabilityProvider.objects``/``all_objects`` and ``timezone.now`` per the
test seam in contract §7.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone as dt_timezone
from types import SimpleNamespace

import pytest
import requests
from django.db.models import Q
from structlog.testing import capture_logs

from tracer.models.observability_provider import ProviderChoices
from tracer.services.observability_providers import RetellConfigurationError, RetellCursorRejected, RetellPage
from tracer.utils import observability_provider as op

pytestmark = pytest.mark.unit

UTC = dt_timezone.utc


def _dt(*args, **kwargs) -> datetime:
    return datetime(*args, tzinfo=UTC, **kwargs)


def _page(calls=None, *, has_more=False, next_key=None, dropped_no_end=0, dropped_missing=0, dropped_failed=0) -> RetellPage:
    return RetellPage(
        calls=calls or [],
        has_more=has_more,
        next_key=next_key,
        dropped_no_end=dropped_no_end,
        dropped_missing=dropped_missing,
        dropped_failed=dropped_failed,
    )


def _calls(n: int, prefix: str = "call") -> list[dict]:
    return [{"call_id": f"{prefix}_{i}"} for i in range(n)]


# --------------------------------------------------------------------------
# Fake ORM: enough of Django's Q/filter/update/values_list semantics to run
# ``_advance_watermark`` / ``_repair_future_watermark`` / ``_write_retell_state``
# against an in-memory table, without a database.
# --------------------------------------------------------------------------


@dataclass
class _FakeRow:
    id: object
    poll_state: dict = field(default_factory=dict)
    last_fetched_at: datetime | None = None
    enabled: bool = True
    provider: str = ProviderChoices.RETELL
    deleted: bool = False  # soft-delete flag: `objects` hides it, `all_objects` must not


def _q_matches(row: _FakeRow, node) -> bool:
    if isinstance(node, Q):
        results = [_q_matches(row, child) for child in node.children]
        result = all(results) if node.connector == "AND" else any(results)
        return (not result) if node.negated else result
    lookup, value = node
    field_name, _, op_name = lookup.partition("__")
    fval = getattr(row, field_name, None)
    if op_name == "":
        return fval == value
    if op_name == "lt":
        return fval is not None and fval < value
    if op_name == "gt":
        return fval is not None and fval > value
    if op_name == "isnull":
        return (fval is None) is value
    raise NotImplementedError(op_name)


class _FakeValuesList(list):
    """Stands in for Django's ``ValuesQuerySet``: both ``.first()`` (used by
    ``_write_retell_state``'s read) and ``.iterator(chunk_size=...)`` (used by
    the scheduled fan-out in ``fetch_observability_logs``) are called on the
    object ``.values_list(...)`` returns, not on the queryset before it.
    """

    def first(self):
        return self[0] if self else None

    def iterator(self, chunk_size=None):
        return iter(self)


class _FakeQuerySet:
    def __init__(self, rows: list[_FakeRow]):
        self.rows = rows

    def filter(self, *args, **kwargs):
        rows = self.rows
        conditions = list(args)
        if kwargs:
            conditions.append(Q(**kwargs))
        for cond in conditions:
            rows = [r for r in rows if _q_matches(r, cond)]
        return _FakeQuerySet(rows)

    def values_list(self, field_name, flat=True):
        return _FakeValuesList(getattr(r, field_name) for r in self.rows)

    def update(self, **kwargs):
        for r in self.rows:
            for k, v in kwargs.items():
                setattr(r, k, v)
        return len(self.rows)

    def get(self, **kwargs):
        matches = self.filter(**kwargs).rows
        if not matches:
            from tracer.models.observability_provider import ObservabilityProvider

            raise ObservabilityProvider.DoesNotExist
        return matches[0]


class _FakeManager:
    """Stands in for a Django Manager: every call starts a fresh table scan.

    ``soft_delete_filter=True`` mirrors the real default ``objects`` manager,
    which hides soft-deleted rows; ``soft_delete_filter=False`` mirrors
    ``all_objects``. The two must be genuinely different managers over the
    same table — contract §4 requires every writer to use ``all_objects`` so a
    primary-key write never becomes a silent no-op on a soft-deleted row, and
    that invariant can only fail under test if the two managers can disagree.
    """

    def __init__(self, table: dict, *, soft_delete_filter: bool):
        self._table = table
        self._soft_delete_filter = soft_delete_filter

    def _rows(self):
        rows = list(self._table.values())
        if self._soft_delete_filter:
            rows = [r for r in rows if not r.deleted]
        return rows

    def filter(self, *args, **kwargs):
        return _FakeQuerySet(self._rows()).filter(*args, **kwargs)

    def get(self, **kwargs):
        return _FakeQuerySet(self._rows()).get(**kwargs)


@pytest.fixture
def fake_table(monkeypatch):
    table: dict[object, _FakeRow] = {}
    monkeypatch.setattr(op.ObservabilityProvider, "all_objects", _FakeManager(table, soft_delete_filter=False))
    monkeypatch.setattr(op.ObservabilityProvider, "objects", _FakeManager(table, soft_delete_filter=True))
    return table


def _retell_provider(row: _FakeRow):
    """A provider-shaped object whose writable fields mirror ``row`` (real code
    reads ``provider.poll_state`` / ``provider.last_fetched_at`` off the passed
    instance, but writes go through the two/three DB writers against ``row``).
    """
    return SimpleNamespace(
        id=row.id,
        provider=ProviderChoices.RETELL,
        poll_state=row.poll_state,
        last_fetched_at=row.last_fetched_at,
        project=SimpleNamespace(id="project-1", organization_id="org-1"),
    )


def _freeze_now(monkeypatch, when: datetime):
    monkeypatch.setattr(op.timezone, "now", lambda: when)


# --------------------------------------------------------------------------
# Writers
# --------------------------------------------------------------------------


def test_only_two_watermark_writers_exist():
    assert not hasattr(op, "_update_last_fetched_at")
    assert callable(op._advance_watermark)
    assert callable(op._repair_future_watermark)


def test_write_retell_state_merges_and_preserves_other_top_level_keys(fake_table):
    pid = uuid.uuid4()
    fake_table[pid] = _FakeRow(id=pid, poll_state={"other_key": "kept"})
    ok = op._write_retell_state(pid, {"bootstrapped": True})
    assert ok
    assert fake_table[pid].poll_state == {"other_key": "kept", "retell": {"bootstrapped": True}}


def test_write_retell_state_skipped_logs_and_returns_false(fake_table):
    pid = uuid.uuid4()  # never inserted into fake_table: the update matches 0 rows
    with capture_logs() as cap:
        ok = op._write_retell_state(pid, {"bootstrapped": True})
    assert ok is False
    assert cap == [{"event": "provider_poll_state_write_skipped", "log_level": "error", "provider_id": str(pid)}]


def test_advance_watermark_is_monotonic(fake_table):
    pid = uuid.uuid4()
    fake_table[pid] = _FakeRow(id=pid, last_fetched_at=_dt(2026, 1, 2))
    n = op._advance_watermark(pid, _dt(2026, 1, 1))  # earlier: no-op
    assert n == 0
    assert fake_table[pid].last_fetched_at == _dt(2026, 1, 2)
    n = op._advance_watermark(pid, _dt(2026, 1, 3))  # later: advances
    assert n == 1
    assert fake_table[pid].last_fetched_at == _dt(2026, 1, 3)


def test_advance_watermark_writes_null_watermark(fake_table):
    pid = uuid.uuid4()
    fake_table[pid] = _FakeRow(id=pid, last_fetched_at=None)
    n = op._advance_watermark(pid, _dt(2026, 1, 1))
    assert n == 1


def test_repair_future_watermark_only_fires_when_later_than_now(fake_table):
    pid = uuid.uuid4()
    fake_table[pid] = _FakeRow(id=pid, last_fetched_at=_dt(2026, 1, 5))
    now = _dt(2026, 1, 1)
    n = op._repair_future_watermark(pid, now, now - timedelta(hours=1))
    assert n == 1
    assert fake_table[pid].last_fetched_at == now - timedelta(hours=1)


def test_writers_update_a_soft_deleted_row_via_all_objects(fake_table):
    # Contract §4: "a primary-key write must never become a silent no-op
    # because the row was soft-deleted between the read and the write" — the
    # reason every writer uses `all_objects`. This fails if any of the three
    # writers (or `_write_retell_state`'s read) used `objects` instead: the
    # `objects` manager hides `deleted=True` rows, so the update would match
    # 0 rows and every assertion below would see the pre-write values.
    pid = uuid.uuid4()
    fake_table[pid] = _FakeRow(id=pid, last_fetched_at=_dt(2026, 1, 1), poll_state={}, deleted=True)

    # `objects` (soft-delete-filtered) must not see this row at all.
    with pytest.raises(op.ObservabilityProvider.DoesNotExist):
        op.ObservabilityProvider.objects.get(id=pid)

    assert op._advance_watermark(pid, _dt(2026, 1, 2)) == 1
    assert fake_table[pid].last_fetched_at == _dt(2026, 1, 2)

    assert op._repair_future_watermark(pid, _dt(2026, 1, 3), _dt(2026, 1, 10)) == 0  # 1/2 is not later than "now" 1/3
    n = op._repair_future_watermark(pid, _dt(2026, 1, 1), _dt(2026, 1, 4))  # 1/2 IS later than "now" 1/1
    assert n == 1
    assert fake_table[pid].last_fetched_at == _dt(2026, 1, 4)

    assert op._write_retell_state(pid, {"bootstrapped": True}) is True
    assert fake_table[pid].poll_state == {"retell": {"bootstrapped": True}}


# --------------------------------------------------------------------------
# _read_retell_state / poll_state normalization
# --------------------------------------------------------------------------


def test_read_retell_state_non_dict_poll_state_returns_empty():
    provider = SimpleNamespace(poll_state="not-a-dict")
    assert op._read_retell_state(provider) == {}


def test_read_retell_state_missing_retell_key_returns_empty():
    provider = SimpleNamespace(poll_state={"other": 1})
    assert op._read_retell_state(provider) == {}


def test_read_retell_state_deep_copies():
    original = {"retell": {"window": {"key": "abc"}}}
    provider = SimpleNamespace(poll_state=original)
    state = op._read_retell_state(provider)
    state["window"]["key"] = "mutated"
    assert original["retell"]["window"]["key"] == "abc"


# --------------------------------------------------------------------------
# Backoff gate
# --------------------------------------------------------------------------


def test_backoff_gate_skips_run_before_backoff_until(fake_table, monkeypatch):
    pid = uuid.uuid4()
    now = _dt(2026, 1, 1, 12, 0, 0)
    until = now + timedelta(minutes=5)
    row = _FakeRow(id=pid, poll_state={"retell": {"bootstrapped": True, "backoff_until": until.isoformat()}})
    fake_table[pid] = row
    provider = _retell_provider(row)
    _freeze_now(monkeypatch, now)
    monkeypatch.setattr(op.ObservabilityService, "fetch_retell_page", lambda *a, **k: pytest.fail("must not fetch"))

    result = op._poll_retell_provider(provider)
    assert result == op.StoreOutcome(0, 0, 0)


def test_backoff_expired_proceeds(fake_table, monkeypatch):
    pid = uuid.uuid4()
    now = _dt(2026, 1, 1, 12, 0, 0)
    until = now - timedelta(minutes=1)
    row = _FakeRow(id=pid, last_fetched_at=now - timedelta(hours=1), poll_state={"retell": {"bootstrapped": True, "backoff_until": until.isoformat()}})
    fake_table[pid] = row
    provider = _retell_provider(row)
    _freeze_now(monkeypatch, now)
    monkeypatch.setattr(op.ObservabilityService, "fetch_retell_page", lambda *a, **k: _page())
    monkeypatch.setattr(op, "process_and_store_logs", lambda *a, **k: op.StoreOutcome(0, 0, 0))

    result = op._poll_retell_provider(provider)
    assert result is not None  # not backoff-skipped


def test_backoff_until_non_string_is_ignored(fake_table, monkeypatch):
    pid = uuid.uuid4()
    now = _dt(2026, 1, 1, 12, 0, 0)
    row = _FakeRow(id=pid, last_fetched_at=now - timedelta(hours=1), poll_state={"retell": {"bootstrapped": True, "backoff_until": 12345}})
    fake_table[pid] = row
    provider = _retell_provider(row)
    _freeze_now(monkeypatch, now)
    monkeypatch.setattr(op.ObservabilityService, "fetch_retell_page", lambda *a, **k: _page())
    monkeypatch.setattr(op, "process_and_store_logs", lambda *a, **k: op.StoreOutcome(0, 0, 0))

    result = op._poll_retell_provider(provider)
    assert result is not None  # ignored, run proceeds


# --------------------------------------------------------------------------
# Bootstrap
# --------------------------------------------------------------------------


def test_bootstrap_runs_regardless_of_old_watermark_and_preserves_other_keys_discards_stale_window(fake_table, monkeypatch):
    pid = uuid.uuid4()
    now = _dt(2026, 1, 1, 12, 0, 0)
    row = _FakeRow(
        id=pid,
        last_fetched_at=_dt(2020, 1, 1),  # old watermark: irrelevant, bootstrap runs anyway (D10)
        poll_state={"other_top_level": "kept", "retell": {"window": {"start": "stale"}}},
    )
    fake_table[pid] = row
    provider = _retell_provider(row)
    _freeze_now(monkeypatch, now)
    seen_bounds = []

    def fake_fetch(prov, start, end, *, pagination_key=None, skip=None):
        seen_bounds.append((start, end))
        return _page(_calls(3))

    monkeypatch.setattr(op.ObservabilityService, "fetch_retell_page", fake_fetch)
    monkeypatch.setattr(op, "process_and_store_logs", lambda *a, **k: op.StoreOutcome(3, 0, 0))

    outcome = op._poll_retell_provider(provider)

    assert outcome == op.StoreOutcome(3, 0, 0)
    assert seen_bounds == [(None, now - op.RETELL_VISIBILITY_LAG)]
    assert fake_table[pid].poll_state["other_top_level"] == "kept"
    assert fake_table[pid].poll_state["retell"] == {"bootstrapped": True}  # stale window discarded
    assert fake_table[pid].last_fetched_at == now - op.RETELL_VISIBILITY_LAG


def test_bootstrap_has_more_is_logged_not_raised(fake_table, monkeypatch):
    pid = uuid.uuid4()
    now = _dt(2026, 1, 1, 12, 0, 0)
    row = _FakeRow(id=pid, poll_state={})
    fake_table[pid] = row
    provider = _retell_provider(row)
    _freeze_now(monkeypatch, now)
    monkeypatch.setattr(op.ObservabilityService, "fetch_retell_page", lambda *a, **k: _page(_calls(1000), has_more=True, next_key=None))
    monkeypatch.setattr(op, "process_and_store_logs", lambda *a, **k: op.StoreOutcome(1000, 0, 0))

    with capture_logs() as cap:
        outcome = op._poll_retell_provider(provider)  # must not raise

    assert outcome == op.StoreOutcome(1000, 0, 0)
    # §7: "bootstrap with has_more logged" — the counts event must actually
    # carry has_more=True, not just fail to raise.
    counts_events = [e for e in cap if e["event"] == "retell_poll_counts"]
    assert counts_events == [{
        "event": "retell_poll_counts", "log_level": "info", "provider_id": str(pid), "mode": "bootstrap",
        "pages_stored": 0, "stored": 1000, "malformed": 0, "export_failed": 0,
        "dropped_no_end": 0, "dropped_missing": 0, "dropped_failed": 0, "has_more": True,
    }]


def test_bootstrap_partial_twice_then_abandoned_on_third(fake_table, monkeypatch):
    pid = uuid.uuid4()
    now = _dt(2026, 1, 1, 12, 0, 0)
    row = _FakeRow(id=pid, poll_state={})
    fake_table[pid] = row
    _freeze_now(monkeypatch, now)
    monkeypatch.setattr(op.ObservabilityService, "fetch_retell_page", lambda *a, **k: _page(_calls(1)))
    # partial: some stored, one export_failed
    monkeypatch.setattr(op, "process_and_store_logs", lambda *a, **k: op.StoreOutcome(1, 0, 1))

    for i in range(1, 3):
        provider = _retell_provider(row)
        result = op._poll_retell_provider(provider)
        assert result is None  # retried, not abandoned yet
        assert fake_table[pid].poll_state["retell"]["failed_runs"] == i
        assert "bootstrapped" not in fake_table[pid].poll_state["retell"]

    provider = _retell_provider(row)
    with capture_logs() as cap:
        result = op._poll_retell_provider(provider)  # 3rd: abandoned, marker set
    assert result == op.StoreOutcome(1, 0, 1)
    assert fake_table[pid].poll_state["retell"] == {"bootstrapped": True}
    # pins impl L324-329's `logger.error("retell_page_abandoned", ...)` in the
    # bootstrap abandon branch — deleting/renaming that call leaves every
    # state/return-value assertion above unchanged, so only the event proves it fired.
    assert {"event": "retell_page_abandoned", "log_level": "error", "provider_id": str(pid),
            "abandoned": 1, "failed_runs": 3} in cap


def test_bootstrap_total_failure_backs_off_and_sets_no_marker(fake_table, monkeypatch):
    pid = uuid.uuid4()
    now = _dt(2026, 1, 1, 12, 0, 0)
    row = _FakeRow(id=pid, poll_state={})
    fake_table[pid] = row
    provider = _retell_provider(row)
    _freeze_now(monkeypatch, now)
    monkeypatch.setattr(op.ObservabilityService, "fetch_retell_page", lambda *a, **k: _page())
    monkeypatch.setattr(op, "process_and_store_logs", lambda *a, **k: op.StoreOutcome(0, 0, 500))

    result = op._poll_retell_provider(provider)
    assert result is None
    retell_state = fake_table[pid].poll_state["retell"]
    assert "bootstrapped" not in retell_state
    assert retell_state["total_failures"] == 1
    assert "backoff_until" in retell_state


# --------------------------------------------------------------------------
# Windowed runs
# --------------------------------------------------------------------------


def test_windowed_single_page_pops_window_and_advances_watermark(fake_table, monkeypatch):
    pid = uuid.uuid4()
    now = _dt(2026, 1, 1, 12, 0, 0)
    wm = now - timedelta(minutes=30)
    row = _FakeRow(id=pid, last_fetched_at=wm, poll_state={"retell": {"bootstrapped": True}})
    fake_table[pid] = row
    provider = _retell_provider(row)
    _freeze_now(monkeypatch, now)
    monkeypatch.setattr(op.ObservabilityService, "fetch_retell_page", lambda *a, **k: _page(_calls(2), has_more=False))
    monkeypatch.setattr(op, "process_and_store_logs", lambda *a, **k: op.StoreOutcome(2, 0, 0))

    outcome = op._poll_retell_provider(provider)
    assert outcome == op.StoreOutcome(2, 0, 0)
    assert "window" not in fake_table[pid].poll_state["retell"]
    assert fake_table[pid].last_fetched_at == now - op.RETELL_VISIBILITY_LAG


def test_retell_page_all_malformed_logged_when_ok_verdict_has_zero_stored(fake_table, monkeypatch):
    # §7 / `_classify` boundary "1000 malformed -> ok + retell_page_all_malformed",
    # reached through the real orchestrator (not `process_and_store_logs`
    # directly) — every other seeded `StoreOutcome` in this file has
    # malformed=0 except the one test that bypasses `_poll_retell_provider`.
    pid = uuid.uuid4()
    now = _dt(2026, 1, 1, 12, 0, 0)
    wm = now - timedelta(minutes=30)
    row = _FakeRow(id=pid, last_fetched_at=wm, poll_state={"retell": {"bootstrapped": True}})
    fake_table[pid] = row
    provider = _retell_provider(row)
    _freeze_now(monkeypatch, now)
    monkeypatch.setattr(op.ObservabilityService, "fetch_retell_page", lambda *a, **k: _page(_calls(5), has_more=False))
    monkeypatch.setattr(op, "process_and_store_logs", lambda *a, **k: op.StoreOutcome(0, 5, 0))

    with capture_logs() as cap:
        outcome = op._poll_retell_provider(provider)

    assert outcome == op.StoreOutcome(0, 5, 0)
    # pins impl L407-408's `logger.warning("retell_page_all_malformed", ...)`
    assert {"event": "retell_page_all_malformed", "log_level": "warning", "provider_id": str(pid), "malformed": 5} in cap
    assert "window" not in fake_table[pid].poll_state["retell"]  # ok verdict: the window still completes normally


def test_windowed_multi_page_persists_key_no_advance_then_advances_on_last_page(fake_table, monkeypatch):
    pid = uuid.uuid4()
    now = _dt(2026, 1, 1, 12, 0, 0)
    wm = now - timedelta(minutes=30)
    row = _FakeRow(id=pid, last_fetched_at=wm, poll_state={"retell": {"bootstrapped": True}})
    fake_table[pid] = row
    _freeze_now(monkeypatch, now)
    monkeypatch.setattr(op.ObservabilityService, "fetch_retell_page", lambda *a, **k: _page(_calls(1, "p1"), has_more=True, next_key="cursor-1"))
    monkeypatch.setattr(op, "process_and_store_logs", lambda *a, **k: op.StoreOutcome(1, 0, 0))

    provider = _retell_provider(row)
    outcome = op._poll_retell_provider(provider)
    assert outcome == op.StoreOutcome(1, 0, 0)
    window = fake_table[pid].poll_state["retell"]["window"]
    assert window["key"] == "cursor-1"
    assert window["pages_stored"] == 1
    assert fake_table[pid].last_fetched_at == wm  # unchanged mid-window

    monkeypatch.setattr(op.ObservabilityService, "fetch_retell_page", lambda *a, **k: _page(_calls(1, "p2"), has_more=False))
    provider = _retell_provider(row)
    outcome = op._poll_retell_provider(provider)
    assert outcome == op.StoreOutcome(1, 0, 0)
    assert "window" not in fake_table[pid].poll_state["retell"]
    assert fake_table[pid].last_fetched_at > wm


def test_next_window_starts_where_the_completed_one_ended(fake_table, monkeypatch):
    # §7: "a completed window is popped and the next run opens a new one
    # starting at its end" — not just that the watermark moved.
    pid = uuid.uuid4()
    now = _dt(2026, 1, 1, 12, 0, 0)
    wm = now - timedelta(minutes=30)
    row = _FakeRow(id=pid, last_fetched_at=wm, poll_state={"retell": {"bootstrapped": True}})
    fake_table[pid] = row
    _freeze_now(monkeypatch, now)
    monkeypatch.setattr(op.ObservabilityService, "fetch_retell_page", lambda *a, **k: _page(_calls(1), has_more=False))
    monkeypatch.setattr(op, "process_and_store_logs", lambda *a, **k: op.StoreOutcome(1, 0, 0))

    provider = _retell_provider(row)
    op._poll_retell_provider(provider)
    first_window_end = fake_table[pid].last_fetched_at  # the watermark IS the completed window's end
    assert "window" not in fake_table[pid].poll_state["retell"]

    later = now + timedelta(minutes=5)
    _freeze_now(monkeypatch, later)
    seen_starts = []
    monkeypatch.setattr(
        op.ObservabilityService, "fetch_retell_page",
        lambda prov, start, end, **k: seen_starts.append(start) or _page(_calls(1), has_more=False),
    )
    provider = _retell_provider(fake_table[pid])
    op._poll_retell_provider(provider)

    assert seen_starts == [first_window_end]


def test_windowed_start_caught_up_to_end_is_a_no_op(fake_table, monkeypatch):
    # An empty range must be a no-op, not a window of zero width.
    # — reached when no window is in progress and the watermark has already
    # caught up to "now minus the visibility lag". Pins that early return: a
    # mutation dropping it would instead try to open a window with a
    # start >= end, and this test's `fetch_retell_page` would be called.
    pid = uuid.uuid4()
    now = _dt(2026, 1, 1, 12, 0, 0)
    wm = now  # the watermark is already at "now": start >= end after the lag is subtracted
    row = _FakeRow(id=pid, last_fetched_at=wm, poll_state={"retell": {"bootstrapped": True}})
    fake_table[pid] = row
    provider = _retell_provider(row)
    _freeze_now(monkeypatch, now)
    monkeypatch.setattr(op.ObservabilityService, "fetch_retell_page", lambda *a, **k: pytest.fail("must not fetch: start >= end"))

    outcome = op._poll_retell_provider(provider)

    assert outcome == op.StoreOutcome(0, 0, 0)
    assert "window" not in fake_table[pid].poll_state["retell"]
    assert fake_table[pid].last_fetched_at == wm  # untouched


def test_digest_history_caps_at_eight_evicts_oldest_newest_last(fake_table, monkeypatch):
    # §7: "a page digest seen within the last 8 pages -> restart" implies the
    # history is actually bounded and ordered; every other test only ever
    # seeds 0 or 1 digest, so eviction and ordering were never exercised.
    pid = uuid.uuid4()
    now = _dt(2026, 1, 1, 12, 0, 0)
    wm = now - timedelta(hours=1)
    old_digests = [f"digest_{i}" for i in range(op.RETELL_DIGEST_HISTORY)]  # already at the cap
    window = {"start": wm.isoformat(), "end": now.isoformat(), "opened_at_hint": False, "narrowed": False,
              "key": "k8", "skip": None, "pages_stored": op.RETELL_DIGEST_HISTORY, "digests": list(old_digests), "restarts": 0}
    row = _FakeRow(id=pid, last_fetched_at=wm, poll_state={"retell": {"bootstrapped": True, "window": window}})
    fake_table[pid] = row
    provider = _retell_provider(row)
    _freeze_now(monkeypatch, now)
    monkeypatch.setattr(op.ObservabilityService, "fetch_retell_page", lambda *a, **k: _page(_calls(1, "fresh"), has_more=True, next_key="k9"))
    monkeypatch.setattr(op, "process_and_store_logs", lambda *a, **k: op.StoreOutcome(1, 0, 0))

    op._poll_retell_provider(provider)

    new_digests = fake_table[pid].poll_state["retell"]["window"]["digests"]
    expected_new_digest = op._page_digest(_calls(1, "fresh"))
    assert len(new_digests) == op.RETELL_DIGEST_HISTORY  # still capped, not 9
    assert new_digests[-1] == expected_new_digest  # newest last
    assert old_digests[0] not in new_digests  # oldest (index 0) evicted
    assert new_digests[:-1] == old_digests[1:]  # the remaining 7 shift down in order


def test_cursor_rejected_restarts_window_with_cause(fake_table, monkeypatch):
    pid = uuid.uuid4()
    now = _dt(2026, 1, 1, 12, 0, 0)
    wm = now - timedelta(minutes=30)
    window = {"start": wm.isoformat(), "end": now.isoformat(), "opened_at_hint": False, "narrowed": False,
              "key": "stale", "skip": None, "pages_stored": 1, "digests": ["deadbeef"], "restarts": 0}
    row = _FakeRow(id=pid, last_fetched_at=wm, poll_state={"retell": {"bootstrapped": True, "window": window}})
    fake_table[pid] = row
    provider = _retell_provider(row)
    _freeze_now(monkeypatch, now)

    def raise_rejected(*a, **k):
        raise RetellCursorRejected(cause="missing_key")

    monkeypatch.setattr(op.ObservabilityService, "fetch_retell_page", raise_rejected)

    with capture_logs() as cap:
        result = op._poll_retell_provider(provider)
    assert result is None
    new_window = fake_table[pid].poll_state["retell"]["window"]
    assert new_window["restarts"] == 1
    assert new_window["key"] is None
    assert new_window["pages_stored"] == 0
    assert new_window["digests"] == []
    # `narrowed` marks a halving, not a restart; the hint logic depends on the difference.
    # (RETELL_MAX_WINDOW_RESTARTS=3) — only the cap-triggered halving branch
    # (impl L509) may set it True. This is the common case with a flaky
    # cursor, so a mutation that sets it unconditionally corrupts poll_state
    # on the steady-state path, not just an edge case.
    assert new_window["narrowed"] is False
    # the test's own name promises the cause reaches the event, not just the state
    assert {"event": "retell_window_restarted", "log_level": "warning", "provider_id": str(pid),
            "cause": "missing_key", "restarts": 1, "pages_stored": 1} in cap


def test_repeated_digest_restarts_window(fake_table, monkeypatch):
    pid = uuid.uuid4()
    now = _dt(2026, 1, 1, 12, 0, 0)
    wm = now - timedelta(minutes=30)
    window = {"start": wm.isoformat(), "end": now.isoformat(), "opened_at_hint": False, "narrowed": False,
              "key": "k1", "skip": None, "pages_stored": 1, "digests": [op._page_digest(_calls(1, "dup"))], "restarts": 0}
    row = _FakeRow(id=pid, last_fetched_at=wm, poll_state={"retell": {"bootstrapped": True, "window": window}})
    fake_table[pid] = row
    provider = _retell_provider(row)
    _freeze_now(monkeypatch, now)
    monkeypatch.setattr(op.ObservabilityService, "fetch_retell_page", lambda *a, **k: _page(_calls(1, "dup"), has_more=True, next_key="k2"))

    result = op._poll_retell_provider(provider)
    assert result is None
    assert fake_table[pid].poll_state["retell"]["window"]["restarts"] == 1


def test_empty_page_with_has_more_after_first_page_restarts(fake_table, monkeypatch):
    pid = uuid.uuid4()
    now = _dt(2026, 1, 1, 12, 0, 0)
    wm = now - timedelta(minutes=30)
    window = {"start": wm.isoformat(), "end": now.isoformat(), "opened_at_hint": False, "narrowed": False,
              "key": "k1", "skip": None, "pages_stored": 1, "digests": ["deadbeef"], "restarts": 0}
    row = _FakeRow(id=pid, last_fetched_at=wm, poll_state={"retell": {"bootstrapped": True, "window": window}})
    fake_table[pid] = row
    provider = _retell_provider(row)
    _freeze_now(monkeypatch, now)
    monkeypatch.setattr(op.ObservabilityService, "fetch_retell_page", lambda *a, **k: _page([], has_more=True, next_key="k2"))

    result = op._poll_retell_provider(provider)
    assert result is None
    assert fake_table[pid].poll_state["retell"]["window"]["restarts"] == 1


def test_page_cap_restarts_window(fake_table, monkeypatch):
    pid = uuid.uuid4()
    now = _dt(2026, 1, 1, 12, 0, 0)
    wm = now - timedelta(minutes=30)
    window = {"start": wm.isoformat(), "end": now.isoformat(), "opened_at_hint": False, "narrowed": False,
              "key": "k1", "skip": None, "pages_stored": op.RETELL_MAX_PAGES_PER_WINDOW - 1, "digests": [], "restarts": 0}
    row = _FakeRow(id=pid, last_fetched_at=wm, poll_state={"retell": {"bootstrapped": True, "window": window}})
    fake_table[pid] = row
    provider = _retell_provider(row)
    _freeze_now(monkeypatch, now)
    monkeypatch.setattr(op.ObservabilityService, "fetch_retell_page", lambda *a, **k: _page(_calls(1, "capped"), has_more=True, next_key="k2"))

    with capture_logs() as cap:
        result = op._poll_retell_provider(provider)
    assert result is None
    assert fake_table[pid].poll_state["retell"]["window"]["restarts"] == 1
    # a mutation that passes a constant cause into every restart must not survive this
    assert {"event": "retell_window_restarted", "log_level": "warning", "provider_id": str(pid),
            "cause": "page_cap", "restarts": 1, "pages_stored": op.RETELL_MAX_PAGES_PER_WINDOW - 1} in cap


def test_three_restarts_halve_window_and_set_hint(fake_table, monkeypatch):
    pid = uuid.uuid4()
    now = _dt(2026, 1, 1, 12, 0, 0)
    start = now - timedelta(hours=2)
    window = {"start": start.isoformat(), "end": now.isoformat(), "opened_at_hint": True, "narrowed": False,
              "key": "k1", "skip": None, "pages_stored": 1, "digests": ["deadbeef"], "restarts": 2}
    row = _FakeRow(id=pid, last_fetched_at=start, poll_state={"retell": {"bootstrapped": True, "window": window}})
    fake_table[pid] = row
    provider = _retell_provider(row)
    _freeze_now(monkeypatch, now)
    monkeypatch.setattr(op.ObservabilityService, "fetch_retell_page", lambda *a, **k: _page([], has_more=True, next_key="k2"))

    result = op._poll_retell_provider(provider)
    assert result is None
    new_window = fake_table[pid].poll_state["retell"]["window"]
    assert new_window["restarts"] == 0
    assert new_window["narrowed"] is True
    expected_width = (now - start) / 2
    assert fake_table[pid].poll_state["retell"]["window_hint_seconds"] == int(expected_width.total_seconds())


def test_hint_caps_new_window_width(fake_table, monkeypatch):
    pid = uuid.uuid4()
    now = _dt(2026, 1, 1, 12, 0, 0)
    wm = now - timedelta(hours=10)  # much wider than any hint
    row = _FakeRow(id=pid, last_fetched_at=wm, poll_state={"retell": {"bootstrapped": True, "window_hint_seconds": 3600}})
    fake_table[pid] = row
    provider = _retell_provider(row)
    _freeze_now(monkeypatch, now)
    seen = []

    def fake_fetch(prov, start, end, *, pagination_key=None, skip=None):
        seen.append((start, end))
        return _page(_calls(1), has_more=False)

    monkeypatch.setattr(op.ObservabilityService, "fetch_retell_page", fake_fetch)
    monkeypatch.setattr(op, "process_and_store_logs", lambda *a, **k: op.StoreOutcome(1, 0, 0))

    op._poll_retell_provider(provider)
    (start_seen, end_seen), = seen
    assert start_seen == wm
    assert (end_seen - start_seen) == timedelta(seconds=3600)


def test_window_narrower_than_hint_does_not_count_toward_streak(fake_table, monkeypatch):
    pid = uuid.uuid4()
    now = _dt(2026, 1, 1, 12, 0, 0)
    wm = now - timedelta(minutes=5)  # natural width well under the 6h hint
    row = _FakeRow(id=pid, last_fetched_at=wm, poll_state={"retell": {"bootstrapped": True}})
    fake_table[pid] = row
    provider = _retell_provider(row)
    _freeze_now(monkeypatch, now)
    monkeypatch.setattr(op.ObservabilityService, "fetch_retell_page", lambda *a, **k: _page(_calls(1), has_more=False))
    monkeypatch.setattr(op, "process_and_store_logs", lambda *a, **k: op.StoreOutcome(1, 0, 0))

    op._poll_retell_provider(provider)
    assert "one_page_streak" not in fake_table[pid].poll_state["retell"]


def test_narrowed_window_one_page_result_does_not_grow_hint(fake_table, monkeypatch):
    pid = uuid.uuid4()
    now = _dt(2026, 1, 1, 12, 0, 0)
    start = now - timedelta(minutes=10)
    window = {"start": start.isoformat(), "end": now.isoformat(), "opened_at_hint": True, "narrowed": True,
              "key": None, "skip": None, "pages_stored": 0, "digests": [], "restarts": 0}
    row = _FakeRow(id=pid, last_fetched_at=start, poll_state={"retell": {"bootstrapped": True, "window": window, "window_hint_seconds": 600}})
    fake_table[pid] = row
    provider = _retell_provider(row)
    _freeze_now(monkeypatch, now)
    monkeypatch.setattr(op.ObservabilityService, "fetch_retell_page", lambda *a, **k: _page(_calls(1), has_more=False))
    monkeypatch.setattr(op, "process_and_store_logs", lambda *a, **k: op.StoreOutcome(1, 0, 0))

    op._poll_retell_provider(provider)
    retell_state = fake_table[pid].poll_state["retell"]
    assert "one_page_streak" not in retell_state
    assert retell_state["window_hint_seconds"] == 600  # unchanged


def test_three_one_page_windows_at_hint_double_it(fake_table, monkeypatch):
    pid = uuid.uuid4()
    now = _dt(2026, 1, 1, 12, 0, 0)
    hint_seconds = 3600
    row = _FakeRow(id=pid, poll_state={"retell": {"bootstrapped": True, "window_hint_seconds": hint_seconds}})
    fake_table[pid] = row
    monkeypatch.setattr(op.ObservabilityService, "fetch_retell_page", lambda *a, **k: _page(_calls(1), has_more=False))
    monkeypatch.setattr(op, "process_and_store_logs", lambda *a, **k: op.StoreOutcome(1, 0, 0))

    for i in range(1, op.RETELL_WINDOW_GROW_AFTER + 1):
        current_now = now + (i - 1) * timedelta(seconds=hint_seconds)
        _freeze_now(monkeypatch, current_now)
        # Set the watermark so the frozen window is exactly hint-width after
        # the visibility lag is subtracted (opened_at_hint requires >= hint).
        fake_table[pid].last_fetched_at = current_now - timedelta(seconds=hint_seconds) - op.RETELL_VISIBILITY_LAG
        provider = _retell_provider(fake_table[pid])
        op._poll_retell_provider(provider)

    retell_state = fake_table[pid].poll_state["retell"]
    assert retell_state["window_hint_seconds"] == hint_seconds * 2
    assert retell_state.get("one_page_streak", 0) == 0


def test_one_page_streak_counts_regardless_of_page_size(fake_table, monkeypatch):
    # §7: "a window with 500-1000 calls at the hint still counts toward the
    # streak (no dead band)" — only whether the window was opened at the hint
    # and finished on page 1 matters, never how many calls it held.
    pid = uuid.uuid4()
    now = _dt(2026, 1, 1, 12, 0, 0)
    hint_seconds = 3600
    row = _FakeRow(
        id=pid,
        last_fetched_at=now - timedelta(seconds=hint_seconds) - op.RETELL_VISIBILITY_LAG,
        poll_state={"retell": {"bootstrapped": True, "window_hint_seconds": hint_seconds}},
    )
    fake_table[pid] = row
    provider = _retell_provider(row)
    _freeze_now(monkeypatch, now)
    monkeypatch.setattr(op.ObservabilityService, "fetch_retell_page", lambda *a, **k: _page(_calls(750), has_more=False))
    monkeypatch.setattr(op, "process_and_store_logs", lambda *a, **k: op.StoreOutcome(750, 0, 0))

    op._poll_retell_provider(provider)

    assert fake_table[pid].poll_state["retell"]["one_page_streak"] == 1


def test_hint_doubling_is_capped_at_max(fake_table, monkeypatch):
    # §7: "three consecutive one-page windows opened at the hint double it
    # (capped)" — start close enough to RETELL_WINDOW_HINT_MAX that a plain
    # doubling would overshoot it; the result must clamp, not exceed.
    pid = uuid.uuid4()
    now = _dt(2026, 1, 1, 12, 0, 0)
    hint_seconds = int(op.RETELL_WINDOW_HINT_MAX.total_seconds()) - 100
    row = _FakeRow(id=pid, poll_state={"retell": {"bootstrapped": True, "window_hint_seconds": hint_seconds}})
    fake_table[pid] = row
    monkeypatch.setattr(op.ObservabilityService, "fetch_retell_page", lambda *a, **k: _page(_calls(1), has_more=False))
    monkeypatch.setattr(op, "process_and_store_logs", lambda *a, **k: op.StoreOutcome(1, 0, 0))

    for i in range(1, op.RETELL_WINDOW_GROW_AFTER + 1):
        current_now = now + (i - 1) * timedelta(seconds=hint_seconds)
        _freeze_now(monkeypatch, current_now)
        fake_table[pid].last_fetched_at = current_now - timedelta(seconds=hint_seconds) - op.RETELL_VISIBILITY_LAG
        provider = _retell_provider(fake_table[pid])
        op._poll_retell_provider(provider)

    retell_state = fake_table[pid].poll_state["retell"]
    assert retell_state["window_hint_seconds"] == int(op.RETELL_WINDOW_HINT_MAX.total_seconds())


def test_multi_page_window_completed_by_cursor_drops_hint_entirely(fake_table, monkeypatch):
    pid = uuid.uuid4()
    now = _dt(2026, 1, 1, 12, 0, 0)
    start = now - timedelta(hours=1)
    window = {"start": start.isoformat(), "end": now.isoformat(), "opened_at_hint": False, "narrowed": False,
              "key": "k1", "skip": None, "pages_stored": 1, "digests": ["deadbeef"], "restarts": 0}
    row = _FakeRow(id=pid, last_fetched_at=start, poll_state={"retell": {"bootstrapped": True, "window": window, "window_hint_seconds": 900}})
    fake_table[pid] = row
    provider = _retell_provider(row)
    _freeze_now(monkeypatch, now)
    monkeypatch.setattr(op.ObservabilityService, "fetch_retell_page", lambda *a, **k: _page(_calls(1, "final"), has_more=False))
    monkeypatch.setattr(op, "process_and_store_logs", lambda *a, **k: op.StoreOutcome(1, 0, 0))

    op._poll_retell_provider(provider)
    retell_state = fake_table[pid].poll_state["retell"]
    assert "window_hint_seconds" not in retell_state
    assert "one_page_streak" not in retell_state


def test_poll_behind_fires_on_completed_window_older_than_warn_threshold(fake_table, monkeypatch):
    pid = uuid.uuid4()
    now = _dt(2026, 1, 1, 12, 0, 0)
    start = now - op.RETELL_BEHIND_WARN - timedelta(minutes=30) - timedelta(minutes=1)
    end = now - op.RETELL_BEHIND_WARN - timedelta(minutes=30)
    window = {"start": start.isoformat(), "end": end.isoformat(), "opened_at_hint": False, "narrowed": False,
              "key": None, "skip": None, "pages_stored": 0, "digests": [], "restarts": 0}
    row = _FakeRow(id=pid, last_fetched_at=start, poll_state={"retell": {"bootstrapped": True, "window": window}})
    fake_table[pid] = row
    provider = _retell_provider(row)
    _freeze_now(monkeypatch, now)
    monkeypatch.setattr(op.ObservabilityService, "fetch_retell_page", lambda *a, **k: _page(_calls(1), has_more=False))
    monkeypatch.setattr(op, "process_and_store_logs", lambda *a, **k: op.StoreOutcome(1, 0, 0))
    logged = []
    monkeypatch.setattr(op, "_log_behind", lambda *a, **k: logged.append(a))

    op._poll_retell_provider(provider)
    assert logged  # fired even though has_more was False


# Never monkeypatch methods on ``op.logger``: it is a structlog lazy proxy,
# and monkeypatch's undo writes the temporary bound method back onto the
# proxy as an instance attribute, pinning that level to a stale processor
# chain for every later test in the session (capture_logs() then sees nothing
# at that level). Always observe events through ``capture_logs()``.


def test_frontier_older_than_error_threshold_logs_stalled():
    now = _dt(2026, 1, 1, 12, 0, 0)
    window_end = now - op.RETELL_BEHIND_ERROR - timedelta(minutes=1)

    with capture_logs() as cap:
        op._log_behind("provider-1", now, window_end, 3)
    assert cap[0]["event"] == "retell_poll_stalled"
    assert cap[0]["log_level"] == "error"


def test_frontier_within_error_threshold_logs_warning():
    now = _dt(2026, 1, 1, 12, 0, 0)
    window_end = now - op.RETELL_BEHIND_WARN - timedelta(minutes=1)

    with capture_logs() as cap:
        op._log_behind("provider-1", now, window_end, 3)
    assert cap[0]["event"] == "retell_poll_behind"
    assert cap[0]["log_level"] == "warning"


# --------------------------------------------------------------------------
# State validation
# --------------------------------------------------------------------------


def test_malformed_window_in_state_treated_as_absent(fake_table, monkeypatch):
    pid = uuid.uuid4()
    now = _dt(2026, 1, 1, 12, 0, 0)
    wm = now - timedelta(minutes=10)
    row = _FakeRow(id=pid, last_fetched_at=wm, poll_state={"retell": {"bootstrapped": True, "window": {"start": "not-a-window"}}})
    fake_table[pid] = row
    provider = _retell_provider(row)
    _freeze_now(monkeypatch, now)
    monkeypatch.setattr(op.ObservabilityService, "fetch_retell_page", lambda *a, **k: _page(_calls(1), has_more=False))
    monkeypatch.setattr(op, "process_and_store_logs", lambda *a, **k: op.StoreOutcome(1, 0, 0))

    result = op._poll_retell_provider(provider)  # must not raise
    assert result is not None


def test_poisoned_watermark_uses_lookback_and_is_repaired(fake_table, monkeypatch):
    pid = uuid.uuid4()
    now = _dt(2026, 1, 1, 12, 0, 0)
    future_wm = now + timedelta(hours=2)
    row = _FakeRow(id=pid, last_fetched_at=future_wm, poll_state={"retell": {"bootstrapped": True}})
    fake_table[pid] = row
    provider = _retell_provider(row)
    _freeze_now(monkeypatch, now)
    monkeypatch.setattr(op.ObservabilityService, "fetch_retell_page", lambda *a, **k: _page(_calls(1), has_more=False))
    monkeypatch.setattr(op, "process_and_store_logs", lambda *a, **k: op.StoreOutcome(1, 0, 0))

    op._poll_retell_provider(provider)
    assert fake_table[pid].last_fetched_at != future_wm  # repaired away from the poisoned value


def test_missing_watermark_after_bootstrap_uses_lookback(fake_table, monkeypatch):
    pid = uuid.uuid4()
    now = _dt(2026, 1, 1, 12, 0, 0)
    row = _FakeRow(id=pid, last_fetched_at=None, poll_state={"retell": {"bootstrapped": True}})
    fake_table[pid] = row
    provider = _retell_provider(row)
    _freeze_now(monkeypatch, now)
    seen = []
    monkeypatch.setattr(op.ObservabilityService, "fetch_retell_page", lambda prov, start, end, **k: seen.append((start, end)) or _page(_calls(1), has_more=False))
    monkeypatch.setattr(op, "process_and_store_logs", lambda *a, **k: op.StoreOutcome(1, 0, 0))

    op._poll_retell_provider(provider)
    (start, _end), = seen
    assert start == (now - op.RETELL_VISIBILITY_LAG - op.RETELL_FUTURE_WATERMARK_LOOKBACK)


def test_provider_poll_state_write_skipped_when_zero_rows_updated_via_poll(fake_table, monkeypatch):
    # Unlike the direct-helper test above, this exercises the event through a
    # real call site: the provider row vanishes from the table between the
    # in-memory bootstrap and `_poll_retell_provider`'s final state write, so
    # the update matches 0 rows and the run must end with no marker set.
    pid = uuid.uuid4()  # deliberately never inserted into fake_table
    provider = SimpleNamespace(
        id=pid, provider=ProviderChoices.RETELL, poll_state={},
        last_fetched_at=None, project=SimpleNamespace(id="project-1", organization_id="org-1"),
    )
    monkeypatch.setattr(op.ObservabilityService, "fetch_retell_page", lambda *a, **k: _page(_calls(1)))
    monkeypatch.setattr(op, "process_and_store_logs", lambda *a, **k: op.StoreOutcome(1, 0, 0))

    with capture_logs() as cap:
        result = op._poll_retell_provider(provider)

    assert result is None
    assert {"event": "provider_poll_state_write_skipped", "log_level": "error", "provider_id": str(pid)} in cap
    assert pid not in fake_table


# --------------------------------------------------------------------------
# Offset fallback
# --------------------------------------------------------------------------


def test_window_at_min_width_falls_back_to_offset_mode(fake_table):
    pid = uuid.uuid4()
    now = _dt(2026, 1, 1, 12, 0, 0)
    start = now - timedelta(seconds=1)  # already at RETELL_MIN_WINDOW: halving is no longer an option
    window = {"start": start.isoformat(), "end": now.isoformat(), "opened_at_hint": False, "narrowed": True,
              "key": "k1", "skip": None, "pages_stored": 0, "digests": [], "restarts": 2}  # about to hit the restart cap
    row = _FakeRow(id=pid, poll_state={"retell": {"bootstrapped": True, "window": window}})
    fake_table[pid] = row
    state = op._read_retell_state(_retell_provider(row))
    state["window"] = window

    op._restart_window(pid, state, cause="page_cap")

    new_window = fake_table[pid].poll_state["retell"]["window"]
    assert new_window["restarts"] == 0  # reset on entering offset mode
    assert new_window["skip"] == 0  # offset mode entered: halving was not possible at the 1s floor


def test_offset_mode_skip_increments_by_page_limit(fake_table, monkeypatch):
    pid = uuid.uuid4()
    now = _dt(2026, 1, 1, 12, 0, 0)
    start = now - timedelta(seconds=1)
    window = {"start": start.isoformat(), "end": now.isoformat(), "opened_at_hint": False, "narrowed": True,
              "key": None, "skip": 0, "pages_stored": 0, "digests": [], "restarts": 0}
    row = _FakeRow(id=pid, last_fetched_at=start, poll_state={"retell": {"bootstrapped": True, "window": window}})
    fake_table[pid] = row
    provider = _retell_provider(row)
    _freeze_now(monkeypatch, now)
    seen_skip = []

    def fake_fetch(prov, s, e, *, pagination_key=None, skip=None):
        seen_skip.append(skip)
        return _page(_calls(1000), has_more=True)

    monkeypatch.setattr(op.ObservabilityService, "fetch_retell_page", fake_fetch)
    monkeypatch.setattr(op, "process_and_store_logs", lambda *a, **k: op.StoreOutcome(1000, 0, 0))

    op._poll_retell_provider(provider)
    assert seen_skip == [0]
    assert fake_table[pid].poll_state["retell"]["window"]["skip"] == op.RETELL_LIST_PAGE_LIMIT


def test_offset_failure_stalls_loudly(fake_table, monkeypatch):
    pid = uuid.uuid4()
    now = _dt(2026, 1, 1, 12, 0, 0)
    start = now - timedelta(seconds=1)
    # Already in offset mode (skip=0) and about to hit the restart cap again:
    # per D15, this is the last-resort path with nowhere further to fall back.
    window = {"start": start.isoformat(), "end": now.isoformat(), "opened_at_hint": False, "narrowed": True,
              "key": None, "skip": 0, "pages_stored": 0, "digests": [], "restarts": 2}
    row = _FakeRow(id=pid, poll_state={"retell": {"bootstrapped": True, "window": window}})
    fake_table[pid] = row
    state = op._read_retell_state(_retell_provider(row))
    state["window"] = window
    with capture_logs() as cap:
        op._restart_window(pid, state, cause="page_cap")

    new_window = fake_table[pid].poll_state["retell"]["window"]
    assert new_window["skip"] == 0
    assert new_window["restarts"] == 0  # reset, but the run never advances
    assert "retell_window_stuck" in [e["event"] for e in cap if e["log_level"] == "error"]


# --------------------------------------------------------------------------
# Partial / total failure handling for windowed runs
# --------------------------------------------------------------------------


def test_windowed_partial_failure_twice_then_abandoned_third(fake_table, monkeypatch):
    pid = uuid.uuid4()
    now = _dt(2026, 1, 1, 12, 0, 0)
    wm = now - timedelta(minutes=10)
    row = _FakeRow(id=pid, last_fetched_at=wm, poll_state={"retell": {"bootstrapped": True}})
    fake_table[pid] = row
    _freeze_now(monkeypatch, now)
    monkeypatch.setattr(op.ObservabilityService, "fetch_retell_page", lambda *a, **k: _page(_calls(2), has_more=False))
    monkeypatch.setattr(op, "process_and_store_logs", lambda *a, **k: op.StoreOutcome(1, 0, 1))

    for i in range(1, 3):
        provider = _retell_provider(fake_table[pid])
        result = op._poll_retell_provider(provider)
        assert result is None
        assert fake_table[pid].poll_state["retell"]["failed_runs"] == i
        assert "window" in fake_table[pid].poll_state["retell"]

    provider = _retell_provider(fake_table[pid])
    with capture_logs() as cap:
        outcome = op._poll_retell_provider(provider)
    assert outcome == op.StoreOutcome(1, 0, 1)
    assert "failed_runs" not in fake_table[pid].poll_state["retell"]
    assert "window" not in fake_table[pid].poll_state["retell"]  # completed and popped
    # pins impl L400-405's windowed `logger.error("retell_page_abandoned", ...)`
    assert {"event": "retell_page_abandoned", "log_level": "error", "provider_id": str(pid),
            "abandoned": 1, "failed_runs": 3} in cap


def test_windowed_total_failure_backs_off_doubles_and_caps_then_clears_on_success(fake_table, monkeypatch):
    pid = uuid.uuid4()
    now = _dt(2026, 1, 1, 12, 0, 0)
    wm = now - timedelta(minutes=10)
    row = _FakeRow(id=pid, last_fetched_at=wm, poll_state={"retell": {"bootstrapped": True}})
    fake_table[pid] = row
    _freeze_now(monkeypatch, now)
    monkeypatch.setattr(op.ObservabilityService, "fetch_retell_page", lambda *a, **k: _page(_calls(1), has_more=False))
    monkeypatch.setattr(op, "process_and_store_logs", lambda *a, **k: op.StoreOutcome(0, 0, 1))

    provider = _retell_provider(fake_table[pid])
    op._poll_retell_provider(provider)
    assert fake_table[pid].poll_state["retell"]["total_failures"] == 1
    first_backoff = op._parse(fake_table[pid].poll_state["retell"]["backoff_until"])

    # simulate backoff expiring, run again while still failing
    _freeze_now(monkeypatch, first_backoff + timedelta(seconds=1))
    provider = _retell_provider(fake_table[pid])
    op._poll_retell_provider(provider)
    assert fake_table[pid].poll_state["retell"]["total_failures"] == 2

    # now succeed: total_failures / backoff_until must clear
    monkeypatch.setattr(op, "process_and_store_logs", lambda *a, **k: op.StoreOutcome(1, 0, 0))
    second_backoff = op._parse(fake_table[pid].poll_state["retell"]["backoff_until"])
    _freeze_now(monkeypatch, second_backoff + timedelta(seconds=1))
    provider = _retell_provider(fake_table[pid])
    op._poll_retell_provider(provider)
    assert "total_failures" not in fake_table[pid].poll_state["retell"]
    assert "backoff_until" not in fake_table[pid].poll_state["retell"]


def test_backoff_never_overflows_after_many_failures():
    assert op._backoff_delay(40) == op.RETELL_BACKOFF_MAX


# --------------------------------------------------------------------------
# Manual runs
# --------------------------------------------------------------------------


def test_manual_run_pages_up_to_cap_and_never_writes(fake_table, monkeypatch):
    pid = uuid.uuid4()
    row = _FakeRow(id=pid, last_fetched_at=None, poll_state={})
    fake_table[pid] = row
    provider = _retell_provider(row)
    calls_made = []

    def fake_fetch(prov, start, end, *, pagination_key=None, skip=None):
        calls_made.append(pagination_key)
        return _page(_calls(1), has_more=True, next_key=f"k{len(calls_made)}")

    monkeypatch.setattr(op.ObservabilityService, "fetch_retell_page", fake_fetch)
    monkeypatch.setattr(op, "process_and_store_logs", lambda *a, **k: op.StoreOutcome(1, 0, 0))

    outcome = op._manual_retell_run(provider, start_time=_dt(2026, 1, 1), end_time=_dt(2026, 1, 2))
    assert len(calls_made) == op.RETELL_MANUAL_RUN_MAX_PAGES
    assert outcome == op.StoreOutcome(1, 0, 0)
    assert fake_table[pid].poll_state == {}  # never written
    assert fake_table[pid].last_fetched_at is None  # never written


def test_manual_run_requires_start_time():
    provider = SimpleNamespace(id=uuid.uuid4(), provider=ProviderChoices.RETELL)
    assert op._manual_retell_run(provider, start_time=None, end_time=None) is None


def test_manual_run_rejects_empty_range():
    provider = SimpleNamespace(id=uuid.uuid4(), provider=ProviderChoices.RETELL)
    same = _dt(2026, 1, 1)
    assert op._manual_retell_run(provider, start_time=same, end_time=same) is None


# ``fetch_observability_logs`` is wrapped by ``@temporal_activity``; the
# wrapper calls ``close_old_connections()``, which probes Django's DB
# connection and trips pytest-django's access guard once any earlier test has
# opened one. ``._original_func`` is the raw function (same convention as the
# ``inline_temporal`` fixture in tracer/tests/conftest.py).


def test_fetch_observability_logs_manual_requires_provider_id():
    with capture_logs() as cap:
        op.fetch_observability_logs._original_func(start_time="2026-01-01T00:00:00+00:00")
    assert cap and cap[0]["event"] == "provider_manual_run_rejected"
    assert cap[0]["log_level"] == "error"
    assert cap[0]["reason"] == "provider_id_required"


# --------------------------------------------------------------------------
# Scheduled dispatch — the shape a Temporal firing actually uses:
# `fetch_observability_logs()` with no arguments at all (or just `provider_id`).
# --------------------------------------------------------------------------


def test_scheduled_no_args_fans_out_over_enabled_providers_only(fake_table, monkeypatch):
    # Two enabled, one disabled: proves `.filter(enabled=True)` is honoured,
    # not just that *some* provider gets dispatched.
    enabled_1, enabled_2, disabled = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    fake_table[enabled_1] = _FakeRow(id=enabled_1, enabled=True)
    fake_table[enabled_2] = _FakeRow(id=enabled_2, enabled=True)
    fake_table[disabled] = _FakeRow(id=disabled, enabled=False)

    dispatched = []

    def fake_dispatch(provider_id, *, scheduled, start_time, end_time):
        dispatched.append((provider_id, scheduled, start_time, end_time))
        return op.StoreOutcome(0, 0, 0)

    monkeypatch.setattr(op, "fetch_logs_for_provider", fake_dispatch)

    op.fetch_observability_logs._original_func()  # exactly how a Temporal firing calls it: no args

    assert sorted(pid for pid, *_ in dispatched) == sorted([enabled_1, enabled_2])
    for _pid, scheduled, start_time, end_time in dispatched:
        assert scheduled is True
        assert start_time is None
        assert end_time is None


def test_scheduled_with_only_provider_id_dispatches_exactly_that_provider(monkeypatch):
    # `provider_id` given + no bounds is still scheduled-shaped (R11-3): it
    # must resolve to a single-element dispatch list, never the enabled=True
    # fan-out query — so this deliberately does NOT patch `fake_table`, and
    # would error if the code took the `.filter(enabled=True)` branch instead.
    target = "provider-123"
    dispatched = []
    monkeypatch.setattr(
        op, "fetch_logs_for_provider",
        lambda provider_id, *, scheduled, start_time, end_time: dispatched.append(
            (provider_id, scheduled, start_time, end_time)
        ) or op.StoreOutcome(0, 0, 0),
    )

    op.fetch_observability_logs._original_func(provider_id=target)

    assert dispatched == [(target, True, None, None)]


def test_scheduled_computes_scheduled_before_any_end_time_defaulting(monkeypatch):
    # Guards R11-3 / contract §4 L163 ("FIRST statement, before any parsing or
    # defaulting"): if a `end_dt = end_time or timezone.now()`-style default
    # were reintroduced above the `scheduled` computation, this call would
    # observe a concrete `end_time` instead of `None` and/or `scheduled=False`.
    seen = {}

    def fake_dispatch(provider_id, *, scheduled, start_time, end_time):
        seen["scheduled"] = scheduled
        seen["start_time"] = start_time
        seen["end_time"] = end_time
        return op.StoreOutcome(0, 0, 0)

    monkeypatch.setattr(op, "fetch_logs_for_provider", fake_dispatch)

    op.fetch_observability_logs._original_func(provider_id="p1")  # no bounds: must stay scheduled-shaped

    assert seen["scheduled"] is True
    assert seen["start_time"] is None
    assert seen["end_time"] is None


# --------------------------------------------------------------------------
# fetch_logs_for_provider — HTTP / configuration errors
# --------------------------------------------------------------------------


def _http_error(status_code, *, with_response=True):
    exc = requests.HTTPError("boom")
    if with_response:
        exc.response = SimpleNamespace(status_code=status_code)
    return exc


def test_http_401_logs_retell_auth_failed(fake_table, monkeypatch):
    pid = uuid.uuid4()
    row = _FakeRow(id=pid, poll_state={}, provider=ProviderChoices.RETELL)
    fake_table[pid] = row

    def boom(provider):
        raise _http_error(401)

    monkeypatch.setattr(op, "_poll_retell_provider", boom)

    with capture_logs() as cap:
        result = op.fetch_logs_for_provider(pid, scheduled=True, start_time=None, end_time=None)

    assert result is None
    # pins impl's first `if provider.provider == ProviderChoices.RETELL and
    # status in (401, 403): logger.error("retell_auth_failed", ...)` branch —
    # exact field set, and no `authentication_failed_for_provider` sneaking in.
    assert cap == [{"event": "retell_auth_failed", "log_level": "error", "provider_id": str(pid), "status_code": 401}]


def test_non_retell_401_logs_authentication_failed_for_provider_not_retell_auth_failed(fake_table, monkeypatch):
    # The four other providers must keep their own auth event; a Retell label here would misroute alerts.
    # Pins impl's `elif provider.provider != ProviderChoices.RETELL and
    # status in (401, 403): logger.error("authentication_failed_for_provider", ...)`
    # — a mutation dropping the `provider.provider == ProviderChoices.RETELL`
    # guard on the FIRST branch would route this to `retell_auth_failed` instead,
    # which the exact-list assertion below catches immediately.
    pid = uuid.uuid4()
    row = _FakeRow(id=pid, poll_state={}, provider=ProviderChoices.BLAND)
    fake_table[pid] = row

    def boom(provider, **kwargs):
        raise _http_error(401)

    monkeypatch.setattr(op, "_poll_other_provider", boom)

    with capture_logs() as cap:
        result = op.fetch_logs_for_provider(pid, scheduled=True, start_time=None, end_time=None)

    assert result is None
    assert cap == [{"event": "authentication_failed_for_provider", "log_level": "error",
                     "provider_type": ProviderChoices.BLAND, "status_code": 401}]


def test_non_retell_403_logs_authentication_failed_for_provider(fake_table, monkeypatch):
    pid = uuid.uuid4()
    row = _FakeRow(id=pid, poll_state={}, provider=ProviderChoices.VAPI)
    fake_table[pid] = row

    def boom(provider, **kwargs):
        raise _http_error(403)

    monkeypatch.setattr(op, "_poll_other_provider", boom)

    with capture_logs() as cap:
        result = op.fetch_logs_for_provider(pid, scheduled=True, start_time=None, end_time=None)

    assert result is None
    assert cap == [{"event": "authentication_failed_for_provider", "log_level": "error",
                     "provider_type": ProviderChoices.VAPI, "status_code": 403}]


def test_non_retell_non_auth_http_error_logs_provider_log_fetch_failed(fake_table, monkeypatch):
    # A non-401/403 status must fall through both auth branches into the
    # generic `provider_log_fetch_failed`, with `provider_type` (row loaded)
    # and `status_code` (an HTTPError) both present.
    pid = uuid.uuid4()
    row = _FakeRow(id=pid, poll_state={}, provider=ProviderChoices.TWILIO)
    fake_table[pid] = row

    def boom(provider, **kwargs):
        raise _http_error(500)

    monkeypatch.setattr(op, "_poll_other_provider", boom)

    with capture_logs() as cap:
        result = op.fetch_logs_for_provider(pid, scheduled=True, start_time=None, end_time=None)

    assert result is None
    assert cap == [{"event": "provider_log_fetch_failed", "log_level": "error", "provider_id": str(pid),
                     "provider_type": ProviderChoices.TWILIO, "status_code": 500, "error_type": "HTTPError"}]


def test_http_error_without_response_logs_status_code_none(fake_table, monkeypatch):
    # `getattr(getattr(exc, "response", None), "status_code", None)` must
    # degrade to `None`, not raise, when `.response` is absent.
    pid = uuid.uuid4()
    row = _FakeRow(id=pid, poll_state={}, provider=ProviderChoices.ELEVEN_LABS)
    fake_table[pid] = row

    def boom(provider, **kwargs):
        raise _http_error(None, with_response=False)

    monkeypatch.setattr(op, "_poll_other_provider", boom)

    with capture_logs() as cap:
        result = op.fetch_logs_for_provider(pid, scheduled=True, start_time=None, end_time=None)

    assert result is None
    assert cap == [{"event": "provider_log_fetch_failed", "log_level": "error", "provider_id": str(pid),
                     "provider_type": ProviderChoices.ELEVEN_LABS, "status_code": None, "error_type": "HTTPError"}]


def test_retell_configuration_error_logged(fake_table, monkeypatch):
    pid = uuid.uuid4()
    row = _FakeRow(id=pid, poll_state={})
    fake_table[pid] = row

    def boom(provider):
        raise RetellConfigurationError("Retell API key is not configured for this agent")

    monkeypatch.setattr(op, "_poll_retell_provider", boom)

    with capture_logs() as cap:
        result = op.fetch_logs_for_provider(pid, scheduled=True, start_time=None, end_time=None)
    assert result is None
    assert cap[-1]["event"] == "retell_configuration_error"
    assert cap[-1]["log_level"] == "error"
    assert "message" not in cap[-1]
    assert cap[-1]["error_type"] == "RetellConfigurationError"


def test_manual_run_cursor_rejected_propagates_to_generic_handler(fake_table, monkeypatch):
    # A manual run does not handle a rejected cursor itself; the generic handler must see it.
    pid = uuid.uuid4()
    row = _FakeRow(id=pid, poll_state={}, provider=ProviderChoices.RETELL)
    fake_table[pid] = row

    def raise_rejected(*a, **k):
        raise RetellCursorRejected(cause="missing_key")

    monkeypatch.setattr(op.ObservabilityService, "fetch_retell_page", raise_rejected)

    with capture_logs() as cap:
        result = op.fetch_logs_for_provider(pid, scheduled=False, start_time=_dt(2026, 1, 1), end_time=None)

    assert result is None
    assert cap == [{"event": "provider_log_fetch_failed", "log_level": "error",
                     "provider_id": str(pid), "provider_type": ProviderChoices.RETELL,
                     "error_type": "RetellCursorRejected"}]


def test_fetch_logs_for_provider_not_found_scheduled_vs_manual(fake_table):
    missing_id = uuid.uuid4()
    assert op.fetch_logs_for_provider(missing_id, scheduled=True, start_time=None, end_time=None) is None
    assert op.fetch_logs_for_provider(missing_id, scheduled=False, start_time=_dt(2026, 1, 1), end_time=None) is None


# --------------------------------------------------------------------------
# ISO parsing
# --------------------------------------------------------------------------


def test_naive_iso_input_is_made_aware(fake_table, monkeypatch):
    seen = {}

    def fake_dispatch(provider_id, *, scheduled, start_time, end_time):
        seen["start_time"] = start_time
        return op.StoreOutcome(0, 0, 0)

    monkeypatch.setattr(op, "fetch_logs_for_provider", fake_dispatch)
    op.fetch_observability_logs._original_func(start_time="2026-01-01T00:00:00", provider_id="p1")
    assert seen["start_time"].tzinfo is not None


def test_parse_returns_aware_datetime():
    dt = op._parse("2026-01-01T00:00:00+00:00")
    assert dt.tzinfo is not None


# --------------------------------------------------------------------------
# Other providers
# --------------------------------------------------------------------------


def test_other_provider_advances_after_store_unconditionally(fake_table, monkeypatch):
    pid = uuid.uuid4()
    now = _dt(2026, 1, 1, 12, 0, 0)
    row = _FakeRow(id=pid, last_fetched_at=now - timedelta(hours=1))
    fake_table[pid] = row
    provider = SimpleNamespace(id=pid, provider=ProviderChoices.VAPI, last_fetched_at=row.last_fetched_at)
    monkeypatch.setattr(op.timezone, "now", lambda: now)
    monkeypatch.setattr(op.ObservabilityService, "get_call_logs", lambda **k: [{"id": "c1"}])
    monkeypatch.setattr(op, "process_and_store_logs", lambda *a, **k: op.StoreOutcome(1, 0, 0))

    outcome = op._poll_other_provider(provider, start_time=None, end_time=None)
    assert outcome == op.StoreOutcome(1, 0, 0)
    assert fake_table[pid].last_fetched_at == now


def test_other_provider_store_exception_does_not_advance_and_propagates(fake_table, monkeypatch):
    pid = uuid.uuid4()
    now = _dt(2026, 1, 1, 12, 0, 0)
    wm = now - timedelta(hours=1)
    row = _FakeRow(id=pid, last_fetched_at=wm)
    fake_table[pid] = row
    provider = SimpleNamespace(id=pid, provider=ProviderChoices.VAPI, last_fetched_at=wm)
    monkeypatch.setattr(op.timezone, "now", lambda: now)
    monkeypatch.setattr(op.ObservabilityService, "get_call_logs", lambda **k: [{"id": "c1"}])

    def boom(*a, **k):
        raise ValueError("normalize blew up")

    monkeypatch.setattr(op, "process_and_store_logs", boom)

    with capture_logs() as cap:
        with pytest.raises(ValueError):
            op._poll_other_provider(provider, start_time=None, end_time=None)
    assert fake_table[pid].last_fetched_at == wm  # unchanged
    # pins impl L568-577's `logger.error("provider_log_processing_failed", ...)`
    # immediately before the bare `raise` — a deleted log call here would leave
    # the `pytest.raises` and watermark assertions unchanged, so only the
    # event itself proves the diagnostic still fires before propagation.
    counts_events = [e for e in cap if e["event"] == "provider_log_processing_failed"]
    assert counts_events == [{
        "event": "provider_log_processing_failed", "log_level": "error",
        "provider_type": ProviderChoices.VAPI, "logs_count": 1, "error_type": "ValueError",
    }]


def test_other_provider_future_watermark_repaired_and_clamped(fake_table, monkeypatch):
    pid = uuid.uuid4()
    now = _dt(2026, 1, 1, 12, 0, 0)
    future_wm = now + timedelta(hours=2)
    row = _FakeRow(id=pid, last_fetched_at=future_wm)
    fake_table[pid] = row
    provider = SimpleNamespace(id=pid, provider=ProviderChoices.BLAND, last_fetched_at=future_wm)
    monkeypatch.setattr(op.timezone, "now", lambda: now)
    seen = {}
    monkeypatch.setattr(op.ObservabilityService, "get_call_logs", lambda **k: seen.setdefault("kw", k) and [])
    monkeypatch.setattr(op, "process_and_store_logs", lambda *a, **k: op.StoreOutcome(0, 0, 0))

    outcome = op._poll_other_provider(provider, start_time=None, end_time=now + timedelta(hours=5))
    assert outcome == op.StoreOutcome(0, 0, 0)
    assert seen["kw"]["end_time"] == now  # clamped to now, never beyond
    assert seen["kw"]["start_time"] == now - op.RETELL_FUTURE_WATERMARK_LOOKBACK


# --------------------------------------------------------------------------
# StoreOutcome / process_and_store_logs early returns
# --------------------------------------------------------------------------


def test_process_and_store_logs_unknown_provider_returns_empty_outcome():
    provider = SimpleNamespace(provider=ProviderChoices.LIVEKIT, project=SimpleNamespace(id="p1", organization_id="o1"))
    assert op.process_and_store_logs([], provider) == op.StoreOutcome(0, 0, 0)


def test_process_and_store_logs_non_list_returns_empty_outcome():
    # TWILIO (not VAPI) so the VAPI api-key resolution branch — which would
    # otherwise touch the DB-backed Selector — never runs.
    provider = SimpleNamespace(provider=ProviderChoices.TWILIO, project=SimpleNamespace(id="p1", organization_id="o1"))
    assert op.process_and_store_logs("not-a-list", provider) == op.StoreOutcome(0, 0, 0)


def test_process_and_store_logs_counts_malformed_and_export_failed(monkeypatch):
    project = SimpleNamespace(id="p1", organization_id="o1")
    provider = SimpleNamespace(provider=ProviderChoices.TWILIO, project=project)

    def normalize(log):
        if log.get("bad"):
            raise ValueError("bad log")
        return {"id": log["id"], "rehost_uploads": {}}

    monkeypatch.setattr(op, "normalize_twilio_data", normalize)
    monkeypatch.setattr(op, "_create_observation_span", lambda *a: SimpleNamespace())
    monkeypatch.setattr(op, "_export_provider_call_to_collector", lambda *a: 0)  # never acked

    outcome = op.process_and_store_logs([{"bad": True}, {"id": "c1"}], provider)
    assert outcome == op.StoreOutcome(0, 1, 1)


def test_poll_state_not_a_dict_normalizes_to_empty():
    provider = SimpleNamespace(poll_state=["not", "a", "dict"])
    assert op._read_retell_state(provider) == {}
