"""Behavioural tests for the Retell poll orchestrator (contract §7).

The ORM is faked in-memory (see ``_FakeManager``/``_FakeQuerySet`` below) so
these tests never touch a database; every case patches
``ObservabilityService.fetch_retell_page``, ``process_and_store_logs``,
``ObservabilityProvider.objects``/``all_objects`` and ``timezone.now`` per the
test seam in contract §7.
"""

from __future__ import annotations

import itertools
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
import requests
from django.db.models import Q
from structlog.testing import capture_logs

from tracer.models.observability_provider import ProviderChoices
from tracer.services.observability_providers import (
    RetellConfigurationError,
    RetellCursorRejected,
    RetellPage,
    _retell_page_digest,
)
from tracer.utils import observability_provider as op

pytestmark = pytest.mark.unit

UTC = UTC

_UNSET = object()


def _dt(*args, **kwargs) -> datetime:
    return datetime(*args, tzinfo=UTC, **kwargs)


def _page(
    calls=None,
    *,
    has_more=False,
    next_key=None,
    drops=(),
    digest=_UNSET,
    listed=None,
    consumed=None,
    indices=None,
) -> RetellPage:
    """A fully hydrated page by default: every listed item came back as a call,
    at consecutive indices, and the digest is the one the fetcher would have
    produced for exactly those ids. Tests about resuming, truncation or a
    changed page pass the fields they are actually about.

    Drops are described the way the fetcher describes them — ``(list index,
    slot)`` pairs — and the three scalars are derived from them here, so a test
    cannot build a page whose totals and whose per-index drops disagree.
    """
    calls = calls or []
    drops = tuple(drops)
    return RetellPage(
        calls=calls,
        has_more=has_more,
        next_key=next_key,
        dropped_no_end=sum(1 for _index, slot in drops if slot == 3),
        dropped_missing=sum(1 for _index, slot in drops if slot == 4),
        dropped_failed=sum(1 for _index, slot in drops if slot == 5),
        digest=_retell_page_digest(calls) if digest is _UNSET else digest,
        listed=len(calls) if listed is None else listed,
        consumed=(len(calls) if listed is None else listed)
        if consumed is None
        else consumed,
        indices=tuple(range(len(calls))) if indices is None else tuple(indices),
        drops=drops,
    )


def _calls(n: int, prefix: str = "call") -> list[dict]:
    return [{"call_id": f"{prefix}_{i}"} for i in range(n)]


def _store_each(stored=1, malformed=0, export_failed=0):
    """``process_and_store_logs`` is called once per call now (v1.15 B5), so a
    fake that returns a fixed StoreOutcome describes ONE call, not a page."""

    def fake(logs, *args, **kwargs):
        n = len(logs)
        return op.StoreOutcome(stored * n, malformed * n, export_failed * n)

    return fake


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
    deleted: bool = (
        False  # soft-delete flag: `objects` hides it, `all_objects` must not
    )


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
    monkeypatch.setattr(
        op.ObservabilityProvider,
        "all_objects",
        _FakeManager(table, soft_delete_filter=False),
    )
    monkeypatch.setattr(
        op.ObservabilityProvider,
        "objects",
        _FakeManager(table, soft_delete_filter=True),
    )
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


def _freeze_now_then_over_budget(monkeypatch, when: datetime, *, calls_per_page=1):
    """`timezone.now()` returns `when` for the run's start and for the
    `calls_per_page` in-page checks of the first page, then
    `when + RETELL_RUN_BUDGET` (well past the F2 per-run budget) on every call
    after that — within one call to this helper; call it again fresh before
    each `_poll_retell_provider` invocation that needs it, it does not
    accumulate across invocations.

    Use where a fake fetch returns identical ``has_more=True`` content on
    every call and the test's intent (predating the F2 budget loop) is "one
    page is stored and checkpointed, then the run returns": under a
    genuinely frozen clock the new budget loop would instead fetch a second,
    byte-identical page in the SAME run and hit a spurious page_repeated
    restart. Reproducing "the budget happened to run out after this one
    (slow) page" is the faithful way to keep that single-page-per-run
    behaviour without weakening what the test actually checks.

    `calls_per_page` must be the number of calls on that page: v1.15 B5 reads
    the clock once per stored call, so a smaller value would make the run
    checkpoint MID-page instead of completing the page these tests are about.
    """
    calls = {"n": 0}

    def fake_now():
        calls["n"] += 1
        return when if calls["n"] <= 1 + calls_per_page else when + op.RETELL_RUN_BUDGET

    monkeypatch.setattr(op.timezone, "now", fake_now)


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
    assert fake_table[pid].poll_state == {
        "other_key": "kept",
        "retell": {"bootstrapped": True},
    }


def test_write_retell_state_skipped_logs_and_returns_false(fake_table):
    pid = uuid.uuid4()  # never inserted into fake_table: the update matches 0 rows
    with capture_logs() as cap:
        ok = op._write_retell_state(pid, {"bootstrapped": True})
    assert ok is False
    assert cap == [
        {
            "event": "provider_poll_state_write_skipped",
            "log_level": "error",
            "provider_id": str(pid),
        }
    ]


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
    fake_table[pid] = _FakeRow(
        id=pid, last_fetched_at=_dt(2026, 1, 1), poll_state={}, deleted=True
    )

    # `objects` (soft-delete-filtered) must not see this row at all.
    with pytest.raises(op.ObservabilityProvider.DoesNotExist):
        op.ObservabilityProvider.objects.get(id=pid)

    assert op._advance_watermark(pid, _dt(2026, 1, 2)) == 1
    assert fake_table[pid].last_fetched_at == _dt(2026, 1, 2)

    assert (
        op._repair_future_watermark(pid, _dt(2026, 1, 3), _dt(2026, 1, 10)) == 0
    )  # 1/2 is not later than "now" 1/3
    n = op._repair_future_watermark(
        pid, _dt(2026, 1, 1), _dt(2026, 1, 4)
    )  # 1/2 IS later than "now" 1/1
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
    row = _FakeRow(
        id=pid,
        poll_state={
            "retell": {"bootstrapped": True, "backoff_until": until.isoformat()}
        },
    )
    fake_table[pid] = row
    provider = _retell_provider(row)
    _freeze_now(monkeypatch, now)
    monkeypatch.setattr(
        op.ObservabilityService,
        "fetch_retell_page",
        lambda *a, **k: pytest.fail("must not fetch"),
    )

    result = op._poll_retell_provider(provider)
    assert result == op.StoreOutcome(0, 0, 0)


def test_backoff_expired_proceeds(fake_table, monkeypatch):
    pid = uuid.uuid4()
    now = _dt(2026, 1, 1, 12, 0, 0)
    until = now - timedelta(minutes=1)
    row = _FakeRow(
        id=pid,
        last_fetched_at=now - timedelta(hours=1),
        poll_state={
            "retell": {"bootstrapped": True, "backoff_until": until.isoformat()}
        },
    )
    fake_table[pid] = row
    provider = _retell_provider(row)
    _freeze_now(monkeypatch, now)
    monkeypatch.setattr(
        op.ObservabilityService, "fetch_retell_page", lambda *a, **k: _page()
    )
    monkeypatch.setattr(
        op, "process_and_store_logs", lambda *a, **k: op.StoreOutcome(0, 0, 0)
    )

    result = op._poll_retell_provider(provider)
    assert result is not None  # not backoff-skipped


def test_backoff_until_non_string_is_ignored(fake_table, monkeypatch):
    pid = uuid.uuid4()
    now = _dt(2026, 1, 1, 12, 0, 0)
    row = _FakeRow(
        id=pid,
        last_fetched_at=now - timedelta(hours=1),
        poll_state={"retell": {"bootstrapped": True, "backoff_until": 12345}},
    )
    fake_table[pid] = row
    provider = _retell_provider(row)
    _freeze_now(monkeypatch, now)
    monkeypatch.setattr(
        op.ObservabilityService, "fetch_retell_page", lambda *a, **k: _page()
    )
    monkeypatch.setattr(
        op, "process_and_store_logs", lambda *a, **k: op.StoreOutcome(0, 0, 0)
    )

    result = op._poll_retell_provider(provider)
    assert result is not None  # ignored, run proceeds


# --------------------------------------------------------------------------
# Bootstrap
# --------------------------------------------------------------------------


def test_bootstrap_runs_regardless_of_old_watermark_and_preserves_other_keys_discards_stale_window(
    fake_table, monkeypatch
):
    pid = uuid.uuid4()
    now = _dt(2026, 1, 1, 12, 0, 0)
    row = _FakeRow(
        id=pid,
        last_fetched_at=_dt(
            2020, 1, 1
        ),  # old watermark: irrelevant, bootstrap runs anyway (D10)
        poll_state={
            "other_top_level": "kept",
            "retell": {"window": {"start": "stale"}},
        },
    )
    fake_table[pid] = row
    provider = _retell_provider(row)
    _freeze_now(monkeypatch, now)
    seen_bounds = []

    def fake_fetch(prov, start, end, *, pagination_key=None, skip=None, **kwargs):
        seen_bounds.append((start, end))
        return _page(_calls(3))

    monkeypatch.setattr(op.ObservabilityService, "fetch_retell_page", fake_fetch)
    monkeypatch.setattr(op, "process_and_store_logs", _store_each())

    outcome = op._poll_retell_provider(provider)

    assert outcome == op.StoreOutcome(3, 0, 0)
    assert seen_bounds == [(None, now - op.RETELL_VISIBILITY_LAG)]
    assert fake_table[pid].poll_state["other_top_level"] == "kept"
    assert fake_table[pid].poll_state["retell"] == {
        "bootstrapped": True
    }  # stale window discarded
    assert fake_table[pid].last_fetched_at == now - op.RETELL_VISIBILITY_LAG


def test_bootstrap_discards_a_valid_stale_ordinary_window_not_just_an_invalid_one(
    fake_table, monkeypatch
):
    # F5/R1 L1: v1.13 §7 says a stale `window` is discarded when `bootstrapped`
    # is falsy — the test above only pins this for an INVALID window (missing
    # keys). A *valid* ordinary window (a real "start") must also be
    # discarded and a fresh bootstrap window opened, not adopted as-is: an
    # adopted ordinary window would run windowed logic (RETELL_MAX_PAGES_PER_WINDOW,
    # the hint machinery, an early watermark advance) while `bootstrapped` is
    # still unset.
    pid = uuid.uuid4()
    now = _dt(2026, 1, 1, 12, 0, 0)
    stale_start = now - timedelta(hours=5)
    valid_ordinary_window = {
        "start": stale_start.isoformat(),
        "end": now.isoformat(),
        "opened_at_hint": False,
        "narrowed": False,
        "key": "stale-cursor",
        "skip": None,
        "pages_stored": 2,
        "progress": 0,
        "page_digest": None,
        "page_counts": [0, 0, 0, 0, 0, 0],
        "digests": ["deadbeef"],
        "restarts": 0,
    }
    row = _FakeRow(
        id=pid,
        poll_state={"retell": {"window": valid_ordinary_window}},
    )
    fake_table[pid] = row
    provider = _retell_provider(row)
    _freeze_now(monkeypatch, now)
    seen_bounds = []

    def fake_fetch(prov, start, end, *, pagination_key=None, skip=None, **kwargs):
        seen_bounds.append((start, end, pagination_key))
        return _page(_calls(1), has_more=False)

    monkeypatch.setattr(op.ObservabilityService, "fetch_retell_page", fake_fetch)
    monkeypatch.setattr(
        op, "process_and_store_logs", lambda *a, **k: op.StoreOutcome(1, 0, 0)
    )

    outcome = op._poll_retell_provider(provider)

    assert outcome == op.StoreOutcome(1, 0, 0)
    # A fresh bootstrap window was opened (start None, key None) — the stale
    # ordinary window's "stale-cursor" key/bounds must never reach the fetch.
    assert seen_bounds == [(None, now - op.RETELL_VISIBILITY_LAG, None)]
    assert fake_table[pid].poll_state["retell"] == {"bootstrapped": True}


def test_bootstrap_resumes_its_own_stale_bootstrap_window_instead_of_discarding_it(
    fake_table, monkeypatch
):
    # F5: the discard rule has one exception — a window that is ITSELF a
    # bootstrap window (start is None) is resumed, not thrown away.
    pid = uuid.uuid4()
    now = _dt(2026, 1, 1, 12, 0, 0)
    frozen_end = (
        now - timedelta(hours=3)
    ).isoformat()  # a much earlier run's frozen end
    bootstrap_window = {
        "start": None,
        "end": frozen_end,
        "opened_at_hint": False,
        "narrowed": False,
        "key": "resume-me",
        "skip": None,
        "pages_stored": 2,
        "progress": 0,
        "page_digest": None,
        "page_counts": [0, 0, 0, 0, 0, 0],
        "digests": ["deadbeef"],
        "restarts": 0,
    }
    row = _FakeRow(id=pid, poll_state={"retell": {"window": bootstrap_window}})
    fake_table[pid] = row
    provider = _retell_provider(row)
    _freeze_now(monkeypatch, now)
    seen = []

    def fake_fetch(prov, start, end, *, pagination_key=None, skip=None, **kwargs):
        seen.append((start, end, pagination_key))
        return _page(_calls(1), has_more=False)

    monkeypatch.setattr(op.ObservabilityService, "fetch_retell_page", fake_fetch)
    monkeypatch.setattr(
        op, "process_and_store_logs", lambda *a, **k: op.StoreOutcome(1, 0, 0)
    )

    op._poll_retell_provider(provider)

    assert seen == [(None, op._parse(frozen_end), "resume-me")]  # resumed, not reopened


def test_bootstrap_has_more_is_logged_not_raised(fake_table, monkeypatch):
    pid = uuid.uuid4()
    now = _dt(2026, 1, 1, 12, 0, 0)
    row = _FakeRow(id=pid, poll_state={})
    fake_table[pid] = row
    provider = _retell_provider(row)
    # F2: the fake fetch below returns byte-identical content on every call;
    # a genuinely frozen clock would let the new budget loop try a second,
    # identical page in the same run and hit a spurious page_repeated
    # restart. This test only cares about one page's outcome/logging.
    _freeze_now_then_over_budget(monkeypatch, now, calls_per_page=1000)
    monkeypatch.setattr(
        op.ObservabilityService,
        "fetch_retell_page",
        lambda *a, **k: _page(_calls(1000), has_more=True, next_key=None),
    )
    monkeypatch.setattr(op, "process_and_store_logs", _store_each())

    with capture_logs() as cap:
        outcome = op._poll_retell_provider(provider)  # must not raise

    assert outcome == op.StoreOutcome(1000, 0, 0)
    # §7: "bootstrap with has_more logged" — the counts event must actually
    # carry has_more=True, not just fail to raise.
    counts_events = [e for e in cap if e["event"] == "retell_poll_counts"]
    assert counts_events == [
        {
            "event": "retell_poll_counts",
            "log_level": "info",
            "provider_id": str(pid),
            "mode": "bootstrap",
            "pages_stored": 0,
            "stored": 1000,
            "malformed": 0,
            "export_failed": 0,
            "dropped_no_end": 0,
            "dropped_missing": 0,
            "dropped_failed": 0,
            "has_more": True,
        }
    ]


def test_bootstrap_partial_twice_then_abandoned_on_third(fake_table, monkeypatch):
    pid = uuid.uuid4()
    now = _dt(2026, 1, 1, 12, 0, 0)
    row = _FakeRow(id=pid, poll_state={})
    fake_table[pid] = row
    _freeze_now(monkeypatch, now)
    monkeypatch.setattr(
        op.ObservabilityService, "fetch_retell_page", lambda *a, **k: _page(_calls(1))
    )
    # partial: some stored, one export_failed
    monkeypatch.setattr(
        op, "process_and_store_logs", lambda *a, **k: op.StoreOutcome(1, 0, 1)
    )

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
    assert {
        "event": "retell_page_abandoned",
        "log_level": "error",
        "provider_id": str(pid),
        "abandoned": 1,
        "failed_runs": 3,
    } in cap


def test_bootstrap_total_failure_backs_off_and_sets_no_marker(fake_table, monkeypatch):
    pid = uuid.uuid4()
    now = _dt(2026, 1, 1, 12, 0, 0)
    row = _FakeRow(id=pid, poll_state={})
    fake_table[pid] = row
    provider = _retell_provider(row)
    _freeze_now(monkeypatch, now)
    monkeypatch.setattr(
        op.ObservabilityService, "fetch_retell_page", lambda *a, **k: _page(_calls(1))
    )
    monkeypatch.setattr(
        op, "process_and_store_logs", lambda *a, **k: op.StoreOutcome(0, 0, 1)
    )

    result = op._poll_retell_provider(provider)
    assert result is None
    retell_state = fake_table[pid].poll_state["retell"]
    assert "bootstrapped" not in retell_state
    assert retell_state["total_failures"] == 1
    assert "backoff_until" in retell_state


# --------------------------------------------------------------------------
# Bootstrap paging / checkpointing (amendment v1.14: B1-B3)
# --------------------------------------------------------------------------


def test_bootstrap_page_one_has_more_persists_window_marker_not_set(
    fake_table, monkeypatch
):
    pid = uuid.uuid4()
    now = _dt(2026, 1, 1, 12, 0, 0)
    row = _FakeRow(id=pid, poll_state={})
    fake_table[pid] = row
    provider = _retell_provider(row)
    # F2: identical content on every call — see the comment on
    # _freeze_now_then_over_budget for why a plain frozen clock would break
    # this "exactly one page this run" assertion.
    _freeze_now_then_over_budget(monkeypatch, now, calls_per_page=5)
    monkeypatch.setattr(
        op.ObservabilityService,
        "fetch_retell_page",
        lambda *a, **k: _page(_calls(5), has_more=True, next_key="page-2-key"),
    )
    monkeypatch.setattr(op, "process_and_store_logs", _store_each())

    outcome = op._poll_retell_provider(provider)

    assert outcome == op.StoreOutcome(5, 0, 0)
    retell_state = fake_table[pid].poll_state["retell"]
    assert "bootstrapped" not in retell_state
    window = retell_state["window"]
    assert window["start"] is None
    assert window["key"] == "page-2-key"
    assert window["pages_stored"] == 1
    assert (
        fake_table[pid].last_fetched_at is None
    )  # untouched until bootstrap completes


def test_bootstrap_completes_on_second_page_marker_set_watermark_is_first_run_end(
    fake_table, monkeypatch
):
    pid = uuid.uuid4()
    now = _dt(2026, 1, 1, 12, 0, 0)
    row = _FakeRow(id=pid, poll_state={})
    fake_table[pid] = row
    # F2: identical content on every call — see _freeze_now_then_over_budget.
    _freeze_now_then_over_budget(monkeypatch, now)
    monkeypatch.setattr(
        op.ObservabilityService,
        "fetch_retell_page",
        lambda *a, **k: _page(_calls(1, "p1"), has_more=True, next_key="cursor-1"),
    )
    monkeypatch.setattr(
        op, "process_and_store_logs", lambda *a, **k: op.StoreOutcome(1, 0, 0)
    )

    provider = _retell_provider(row)
    outcome = op._poll_retell_provider(provider)
    assert outcome == op.StoreOutcome(1, 0, 0)
    assert fake_table[pid].last_fetched_at is None
    frozen_end = fake_table[pid].poll_state["retell"]["window"]["end"]

    # A later run: "now" moves on, but the bootstrap window's frozen end must not.
    later = now + timedelta(hours=2)
    _freeze_now(monkeypatch, later)
    seen = []

    def resume_fetch(prov, start, end, *, pagination_key=None, skip=None, **kwargs):
        seen.append((start, end, pagination_key))
        return _page(_calls(1, "p2"), has_more=False)

    monkeypatch.setattr(op.ObservabilityService, "fetch_retell_page", resume_fetch)
    provider = _retell_provider(fake_table[pid])
    outcome = op._poll_retell_provider(provider)

    assert outcome == op.StoreOutcome(1, 0, 0)
    assert seen == [(None, op._parse(frozen_end), "cursor-1")]
    assert fake_table[pid].poll_state["retell"] == {"bootstrapped": True}
    assert fake_table[pid].last_fetched_at == op._parse(frozen_end)


def test_bootstrap_capped_at_max_pages_completes(fake_table, monkeypatch):
    pid = uuid.uuid4()
    now = _dt(2026, 1, 1, 12, 0, 0)
    # F6: frozen_end deliberately differs from `now - RETELL_VISIBILITY_LAG`
    # (the CURRENT run's end) so a completion that wrote the wrong end
    # (this run's, not the first bootstrap run's) would fail the assertion below.
    frozen_end = (now - timedelta(hours=2) - op.RETELL_VISIBILITY_LAG).isoformat()
    row = _FakeRow(
        id=pid,
        poll_state={
            "retell": {
                "window": {
                    "start": None,
                    "end": frozen_end,
                    "opened_at_hint": False,
                    "narrowed": False,
                    "key": "cursor-9",
                    "skip": None,
                    "pages_stored": op.RETELL_BOOTSTRAP_MAX_PAGES - 1,
                    "progress": 0,
                    "page_digest": None,
                    "page_counts": [0, 0, 0, 0, 0, 0],
                    "digests": [],
                    "restarts": 0,
                }
            }
        },
    )
    fake_table[pid] = row
    provider = _retell_provider(row)
    _freeze_now(monkeypatch, now)
    monkeypatch.setattr(
        op.ObservabilityService,
        "fetch_retell_page",
        lambda *a, **k: _page(_calls(1, "last"), has_more=True, next_key="cursor-10"),
    )
    monkeypatch.setattr(
        op, "process_and_store_logs", lambda *a, **k: op.StoreOutcome(1, 0, 0)
    )

    with capture_logs() as cap:
        outcome = op._poll_retell_provider(provider)

    assert outcome == op.StoreOutcome(1, 0, 0)
    assert fake_table[pid].poll_state["retell"] == {"bootstrapped": True}
    assert fake_table[pid].last_fetched_at == op._parse(frozen_end)
    # v1.15 B8: `total_pages` is gone from the event with the counter itself.
    assert {
        "event": "retell_bootstrap_capped",
        "log_level": "info",
        "provider_id": str(pid),
        "pages_stored": op.RETELL_BOOTSTRAP_MAX_PAGES,
    } in cap


def _reject_all(*args, **kwargs):
    raise RetellCursorRejected(cause="missing_key")


def _drive_bootstrap_pages(fake_table, monkeypatch, pid, when, count, *, prefix):
    """`count` separate simulated runs, each storing exactly one bootstrap page
    of unique content. Returns, per run, whether the bootstrap cap fired."""
    fired = []
    for i in range(count):
        monkeypatch.setattr(
            op.ObservabilityService,
            "fetch_retell_page",
            lambda *a, i=i, **k: _page(
                _calls(1, f"{prefix}{i}"), has_more=True, next_key=f"{prefix}-k{i}"
            ),
        )
        _freeze_now_then_over_budget(monkeypatch, when)
        with capture_logs() as cap:
            op._poll_retell_provider(_retell_provider(fake_table[pid]))
        fired.append(any(e["event"] == "retell_bootstrap_capped" for e in cap))
    return fired


def _drive_bootstrap_to_stuck(fake_table, monkeypatch, pid, when):
    """RETELL_MAX_WINDOW_RESTARTS cursor-mode restarts (which switch the
    bootstrap to offset mode) plus that many again in offset mode: the point
    where paging has failed every way there is."""
    monkeypatch.setattr(op.ObservabilityService, "fetch_retell_page", _reject_all)
    _freeze_now(monkeypatch, when)
    for _ in range(2 * op.RETELL_MAX_WINDOW_RESTARTS):
        assert op._poll_retell_provider(_retell_provider(fake_table[pid])) is None


def test_bootstrap_cap_fires_on_ten_contiguous_pages(fake_table, monkeypatch):
    # v1.15 B5: the cap is RETELL_BOOTSTRAP_MAX_PAGES CONTIGUOUS stored pages
    # of one attempt — with no restart in the way, the tenth fires it.
    pid = uuid.uuid4()
    now = _dt(2026, 1, 1, 12, 0, 0)
    fake_table[pid] = _FakeRow(id=pid, poll_state={})
    monkeypatch.setattr(op, "process_and_store_logs", _store_each())

    fired = _drive_bootstrap_pages(
        fake_table, monkeypatch, pid, now, op.RETELL_BOOTSTRAP_MAX_PAGES, prefix="p"
    )

    assert fired == [False] * (op.RETELL_BOOTSTRAP_MAX_PAGES - 1) + [True]
    assert fake_table[pid].poll_state["retell"] == {"bootstrapped": True}


def test_bootstrap_cap_does_not_count_pages_replayed_after_a_restart(
    fake_table, monkeypatch
):
    # v1.15 B5 (the second half of P1-B): a restart re-lists the bootstrap from
    # page 1, so the pages it replays cover history the earlier attempt already
    # covered. Counting them toward the cap would end the bootstrap after far
    # fewer than RETELL_BOOTSTRAP_MAX_PAGES distinct pages. Five pages, a
    # restart, then ten: the cap must fire on the TENTH of the new attempt.
    pid = uuid.uuid4()
    now = _dt(2026, 1, 1, 12, 0, 0)
    fake_table[pid] = _FakeRow(id=pid, poll_state={})
    monkeypatch.setattr(op, "process_and_store_logs", _store_each())

    assert (
        _drive_bootstrap_pages(fake_table, monkeypatch, pid, now, 5, prefix="a")
        == [False] * 5
    )

    monkeypatch.setattr(op.ObservabilityService, "fetch_retell_page", _reject_all)
    _freeze_now(monkeypatch, now)
    assert op._poll_retell_provider(_retell_provider(fake_table[pid])) is None
    assert fake_table[pid].poll_state["retell"]["window"]["pages_stored"] == 0

    fired = _drive_bootstrap_pages(
        fake_table, monkeypatch, pid, now, op.RETELL_BOOTSTRAP_MAX_PAGES, prefix="b"
    )

    # Under the deleted `total_pages` rule the 5th page here (5 replayed + 5)
    # would have capped the bootstrap instead.
    assert fired == [False] * (op.RETELL_BOOTSTRAP_MAX_PAGES - 1) + [True]
    assert fake_table[pid].poll_state["retell"] == {"bootstrapped": True}


def test_bootstrap_stuck_keeps_the_window_backs_off_and_never_advances(
    fake_table, monkeypatch
):
    # v1.15 B6: cursor rejected every time, then offset mode rejected too.
    # v1.14 completed the bootstrap here, advancing the watermark over history
    # it had never covered; the bootstrap must instead be kept and retried.
    pid = uuid.uuid4()
    now = _dt(2026, 1, 1, 12, 0, 0)
    fake_table[pid] = _FakeRow(id=pid, poll_state={})
    frozen_end = now - op.RETELL_VISIBILITY_LAG

    with capture_logs() as cap:
        _drive_bootstrap_to_stuck(fake_table, monkeypatch, pid, now)

    state = fake_table[pid].poll_state["retell"]
    assert "bootstrapped" not in state
    assert fake_table[pid].last_fetched_at is None  # watermark untouched
    assert state["bootstrap_stuck"] == 1
    assert op._parse(state["backoff_until"]) == now + op._backoff_delay(1)
    window = state["window"]
    assert op._parse(window["end"]) == frozen_end  # the SAME frozen end is kept
    assert window["key"] is None and window["skip"] is None  # fresh cursor attempt
    assert window["pages_stored"] == 0 and window["restarts"] == 0
    assert window["digests"] == [] and window["progress"] == 0
    assert {
        "event": "retell_bootstrap_stuck",
        "log_level": "error",
        "provider_id": str(pid),
        "stuck_runs": 1,
        "backoff_until": state["backoff_until"],
    } in cap
    # B8: a bootstrap window no longer reports retell_window_stuck.
    assert not [e for e in cap if e["event"] == "retell_window_stuck"]


def test_run_inside_the_bootstrap_stuck_backoff_is_skipped(fake_table, monkeypatch):
    # v1.15 B6: the escalating backoff is what keeps a broken bootstrap from
    # spinning; the ordinary backoff gate has to honour it.
    pid = uuid.uuid4()
    now = _dt(2026, 1, 1, 12, 0, 0)
    fake_table[pid] = _FakeRow(id=pid, poll_state={})
    _drive_bootstrap_to_stuck(fake_table, monkeypatch, pid, now)

    fetches = []
    monkeypatch.setattr(
        op.ObservabilityService,
        "fetch_retell_page",
        lambda *a, **k: fetches.append(1) or _page(),
    )
    _freeze_now(monkeypatch, now + timedelta(minutes=1))
    with capture_logs() as cap:
        result = op._poll_retell_provider(_retell_provider(fake_table[pid]))

    assert result == op.StoreOutcome(0, 0, 0)
    assert fetches == []
    assert [e["event"] for e in cap] == ["retell_poll_backoff"]


def test_bootstrap_resumes_after_the_backoff_and_completes_with_the_original_end(
    fake_table, monkeypatch
):
    # v1.15 B6: when Retell's pagination recovers, the SAME bootstrap window
    # finishes — with the end frozen on its very first run, so the first
    # ordinary window covers everything after it and nothing is skipped.
    pid = uuid.uuid4()
    now = _dt(2026, 1, 1, 12, 0, 0)
    fake_table[pid] = _FakeRow(id=pid, poll_state={})
    original_end = now - op.RETELL_VISIBILITY_LAG
    _drive_bootstrap_to_stuck(fake_table, monkeypatch, pid, now)

    later = now + op._backoff_delay(1) + timedelta(seconds=1)
    _freeze_now(monkeypatch, later)
    seen = []

    def fake_fetch(prov, start, end, *, pagination_key=None, skip=None, **kwargs):
        seen.append((start, end, pagination_key, skip))
        return _page(_calls(1, "recovered"), has_more=False)

    monkeypatch.setattr(op.ObservabilityService, "fetch_retell_page", fake_fetch)
    monkeypatch.setattr(op, "process_and_store_logs", _store_each())

    outcome = op._poll_retell_provider(_retell_provider(fake_table[pid]))

    assert outcome == op.StoreOutcome(1, 0, 0)
    assert seen == [(None, original_end, None, None)]  # same window, cursor mode
    assert fake_table[pid].poll_state["retell"] == {"bootstrapped": True}
    assert fake_table[pid].last_fetched_at == original_end


def test_second_bootstrap_stuck_cycle_escalates_the_backoff(fake_table, monkeypatch):
    # v1.15 B6: `bootstrap_stuck` is cleared only by completion, so repeated
    # cycles keep escalating instead of retrying every 10 minutes forever.
    pid = uuid.uuid4()
    now = _dt(2026, 1, 1, 12, 0, 0)
    fake_table[pid] = _FakeRow(id=pid, poll_state={})
    _drive_bootstrap_to_stuck(fake_table, monkeypatch, pid, now)

    later = now + op._backoff_delay(1) + timedelta(seconds=1)
    _drive_bootstrap_to_stuck(fake_table, monkeypatch, pid, later)

    state = fake_table[pid].poll_state["retell"]
    assert state["bootstrap_stuck"] == 2
    assert op._parse(state["backoff_until"]) == later + op._backoff_delay(2)
    assert op._backoff_delay(2) > op._backoff_delay(1)


def test_a_stored_page_between_stuck_cycles_does_not_reset_bootstrap_stuck(
    fake_table, monkeypatch
):
    # v1.15 B6: the successful-page `state.pop("total_failures")` must not take
    # `bootstrap_stuck` with it — a bootstrap that limps one page forward per
    # cycle must still escalate.
    pid = uuid.uuid4()
    now = _dt(2026, 1, 1, 12, 0, 0)
    fake_table[pid] = _FakeRow(id=pid, poll_state={})
    monkeypatch.setattr(op, "process_and_store_logs", _store_each())
    _drive_bootstrap_to_stuck(fake_table, monkeypatch, pid, now)

    later = now + op._backoff_delay(1) + timedelta(seconds=1)
    _drive_bootstrap_pages(fake_table, monkeypatch, pid, later, 1, prefix="ok")
    state = fake_table[pid].poll_state["retell"]
    assert state["bootstrap_stuck"] == 1  # survived a successful page
    assert "backoff_until" not in state  # but the backoff itself was cleared

    _drive_bootstrap_to_stuck(fake_table, monkeypatch, pid, later)
    assert fake_table[pid].poll_state["retell"]["bootstrap_stuck"] == 2


def test_bootstrap_cursor_rejected_three_times_switches_to_offset_mode(
    fake_table, monkeypatch
):
    pid = uuid.uuid4()
    now = _dt(2026, 1, 1, 12, 0, 0)
    row = _FakeRow(id=pid, poll_state={})
    fake_table[pid] = row
    _freeze_now(monkeypatch, now)

    def raise_rejected(*a, **k):
        raise RetellCursorRejected(cause="missing_key")

    monkeypatch.setattr(op.ObservabilityService, "fetch_retell_page", raise_rejected)

    for _ in range(op.RETELL_MAX_WINDOW_RESTARTS):
        provider = _retell_provider(fake_table[pid])
        assert op._poll_retell_provider(provider) is None

    retell_state = fake_table[pid].poll_state["retell"]
    assert "bootstrapped" not in retell_state
    window = retell_state["window"]
    assert window["start"] is None
    assert (
        window["skip"] == 0
    )  # switched to offset mode, no halving (no start to halve)
    assert window["restarts"] == 0
    assert (
        "window_hint_seconds" not in retell_state
    )  # never written by a bootstrap restart


def test_large_page_checkpoints_before_returning_then_resumes_from_cursor(
    fake_table, monkeypatch
):
    # Contract v1.14 C3: a page holding up to RETELL_LIST_PAGE_LIMIT calls,
    # however slow its hydration, must checkpoint after exactly one page so a
    # run that dies mid-page loses at most that page. "Slow" is simulated
    # with a call log, never a real sleep.
    assert (
        op.RETELL_LIST_PAGE_LIMIT == 100
    )  # a future bump is a deliberate contract change

    pid = uuid.uuid4()
    now = _dt(2026, 1, 1, 12, 0, 0)
    wm = now - timedelta(hours=1)
    row = _FakeRow(
        id=pid, last_fetched_at=wm, poll_state={"retell": {"bootstrapped": True}}
    )
    fake_table[pid] = row
    # F2 (the review fix that lets a run page through several pages within a
    # wall-clock budget) added a second `timezone.now()` call after a stored
    # has_more page. This fake returns identical content on every call, so a
    # genuinely frozen clock would let the run try a second, identical page
    # in the SAME run and hit a spurious page_repeated restart instead of
    # returning. Simulating "this one (slow) page alone used the whole
    # budget" is the faithful way to keep proving the checkpoint-then-return
    # property C3 asks for.
    _freeze_now_then_over_budget(
        monkeypatch, now, calls_per_page=op.RETELL_LIST_PAGE_LIMIT
    )

    fetch_calls = []

    def slow_fetch(prov, start, end, *, pagination_key=None, skip=None, **kwargs):
        fetch_calls.append((start, end, pagination_key, skip))
        return _page(
            _calls(op.RETELL_LIST_PAGE_LIMIT, "big"),
            has_more=True,
            next_key="resume-cursor",
        )

    monkeypatch.setattr(op.ObservabilityService, "fetch_retell_page", slow_fetch)
    monkeypatch.setattr(op, "process_and_store_logs", _store_each())

    provider = _retell_provider(row)
    outcome = op._poll_retell_provider(provider)

    assert outcome == op.StoreOutcome(op.RETELL_LIST_PAGE_LIMIT, 0, 0)
    assert len(fetch_calls) == 1  # exactly one page per run, however large or slow
    window = fake_table[pid].poll_state["retell"]["window"]
    assert window["key"] == "resume-cursor"
    assert window["pages_stored"] == 1
    assert (
        fake_table[pid].last_fetched_at == wm
    )  # not advanced: the window isn't complete

    # A second run resumes from the persisted cursor rather than re-listing.
    _freeze_now_then_over_budget(
        monkeypatch, now, calls_per_page=op.RETELL_LIST_PAGE_LIMIT
    )
    provider = _retell_provider(fake_table[pid])
    op._poll_retell_provider(provider)

    assert len(fetch_calls) == 2
    assert (
        fetch_calls[1][2] == "resume-cursor"
    )  # pagination_key passed through, not None


# --------------------------------------------------------------------------
# Per-run wall-clock budget with per-page checkpointing (v1.14 review fix F2)
# --------------------------------------------------------------------------


def test_run_budget_pages_through_multiple_pages_within_one_run_when_clock_never_advances(
    fake_table, monkeypatch
):
    # F2(a): with a genuinely frozen clock the per-run budget never elapses,
    # so ONE call to `_poll_retell_provider` pages all the way through to
    # completion, persisting the checkpoint (cursor) after EACH page along
    # the way — not just once at the end.
    pid = uuid.uuid4()
    now = _dt(2026, 1, 1, 12, 0, 0)
    wm = now - timedelta(hours=1)
    row = _FakeRow(
        id=pid, last_fetched_at=wm, poll_state={"retell": {"bootstrapped": True}}
    )
    fake_table[pid] = row
    provider = _retell_provider(row)
    _freeze_now(monkeypatch, now)

    pages = [
        _page(_calls(1, "p1"), has_more=True, next_key="cursor-1"),
        _page(_calls(1, "p2"), has_more=True, next_key="cursor-2"),
        _page(_calls(1, "p3"), has_more=False),
    ]
    fetch_calls = []

    def fake_fetch(prov, start, end, *, pagination_key=None, skip=None, **kwargs):
        fetch_calls.append(pagination_key)
        return pages[len(fetch_calls) - 1]

    monkeypatch.setattr(op.ObservabilityService, "fetch_retell_page", fake_fetch)
    monkeypatch.setattr(
        op, "process_and_store_logs", lambda *a, **k: op.StoreOutcome(1, 0, 0)
    )

    persisted_keys = []
    real_write = op._write_retell_state

    def spy_write(provider_id, state):
        persisted_keys.append((state.get("window") or {}).get("key"))
        return real_write(provider_id, state)

    monkeypatch.setattr(op, "_write_retell_state", spy_write)

    outcome = op._poll_retell_provider(provider)

    assert outcome == op.StoreOutcome(1, 0, 0)
    assert fetch_calls == [None, "cursor-1", "cursor-2"]  # three pages, ONE run
    # the cursor was checkpointed after each page, not just the last write:
    assert persisted_keys == ["cursor-1", "cursor-2", None]
    assert "window" not in fake_table[pid].poll_state["retell"]  # window completed
    assert fake_table[pid].last_fetched_at is not None


def test_run_budget_exceeded_after_one_page_returns_with_cursor_persisted(
    fake_table, monkeypatch
):
    # F2(b): once the per-run budget has elapsed, the run returns after the
    # page it just checkpointed instead of fetching another, even though
    # `has_more` is still True.
    pid = uuid.uuid4()
    now = _dt(2026, 1, 1, 12, 0, 0)
    wm = now - timedelta(hours=1)
    row = _FakeRow(
        id=pid, last_fetched_at=wm, poll_state={"retell": {"bootstrapped": True}}
    )
    fake_table[pid] = row
    provider = _retell_provider(row)
    _freeze_now_then_over_budget(monkeypatch, now)
    fetch_calls = []

    def fake_fetch(prov, start, end, *, pagination_key=None, skip=None, **kwargs):
        fetch_calls.append(pagination_key)
        return _page(
            _calls(1, f"p{len(fetch_calls)}"),
            has_more=True,
            next_key=f"cursor-{len(fetch_calls)}",
        )

    monkeypatch.setattr(op.ObservabilityService, "fetch_retell_page", fake_fetch)
    monkeypatch.setattr(
        op, "process_and_store_logs", lambda *a, **k: op.StoreOutcome(1, 0, 0)
    )

    outcome = op._poll_retell_provider(provider)

    assert outcome == op.StoreOutcome(1, 0, 0)
    assert (
        len(fetch_calls) == 1
    )  # budget exhausted after the first page: no second fetch this run
    window = fake_table[pid].poll_state["retell"]["window"]
    assert window["key"] == "cursor-1"
    assert window["pages_stored"] == 1


# --------------------------------------------------------------------------
# Activity-level deadline (v1.14 review fix F1 / N1): the deadline threaded
# in from the scheduled fan-out caps the per-run budget from outside.
# --------------------------------------------------------------------------


def test_deadline_in_the_past_stores_at_most_one_page_and_persists_cursor(
    fake_table, monkeypatch
):
    # F1(a): a deadline that has already elapsed by the time the run starts
    # must behave like the run budget already being exhausted — one page
    # stored and checkpointed, then return, even though the clock never
    # advances and `has_more` stays True.
    # The full page IS stored here only because the fetcher is mocked: the real
    # one is handed the same elapsed deadline and would submit nothing, so this
    # pins the page LOOP's exit, not what a real run would store (v1.15.1 F7).
    pid = uuid.uuid4()
    now = _dt(2026, 1, 1, 12, 0, 0)
    wm = now - timedelta(hours=1)
    row = _FakeRow(
        id=pid, last_fetched_at=wm, poll_state={"retell": {"bootstrapped": True}}
    )
    fake_table[pid] = row
    provider = _retell_provider(row)
    _freeze_now(
        monkeypatch, now
    )  # genuinely frozen: only `deadline` can end the run early

    fetch_calls = []

    def fake_fetch(prov, start, end, *, pagination_key=None, skip=None, **kwargs):
        fetch_calls.append(pagination_key)
        return _page(
            _calls(1, f"p{len(fetch_calls)}"),
            has_more=True,
            next_key=f"cursor-{len(fetch_calls)}",
        )

    monkeypatch.setattr(op.ObservabilityService, "fetch_retell_page", fake_fetch)
    monkeypatch.setattr(
        op, "process_and_store_logs", lambda *a, **k: op.StoreOutcome(1, 0, 0)
    )

    outcome = op._poll_retell_provider(provider, deadline=now - timedelta(minutes=1))

    assert outcome == op.StoreOutcome(1, 0, 0)
    assert len(fetch_calls) == 1  # deadline already past: no second fetch this run
    window = fake_table[pid].poll_state["retell"]["window"]
    assert window["key"] == "cursor-1"
    assert window["pages_stored"] == 1


def test_deadline_far_away_leaves_multi_page_run_unchanged(fake_table, monkeypatch):
    # F1(b): a deadline far in the future must not itself cap anything — with
    # the clock genuinely frozen, the run budget governs exactly as it did
    # before F1, and pages through to completion in one run.
    pid = uuid.uuid4()
    now = _dt(2026, 1, 1, 12, 0, 0)
    wm = now - timedelta(hours=1)
    row = _FakeRow(
        id=pid, last_fetched_at=wm, poll_state={"retell": {"bootstrapped": True}}
    )
    fake_table[pid] = row
    provider = _retell_provider(row)
    _freeze_now(monkeypatch, now)

    pages = [
        _page(_calls(1, "p1"), has_more=True, next_key="cursor-1"),
        _page(_calls(1, "p2"), has_more=True, next_key="cursor-2"),
        _page(_calls(1, "p3"), has_more=False),
    ]
    fetch_calls = []

    def fake_fetch(prov, start, end, *, pagination_key=None, skip=None, **kwargs):
        fetch_calls.append(pagination_key)
        return pages[len(fetch_calls) - 1]

    monkeypatch.setattr(op.ObservabilityService, "fetch_retell_page", fake_fetch)
    monkeypatch.setattr(
        op, "process_and_store_logs", lambda *a, **k: op.StoreOutcome(1, 0, 0)
    )

    outcome = op._poll_retell_provider(provider, deadline=now + timedelta(hours=5))

    assert outcome == op.StoreOutcome(1, 0, 0)
    assert fetch_calls == [None, "cursor-1", "cursor-2"]  # all three pages, one run
    assert "window" not in fake_table[pid].poll_state["retell"]  # window completed


def test_deadline_none_means_only_the_run_budget_applies(fake_table, monkeypatch):
    # F1: manual runs / direct callers pass no deadline at all; the run
    # budget alone must still govern, exactly as before F1 existed.
    pid = uuid.uuid4()
    now = _dt(2026, 1, 1, 12, 0, 0)
    wm = now - timedelta(hours=1)
    row = _FakeRow(
        id=pid, last_fetched_at=wm, poll_state={"retell": {"bootstrapped": True}}
    )
    fake_table[pid] = row
    provider = _retell_provider(row)
    _freeze_now_then_over_budget(monkeypatch, now)
    fetch_calls = []

    def fake_fetch(prov, start, end, *, pagination_key=None, skip=None, **kwargs):
        fetch_calls.append(pagination_key)
        return _page(
            _calls(1, f"p{len(fetch_calls)}"),
            has_more=True,
            next_key=f"cursor-{len(fetch_calls)}",
        )

    monkeypatch.setattr(op.ObservabilityService, "fetch_retell_page", fake_fetch)
    monkeypatch.setattr(
        op, "process_and_store_logs", lambda *a, **k: op.StoreOutcome(1, 0, 0)
    )

    outcome = op._poll_retell_provider(provider)  # no deadline kwarg at all

    assert outcome == op.StoreOutcome(1, 0, 0)
    assert len(fetch_calls) == 1


def test_fetch_logs_for_provider_wires_the_deadline_to_the_scheduled_poll_only(
    fake_table, monkeypatch
):
    # R3 M1: the second half of the wire. `fetch_logs_for_provider` must hand
    # the activity deadline to `_poll_retell_provider` unchanged, and the
    # manual path must never see it (manual runs are page-capped, not
    # budgeted).
    pid = uuid.uuid4()
    fake_table[pid] = _FakeRow(id=pid, poll_state={"retell": {"bootstrapped": True}})
    deadline = _dt(2026, 1, 1, 14, 0, 0)
    seen = {}

    def fake_poll(provider, **kwargs):
        seen["poll"] = kwargs
        return op.StoreOutcome(0, 0, 0)

    def fake_manual(provider, **kwargs):
        seen["manual"] = kwargs
        return op.StoreOutcome(0, 0, 0)

    monkeypatch.setattr(op, "_poll_retell_provider", fake_poll)
    monkeypatch.setattr(op, "_manual_retell_run", fake_manual)

    op.fetch_logs_for_provider(
        pid, scheduled=True, start_time=None, end_time=None, deadline=deadline
    )
    assert seen == {"poll": {"deadline": deadline}}

    seen.clear()
    start, end = _dt(2026, 1, 1, 10, 0, 0), _dt(2026, 1, 1, 11, 0, 0)
    op.fetch_logs_for_provider(
        pid, scheduled=False, start_time=start, end_time=end, deadline=deadline
    )
    assert seen == {"manual": {"start_time": start, "end_time": end}}


def test_behind_events_read_a_fresh_clock_not_the_run_start(fake_table, monkeypatch):
    # R3 L1 (pins F2/N2): a multi-page run that takes real time must report
    # a growing frontier age after each page, and the completion-time behind
    # check must see the time the run actually took. With the run-start `now`
    # every in-loop age would be a constant 60 s and the final check would
    # never fire.
    pid = uuid.uuid4()
    now = _dt(2026, 1, 1, 12, 0, 0)
    wm = now - timedelta(hours=1)
    row = _FakeRow(
        id=pid, last_fetched_at=wm, poll_state={"retell": {"bootstrapped": True}}
    )
    fake_table[pid] = row
    provider = _retell_provider(row)
    clock = {"t": now}
    monkeypatch.setattr(op.timezone, "now", lambda: clock["t"])

    # Two 8-minute pages stay inside the 20-minute run budget; the third
    # pushes the frontier age past RETELL_BEHIND_WARN at completion.
    step = timedelta(minutes=8)
    pages = [
        _page(_calls(1, "p1"), has_more=True, next_key="cursor-1"),
        _page(_calls(1, "p2"), has_more=True, next_key="cursor-2"),
        _page(_calls(1, "p3"), has_more=False),
    ]
    fetch_calls = []

    def fake_fetch(prov, start, end, *, pagination_key=None, skip=None, **kwargs):
        clock["t"] = clock["t"] + step  # each page takes `step` of wall time
        fetch_calls.append(pagination_key)
        return pages[len(fetch_calls) - 1]

    monkeypatch.setattr(op.ObservabilityService, "fetch_retell_page", fake_fetch)
    monkeypatch.setattr(
        op, "process_and_store_logs", lambda *a, **k: op.StoreOutcome(1, 0, 0)
    )

    with capture_logs() as cap:
        outcome = op._poll_retell_provider(provider)

    assert outcome == op.StoreOutcome(1, 0, 0)
    assert fetch_calls == [None, "cursor-1", "cursor-2"]
    ages = [
        e["frontier_age_seconds"] for e in cap if e["event"] == "retell_poll_behind"
    ]
    lag = int(op.RETELL_VISIBILITY_LAG.total_seconds())
    step_s = int(step.total_seconds())
    assert ages == [step_s + lag, 2 * step_s + lag, 3 * step_s + lag]


# --------------------------------------------------------------------------
# --------------------------------------------------------------------------
# Call-level checkpoints (amendment v1.15): the deadline reaches INSIDE a page
# --------------------------------------------------------------------------


def _windowed_row(pid, now, window=None):
    row = _FakeRow(
        id=pid,
        last_fetched_at=now - timedelta(minutes=30),
        poll_state={
            "retell": {"bootstrapped": True, **({"window": window} if window else {})}
        },
    )
    return row


def _in_progress_window(
    now, *, key="cursor-1", progress=0, page_digest=None, counts=None
):
    return {
        "start": (now - timedelta(minutes=30)).isoformat(),
        "end": (now - op.RETELL_VISIBILITY_LAG).isoformat(),
        "opened_at_hint": False,
        "narrowed": False,
        "key": key,
        "skip": None,
        "pages_stored": 1,
        "digests": [],
        "restarts": 0,
        "progress": progress,
        "page_digest": page_digest,
        "page_counts": counts or [0, 0, 0, 0, 0, 0],
    }


def test_a_page_whose_stores_outlive_the_budget_checkpoints_and_resumes_mid_page(
    fake_table, monkeypatch
):
    # The reviewer's P1-A regression. A full page's stores can outlive the run
    # deadline; before v1.15 the cursor was written only after the whole page,
    # so the activity was killed mid-page and the next tick re-listed and
    # re-stored the same page forever. The run must now checkpoint at the call
    # it reached and the next run must pick the page up there.
    pid = uuid.uuid4()
    now = _dt(2026, 1, 1, 12, 0, 0)
    row = _windowed_row(pid, now)
    fake_table[pid] = row
    stop_after = 40
    all_calls = _calls(op.RETELL_LIST_PAGE_LIMIT, "big")
    digest = _retell_page_digest(all_calls)

    clock = {"t": now}
    stores = {"n": 0}
    monkeypatch.setattr(op.timezone, "now", lambda: clock["t"])

    def slow_store(logs, *a, **k):
        stores["n"] += 1
        if stores["n"] == stop_after:  # this call is the one that ran us out of time
            clock["t"] = now + op.RETELL_RUN_BUDGET + timedelta(seconds=1)
        return op.StoreOutcome(len(logs), 0, 0)

    monkeypatch.setattr(op, "process_and_store_logs", slow_store)
    monkeypatch.setattr(
        op.ObservabilityService,
        "fetch_retell_page",
        lambda *a, **k: _page(all_calls, has_more=True, next_key="cursor-2"),
    )

    with capture_logs() as cap:
        outcome = op._poll_retell_provider(_retell_provider(row))

    assert outcome == op.StoreOutcome(stop_after, 0, 0)  # THIS run's stores
    window = fake_table[pid].poll_state["retell"]["window"]
    assert window["progress"] == stop_after
    assert window["page_digest"] == digest
    assert window["page_counts"] == [stop_after, 0, 0, 0, 0, 0]
    assert window["key"] is None  # cursor NOT advanced: the page is unfinished
    assert window["pages_stored"] == 0  # nor does an unfinished page count
    assert window["digests"] == []  # only a COMPLETE page is remembered
    assert fake_table[pid].last_fetched_at == now - timedelta(minutes=30)
    assert {
        "event": "retell_page_checkpointed",
        "log_level": "info",
        "provider_id": str(pid),
        "progress": stop_after,
        "listed": op.RETELL_LIST_PAGE_LIMIT,
    } in cap
    assert not [e for e in cap if e["event"] == "retell_poll_counts"]  # page unfinished

    # Next run: the same page, resumed where it stopped.
    clock["t"] = now + timedelta(minutes=10)
    seen = []

    def resume_fetch(prov, start, end, *, pagination_key=None, skip=None, **kwargs):
        seen.append((pagination_key, kwargs["resume_from"], kwargs["resume_digest"]))
        return _page(
            all_calls[stop_after:],
            has_more=False,
            digest=digest,
            listed=op.RETELL_LIST_PAGE_LIMIT,
            consumed=op.RETELL_LIST_PAGE_LIMIT,
            indices=range(stop_after, op.RETELL_LIST_PAGE_LIMIT),
        )

    monkeypatch.setattr(op.ObservabilityService, "fetch_retell_page", resume_fetch)
    with capture_logs() as cap:
        outcome = op._poll_retell_provider(_retell_provider(fake_table[pid]))

    assert seen == [(None, stop_after, digest)]
    assert outcome == op.StoreOutcome(op.RETELL_LIST_PAGE_LIMIT, 0, 0)  # page total
    counts = [e for e in cap if e["event"] == "retell_poll_counts"]
    assert len(counts) == 1 and counts[0]["stored"] == op.RETELL_LIST_PAGE_LIMIT
    assert not [e for e in cap if e["event"] == "retell_window_restarted"]  # no repeat
    assert "window" not in fake_table[pid].poll_state["retell"]  # completed and popped
    assert fake_table[pid].last_fetched_at == now - op.RETELL_VISIBILITY_LAG


def test_a_hydration_truncated_page_checkpoints_at_consumed(fake_table, monkeypatch):
    # v1.15 A3/B5: the fetcher itself can stop at the deadline part-way through
    # a page. Everything it did return is stored, then the run checkpoints at
    # `consumed` — not at the number of calls it happened to store.
    pid = uuid.uuid4()
    now = _dt(2026, 1, 1, 12, 0, 0)
    row = _windowed_row(pid, now)
    fake_table[pid] = row
    _freeze_now(monkeypatch, now)  # the budget never elapses: only `consumed` stops it
    hydrated = _calls(6, "h")
    monkeypatch.setattr(
        op.ObservabilityService,
        "fetch_retell_page",
        lambda *a, **k: _page(
            hydrated, has_more=True, next_key="cursor-2", listed=10, consumed=6
        ),
    )
    monkeypatch.setattr(op, "process_and_store_logs", _store_each())

    with capture_logs() as cap:
        outcome = op._poll_retell_provider(_retell_provider(row))

    assert outcome == op.StoreOutcome(6, 0, 0)
    window = fake_table[pid].poll_state["retell"]["window"]
    assert window["progress"] == 6
    assert window["page_digest"] == _retell_page_digest(hydrated)
    assert window["key"] is None  # the page is not finished, the cursor stays
    assert {
        "event": "retell_page_checkpointed",
        "log_level": "info",
        "provider_id": str(pid),
        "progress": 6,
        "listed": 10,
    } in cap


def test_a_second_checkpoint_on_the_same_page_returns_the_pages_stores_so_far(
    fake_table, monkeypatch
):
    # v1.15.2 F10: one meaning for both return paths — the current page's
    # counts, whichever run stored them. The only caller reads None versus not
    # None, so a second convention for the checkpoint bought nothing and cost a
    # snapshot of `page_counts` on every page.
    pid = uuid.uuid4()
    now = _dt(2026, 1, 1, 12, 0, 0)
    page_calls = _calls(100, "two-stop")
    digest = _retell_page_digest(page_calls)
    window = _in_progress_window(
        now, key=None, progress=40, page_digest=digest, counts=[40, 0, 0, 0, 0, 0]
    )
    row = _windowed_row(pid, now, window)
    fake_table[pid] = row

    clock = {"t": now}
    stores = {"n": 0}
    monkeypatch.setattr(op.timezone, "now", lambda: clock["t"])

    def slow_store(logs, *a, **k):
        stores["n"] += 1
        if stores["n"] == 10:
            clock["t"] = now + op.RETELL_RUN_BUDGET + timedelta(seconds=1)
        return op.StoreOutcome(len(logs), 0, 0)

    monkeypatch.setattr(op, "process_and_store_logs", slow_store)
    monkeypatch.setattr(
        op.ObservabilityService,
        "fetch_retell_page",
        lambda *a, **k: _page(
            page_calls[40:],
            has_more=True,
            next_key="cursor-2",
            digest=digest,
            listed=100,
            indices=range(40, 100),
        ),
    )

    outcome = op._poll_retell_provider(_retell_provider(row))

    window = fake_table[pid].poll_state["retell"]["window"]
    assert window["progress"] == 50
    assert window["page_counts"] == [50, 0, 0, 0, 0, 0]  # the page's running total
    assert outcome == op.StoreOutcome(50, 0, 0)  # which is what the run returns
    assert outcome != op.StoreOutcome(10, 0, 0)  # not the 10 this run stored


def test_the_next_page_in_the_same_run_starts_from_zero_progress(
    fake_table, monkeypatch
):
    # v1.15 B1: the progress keys describe the page the cursor points at, so
    # advancing the cursor must clear them. Carried over, the next page would
    # be asked to resume at an offset that means nothing on it — and would
    # report a spurious `retell_page_changed` when its digest did not match.
    pid = uuid.uuid4()
    now = _dt(2026, 1, 1, 12, 0, 0)
    page_one = _calls(10, "one")
    window = _in_progress_window(
        now,
        key=None,
        progress=4,
        page_digest=_retell_page_digest(page_one),
        counts=[4, 0, 0, 0, 0, 0],
    )
    row = _windowed_row(pid, now, window)
    fake_table[pid] = row
    _freeze_now(monkeypatch, now)  # both pages fit in one run
    monkeypatch.setattr(op, "process_and_store_logs", _store_each())
    seen = []

    def fake_fetch(prov, start, end, *, pagination_key=None, skip=None, **kwargs):
        seen.append((kwargs["resume_from"], kwargs["resume_digest"]))
        if len(seen) == 1:
            return _page(
                page_one[4:],
                has_more=True,
                next_key="cursor-2",
                digest=_retell_page_digest(page_one),
                listed=10,
                indices=range(4, 10),
            )
        return _page(_calls(3, "two"), has_more=False)

    monkeypatch.setattr(op.ObservabilityService, "fetch_retell_page", fake_fetch)

    with capture_logs() as cap:
        op._poll_retell_provider(_retell_provider(row))

    assert seen[0] == (4, _retell_page_digest(page_one))  # page 1 resumed
    assert seen[1] == (0, None)  # page 2 starts clean
    assert not [e for e in cap if e["event"] == "retell_page_changed"]


def test_a_resumed_page_that_changed_is_re_stored_from_zero(fake_table, monkeypatch):
    # v1.15 B3: the page under the cursor changed between runs, so the progress
    # into the old one means nothing. Re-store it whole — a re-emit is cheap and
    # idempotent, a skipped call is silent loss.
    pid = uuid.uuid4()
    now = _dt(2026, 1, 1, 12, 0, 0)
    window = _in_progress_window(now, key=None, progress=3, page_digest="an-older-page")
    row = _windowed_row(pid, now, window)
    fake_table[pid] = row
    _freeze_now(monkeypatch, now)
    fresh = _calls(5, "fresh")
    monkeypatch.setattr(
        op.ObservabilityService,
        "fetch_retell_page",
        lambda *a, **k: _page(fresh, has_more=False),
    )
    monkeypatch.setattr(op, "process_and_store_logs", _store_each())

    with capture_logs() as cap:
        outcome = op._poll_retell_provider(_retell_provider(row))

    assert outcome == op.StoreOutcome(5, 0, 0)  # all five, not just the last two
    assert {
        "event": "retell_page_changed",
        "log_level": "warning",
        "provider_id": str(pid),
        "progress": 3,
    } in cap
    assert "window" not in fake_table[pid].poll_state["retell"]


def test_a_second_page_change_only_warns_and_still_re_stores_the_page(
    fake_table, monkeypatch
):
    # v1.15.2 F9: one page changing under a resume is ordinary (Retell may have
    # been listing while a call ended), so it stays a warning and the page is
    # re-stored. Only the counter is new — and it has to survive a run that did
    # not complete the page, or a repeating mismatch could never add up.
    pid = uuid.uuid4()
    now = _dt(2026, 1, 1, 12, 0, 0)
    window = _in_progress_window(now, key=None, progress=3, page_digest="an-older-page")
    row = _windowed_row(pid, now, window)
    row.poll_state["retell"]["page_changed"] = 1
    fake_table[pid] = row
    _freeze_now(monkeypatch, now)
    monkeypatch.setattr(
        op.ObservabilityService,
        "fetch_retell_page",
        lambda *a, **k: _page(_calls(5, "fresh"), has_more=False),
    )
    stored = []

    def store(logs, *a, **k):  # one call fails to export: `partial`, so the
        stored.extend(logs)  # page is retried and the run's state survives
        return (
            op.StoreOutcome(0, 0, 1) if len(stored) == 5 else op.StoreOutcome(1, 0, 0)
        )

    monkeypatch.setattr(op, "process_and_store_logs", store)

    with capture_logs() as cap:
        assert op._poll_retell_provider(_retell_provider(row)) is None

    assert len(stored) == 5  # re-stored from 0, not resumed at 3
    state = fake_table[pid].poll_state["retell"]
    assert state["page_changed"] == 2
    assert "backoff_until" not in state
    assert [e["event"] for e in cap if e["event"].startswith("retell_page_")] == [
        "retell_page_changed"
    ]


def test_a_third_page_change_in_a_row_backs_off_with_an_error_event(
    fake_table, monkeypatch
):
    # v1.15.2 F9, the R2 Medium finding. A page that comes back in a new order
    # every time it is listed re-stores its prefix and checkpoints again on
    # every run, for ever: the cursor never advances, so the watermark never
    # does either and `retell_poll_behind` — the event an operator alerts on —
    # is never even reached. The third mismatch in a row is treated as the
    # outage it is: back off, and say so at error level.
    pid = uuid.uuid4()
    now = _dt(2026, 1, 1, 12, 0, 0)
    window = _in_progress_window(now, key=None, progress=3, page_digest="an-older-page")
    row = _windowed_row(pid, now, window)
    row.poll_state["retell"]["page_changed"] = 2
    fake_table[pid] = row
    _freeze_now(monkeypatch, now)
    stored = []

    def store(logs, *a, **k):
        stored.extend(logs)
        return op.StoreOutcome(len(logs), 0, 0)

    monkeypatch.setattr(op, "process_and_store_logs", store)
    monkeypatch.setattr(
        op.ObservabilityService,
        "fetch_retell_page",
        lambda *a, **k: _page(_calls(5, "fresh"), has_more=False),
    )

    with capture_logs() as cap:
        assert op._poll_retell_provider(_retell_provider(row)) is None

    assert stored == []  # the run stops at the mismatch instead of re-storing
    state = fake_table[pid].poll_state["retell"]
    assert state["page_changed"] == 3
    assert state["total_failures"] == 1
    assert state["backoff_until"] == (now + op._backoff_delay(1)).isoformat()
    assert state["window"]["progress"] == 0
    assert state["window"]["page_digest"] is None
    assert state["window"]["key"] is None  # the cursor is where it was
    assert [e for e in cap if e["event"] == "retell_page_unstable"] == [
        {
            "event": "retell_page_unstable",
            "log_level": "error",
            "provider_id": str(pid),
            "page_changed": 3,
            "backoff_until": state["backoff_until"],
        }
    ]
    assert not [e for e in cap if e["event"] == "retell_page_changed"]


def test_a_completed_page_clears_the_page_changed_counter(fake_table, monkeypatch):
    # v1.15.2 F9: only mismatches IN A ROW say the listing itself is unusable.
    # A page that resumes against its own digest and completes proves the
    # opposite, so the count of changes that preceded it must not be carried
    # into some later, unrelated page's first wobble.
    pid = uuid.uuid4()
    now = _dt(2026, 1, 1, 12, 0, 0)
    page_calls = _calls(5, "steady")
    digest = _retell_page_digest(page_calls)
    window = _in_progress_window(
        now, key=None, progress=2, page_digest=digest, counts=[2, 0, 0, 0, 0, 0]
    )
    row = _windowed_row(pid, now, window)
    row.poll_state["retell"]["page_changed"] = 2
    fake_table[pid] = row
    _freeze_now(monkeypatch, now)
    monkeypatch.setattr(op, "process_and_store_logs", _store_each())
    monkeypatch.setattr(
        op.ObservabilityService,
        "fetch_retell_page",
        lambda *a, **k: _page(
            page_calls[2:], has_more=False, digest=digest, listed=5, indices=range(2, 5)
        ),
    )

    with capture_logs() as cap:
        assert op._poll_retell_provider(_retell_provider(row)) == op.StoreOutcome(
            5, 0, 0
        )

    assert not [e for e in cap if e["event"] == "retell_page_unstable"]
    assert "page_changed" not in fake_table[pid].poll_state["retell"]


def test_a_partial_verdict_retry_resets_the_page_progress(fake_table, monkeypatch):
    # v1.15 B5: a `partial` page is retried whole next run, so the progress and
    # the counts of the attempt that failed must not survive into it.
    pid = uuid.uuid4()
    now = _dt(2026, 1, 1, 12, 0, 0)
    page_calls = _calls(5, "part")
    digest = _retell_page_digest(page_calls)
    window = _in_progress_window(
        now, key=None, progress=2, page_digest=digest, counts=[2, 0, 0, 0, 0, 0]
    )
    row = _windowed_row(pid, now, window)
    fake_table[pid] = row
    _freeze_now(monkeypatch, now)
    monkeypatch.setattr(
        op.ObservabilityService,
        "fetch_retell_page",
        lambda *a, **k: _page(
            page_calls[2:], has_more=False, digest=digest, listed=5, indices=range(2, 5)
        ),
    )
    monkeypatch.setattr(
        op, "process_and_store_logs", lambda *a, **k: op.StoreOutcome(0, 0, 1)
    )

    assert op._poll_retell_provider(_retell_provider(row)) is None

    state = fake_table[pid].poll_state["retell"]
    assert state["failed_runs"] == 1
    window = state["window"]
    assert window["progress"] == 0
    assert window["page_digest"] is None
    assert window["page_counts"] == [0, 0, 0, 0, 0, 0]


def test_a_total_verdict_also_resets_the_page_progress(fake_table, monkeypatch):
    # Deviation from v1.15 B5, which names the reset only for `partial`: a
    # `total` page is likewise retried whole. Were its progress kept, the next
    # run would resume past every call, store nothing, re-classify the same
    # all-failed counts as `total` again and escalate the backoff for ever
    # without ever retrying a single call.
    pid = uuid.uuid4()
    now = _dt(2026, 1, 1, 12, 0, 0)
    row = _windowed_row(pid, now)
    fake_table[pid] = row
    _freeze_now(monkeypatch, now)
    seen = []

    def fake_fetch(prov, start, end, *, pagination_key=None, skip=None, **kwargs):
        seen.append(kwargs["resume_from"])
        return _page(_calls(3, "t"), has_more=False, listed=4, drops=((3, 5),))

    monkeypatch.setattr(op.ObservabilityService, "fetch_retell_page", fake_fetch)
    monkeypatch.setattr(
        op, "process_and_store_logs", lambda *a, **k: op.StoreOutcome(0, 0, 1)
    )

    with capture_logs() as cap:
        assert op._poll_retell_provider(_retell_provider(row)) is None

    # v1.15.2 F11: the one event that says WHY the backoff started must carry
    # the failed page's own numbers. `_reset_page_progress` runs before
    # `_on_total_failure` logs them, so it has to rebind `page_counts` — were it
    # to clear the list in place, this event would report an all-zero page.
    incomplete = [e for e in cap if e["event"] == "retell_store_incomplete"]
    assert len(incomplete) == 1
    assert incomplete[0]["stored"] == 0
    assert incomplete[0]["export_failed"] == 3
    assert incomplete[0]["dropped_failed"] == 1

    state = fake_table[pid].poll_state["retell"]
    assert state["total_failures"] == 1
    window = state["window"]
    assert window["progress"] == 0
    assert window["page_counts"] == [0, 0, 0, 0, 0, 0]

    # The next run (after the backoff) re-lists and re-stores the page from 0.
    _freeze_now(monkeypatch, now + op._backoff_delay(1) + timedelta(seconds=1))
    op._poll_retell_provider(_retell_provider(fake_table[pid]))
    assert seen == [0, 0]


def test_the_activity_deadline_also_stops_inside_a_page(fake_table, monkeypatch):
    # v1.15 B2: `run_deadline` is min(run budget, activity deadline), and it is
    # the value the store loop checks — so the fan-out's deadline bounds a page
    # from the inside too, not just the page loop from the outside.
    pid = uuid.uuid4()
    now = _dt(2026, 1, 1, 12, 0, 0)
    row = _windowed_row(pid, now)
    fake_table[pid] = row
    _freeze_now(monkeypatch, now)  # the run budget alone would never fire
    seen_deadline = []

    def fake_fetch(prov, start, end, *, pagination_key=None, skip=None, **kwargs):
        seen_deadline.append(kwargs["deadline"])
        return _page(_calls(5, "d"), has_more=True, next_key="cursor-2")

    monkeypatch.setattr(op.ObservabilityService, "fetch_retell_page", fake_fetch)
    monkeypatch.setattr(op, "process_and_store_logs", _store_each())

    activity_deadline = now - timedelta(minutes=1)
    outcome = op._poll_retell_provider(
        _retell_provider(row), deadline=activity_deadline
    )

    assert seen_deadline == [activity_deadline]  # narrowed past the run budget
    assert outcome == op.StoreOutcome(1, 0, 0)  # stopped after the first call
    window = fake_table[pid].poll_state["retell"]["window"]
    assert window["progress"] == 1
    assert window["key"] is None


def test_drops_before_a_checkpoint_still_make_the_completed_page_partial(
    fake_table, monkeypatch
):
    # v1.15.1 F1, the R1 High finding. A resumed response only describes the
    # slice it hydrated, so the two 500s of the first run appear in no later
    # response. Counted only there, they would vanish at the checkpoint: the
    # page would complete as "ok", the cursor and the watermark would move past
    # it, and two retryable calls would be excluded from every later window
    # with nothing logged. The window must accumulate drops as it does stores.
    pid = uuid.uuid4()
    now = _dt(2026, 1, 1, 12, 0, 0)
    row = _windowed_row(pid, now)
    fake_table[pid] = row
    _freeze_now(monkeypatch, now)
    listed = _calls(10, "mix")
    digest = _retell_page_digest(listed)
    monkeypatch.setattr(op, "process_and_store_logs", _store_each())

    # Run 1: hydration reached index 4 and two of those five calls failed 500x3.
    monkeypatch.setattr(
        op.ObservabilityService,
        "fetch_retell_page",
        lambda *a, **k: _page(
            [listed[0], listed[2], listed[4]],
            has_more=False,
            drops=((1, 5), (3, 5)),
            digest=digest,
            listed=10,
            consumed=5,
            indices=(0, 2, 4),
        ),
    )
    assert op._poll_retell_provider(_retell_provider(row)) == op.StoreOutcome(3, 0, 0)
    window = fake_table[pid].poll_state["retell"]["window"]
    assert window["progress"] == 5
    assert window["page_counts"] == [3, 0, 0, 0, 0, 2]  # the drops are remembered

    # Run 2: the tail hydrates cleanly, so this response drops nothing.
    monkeypatch.setattr(
        op.ObservabilityService,
        "fetch_retell_page",
        lambda *a, **k: _page(
            listed[5:],
            has_more=False,
            digest=digest,
            listed=10,
            consumed=10,
            indices=range(5, 10),
        ),
    )
    with capture_logs() as cap:
        assert op._poll_retell_provider(_retell_provider(fake_table[pid])) is None

    counts = [e for e in cap if e["event"] == "retell_poll_counts"]
    assert len(counts) == 1
    assert counts[0]["stored"] == 8
    assert counts[0]["dropped_failed"] == 2  # the PAGE's total, not the last slice
    incomplete = [e for e in cap if e["event"] == "retell_store_incomplete"]
    assert len(incomplete) == 1 and incomplete[0]["dropped_failed"] == 2
    state = fake_table[pid].poll_state["retell"]
    assert state["failed_runs"] == 1  # `partial`: the page is retried
    assert state["window"]["progress"] == 0  # and retried whole
    assert state["window"]["page_counts"] == [0, 0, 0, 0, 0, 0]
    assert fake_table[pid].last_fetched_at == now - timedelta(minutes=30)


def test_drops_past_a_mid_loop_checkpoint_are_counted_once_not_twice(
    fake_table, monkeypatch
):
    # v1.15.2 F8, the R2 High finding. The window's counts must cover exactly
    # what `progress` covers. A mid-loop checkpoint stops `progress` at the call
    # the deadline landed on, while the response that arrived had already
    # decided the fate of items far past it — and the next run, resuming at
    # `progress`, is told about those same drops again. Counted when the
    # response arrives, they land in the window twice: here that turns
    # `dropped_failed=6` on a page of 10 malformed calls into 12, which is the
    # difference between `partial` (retry the page, keep polling) and `total`
    # (an escalating backoff and no live calls for this provider for hours).
    pid = uuid.uuid4()
    now = _dt(2026, 1, 1, 12, 0, 0)
    row = _windowed_row(pid, now)
    fake_table[pid] = row
    listed = _calls(16, "r2")
    digest = _retell_page_digest(listed)
    # 0 and 2-9 hydrated; 1 and 10-15 failed 500x3, so the page really drops
    # seven. The drop at index 1 sits exactly on the checkpoint boundary
    # (progress == 1 after the first store): counted with `<=` it would be
    # counted again on the resume, which is the R2 High one drop at a time.
    failed = ((1, 5),) + tuple((index, 5) for index in range(10, 16))
    hydrated = listed[:1] + listed[2:10]
    monkeypatch.setattr(
        op.ObservabilityService,
        "fetch_retell_page",
        lambda *a, **k: _page(
            hydrated,
            has_more=False,
            drops=failed,
            digest=digest,
            listed=16,
            consumed=16,
            indices=(0, *range(2, 10)),
        ),
    )

    clock = {"t": now}
    stores = {"n": 0}
    monkeypatch.setattr(op.timezone, "now", lambda: clock["t"])

    def slow_store(logs, *a, **k):  # every call stores malformed
        stores["n"] += 1
        if stores["n"] == 1:  # the deadline goes during the first store
            clock["t"] = now + op.RETELL_RUN_BUDGET + timedelta(seconds=1)
        return op.StoreOutcome(0, len(logs), 0)

    monkeypatch.setattr(op, "process_and_store_logs", slow_store)

    assert op._poll_retell_provider(_retell_provider(row)) == op.StoreOutcome(0, 1, 0)
    window = fake_table[pid].poll_state["retell"]["window"]
    assert window["progress"] == 1
    # Not one of the seven is behind the checkpoint, so not one is counted yet.
    assert window["page_counts"] == [0, 1, 0, 0, 0, 0]

    # Run 2 resumes at 1, is told about the same seven drops, and completes.
    monkeypatch.setattr(
        op.ObservabilityService,
        "fetch_retell_page",
        lambda *a, **k: _page(
            listed[2:10],
            has_more=False,
            drops=failed,
            digest=digest,
            listed=16,
            consumed=16,
            indices=range(2, 10),
        ),
    )
    with capture_logs() as cap:
        assert op._poll_retell_provider(_retell_provider(fake_table[pid])) is None

    counts = [e for e in cap if e["event"] == "retell_poll_counts"]
    assert len(counts) == 1
    assert counts[0]["malformed"] == 9 and counts[0]["stored"] == 0
    assert counts[0]["dropped_failed"] == 7  # the page's seven, not fourteen
    state = fake_table[pid].poll_state["retell"]
    assert state["failed_runs"] == 1  # `partial`: the page is retried
    assert "total_failures" not in state and "backoff_until" not in state


def test_a_page_hydrated_to_zero_neither_writes_state_nor_logs_a_checkpoint(
    fake_table, monkeypatch
):
    # v1.15.1 F7: the deadline went during the list request, so hydration
    # submitted nothing. There is no progress, no page identity and no count to
    # remember — writing state and announcing a checkpoint would report a run
    # that learned nothing, and the next run re-lists this page from 0 anyway.
    pid = uuid.uuid4()
    now = _dt(2026, 1, 1, 12, 0, 0)
    row = _windowed_row(pid, now)
    fake_table[pid] = row
    _freeze_now(monkeypatch, now)
    monkeypatch.setattr(op, "process_and_store_logs", _store_each())
    monkeypatch.setattr(
        op.ObservabilityService,
        "fetch_retell_page",
        lambda *a, **k: _page(
            [],
            has_more=True,
            next_key="cursor-2",
            digest=_retell_page_digest(_calls(10, "unseen")),
            listed=10,
            consumed=0,
            indices=(),
        ),
    )

    with capture_logs() as cap:
        outcome = op._poll_retell_provider(
            _retell_provider(row), deadline=now - timedelta(minutes=1)
        )

    assert outcome == op.StoreOutcome(0, 0, 0)
    assert not [e for e in cap if e["event"] == "retell_page_checkpointed"]
    assert "window" not in fake_table[pid].poll_state["retell"]  # nothing written


def test_a_mismatch_on_a_spent_deadline_is_still_counted(fake_table, monkeypatch):
    # The one path where a page is both unstable and making no progress: the
    # digest mismatches and the deadline went during the list request. The
    # "nothing learned" shortcut must not swallow the mismatch count, or such
    # a page could log a warning every run and never reach the backoff.
    pid = uuid.uuid4()
    now = _dt(2026, 1, 1, 12, 0, 0)
    window = _in_progress_window(
        now, progress=3, page_digest="old", counts=[3, 0, 0, 0, 0, 0]
    )
    row = _windowed_row(pid, now, window)
    fake_table[pid] = row
    _freeze_now(monkeypatch, now)
    monkeypatch.setattr(op, "process_and_store_logs", _store_each())
    monkeypatch.setattr(
        op.ObservabilityService,
        "fetch_retell_page",
        lambda *a, **k: _page(
            [],
            has_more=True,
            next_key="cursor-2",
            digest="new",
            listed=10,
            consumed=0,
            indices=(),
        ),
    )

    with capture_logs() as cap:
        outcome = op._poll_retell_provider(
            _retell_provider(row), deadline=now - timedelta(minutes=1)
        )

    assert outcome == op.StoreOutcome(0, 0, 0)
    state = fake_table[pid].poll_state["retell"]
    assert state["page_changed"] == 1  # persisted, not swallowed
    assert state["window"]["progress"] == 0
    assert state["window"]["page_digest"] == "new"
    assert [e["event"] for e in cap if e["event"].startswith("retell_page_")] == [
        "retell_page_changed",
        "retell_page_checkpointed",
    ]


def test_a_checkpointing_run_still_raises_the_behind_alarm(fake_table, monkeypatch):
    # A slow page is exactly when the frontier ages while the cursor stands
    # still. If the alarm fired only on a cursor advance, a provider hours
    # behind could checkpoint every tick for a day with nothing above info.
    pid = uuid.uuid4()
    now = _dt(2026, 1, 1, 12, 0, 0)
    behind = op.RETELL_WINDOW_HINT_MAX + op.RETELL_BEHIND_ERROR + timedelta(hours=1)
    row = _FakeRow(
        id=pid,
        last_fetched_at=now - behind,
        poll_state={"retell": {"bootstrapped": True}},
    )
    fake_table[pid] = row
    clock = {"t": now}
    stores = {"n": 0}
    monkeypatch.setattr(op.timezone, "now", lambda: clock["t"])

    def slow_store(logs, *a, **k):
        stores["n"] += 1
        if stores["n"] == 1:
            clock["t"] = now + op.RETELL_RUN_BUDGET + timedelta(seconds=1)
        return op.StoreOutcome(len(logs), 0, 0)

    monkeypatch.setattr(op, "process_and_store_logs", slow_store)
    monkeypatch.setattr(
        op.ObservabilityService,
        "fetch_retell_page",
        lambda *a, **k: _page(_calls(50, "slow"), has_more=True, next_key="cursor-2"),
    )

    with capture_logs() as cap:
        op._poll_retell_provider(_retell_provider(row))

    assert [e["event"] for e in cap if e["event"] == "retell_page_checkpointed"]
    # the window opened at the hint, so its end is 7 h old by the checkpoint
    window_end = now - behind + op.RETELL_WINDOW_HINT_MAX
    assert [e for e in cap if e["event"] == "retell_poll_stalled"] == [
        {
            "event": "retell_poll_stalled",
            "log_level": "error",
            "provider_id": str(pid),
            "frontier_age_seconds": int((clock["t"] - window_end).total_seconds()),
            "pages_stored": 0,
        }
    ]


def test_a_checkpoint_on_a_fresh_frontier_stays_quiet(fake_table, monkeypatch):
    # The shared activity deadline can force a checkpoint on a window whose
    # frontier is only a minute old; that is not "behind" and must not warn.
    pid = uuid.uuid4()
    now = _dt(2026, 1, 1, 12, 0, 0)
    row = _windowed_row(pid, now)  # frontier 60 s old
    fake_table[pid] = row
    clock = {"t": now}
    stores = {"n": 0}
    monkeypatch.setattr(op.timezone, "now", lambda: clock["t"])

    def slow_store(logs, *a, **k):
        stores["n"] += 1
        if stores["n"] == 1:
            clock["t"] = now + timedelta(minutes=2)  # past the activity deadline
        return op.StoreOutcome(len(logs), 0, 0)

    monkeypatch.setattr(op, "process_and_store_logs", slow_store)
    monkeypatch.setattr(
        op.ObservabilityService,
        "fetch_retell_page",
        lambda *a, **k: _page(_calls(50, "fresh"), has_more=True, next_key="cursor-2"),
    )

    with capture_logs() as cap:
        op._poll_retell_provider(
            _retell_provider(row), deadline=now + timedelta(minutes=1)
        )

    events = [e["event"] for e in cap]
    assert "retell_page_checkpointed" in events
    assert not {"retell_poll_behind", "retell_poll_stalled"} & set(events)


def test_a_bootstrap_checkpoint_never_reports_behind(fake_table, monkeypatch):
    # The bootstrap frontier is frozen by design; ageing is not "behind".
    pid = uuid.uuid4()
    now = _dt(2026, 1, 1, 12, 0, 0)
    row = _FakeRow(id=pid, last_fetched_at=None, poll_state={})
    fake_table[pid] = row
    clock = {"t": now}
    stores = {"n": 0}
    monkeypatch.setattr(op.timezone, "now", lambda: clock["t"])

    def slow_store(logs, *a, **k):
        stores["n"] += 1
        if stores["n"] == 1:
            clock["t"] = now + op.RETELL_RUN_BUDGET + op.RETELL_BEHIND_ERROR
        return op.StoreOutcome(len(logs), 0, 0)

    monkeypatch.setattr(op, "process_and_store_logs", slow_store)
    monkeypatch.setattr(
        op.ObservabilityService,
        "fetch_retell_page",
        lambda *a, **k: _page(_calls(50, "boot"), has_more=True, next_key="cursor-2"),
    )

    with capture_logs() as cap:
        op._poll_retell_provider(_retell_provider(row))

    events = [e["event"] for e in cap]
    assert "retell_page_checkpointed" in events
    assert not {"retell_poll_behind", "retell_poll_stalled"} & set(events)


def test_a_restart_forgets_the_mismatches_of_the_page_it_abandons(
    fake_table, monkeypatch
):
    # `page_changed` is per page: a window restart re-lists from page 1, so
    # mismatches seen on the abandoned page must not count toward a later one.
    pid = uuid.uuid4()
    now = _dt(2026, 1, 1, 12, 0, 0)
    window = _in_progress_window(now, progress=3, page_digest="old")
    row = _windowed_row(pid, now, window)
    row.poll_state["retell"]["page_changed"] = 2
    fake_table[pid] = row
    _freeze_now(monkeypatch, now)

    def rejected(*a, **k):
        raise op.RetellCursorRejected(cause="http_422")

    monkeypatch.setattr(op.ObservabilityService, "fetch_retell_page", rejected)

    assert op._poll_retell_provider(_retell_provider(row)) is None
    state = fake_table[pid].poll_state["retell"]
    assert "page_changed" not in state
    assert state["window"]["restarts"] == 1


@pytest.mark.parametrize("pages_stored", [1, 0])
def test_an_empty_listing_on_a_resume_is_a_paging_fault_not_a_mismatch(
    fake_table, monkeypatch, pages_stored
):
    # An empty page with has_more is judged before the digest comparison, so
    # it restarts the window and is never scored as page instability. The
    # pages_stored == 0 case is the ordinary first-page resume: nothing
    # completed yet, but progress says paging is under way, and scoring the
    # empty listing as a mismatch there would complete a phantom page and
    # advance the cursor past calls that were never stored.
    pid = uuid.uuid4()
    now = _dt(2026, 1, 1, 12, 0, 0)
    window = _in_progress_window(now, progress=3, page_digest="old")
    window["pages_stored"] = pages_stored
    row = _windowed_row(pid, now, window)
    fake_table[pid] = row
    _freeze_now(monkeypatch, now)
    monkeypatch.setattr(
        op.ObservabilityService,
        "fetch_retell_page",
        lambda *a, **k: _page(
            [],
            has_more=True,
            next_key="k",
            digest=None,
            listed=0,
            consumed=0,
            indices=(),
        ),
    )

    with capture_logs() as cap:
        assert op._poll_retell_provider(_retell_provider(row)) is None

    state = fake_table[pid].poll_state["retell"]
    assert "page_changed" not in state
    restarted = [e for e in cap if e["event"] == "retell_window_restarted"]
    assert len(restarted) == 1 and restarted[0]["cause"] == "empty_page"
    assert not [e for e in cap if e["event"] == "retell_page_changed"]


def test_a_resumed_page_re_ordered_under_us_is_re_stored_from_zero(
    fake_table, monkeypatch
):
    # v1.15.1 F2: the members are unchanged, only the order moved. The resume
    # skips items [:2] by POSITION, so a digest blind to order would skip two
    # calls that are no longer there and never store them.
    pid = uuid.uuid4()
    now = _dt(2026, 1, 1, 12, 0, 0)
    page_calls = _calls(5, "shuf")
    reordered = page_calls[3:] + page_calls[:3]
    window = _in_progress_window(
        now,
        key=None,
        progress=2,
        page_digest=_retell_page_digest(page_calls),
        counts=[2, 0, 0, 0, 0, 0],
    )
    row = _windowed_row(pid, now, window)
    fake_table[pid] = row
    _freeze_now(monkeypatch, now)
    monkeypatch.setattr(op, "process_and_store_logs", _store_each())
    monkeypatch.setattr(
        op.ObservabilityService,
        "fetch_retell_page",
        lambda *a, **k: _page(reordered, has_more=False),
    )

    with capture_logs() as cap:
        outcome = op._poll_retell_provider(_retell_provider(row))

    assert outcome == op.StoreOutcome(5, 0, 0)  # all five, not the last three
    assert [e["event"] for e in cap if e["event"] == "retell_page_changed"] == [
        "retell_page_changed"
    ]


# Mutation guards (v1.14 review fix F6)
# --------------------------------------------------------------------------


def test_write_retell_state_never_precedes_process_and_store_logs_for_a_page(
    fake_table, monkeypatch
):
    # F6(a): B3's checkpoint property requires the store to happen before the
    # state write, for every page. Every other test only checks the FINAL
    # state, never the ORDER `process_and_store_logs` and
    # `_write_retell_state` ran in — a mutation that hoisted the write above
    # the store (silently reversing B3's loss direction) would survive all of
    # them undetected.
    pid = uuid.uuid4()
    now = _dt(2026, 1, 1, 12, 0, 0)
    row = _FakeRow(id=pid, poll_state={})
    fake_table[pid] = row
    provider = _retell_provider(row)
    _freeze_now(monkeypatch, now)
    monkeypatch.setattr(
        op.ObservabilityService,
        "fetch_retell_page",
        lambda *a, **k: _page(_calls(1), has_more=False),
    )

    order = []
    monkeypatch.setattr(
        op,
        "process_and_store_logs",
        lambda *a, **k: order.append("store") or op.StoreOutcome(1, 0, 0),
    )
    real_write = op._write_retell_state

    def spy_write(provider_id, state):
        order.append("write")
        return real_write(provider_id, state)

    monkeypatch.setattr(op, "_write_retell_state", spy_write)

    outcome = op._poll_retell_provider(provider)

    assert outcome == op.StoreOutcome(1, 0, 0)
    assert order == ["store", "write"]


def test_bootstrap_page_cap_check_never_applies_the_ordinary_window_guard(
    fake_table, monkeypatch
):
    # F6(b): RETELL_MAX_PAGES_PER_WINDOW must never gate a bootstrap window —
    # only RETELL_BOOTSTRAP_MAX_PAGES does. `pages_stored` is set well past
    # the ORDINARY-window cap on purpose: if a mutation dropped the
    # `not bootstrap` guard on that check, this page would restart
    # (page_cap) before ever being stored, instead of completing normally
    # through the bootstrap total_pages cap as it must.
    pid = uuid.uuid4()
    now = _dt(2026, 1, 1, 12, 0, 0)
    frozen_end = (now - op.RETELL_VISIBILITY_LAG).isoformat()
    window = {
        "start": None,
        "end": frozen_end,
        "opened_at_hint": False,
        "narrowed": False,
        "key": "cursor-x",
        "skip": None,
        "pages_stored": op.RETELL_MAX_PAGES_PER_WINDOW,  # > the ORDINARY-window cap
        "progress": 0,
        "page_digest": None,
        "page_counts": [0, 0, 0, 0, 0, 0],
        "digests": [],
        "restarts": 0,
    }
    row = _FakeRow(id=pid, poll_state={"retell": {"window": window}})
    fake_table[pid] = row
    provider = _retell_provider(row)
    _freeze_now(monkeypatch, now)
    monkeypatch.setattr(
        op.ObservabilityService,
        "fetch_retell_page",
        lambda *a, **k: _page(
            _calls(1, "over-window-cap"), has_more=True, next_key="cursor-y"
        ),
    )
    monkeypatch.setattr(
        op, "process_and_store_logs", lambda *a, **k: op.StoreOutcome(1, 0, 0)
    )

    with capture_logs() as cap:
        outcome = op._poll_retell_provider(provider)

    # Stored and completed via the BOOTSTRAP cap — never restarted via the
    # ordinary-window "page_cap" guard.
    assert outcome == op.StoreOutcome(1, 0, 0)
    assert fake_table[pid].poll_state["retell"] == {"bootstrapped": True}
    assert fake_table[pid].last_fetched_at == op._parse(frozen_end)
    assert not [e for e in cap if e["event"] == "retell_window_restarted"]
    assert any(e["event"] == "retell_bootstrap_capped" for e in cap)


# --------------------------------------------------------------------------
# Windowed runs
# --------------------------------------------------------------------------


def test_windowed_single_page_pops_window_and_advances_watermark(
    fake_table, monkeypatch
):
    pid = uuid.uuid4()
    now = _dt(2026, 1, 1, 12, 0, 0)
    wm = now - timedelta(minutes=30)
    row = _FakeRow(
        id=pid, last_fetched_at=wm, poll_state={"retell": {"bootstrapped": True}}
    )
    fake_table[pid] = row
    provider = _retell_provider(row)
    _freeze_now(monkeypatch, now)
    monkeypatch.setattr(
        op.ObservabilityService,
        "fetch_retell_page",
        lambda *a, **k: _page(_calls(2), has_more=False),
    )
    monkeypatch.setattr(op, "process_and_store_logs", _store_each())

    outcome = op._poll_retell_provider(provider)
    assert outcome == op.StoreOutcome(2, 0, 0)
    assert "window" not in fake_table[pid].poll_state["retell"]
    assert fake_table[pid].last_fetched_at == now - op.RETELL_VISIBILITY_LAG


def test_retell_page_all_malformed_logged_when_ok_verdict_has_zero_stored(
    fake_table, monkeypatch
):
    # §7 / `_classify` boundary "1000 malformed -> ok + retell_page_all_malformed",
    # reached through the real orchestrator (not `process_and_store_logs`
    # directly) — every other seeded `StoreOutcome` in this file has
    # malformed=0 except the one test that bypasses `_poll_retell_provider`.
    pid = uuid.uuid4()
    now = _dt(2026, 1, 1, 12, 0, 0)
    wm = now - timedelta(minutes=30)
    row = _FakeRow(
        id=pid, last_fetched_at=wm, poll_state={"retell": {"bootstrapped": True}}
    )
    fake_table[pid] = row
    provider = _retell_provider(row)
    _freeze_now(monkeypatch, now)
    monkeypatch.setattr(
        op.ObservabilityService,
        "fetch_retell_page",
        lambda *a, **k: _page(_calls(5), has_more=False),
    )
    monkeypatch.setattr(
        op, "process_and_store_logs", _store_each(stored=0, malformed=1)
    )

    with capture_logs() as cap:
        outcome = op._poll_retell_provider(provider)

    assert outcome == op.StoreOutcome(0, 5, 0)
    # pins impl L407-408's `logger.warning("retell_page_all_malformed", ...)`
    assert {
        "event": "retell_page_all_malformed",
        "log_level": "warning",
        "provider_id": str(pid),
        "malformed": 5,
    } in cap
    assert (
        "window" not in fake_table[pid].poll_state["retell"]
    )  # ok verdict: the window still completes normally


def test_windowed_multi_page_persists_key_no_advance_then_advances_on_last_page(
    fake_table, monkeypatch
):
    pid = uuid.uuid4()
    now = _dt(2026, 1, 1, 12, 0, 0)
    wm = now - timedelta(minutes=30)
    row = _FakeRow(
        id=pid, last_fetched_at=wm, poll_state={"retell": {"bootstrapped": True}}
    )
    fake_table[pid] = row
    # F2: identical content on every call — see _freeze_now_then_over_budget.
    _freeze_now_then_over_budget(monkeypatch, now)
    monkeypatch.setattr(
        op.ObservabilityService,
        "fetch_retell_page",
        lambda *a, **k: _page(_calls(1, "p1"), has_more=True, next_key="cursor-1"),
    )
    monkeypatch.setattr(
        op, "process_and_store_logs", lambda *a, **k: op.StoreOutcome(1, 0, 0)
    )

    provider = _retell_provider(row)
    outcome = op._poll_retell_provider(provider)
    assert outcome == op.StoreOutcome(1, 0, 0)
    window = fake_table[pid].poll_state["retell"]["window"]
    assert window["key"] == "cursor-1"
    assert window["pages_stored"] == 1
    assert fake_table[pid].last_fetched_at == wm  # unchanged mid-window

    _freeze_now(monkeypatch, now)
    monkeypatch.setattr(
        op.ObservabilityService,
        "fetch_retell_page",
        lambda *a, **k: _page(_calls(1, "p2"), has_more=False),
    )
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
    row = _FakeRow(
        id=pid, last_fetched_at=wm, poll_state={"retell": {"bootstrapped": True}}
    )
    fake_table[pid] = row
    _freeze_now(monkeypatch, now)
    monkeypatch.setattr(
        op.ObservabilityService,
        "fetch_retell_page",
        lambda *a, **k: _page(_calls(1), has_more=False),
    )
    monkeypatch.setattr(
        op, "process_and_store_logs", lambda *a, **k: op.StoreOutcome(1, 0, 0)
    )

    provider = _retell_provider(row)
    op._poll_retell_provider(provider)
    first_window_end = fake_table[
        pid
    ].last_fetched_at  # the watermark IS the completed window's end
    assert "window" not in fake_table[pid].poll_state["retell"]

    later = now + timedelta(minutes=5)
    _freeze_now(monkeypatch, later)
    seen_starts = []
    monkeypatch.setattr(
        op.ObservabilityService,
        "fetch_retell_page",
        lambda prov, start, end, **k: (
            seen_starts.append(start) or _page(_calls(1), has_more=False)
        ),
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
    row = _FakeRow(
        id=pid, last_fetched_at=wm, poll_state={"retell": {"bootstrapped": True}}
    )
    fake_table[pid] = row
    provider = _retell_provider(row)
    _freeze_now(monkeypatch, now)
    monkeypatch.setattr(
        op.ObservabilityService,
        "fetch_retell_page",
        lambda *a, **k: pytest.fail("must not fetch: start >= end"),
    )

    outcome = op._poll_retell_provider(provider)

    assert outcome == op.StoreOutcome(0, 0, 0)
    assert "window" not in fake_table[pid].poll_state["retell"]
    assert fake_table[pid].last_fetched_at == wm  # untouched


def test_digest_history_caps_at_eight_evicts_oldest_newest_last(
    fake_table, monkeypatch
):
    # §7: "a page digest seen within the last 8 pages -> restart" implies the
    # history is actually bounded and ordered; every other test only ever
    # seeds 0 or 1 digest, so eviction and ordering were never exercised.
    pid = uuid.uuid4()
    now = _dt(2026, 1, 1, 12, 0, 0)
    wm = now - timedelta(hours=1)
    old_digests = [
        f"digest_{i}" for i in range(op.RETELL_DIGEST_HISTORY)
    ]  # already at the cap
    window = {
        "start": wm.isoformat(),
        "end": now.isoformat(),
        "opened_at_hint": False,
        "narrowed": False,
        "key": "k8",
        "skip": None,
        "pages_stored": op.RETELL_DIGEST_HISTORY,
        "progress": 0,
        "page_digest": None,
        "page_counts": [0, 0, 0, 0, 0, 0],
        "digests": list(old_digests),
        "restarts": 0,
    }
    row = _FakeRow(
        id=pid,
        last_fetched_at=wm,
        poll_state={"retell": {"bootstrapped": True, "window": window}},
    )
    fake_table[pid] = row
    provider = _retell_provider(row)
    # F2: identical content on every call — see _freeze_now_then_over_budget.
    _freeze_now_then_over_budget(monkeypatch, now)
    monkeypatch.setattr(
        op.ObservabilityService,
        "fetch_retell_page",
        lambda *a, **k: _page(_calls(1, "fresh"), has_more=True, next_key="k9"),
    )
    monkeypatch.setattr(
        op, "process_and_store_logs", lambda *a, **k: op.StoreOutcome(1, 0, 0)
    )

    op._poll_retell_provider(provider)

    new_digests = fake_table[pid].poll_state["retell"]["window"]["digests"]
    expected_new_digest = _retell_page_digest(_calls(1, "fresh"))
    assert len(new_digests) == op.RETELL_DIGEST_HISTORY  # still capped, not 9
    assert new_digests[-1] == expected_new_digest  # newest last
    assert old_digests[0] not in new_digests  # oldest (index 0) evicted
    assert new_digests[:-1] == old_digests[1:]  # the remaining 7 shift down in order


def test_cursor_rejected_restarts_window_with_cause(fake_table, monkeypatch):
    pid = uuid.uuid4()
    now = _dt(2026, 1, 1, 12, 0, 0)
    wm = now - timedelta(minutes=30)
    window = {
        "start": wm.isoformat(),
        "end": now.isoformat(),
        "opened_at_hint": False,
        "narrowed": False,
        "key": "stale",
        "skip": None,
        "pages_stored": 1,
        "progress": 0,
        "page_digest": None,
        "page_counts": [0, 0, 0, 0, 0, 0],
        "digests": ["deadbeef"],
        "restarts": 0,
    }
    row = _FakeRow(
        id=pid,
        last_fetched_at=wm,
        poll_state={"retell": {"bootstrapped": True, "window": window}},
    )
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
    assert {
        "event": "retell_window_restarted",
        "log_level": "warning",
        "provider_id": str(pid),
        "cause": "missing_key",
        "restarts": 1,
        "pages_stored": 1,
    } in cap


def test_repeated_digest_restarts_window(fake_table, monkeypatch):
    pid = uuid.uuid4()
    now = _dt(2026, 1, 1, 12, 0, 0)
    wm = now - timedelta(minutes=30)
    window = {
        "start": wm.isoformat(),
        "end": now.isoformat(),
        "opened_at_hint": False,
        "narrowed": False,
        "key": "k1",
        "skip": None,
        "pages_stored": 1,
        "progress": 0,
        "page_digest": None,
        "page_counts": [0, 0, 0, 0, 0, 0],
        "digests": [_retell_page_digest(_calls(1, "dup"))],
        "restarts": 0,
    }
    row = _FakeRow(
        id=pid,
        last_fetched_at=wm,
        poll_state={"retell": {"bootstrapped": True, "window": window}},
    )
    fake_table[pid] = row
    provider = _retell_provider(row)
    _freeze_now(monkeypatch, now)
    monkeypatch.setattr(
        op.ObservabilityService,
        "fetch_retell_page",
        lambda *a, **k: _page(_calls(1, "dup"), has_more=True, next_key="k2"),
    )

    result = op._poll_retell_provider(provider)
    assert result is None
    assert fake_table[pid].poll_state["retell"]["window"]["restarts"] == 1


def test_empty_page_with_has_more_after_first_page_restarts(fake_table, monkeypatch):
    pid = uuid.uuid4()
    now = _dt(2026, 1, 1, 12, 0, 0)
    wm = now - timedelta(minutes=30)
    window = {
        "start": wm.isoformat(),
        "end": now.isoformat(),
        "opened_at_hint": False,
        "narrowed": False,
        "key": "k1",
        "skip": None,
        "pages_stored": 1,
        "progress": 0,
        "page_digest": None,
        "page_counts": [0, 0, 0, 0, 0, 0],
        "digests": ["deadbeef"],
        "restarts": 0,
    }
    row = _FakeRow(
        id=pid,
        last_fetched_at=wm,
        poll_state={"retell": {"bootstrapped": True, "window": window}},
    )
    fake_table[pid] = row
    provider = _retell_provider(row)
    _freeze_now(monkeypatch, now)
    monkeypatch.setattr(
        op.ObservabilityService,
        "fetch_retell_page",
        lambda *a, **k: _page([], has_more=True, next_key="k2"),
    )

    result = op._poll_retell_provider(provider)
    assert result is None
    assert fake_table[pid].poll_state["retell"]["window"]["restarts"] == 1


def test_page_cap_restarts_window(fake_table, monkeypatch):
    pid = uuid.uuid4()
    now = _dt(2026, 1, 1, 12, 0, 0)
    wm = now - timedelta(minutes=30)
    window = {
        "start": wm.isoformat(),
        "end": now.isoformat(),
        "opened_at_hint": False,
        "narrowed": False,
        "key": "k1",
        "skip": None,
        "pages_stored": op.RETELL_MAX_PAGES_PER_WINDOW - 1,
        "progress": 0,
        "page_digest": None,
        "page_counts": [0, 0, 0, 0, 0, 0],
        "digests": [],
        "restarts": 0,
    }
    row = _FakeRow(
        id=pid,
        last_fetched_at=wm,
        poll_state={"retell": {"bootstrapped": True, "window": window}},
    )
    fake_table[pid] = row
    provider = _retell_provider(row)
    _freeze_now(monkeypatch, now)
    monkeypatch.setattr(
        op.ObservabilityService,
        "fetch_retell_page",
        lambda *a, **k: _page(_calls(1, "capped"), has_more=True, next_key="k2"),
    )

    with capture_logs() as cap:
        result = op._poll_retell_provider(provider)
    assert result is None
    assert fake_table[pid].poll_state["retell"]["window"]["restarts"] == 1
    # a mutation that passes a constant cause into every restart must not survive this
    assert {
        "event": "retell_window_restarted",
        "log_level": "warning",
        "provider_id": str(pid),
        "cause": "page_cap",
        "restarts": 1,
        "pages_stored": op.RETELL_MAX_PAGES_PER_WINDOW - 1,
    } in cap


def test_three_restarts_halve_window_and_set_hint(fake_table, monkeypatch):
    pid = uuid.uuid4()
    now = _dt(2026, 1, 1, 12, 0, 0)
    start = now - timedelta(hours=2)
    window = {
        "start": start.isoformat(),
        "end": now.isoformat(),
        "opened_at_hint": True,
        "narrowed": False,
        "key": "k1",
        "skip": None,
        "pages_stored": 1,
        "progress": 0,
        "page_digest": None,
        "page_counts": [0, 0, 0, 0, 0, 0],
        "digests": ["deadbeef"],
        "restarts": 2,
    }
    row = _FakeRow(
        id=pid,
        last_fetched_at=start,
        poll_state={"retell": {"bootstrapped": True, "window": window}},
    )
    fake_table[pid] = row
    provider = _retell_provider(row)
    _freeze_now(monkeypatch, now)
    monkeypatch.setattr(
        op.ObservabilityService,
        "fetch_retell_page",
        lambda *a, **k: _page([], has_more=True, next_key="k2"),
    )

    result = op._poll_retell_provider(provider)
    assert result is None
    new_window = fake_table[pid].poll_state["retell"]["window"]
    assert new_window["restarts"] == 0
    assert new_window["narrowed"] is True
    expected_width = (now - start) / 2
    assert fake_table[pid].poll_state["retell"]["window_hint_seconds"] == int(
        expected_width.total_seconds()
    )


def test_hint_caps_new_window_width(fake_table, monkeypatch):
    pid = uuid.uuid4()
    now = _dt(2026, 1, 1, 12, 0, 0)
    wm = now - timedelta(hours=10)  # much wider than any hint
    row = _FakeRow(
        id=pid,
        last_fetched_at=wm,
        poll_state={"retell": {"bootstrapped": True, "window_hint_seconds": 3600}},
    )
    fake_table[pid] = row
    provider = _retell_provider(row)
    _freeze_now(monkeypatch, now)
    seen = []

    def fake_fetch(prov, start, end, *, pagination_key=None, skip=None, **kwargs):
        seen.append((start, end))
        return _page(_calls(1), has_more=False)

    monkeypatch.setattr(op.ObservabilityService, "fetch_retell_page", fake_fetch)
    monkeypatch.setattr(
        op, "process_and_store_logs", lambda *a, **k: op.StoreOutcome(1, 0, 0)
    )

    op._poll_retell_provider(provider)
    ((start_seen, end_seen),) = seen
    assert start_seen == wm
    assert (end_seen - start_seen) == timedelta(seconds=3600)


def test_window_narrower_than_hint_does_not_count_toward_streak(
    fake_table, monkeypatch
):
    pid = uuid.uuid4()
    now = _dt(2026, 1, 1, 12, 0, 0)
    wm = now - timedelta(minutes=5)  # natural width well under the 6h hint
    row = _FakeRow(
        id=pid, last_fetched_at=wm, poll_state={"retell": {"bootstrapped": True}}
    )
    fake_table[pid] = row
    provider = _retell_provider(row)
    _freeze_now(monkeypatch, now)
    monkeypatch.setattr(
        op.ObservabilityService,
        "fetch_retell_page",
        lambda *a, **k: _page(_calls(1), has_more=False),
    )
    monkeypatch.setattr(
        op, "process_and_store_logs", lambda *a, **k: op.StoreOutcome(1, 0, 0)
    )

    op._poll_retell_provider(provider)
    assert "one_page_streak" not in fake_table[pid].poll_state["retell"]


def test_narrowed_window_one_page_result_does_not_grow_hint(fake_table, monkeypatch):
    pid = uuid.uuid4()
    now = _dt(2026, 1, 1, 12, 0, 0)
    start = now - timedelta(minutes=10)
    window = {
        "start": start.isoformat(),
        "end": now.isoformat(),
        "opened_at_hint": True,
        "narrowed": True,
        "key": None,
        "skip": None,
        "pages_stored": 0,
        "progress": 0,
        "page_digest": None,
        "page_counts": [0, 0, 0, 0, 0, 0],
        "digests": [],
        "restarts": 0,
    }
    row = _FakeRow(
        id=pid,
        last_fetched_at=start,
        poll_state={
            "retell": {
                "bootstrapped": True,
                "window": window,
                "window_hint_seconds": 600,
            }
        },
    )
    fake_table[pid] = row
    provider = _retell_provider(row)
    _freeze_now(monkeypatch, now)
    monkeypatch.setattr(
        op.ObservabilityService,
        "fetch_retell_page",
        lambda *a, **k: _page(_calls(1), has_more=False),
    )
    monkeypatch.setattr(
        op, "process_and_store_logs", lambda *a, **k: op.StoreOutcome(1, 0, 0)
    )

    op._poll_retell_provider(provider)
    retell_state = fake_table[pid].poll_state["retell"]
    assert "one_page_streak" not in retell_state
    assert retell_state["window_hint_seconds"] == 600  # unchanged


def test_three_one_page_windows_at_hint_double_it(fake_table, monkeypatch):
    pid = uuid.uuid4()
    now = _dt(2026, 1, 1, 12, 0, 0)
    hint_seconds = 3600
    row = _FakeRow(
        id=pid,
        poll_state={
            "retell": {"bootstrapped": True, "window_hint_seconds": hint_seconds}
        },
    )
    fake_table[pid] = row
    monkeypatch.setattr(
        op.ObservabilityService,
        "fetch_retell_page",
        lambda *a, **k: _page(_calls(1), has_more=False),
    )
    monkeypatch.setattr(
        op, "process_and_store_logs", lambda *a, **k: op.StoreOutcome(1, 0, 0)
    )

    for i in range(1, op.RETELL_WINDOW_GROW_AFTER + 1):
        current_now = now + (i - 1) * timedelta(seconds=hint_seconds)
        _freeze_now(monkeypatch, current_now)
        # Set the watermark so the frozen window is exactly hint-width after
        # the visibility lag is subtracted (opened_at_hint requires >= hint).
        fake_table[pid].last_fetched_at = (
            current_now - timedelta(seconds=hint_seconds) - op.RETELL_VISIBILITY_LAG
        )
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
        last_fetched_at=now
        - timedelta(seconds=hint_seconds)
        - op.RETELL_VISIBILITY_LAG,
        poll_state={
            "retell": {"bootstrapped": True, "window_hint_seconds": hint_seconds}
        },
    )
    fake_table[pid] = row
    provider = _retell_provider(row)
    _freeze_now(monkeypatch, now)
    monkeypatch.setattr(
        op.ObservabilityService,
        "fetch_retell_page",
        lambda *a, **k: _page(_calls(750), has_more=False),
    )
    monkeypatch.setattr(
        op, "process_and_store_logs", lambda *a, **k: op.StoreOutcome(750, 0, 0)
    )

    op._poll_retell_provider(provider)

    assert fake_table[pid].poll_state["retell"]["one_page_streak"] == 1


def test_hint_doubling_is_capped_at_max(fake_table, monkeypatch):
    # §7: "three consecutive one-page windows opened at the hint double it
    # (capped)" — start close enough to RETELL_WINDOW_HINT_MAX that a plain
    # doubling would overshoot it; the result must clamp, not exceed.
    pid = uuid.uuid4()
    now = _dt(2026, 1, 1, 12, 0, 0)
    hint_seconds = int(op.RETELL_WINDOW_HINT_MAX.total_seconds()) - 100
    row = _FakeRow(
        id=pid,
        poll_state={
            "retell": {"bootstrapped": True, "window_hint_seconds": hint_seconds}
        },
    )
    fake_table[pid] = row
    monkeypatch.setattr(
        op.ObservabilityService,
        "fetch_retell_page",
        lambda *a, **k: _page(_calls(1), has_more=False),
    )
    monkeypatch.setattr(
        op, "process_and_store_logs", lambda *a, **k: op.StoreOutcome(1, 0, 0)
    )

    for i in range(1, op.RETELL_WINDOW_GROW_AFTER + 1):
        current_now = now + (i - 1) * timedelta(seconds=hint_seconds)
        _freeze_now(monkeypatch, current_now)
        fake_table[pid].last_fetched_at = (
            current_now - timedelta(seconds=hint_seconds) - op.RETELL_VISIBILITY_LAG
        )
        provider = _retell_provider(fake_table[pid])
        op._poll_retell_provider(provider)

    retell_state = fake_table[pid].poll_state["retell"]
    assert retell_state["window_hint_seconds"] == int(
        op.RETELL_WINDOW_HINT_MAX.total_seconds()
    )


def test_multi_page_window_completed_by_cursor_drops_hint_entirely(
    fake_table, monkeypatch
):
    pid = uuid.uuid4()
    now = _dt(2026, 1, 1, 12, 0, 0)
    start = now - timedelta(hours=1)
    window = {
        "start": start.isoformat(),
        "end": now.isoformat(),
        "opened_at_hint": False,
        "narrowed": False,
        "key": "k1",
        "skip": None,
        "pages_stored": 1,
        "progress": 0,
        "page_digest": None,
        "page_counts": [0, 0, 0, 0, 0, 0],
        "digests": ["deadbeef"],
        "restarts": 0,
    }
    row = _FakeRow(
        id=pid,
        last_fetched_at=start,
        poll_state={
            "retell": {
                "bootstrapped": True,
                "window": window,
                "window_hint_seconds": 900,
            }
        },
    )
    fake_table[pid] = row
    provider = _retell_provider(row)
    _freeze_now(monkeypatch, now)
    monkeypatch.setattr(
        op.ObservabilityService,
        "fetch_retell_page",
        lambda *a, **k: _page(_calls(1, "final"), has_more=False),
    )
    monkeypatch.setattr(
        op, "process_and_store_logs", lambda *a, **k: op.StoreOutcome(1, 0, 0)
    )

    op._poll_retell_provider(provider)
    retell_state = fake_table[pid].poll_state["retell"]
    assert "window_hint_seconds" not in retell_state
    assert "one_page_streak" not in retell_state


def test_poll_behind_fires_on_completed_window_older_than_warn_threshold(
    fake_table, monkeypatch
):
    pid = uuid.uuid4()
    now = _dt(2026, 1, 1, 12, 0, 0)
    start = now - op.RETELL_BEHIND_WARN - timedelta(minutes=30) - timedelta(minutes=1)
    end = now - op.RETELL_BEHIND_WARN - timedelta(minutes=30)
    window = {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "opened_at_hint": False,
        "narrowed": False,
        "key": None,
        "skip": None,
        "pages_stored": 0,
        "progress": 0,
        "page_digest": None,
        "page_counts": [0, 0, 0, 0, 0, 0],
        "digests": [],
        "restarts": 0,
    }
    row = _FakeRow(
        id=pid,
        last_fetched_at=start,
        poll_state={"retell": {"bootstrapped": True, "window": window}},
    )
    fake_table[pid] = row
    provider = _retell_provider(row)
    _freeze_now(monkeypatch, now)
    monkeypatch.setattr(
        op.ObservabilityService,
        "fetch_retell_page",
        lambda *a, **k: _page(_calls(1), has_more=False),
    )
    monkeypatch.setattr(
        op, "process_and_store_logs", lambda *a, **k: op.StoreOutcome(1, 0, 0)
    )
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
    row = _FakeRow(
        id=pid,
        last_fetched_at=wm,
        poll_state={
            "retell": {"bootstrapped": True, "window": {"start": "not-a-window"}}
        },
    )
    fake_table[pid] = row
    provider = _retell_provider(row)
    _freeze_now(monkeypatch, now)
    monkeypatch.setattr(
        op.ObservabilityService,
        "fetch_retell_page",
        lambda *a, **k: _page(_calls(1), has_more=False),
    )
    monkeypatch.setattr(
        op, "process_and_store_logs", lambda *a, **k: op.StoreOutcome(1, 0, 0)
    )

    result = op._poll_retell_provider(provider)  # must not raise
    assert result is not None


def test_poisoned_watermark_uses_lookback_and_is_repaired(fake_table, monkeypatch):
    pid = uuid.uuid4()
    now = _dt(2026, 1, 1, 12, 0, 0)
    future_wm = now + timedelta(hours=2)
    row = _FakeRow(
        id=pid, last_fetched_at=future_wm, poll_state={"retell": {"bootstrapped": True}}
    )
    fake_table[pid] = row
    provider = _retell_provider(row)
    _freeze_now(monkeypatch, now)
    monkeypatch.setattr(
        op.ObservabilityService,
        "fetch_retell_page",
        lambda *a, **k: _page(_calls(1), has_more=False),
    )
    monkeypatch.setattr(
        op, "process_and_store_logs", lambda *a, **k: op.StoreOutcome(1, 0, 0)
    )

    op._poll_retell_provider(provider)
    assert (
        fake_table[pid].last_fetched_at != future_wm
    )  # repaired away from the poisoned value


def test_missing_watermark_after_bootstrap_uses_lookback(fake_table, monkeypatch):
    pid = uuid.uuid4()
    now = _dt(2026, 1, 1, 12, 0, 0)
    row = _FakeRow(
        id=pid, last_fetched_at=None, poll_state={"retell": {"bootstrapped": True}}
    )
    fake_table[pid] = row
    provider = _retell_provider(row)
    _freeze_now(monkeypatch, now)
    seen = []
    monkeypatch.setattr(
        op.ObservabilityService,
        "fetch_retell_page",
        lambda prov, start, end, **k: (
            seen.append((start, end)) or _page(_calls(1), has_more=False)
        ),
    )
    monkeypatch.setattr(
        op, "process_and_store_logs", lambda *a, **k: op.StoreOutcome(1, 0, 0)
    )

    op._poll_retell_provider(provider)
    ((start, _end),) = seen
    assert start == (
        now - op.RETELL_VISIBILITY_LAG - op.RETELL_FUTURE_WATERMARK_LOOKBACK
    )


def test_provider_poll_state_write_skipped_when_zero_rows_updated_via_poll(
    fake_table, monkeypatch
):
    # Unlike the direct-helper test above, this exercises the event through a
    # real call site: the provider row vanishes from the table between the
    # in-memory bootstrap and `_poll_retell_provider`'s final state write, so
    # the update matches 0 rows and the run must end with no marker set.
    pid = uuid.uuid4()  # deliberately never inserted into fake_table
    provider = SimpleNamespace(
        id=pid,
        provider=ProviderChoices.RETELL,
        poll_state={},
        last_fetched_at=None,
        project=SimpleNamespace(id="project-1", organization_id="org-1"),
    )
    monkeypatch.setattr(
        op.ObservabilityService, "fetch_retell_page", lambda *a, **k: _page(_calls(1))
    )
    monkeypatch.setattr(
        op, "process_and_store_logs", lambda *a, **k: op.StoreOutcome(1, 0, 0)
    )

    with capture_logs() as cap:
        result = op._poll_retell_provider(provider)

    assert result is None
    assert {
        "event": "provider_poll_state_write_skipped",
        "log_level": "error",
        "provider_id": str(pid),
    } in cap
    assert pid not in fake_table


# --------------------------------------------------------------------------
# Offset fallback
# --------------------------------------------------------------------------


def test_window_at_min_width_falls_back_to_offset_mode(fake_table):
    pid = uuid.uuid4()
    now = _dt(2026, 1, 1, 12, 0, 0)
    start = now - timedelta(
        seconds=1
    )  # already at RETELL_MIN_WINDOW: halving is no longer an option
    window = {
        "start": start.isoformat(),
        "end": now.isoformat(),
        "opened_at_hint": False,
        "narrowed": True,
        "key": "k1",
        "skip": None,
        "pages_stored": 0,
        "progress": 0,
        "page_digest": None,
        "page_counts": [0, 0, 0, 0, 0, 0],
        "digests": [],
        "restarts": 2,
    }  # about to hit the restart cap
    row = _FakeRow(
        id=pid, poll_state={"retell": {"bootstrapped": True, "window": window}}
    )
    fake_table[pid] = row
    state = op._read_retell_state(_retell_provider(row))
    state["window"] = window

    op._restart_window(pid, state, cause="page_cap")

    new_window = fake_table[pid].poll_state["retell"]["window"]
    assert new_window["restarts"] == 0  # reset on entering offset mode
    assert (
        new_window["skip"] == 0
    )  # offset mode entered: halving was not possible at the 1s floor


def test_offset_mode_skip_increments_by_page_limit(fake_table, monkeypatch):
    pid = uuid.uuid4()
    now = _dt(2026, 1, 1, 12, 0, 0)
    start = now - timedelta(seconds=1)
    window = {
        "start": start.isoformat(),
        "end": now.isoformat(),
        "opened_at_hint": False,
        "narrowed": True,
        "key": None,
        "skip": 0,
        "pages_stored": 0,
        "progress": 0,
        "page_digest": None,
        "page_counts": [0, 0, 0, 0, 0, 0],
        "digests": [],
        "restarts": 0,
    }
    row = _FakeRow(
        id=pid,
        last_fetched_at=start,
        poll_state={"retell": {"bootstrapped": True, "window": window}},
    )
    fake_table[pid] = row
    provider = _retell_provider(row)
    # F2: identical content on every call — see _freeze_now_then_over_budget.
    _freeze_now_then_over_budget(monkeypatch, now, calls_per_page=1000)
    seen_skip = []

    def fake_fetch(prov, s, e, *, pagination_key=None, skip=None, **kwargs):
        seen_skip.append(skip)
        return _page(_calls(1000), has_more=True)

    monkeypatch.setattr(op.ObservabilityService, "fetch_retell_page", fake_fetch)
    monkeypatch.setattr(op, "process_and_store_logs", _store_each())

    op._poll_retell_provider(provider)
    assert seen_skip == [0]
    assert (
        fake_table[pid].poll_state["retell"]["window"]["skip"]
        == op.RETELL_LIST_PAGE_LIMIT
    )


def test_offset_failure_stalls_loudly(fake_table, monkeypatch):
    pid = uuid.uuid4()
    now = _dt(2026, 1, 1, 12, 0, 0)
    start = now - timedelta(seconds=1)
    # Already in offset mode (skip=0) and about to hit the restart cap again:
    # per D15, this is the last-resort path with nowhere further to fall back.
    window = {
        "start": start.isoformat(),
        "end": now.isoformat(),
        "opened_at_hint": False,
        "narrowed": True,
        "key": None,
        "skip": 0,
        "pages_stored": 0,
        "progress": 0,
        "page_digest": None,
        "page_counts": [0, 0, 0, 0, 0, 0],
        "digests": [],
        "restarts": 2,
    }
    row = _FakeRow(
        id=pid, poll_state={"retell": {"bootstrapped": True, "window": window}}
    )
    fake_table[pid] = row
    state = op._read_retell_state(_retell_provider(row))
    state["window"] = window
    with capture_logs() as cap:
        op._restart_window(pid, state, cause="page_cap")

    new_window = fake_table[pid].poll_state["retell"]["window"]
    assert new_window["skip"] == 0
    assert new_window["restarts"] == 0  # reset, but the run never advances
    assert "retell_window_stuck" in [
        e["event"] for e in cap if e["log_level"] == "error"
    ]


# --------------------------------------------------------------------------
# Partial / total failure handling for windowed runs
# --------------------------------------------------------------------------


def test_windowed_partial_failure_twice_then_abandoned_third(fake_table, monkeypatch):
    pid = uuid.uuid4()
    now = _dt(2026, 1, 1, 12, 0, 0)
    wm = now - timedelta(minutes=10)
    row = _FakeRow(
        id=pid, last_fetched_at=wm, poll_state={"retell": {"bootstrapped": True}}
    )
    fake_table[pid] = row
    _freeze_now(monkeypatch, now)
    monkeypatch.setattr(
        op.ObservabilityService,
        "fetch_retell_page",
        lambda *a, **k: _page(_calls(2), has_more=False),
    )
    # Two calls on the page: the first stores, the second fails to export —
    # a `partial` verdict whose retry (v1.15 B5) re-stores the page from 0,
    # so the same alternation repeats run after run.
    per_call = itertools.cycle([op.StoreOutcome(1, 0, 0), op.StoreOutcome(0, 0, 1)])
    monkeypatch.setattr(op, "process_and_store_logs", lambda *a, **k: next(per_call))

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
    assert {
        "event": "retell_page_abandoned",
        "log_level": "error",
        "provider_id": str(pid),
        "abandoned": 1,
        "failed_runs": 3,
    } in cap


def test_windowed_total_failure_backs_off_doubles_and_caps_then_clears_on_success(
    fake_table, monkeypatch
):
    pid = uuid.uuid4()
    now = _dt(2026, 1, 1, 12, 0, 0)
    wm = now - timedelta(minutes=10)
    row = _FakeRow(
        id=pid, last_fetched_at=wm, poll_state={"retell": {"bootstrapped": True}}
    )
    fake_table[pid] = row
    _freeze_now(monkeypatch, now)
    monkeypatch.setattr(
        op.ObservabilityService,
        "fetch_retell_page",
        lambda *a, **k: _page(_calls(1), has_more=False),
    )
    monkeypatch.setattr(
        op, "process_and_store_logs", lambda *a, **k: op.StoreOutcome(0, 0, 1)
    )

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
    monkeypatch.setattr(
        op, "process_and_store_logs", lambda *a, **k: op.StoreOutcome(1, 0, 0)
    )
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

    def fake_fetch(prov, start, end, *, pagination_key=None, skip=None, **kwargs):
        calls_made.append(pagination_key)
        return _page(_calls(1), has_more=True, next_key=f"k{len(calls_made)}")

    monkeypatch.setattr(op.ObservabilityService, "fetch_retell_page", fake_fetch)
    monkeypatch.setattr(
        op, "process_and_store_logs", lambda *a, **k: op.StoreOutcome(1, 0, 0)
    )

    outcome = op._manual_retell_run(
        provider, start_time=_dt(2026, 1, 1), end_time=_dt(2026, 1, 2)
    )
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
        op.fetch_observability_logs._original_func(
            start_time="2026-01-01T00:00:00+00:00"
        )
    assert cap and cap[0]["event"] == "provider_manual_run_rejected"
    assert cap[0]["log_level"] == "error"
    assert cap[0]["reason"] == "provider_id_required"


# --------------------------------------------------------------------------
# Scheduled dispatch — the shape a Temporal firing actually uses:
# `fetch_observability_logs()` with no arguments at all (or just `provider_id`).
# --------------------------------------------------------------------------


def test_scheduled_no_args_fans_out_over_enabled_providers_only(
    fake_table, monkeypatch
):
    # Two enabled, one disabled: proves `.filter(enabled=True)` is honoured,
    # not just that *some* provider gets dispatched.
    enabled_1, enabled_2, disabled = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    fake_table[enabled_1] = _FakeRow(id=enabled_1, enabled=True)
    fake_table[enabled_2] = _FakeRow(id=enabled_2, enabled=True)
    fake_table[disabled] = _FakeRow(id=disabled, enabled=False)

    dispatched = []

    def fake_dispatch(provider_id, *, scheduled, start_time, end_time, deadline=None):
        dispatched.append((provider_id, scheduled, start_time, end_time))
        return op.StoreOutcome(0, 0, 0)

    monkeypatch.setattr(op, "fetch_logs_for_provider", fake_dispatch)

    op.fetch_observability_logs._original_func()  # exactly how a Temporal firing calls it: no args

    assert sorted(pid for pid, *_ in dispatched) == sorted([enabled_1, enabled_2])
    for _pid, scheduled, start_time, end_time in dispatched:
        assert scheduled is True
        assert start_time is None
        assert end_time is None


def test_fan_out_shuffles_dispatch_order_for_fairness(fake_table, monkeypatch):
    # F1(d)/N1: `random.shuffle` over the materialised provider-id list means
    # a starved tail rotates between firings instead of the same providers
    # always dispatching last (a plain `.iterator()` over an unordered
    # queryset can otherwise be stable enough to starve the same tail every
    # time). Patch shuffle to a deterministic reverse and assert dispatch
    # follows the shuffled order, not table-insertion order.
    p1, p2, p3 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    fake_table[p1] = _FakeRow(id=p1, enabled=True)
    fake_table[p2] = _FakeRow(id=p2, enabled=True)
    fake_table[p3] = _FakeRow(id=p3, enabled=True)

    dispatched = []

    def fake_dispatch(provider_id, *, scheduled, start_time, end_time, deadline=None):
        dispatched.append(provider_id)
        return op.StoreOutcome(0, 0, 0)

    monkeypatch.setattr(op, "fetch_logs_for_provider", fake_dispatch)
    monkeypatch.setattr(op.random, "shuffle", lambda lst: lst.reverse())

    op.fetch_observability_logs._original_func()

    assert dispatched == [p3, p2, p1]  # reverse of insertion order, proving shuffle ran


def test_fan_out_skips_remaining_providers_after_deadline_and_logs_once(
    fake_table, monkeypatch
):
    # F1(c)/N1: once the activity-level deadline has passed, providers not
    # yet dispatched are skipped instead of each burning a fresh run budget
    # against an already-blown 3h activity time_limit, and the skip is
    # logged ONCE with a count field — not once per skipped provider.
    p1, p2, p3 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    fake_table[p1] = _FakeRow(id=p1, enabled=True)
    fake_table[p2] = _FakeRow(id=p2, enabled=True)
    fake_table[p3] = _FakeRow(id=p3, enabled=True)
    monkeypatch.setattr(
        op.random, "shuffle", lambda lst: None
    )  # keep insertion order so the assertion below is readable

    now = _dt(2026, 1, 1, 12, 0, 0)
    clock = {"t": now}
    monkeypatch.setattr(op.timezone, "now", lambda: clock["t"])

    dispatched = []
    deadlines = []

    def fake_dispatch(provider_id, *, scheduled, start_time, end_time, deadline=None):
        dispatched.append(provider_id)
        deadlines.append(deadline)
        # Simulate the first provider's run alone consuming the whole
        # activity budget — exactly the scenario F1 protects against.
        clock["t"] = clock["t"] + op.RETELL_ACTIVITY_BUDGET + timedelta(minutes=1)
        return op.StoreOutcome(0, 0, 0)

    monkeypatch.setattr(op, "fetch_logs_for_provider", fake_dispatch)

    with capture_logs() as cap:
        op.fetch_observability_logs._original_func()

    assert dispatched == [p1]  # only the first provider ran before the deadline passed
    # R3 M1: the deadline actually travels down the wire — dropping the
    # `deadline=deadline` kwarg in the fan-out would silently revert to
    # per-provider budgets with every other assertion here still green.
    assert deadlines == [now + op.RETELL_ACTIVITY_BUDGET]
    budget_events = [e for e in cap if e["event"] == "retell_activity_budget_exhausted"]
    assert len(budget_events) == 1  # once, not once per skipped provider
    assert budget_events[0]["log_level"] == "warning"
    assert budget_events[0]["skipped_providers"] == 2
    assert set(budget_events[0]) == {"event", "log_level", "skipped_providers"}


def test_scheduled_with_only_provider_id_dispatches_exactly_that_provider(monkeypatch):
    # `provider_id` given + no bounds is still scheduled-shaped (R11-3): it
    # must resolve to a single-element dispatch list, never the enabled=True
    # fan-out query — so this deliberately does NOT patch `fake_table`, and
    # would error if the code took the `.filter(enabled=True)` branch instead.
    target = "provider-123"
    dispatched = []
    monkeypatch.setattr(
        op,
        "fetch_logs_for_provider",
        lambda provider_id, *, scheduled, start_time, end_time, deadline=None: (
            dispatched.append((provider_id, scheduled, start_time, end_time))
            or op.StoreOutcome(0, 0, 0)
        ),
    )

    op.fetch_observability_logs._original_func(provider_id=target)

    assert dispatched == [(target, True, None, None)]


def test_scheduled_computes_scheduled_before_any_end_time_defaulting(monkeypatch):
    # Guards R11-3 / contract §4 L163 ("FIRST statement, before any parsing or
    # defaulting"): if a `end_dt = end_time or timezone.now()`-style default
    # were reintroduced above the `scheduled` computation, this call would
    # observe a concrete `end_time` instead of `None` and/or `scheduled=False`.
    seen = {}

    def fake_dispatch(provider_id, *, scheduled, start_time, end_time, deadline=None):
        seen["scheduled"] = scheduled
        seen["start_time"] = start_time
        seen["end_time"] = end_time
        return op.StoreOutcome(0, 0, 0)

    monkeypatch.setattr(op, "fetch_logs_for_provider", fake_dispatch)

    op.fetch_observability_logs._original_func(
        provider_id="p1"
    )  # no bounds: must stay scheduled-shaped

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

    def boom(provider, **kwargs):
        raise _http_error(401)

    monkeypatch.setattr(op, "_poll_retell_provider", boom)

    with capture_logs() as cap:
        result = op.fetch_logs_for_provider(
            pid, scheduled=True, start_time=None, end_time=None
        )

    assert result is None
    # pins impl's first `if provider.provider == ProviderChoices.RETELL and
    # status in (401, 403): logger.error("retell_auth_failed", ...)` branch —
    # exact field set, and no `authentication_failed_for_provider` sneaking in.
    assert cap == [
        {
            "event": "retell_auth_failed",
            "log_level": "error",
            "provider_id": str(pid),
            "status_code": 401,
        }
    ]


def test_non_retell_401_logs_authentication_failed_for_provider_not_retell_auth_failed(
    fake_table, monkeypatch
):
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
        result = op.fetch_logs_for_provider(
            pid, scheduled=True, start_time=None, end_time=None
        )

    assert result is None
    assert cap == [
        {
            "event": "authentication_failed_for_provider",
            "log_level": "error",
            "provider_type": ProviderChoices.BLAND,
            "status_code": 401,
        }
    ]


def test_non_retell_403_logs_authentication_failed_for_provider(
    fake_table, monkeypatch
):
    pid = uuid.uuid4()
    row = _FakeRow(id=pid, poll_state={}, provider=ProviderChoices.VAPI)
    fake_table[pid] = row

    def boom(provider, **kwargs):
        raise _http_error(403)

    monkeypatch.setattr(op, "_poll_other_provider", boom)

    with capture_logs() as cap:
        result = op.fetch_logs_for_provider(
            pid, scheduled=True, start_time=None, end_time=None
        )

    assert result is None
    assert cap == [
        {
            "event": "authentication_failed_for_provider",
            "log_level": "error",
            "provider_type": ProviderChoices.VAPI,
            "status_code": 403,
        }
    ]


def test_non_retell_non_auth_http_error_logs_provider_log_fetch_failed(
    fake_table, monkeypatch
):
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
        result = op.fetch_logs_for_provider(
            pid, scheduled=True, start_time=None, end_time=None
        )

    assert result is None
    assert cap == [
        {
            "event": "provider_log_fetch_failed",
            "log_level": "error",
            "provider_id": str(pid),
            "provider_type": ProviderChoices.TWILIO,
            "status_code": 500,
            "error_type": "HTTPError",
        }
    ]


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
        result = op.fetch_logs_for_provider(
            pid, scheduled=True, start_time=None, end_time=None
        )

    assert result is None
    assert cap == [
        {
            "event": "provider_log_fetch_failed",
            "log_level": "error",
            "provider_id": str(pid),
            "provider_type": ProviderChoices.ELEVEN_LABS,
            "status_code": None,
            "error_type": "HTTPError",
        }
    ]


def test_retell_configuration_error_logged(fake_table, monkeypatch):
    pid = uuid.uuid4()
    row = _FakeRow(id=pid, poll_state={})
    fake_table[pid] = row

    def boom(provider, **kwargs):
        raise RetellConfigurationError(
            "Retell API key is not configured for this agent"
        )

    monkeypatch.setattr(op, "_poll_retell_provider", boom)

    with capture_logs() as cap:
        result = op.fetch_logs_for_provider(
            pid, scheduled=True, start_time=None, end_time=None
        )
    assert result is None
    assert cap[-1]["event"] == "retell_configuration_error"
    assert cap[-1]["log_level"] == "error"
    assert "message" not in cap[-1]
    assert cap[-1]["error_type"] == "RetellConfigurationError"


def test_manual_run_cursor_rejected_propagates_to_generic_handler(
    fake_table, monkeypatch
):
    # A manual run does not handle a rejected cursor itself; the generic handler must see it.
    pid = uuid.uuid4()
    row = _FakeRow(id=pid, poll_state={}, provider=ProviderChoices.RETELL)
    fake_table[pid] = row

    def raise_rejected(*a, **k):
        raise RetellCursorRejected(cause="missing_key")

    monkeypatch.setattr(op.ObservabilityService, "fetch_retell_page", raise_rejected)

    with capture_logs() as cap:
        result = op.fetch_logs_for_provider(
            pid, scheduled=False, start_time=_dt(2026, 1, 1), end_time=None
        )

    assert result is None
    assert cap == [
        {
            "event": "provider_log_fetch_failed",
            "log_level": "error",
            "provider_id": str(pid),
            "provider_type": ProviderChoices.RETELL,
            "error_type": "RetellCursorRejected",
        }
    ]


def test_fetch_logs_for_provider_not_found_scheduled_vs_manual(fake_table):
    missing_id = uuid.uuid4()
    assert (
        op.fetch_logs_for_provider(
            missing_id, scheduled=True, start_time=None, end_time=None
        )
        is None
    )
    assert (
        op.fetch_logs_for_provider(
            missing_id, scheduled=False, start_time=_dt(2026, 1, 1), end_time=None
        )
        is None
    )


# --------------------------------------------------------------------------
# ISO parsing
# --------------------------------------------------------------------------


def test_naive_iso_input_is_made_aware(fake_table, monkeypatch):
    seen = {}

    def fake_dispatch(provider_id, *, scheduled, start_time, end_time, deadline=None):
        seen["start_time"] = start_time
        return op.StoreOutcome(0, 0, 0)

    monkeypatch.setattr(op, "fetch_logs_for_provider", fake_dispatch)
    op.fetch_observability_logs._original_func(
        start_time="2026-01-01T00:00:00", provider_id="p1"
    )
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
    provider = SimpleNamespace(
        id=pid, provider=ProviderChoices.VAPI, last_fetched_at=row.last_fetched_at
    )
    monkeypatch.setattr(op.timezone, "now", lambda: now)
    monkeypatch.setattr(
        op.ObservabilityService, "get_call_logs", lambda **k: [{"id": "c1"}]
    )
    monkeypatch.setattr(
        op, "process_and_store_logs", lambda *a, **k: op.StoreOutcome(1, 0, 0)
    )

    outcome = op._poll_other_provider(provider, start_time=None, end_time=None)
    assert outcome == op.StoreOutcome(1, 0, 0)
    assert fake_table[pid].last_fetched_at == now


def test_other_provider_store_exception_does_not_advance_and_propagates(
    fake_table, monkeypatch
):
    pid = uuid.uuid4()
    now = _dt(2026, 1, 1, 12, 0, 0)
    wm = now - timedelta(hours=1)
    row = _FakeRow(id=pid, last_fetched_at=wm)
    fake_table[pid] = row
    provider = SimpleNamespace(
        id=pid, provider=ProviderChoices.VAPI, last_fetched_at=wm
    )
    monkeypatch.setattr(op.timezone, "now", lambda: now)
    monkeypatch.setattr(
        op.ObservabilityService, "get_call_logs", lambda **k: [{"id": "c1"}]
    )

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
    assert counts_events == [
        {
            "event": "provider_log_processing_failed",
            "log_level": "error",
            "provider_type": ProviderChoices.VAPI,
            "logs_count": 1,
            "error_type": "ValueError",
        }
    ]


def test_other_provider_future_watermark_repaired_and_clamped(fake_table, monkeypatch):
    pid = uuid.uuid4()
    now = _dt(2026, 1, 1, 12, 0, 0)
    future_wm = now + timedelta(hours=2)
    row = _FakeRow(id=pid, last_fetched_at=future_wm)
    fake_table[pid] = row
    provider = SimpleNamespace(
        id=pid, provider=ProviderChoices.BLAND, last_fetched_at=future_wm
    )
    monkeypatch.setattr(op.timezone, "now", lambda: now)
    seen = {}
    monkeypatch.setattr(
        op.ObservabilityService,
        "get_call_logs",
        lambda **k: seen.setdefault("kw", k) and [],
    )
    monkeypatch.setattr(
        op, "process_and_store_logs", lambda *a, **k: op.StoreOutcome(0, 0, 0)
    )

    outcome = op._poll_other_provider(
        provider, start_time=None, end_time=now + timedelta(hours=5)
    )
    assert outcome == op.StoreOutcome(0, 0, 0)
    assert seen["kw"]["end_time"] == now  # clamped to now, never beyond
    assert seen["kw"]["start_time"] == now - op.RETELL_FUTURE_WATERMARK_LOOKBACK


# --------------------------------------------------------------------------
# StoreOutcome / process_and_store_logs early returns
# --------------------------------------------------------------------------


def test_process_and_store_logs_unknown_provider_returns_empty_outcome():
    provider = SimpleNamespace(
        provider=ProviderChoices.LIVEKIT,
        project=SimpleNamespace(id="p1", organization_id="o1"),
    )
    assert op.process_and_store_logs([], provider) == op.StoreOutcome(0, 0, 0)


def test_process_and_store_logs_non_list_returns_empty_outcome():
    # TWILIO (not VAPI) so the VAPI api-key resolution branch — which would
    # otherwise touch the DB-backed Selector — never runs.
    provider = SimpleNamespace(
        provider=ProviderChoices.TWILIO,
        project=SimpleNamespace(id="p1", organization_id="o1"),
    )
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
    monkeypatch.setattr(
        op, "_export_provider_call_to_collector", lambda *a: 0
    )  # never acked

    outcome = op.process_and_store_logs([{"bad": True}, {"id": "c1"}], provider)
    assert outcome == op.StoreOutcome(0, 1, 1)


def test_poll_state_not_a_dict_normalizes_to_empty():
    provider = SimpleNamespace(poll_state=["not", "a", "dict"])
    assert op._read_retell_state(provider) == {}
