"""Live ClickHouse 25.3 proofs for trajectory_items GC, audit and erasure.

Each test owns a private database with the spans table (002, 024) and 040
applied, or no 040 for the pre-040 case. Redis tests use a private database
index on the test Redis and clear it before and after.
"""

from __future__ import annotations

import fnmatch
import io
import json
import os
import random
import re
import uuid
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from django.core.management import CommandError, call_command
from django.test import override_settings

from conftest import (
    _ch_test_apply_v2_schema,
    _ch_test_native_client,
    _ch_test_owned_database,
)
from tracer.services.clickhouse.v2 import trajectory_lifecycle as lifecycle
from tracer.services.trajectory_seen_contract import (
    CONTRACT_KEY,
    EPOCH_KEY,
    GC_LATCH_KEY,
    GC_MARGIN,
    IN_FLIGHT_MARGIN,
    LEGACY_QUIET_PERIOD,
    MAX_REUSE_AGE_CAP,
    QUIET_MARK_TTL,
    QUIET_SINCE_KEY,
    SEEN_KEY_PREFIX,
    SEEN_V2_KEY_PREFIX,
    clear_project_seen_keys,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
SCHEMA_FILES = [
    "002_spans_v2.sql",
    "024_spans_created_at_index.sql",
    "040_trajectory_items.sql",
]
PRE040_FILES = ["002_spans_v2.sql", "024_spans_created_at_index.sql"]
HOUR_US = 3_600_000_000
DAY_US = 24 * HOUR_US
MARGIN_US = int(GC_MARGIN.total_seconds() * 1_000_000)
QUIET_US = int(LEGACY_QUIET_PERIOD.total_seconds() * 1_000_000)
# A quiet-period mark old enough for GC to delete (the epoch).
MATURE_QUIET_MARK = "0"

GC_ON = override_settings(FI_TRAJECTORY_GC_DELETES_ENABLED=True)


class Contract:
    """Stands in for the collectors' Redis (contract key, latch, seen keys).

    By default the GC quiet period has run its course (``quiet=True``); the
    quiet-period tests start without the mark.
    """

    def __init__(self, present: bool = True, keys=(), *, quiet: bool = True):
        self.values: dict[str, str] = {CONTRACT_KEY: "1"} if present else {}
        if present and quiet:
            self.values[QUIET_SINCE_KEY] = MATURE_QUIET_MARK
        self.values.update(dict.fromkeys(keys, "h"))
        self.ttls: dict[str, int] = {}

    def exists(self, *keys):
        return sum(key in self.values for key in keys)

    def get(self, key):
        return self.values.get(key)

    def set(self, key, value, nx=False, **kwargs):
        if nx and key in self.values:
            return None
        self.values[key] = value
        return True

    def expire(self, key, seconds):
        if key not in self.values:
            return False
        self.ttls[key] = int(seconds)
        return True

    def delete(self, *keys):
        return sum(self.values.pop(key, None) is not None for key in keys)

    def scan_iter(self, match="*", count=None):
        return iter([k for k in list(self.values) if fnmatch.fnmatchcase(k, match)])

    def close(self):
        pass


class Store:
    def __init__(self, client, database):
        self.client = client
        self.database = database
        self.statements: list[str] = []

    def execute(self, sql, params=None):
        self.statements.append(sql)
        return self.client.execute(sql, params or {})

    def deletes(self) -> list[str]:
        return [s for s in self.statements if s.lstrip().startswith("DELETE")]

    def masks(self) -> list[str]:
        return [s for s in self.statements if "APPLY DELETED MASK" in s]

    def now_us(self) -> int:
        return int(
            self.client.execute("SELECT toUnixTimestamp64Micro(now64(6, 'UTC'))")[0][0]
        )

    def bucket(self, project_id: str) -> int:
        return int(
            self.client.execute(
                "SELECT modulo(cityHash64(toUUID(%(p)s)), 32)", {"p": project_id}
            )[0][0]
        )

    def project_in_bucket(self, bucket: int, *, exclude=()) -> str:
        while True:
            pid = str(uuid.uuid4())
            if pid not in exclude and self.bucket(pid) == bucket:
                return pid

    def add_items(self, rows):
        """rows: (project, scope, hash, persisted_us[, content])."""
        if not rows:
            return
        values = [
            (p, s, h, (r[0] if r else f"content-{h}"), int(t))
            for p, s, h, t, *r in rows
        ]
        # optimize_on_insert=0 keeps several copies of one key in one insert
        # block; the default would collapse them before GC ever sees them.
        self.client.execute(
            "INSERT INTO trajectory_items "
            "(project_id, scope_id, item_hash, content, persisted_at) "
            "SELECT toUUID(r.1), r.2, r.3, r.4, "
            "fromUnixTimestamp64Micro(toInt64(r.5), 'UTC') "
            "FROM (SELECT arrayJoin(%(rows)s) AS r)",
            {"rows": values},
            settings={"optimize_on_insert": 0},
        )

    def add_spans(self, rows):
        """rows: dicts with project, created_us, scope, inputs, outputs, tools."""
        if not rows:
            return
        values = [
            (
                r["project"],
                r.get("id") or uuid.uuid4().hex,
                int(r["created_us"]),
                r.get("scope", ""),
                ",".join(r.get("inputs", ())),
                ",".join(r.get("outputs", ())),
                ",".join(r.get("tools", ())),
                r.get("encoding", "items_v1"),
                r.get("envelope", ""),
            )
            for r in rows
        ]
        self.client.execute(
            "INSERT INTO spans (project_id, id, trace_id, start_time, created_at, "
            "item_scope_id, input_item_hashes, output_item_hashes, tool_def_hashes, "
            "payload_encoding, payload_envelope) "
            "SELECT toUUID(r.1), r.2, r.2, "
            "fromUnixTimestamp64Micro(toInt64(r.3), 'UTC'), "
            "fromUnixTimestamp64Micro(toInt64(r.3), 'UTC'), r.4, "
            "arrayFilter(x -> x != '', splitByChar(',', r.5)), "
            "arrayFilter(x -> x != '', splitByChar(',', r.6)), "
            "arrayFilter(x -> x != '', splitByChar(',', r.7)), r.8, r.9 "
            "FROM (SELECT arrayJoin(%(rows)s) AS r)",
            {"rows": values},
        )

    def items(self, project_id=None) -> set[tuple[str, str, str, int]]:
        where = "WHERE project_id = toUUID(%(p)s)" if project_id else ""
        return {
            (str(p), str(s), str(h).rstrip("\x00"), int(t))
            for p, s, h, t in self.client.execute(
                "SELECT toString(project_id), scope_id, toString(item_hash), "
                f"toUnixTimestamp64Micro(persisted_at) FROM trajectory_items {where}",
                {"p": project_id} if project_id else {},
            )
        }

    def masked_partitions(self) -> set[str]:
        return {
            str(row[0])
            for row in self.client.execute(
                "SELECT DISTINCT partition_id FROM system.parts "
                "WHERE database = %(db)s AND table = 'trajectory_items' "
                "AND active AND has_lightweight_delete",
                {"db": self.database},
            )
        }


def _h() -> str:
    return uuid.uuid4().hex


@contextmanager
def _store(files=SCHEMA_FILES):
    with _ch_test_owned_database("test_traj_lc_") as database:
        _ch_test_apply_v2_schema(database, files=files)
        with _ch_test_native_client(database=database) as client:
            yield Store(client, database)


@pytest.fixture
def store():
    with _store() as value:
        yield value


def _gc(store, *, present=True, redis_client=None, **kwargs):
    return lifecycle.trajectory_items_gc(
        execute=store.execute,
        redis_client=redis_client if redis_client is not None else Contract(present),
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Operator switch (critique: deletes need an explicit switch, default off)
# ---------------------------------------------------------------------------


def test_gc_does_not_delete_without_gc_deletes_key():
    """Switch off (the default): no ClickHouse or Redis call at all."""

    def refuse(*args, **kwargs):
        raise AssertionError("GC touched ClickHouse with the switch off")

    redis_client = MagicMock()
    with override_settings(FI_TRAJECTORY_GC_DELETES_ENABLED=False):
        report = lifecycle.trajectory_items_gc(
            execute=refuse, redis_client=redis_client
        )
    assert report["status"] == "deletes_disabled"
    assert report["delete_statements"] == 0
    assert not redis_client.mock_calls


def test_gc_deletes_are_off_by_default():
    from django.conf import settings

    assert settings.FI_TRAJECTORY_GC_DELETES_ENABLED is False


@pytest.mark.integration
def test_gc_switch_off_keeps_orphans_on_live_store(store):
    pid = str(uuid.uuid4())
    store.add_items([(pid, "s", _h(), store.now_us() - 10 * DAY_US)])
    with override_settings(FI_TRAJECTORY_GC_DELETES_ENABLED=False):
        assert _gc(store)["status"] == "deletes_disabled"
    assert len(store.items(pid)) == 1
    assert not store.statements


@pytest.mark.integration
def test_gc_deletes_when_key_set_and_latch_clean(store):
    pid = str(uuid.uuid4())
    store.add_items([(pid, "s", _h(), store.now_us() - 10 * DAY_US)])
    with GC_ON:
        report = _gc(store)
    assert report["status"] == "ok"
    assert report["delete_statements"] == 1
    assert store.items(pid) == set()


# ---------------------------------------------------------------------------
# Latch (LC-2)
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_gc_latches_when_span_reuses_item_older_than_cap(store):
    now = store.now_us()
    pid, scope, key = str(uuid.uuid4()), "session-1", _h()
    # Reused 4 days after its copy was persisted: past the 72 h bound.
    store.add_items([(pid, scope, key, now - 4 * DAY_US - 2 * HOUR_US)])
    store.add_spans(
        [
            {
                "project": pid,
                "created_us": now - 2 * HOUR_US,
                "scope": scope,
                "inputs": [key],
            }
        ]
    )
    orphan = str(uuid.uuid4())
    store.add_items([(orphan, "s", _h(), now - 20 * DAY_US)])
    with GC_ON:
        report = _gc(store)
    assert report["status"] == "latched"
    assert report["reason"] == "reference_violation"
    assert report["audit"]["stale"] == 1
    assert not store.deletes()
    assert len(store.items(orphan)) == 1


@pytest.mark.integration
def test_gc_latches_on_missing_referenced_item(store):
    now = store.now_us()
    pid = str(uuid.uuid4())
    store.add_spans(
        [
            {
                "project": pid,
                "created_us": now - 3 * HOUR_US,
                "scope": "sess",
                "tools": ["p:" + _h()],
            }
        ]
    )
    orphan = str(uuid.uuid4())
    store.add_items([(orphan, "s", _h(), now - 20 * DAY_US)])
    with GC_ON:
        report = _gc(store)
    assert report["status"] == "latched"
    assert report["audit"]["missing"] == 1
    assert not store.deletes()
    assert len(store.items(orphan)) == 1


@pytest.mark.integration
def test_gc_latch_follows_envelope_shape_references(store):
    now = store.now_us()
    pid = str(uuid.uuid4())
    shape = _h()
    store.add_spans(
        [
            {
                "project": pid,
                "created_us": now - 3 * HOUR_US,
                "scope": "sess",
                "envelope": json.dumps({"in": {"sh": shape}}),
            }
        ]
    )
    with GC_ON:
        assert _gc(store)["audit"]["missing"] == 1
    store.add_items([(pid, "sess", shape, now - 4 * HOUR_US)])
    with GC_ON:
        assert _gc(store)["status"] == "ok"


@pytest.mark.integration
def test_gc_requires_contract_key(store, seen_redis):
    """Real Redis: no contract key and recent items_v1 spans means latched."""
    now = store.now_us()
    pid, key = str(uuid.uuid4()), _h()
    store.add_items([(pid, "sess", key, now - 5 * HOUR_US)])
    store.add_spans(
        [
            {
                "project": pid,
                "created_us": now - 4 * HOUR_US,
                "scope": "sess",
                "inputs": [key],
            }
        ]
    )
    orphan = str(uuid.uuid4())
    store.add_items([(orphan, "s", _h(), now - 20 * DAY_US)])
    with GC_ON:
        report = lifecycle.trajectory_items_gc(
            execute=store.execute, redis_client=seen_redis
        )
        assert report == {
            **report,
            "status": "latched",
            "reason": "contract_key_absent",
        }
        assert not store.deletes()
        assert len(store.items(orphan)) == 1

        seen_redis.set(CONTRACT_KEY, "1", ex=3600)
        # The key alone does not let GC delete: the quiet period starts now.
        report = lifecycle.trajectory_items_gc(
            execute=store.execute, redis_client=seen_redis
        )
        assert report["status"] == "waiting", report
        assert report["reason"] == "legacy_quiet_period"
        assert not store.deletes()
        started = int(seen_redis.get(QUIET_SINCE_KEY))
        assert abs(started - store.now_us()) < HOUR_US
        assert 0 < seen_redis.ttl(QUIET_SINCE_KEY) <= QUIET_MARK_TTL.total_seconds()

        seen_redis.set(QUIET_SINCE_KEY, str(started - QUIET_US - HOUR_US))
        report = lifecycle.trajectory_items_gc(
            execute=store.execute, redis_client=seen_redis
        )
    assert report["status"] == "ok"
    assert store.items(orphan) == set()
    assert len(store.items(pid)) == 1


@pytest.mark.integration
def test_gc_without_contract_key_and_no_dedup_is_inactive_not_alert(store):
    pid = str(uuid.uuid4())
    store.add_items([(pid, "s", _h(), store.now_us() - 20 * DAY_US)])
    with GC_ON:
        report = _gc(store, present=False)
    assert report["status"] == "inactive"
    assert not store.deletes()
    assert len(store.items(pid)) == 1


def _stale_reuse(store, now):
    """A span that reuses a copy persisted 4 days earlier: past the bound."""
    pid, scope, key = str(uuid.uuid4()), "session-1", _h()
    store.add_items([(pid, scope, key, now - 4 * DAY_US - 2 * HOUR_US)])
    store.add_spans(
        [
            {
                "project": pid,
                "created_us": now - 2 * HOUR_US,
                "scope": scope,
                "inputs": [key],
            }
        ]
    )
    return pid


@pytest.mark.integration
def test_gc_latch_persists_until_operator_clears_it(store, monkeypatch):
    """A violation keeps GC stopped after its span leaves the audit window."""
    now = store.now_us()
    _stale_reuse(store, now)
    orphan = str(uuid.uuid4())
    store.add_items([(orphan, "s", _h(), now - 20 * DAY_US)])
    seen = Contract()
    with GC_ON:
        first = _gc(store, redis_client=seen)
    assert first["status"] == "latched" and first["reason"] == "reference_violation"
    latch = json.loads(seen.values[GC_LATCH_KEY])
    assert latch["reason"] == "reference_violation" and latch["stale"] == 1

    monkeypatch.setattr(
        lifecycle, "open_executor", lambda: (store.execute, lambda: None)
    )
    monkeypatch.setattr(lifecycle, "open_seen_redis", lambda: seen)
    # While the audit still sees the violation, a plain clear is refused.
    with pytest.raises(CommandError, match="stays set"):
        call_command("trajectory_audit", "--clear-gc-latch", stdout=io.StringIO())
    assert GC_LATCH_KEY in seen.values

    # The violating span is no longer in the audit window (here: gone).
    store.client.execute("TRUNCATE TABLE spans")
    with GC_ON:
        second = _gc(store, redis_client=seen)
    assert second["status"] == "latched" and second["reason"] == "persisted_latch"
    assert not store.deletes()
    assert len(store.items(orphan)) == 1

    out = io.StringIO()
    call_command("trajectory_audit", "--clear-gc-latch", stdout=out)
    assert '"gc_latch_cleared": true' in out.getvalue()
    assert GC_LATCH_KEY not in seen.values
    with GC_ON:
        third = _gc(store, redis_client=seen)
    assert third["status"] == "ok"
    assert store.items(orphan) == set()


@pytest.mark.integration
def test_trajectory_audit_force_clears_latch_despite_violations(store, monkeypatch):
    now = store.now_us()
    _stale_reuse(store, now)
    seen = Contract(keys=[GC_LATCH_KEY])
    monkeypatch.setattr(
        lifecycle, "open_executor", lambda: (store.execute, lambda: None)
    )
    monkeypatch.setattr(lifecycle, "open_seen_redis", lambda: seen)
    with pytest.raises(CommandError, match="violation"):
        call_command(
            "trajectory_audit", "--clear-gc-latch", "--force", stdout=io.StringIO()
        )
    assert GC_LATCH_KEY not in seen.values


@pytest.mark.integration
def test_gc_waits_while_legacy_seen_keys_exist(store):
    """A legacy-layout collector may reuse for up to 7 days: no deletes."""
    orphan = str(uuid.uuid4())
    store.add_items([(orphan, "s", _h(), store.now_us() - 20 * DAY_US)])
    legacy = f"traj:seen:{uuid.uuid4()}:sess"
    seen = Contract(keys=[legacy, f"traj:seen:v2:{uuid.uuid4()}:e1:sess:7"])
    with GC_ON:
        report = _gc(store, redis_client=seen)
    assert report["status"] == "latched" and report["reason"] == "legacy_seen_keys"
    assert not store.deletes()
    assert GC_LATCH_KEY not in seen.values  # clears itself once the keys expire
    # Seeing a legacy key restarted the quiet period.
    assert QUIET_SINCE_KEY not in seen.values
    del seen.values[legacy]
    with GC_ON:
        # The key is gone, but a legacy pod may still reuse from its local
        # LRU for 7 days: GC waits out the quiet period first.
        waiting = _gc(store, redis_client=seen)
    assert waiting["status"] == "waiting", waiting
    assert not store.deletes()
    assert len(store.items(orphan)) == 1
    started = int(seen.values[QUIET_SINCE_KEY])
    seen.values[QUIET_SINCE_KEY] = str(started - QUIET_US - HOUR_US)
    with GC_ON:
        assert _gc(store, redis_client=seen)["status"] == "ok"
    assert store.items(orphan) == set()


@pytest.mark.integration
def test_gc_quiet_period_starts_once_and_must_run_its_course(store):
    """No mark yet: GC marks the start (set-if-absent) and deletes nothing.

    A later run keeps the first mark; deletes start only once it is
    LEGACY_QUIET_PERIOD old.
    """
    orphan = str(uuid.uuid4())
    store.add_items([(orphan, "s", _h(), store.now_us() - 20 * DAY_US)])
    seen = Contract(quiet=False)
    with GC_ON:
        first = _gc(store, redis_client=seen)
    assert first["status"] == "waiting" and first["reason"] == "legacy_quiet_period"
    started = int(seen.values[QUIET_SINCE_KEY])
    assert abs(started - store.now_us()) < HOUR_US
    assert seen.ttls[QUIET_SINCE_KEY] == int(QUIET_MARK_TTL.total_seconds())

    with GC_ON:
        assert _gc(store, redis_client=seen)["status"] == "waiting"
    assert int(seen.values[QUIET_SINCE_KEY]) == started  # never moved forward

    seen.values[QUIET_SINCE_KEY] = str(store.now_us() - QUIET_US + HOUR_US)
    with GC_ON:
        assert _gc(store, redis_client=seen)["status"] == "waiting"
    assert not store.deletes()
    assert len(store.items(orphan)) == 1

    seen.values[QUIET_SINCE_KEY] = str(store.now_us() - QUIET_US - HOUR_US)
    with GC_ON:
        assert _gc(store, redis_client=seen)["status"] == "ok"
    assert store.items(orphan) == set()


@pytest.mark.integration
def test_gc_quiet_period_restarts_without_contract_key(store):
    orphan = str(uuid.uuid4())
    store.add_items([(orphan, "s", _h(), store.now_us() - 20 * DAY_US)])
    seen = Contract()
    del seen.values[CONTRACT_KEY]
    with GC_ON:
        assert _gc(store, redis_client=seen)["status"] == "inactive"
    assert QUIET_SINCE_KEY not in seen.values
    seen.values[CONTRACT_KEY] = "1"
    with GC_ON:
        assert _gc(store, redis_client=seen)["status"] == "waiting"
    assert len(store.items(orphan)) == 1


@pytest.mark.integration
def test_gc_audits_while_legacy_keys_hold_it_back(store):
    """Legacy reuse past the bound latches GC for good, not only while keys live."""
    now = store.now_us()
    _stale_reuse(store, now)
    legacy = f"traj:seen:{uuid.uuid4()}:sess"
    seen = Contract(keys=[legacy])
    with GC_ON:
        report = _gc(store, redis_client=seen)
    assert report["status"] == "latched"
    assert report["reason"] == "reference_violation"
    assert json.loads(seen.values[GC_LATCH_KEY])["stale"] == 1
    assert not store.deletes()


@pytest.mark.integration
def test_legacy_seen_key_scan_matches_only_the_legacy_layout(seen_redis):
    from tracer.services.trajectory_seen_contract import legacy_seen_keys_present

    pid = str(uuid.uuid4())
    for key in (
        f"traj:seen:v2:{pid}:e1:sess:10",
        f"traj:seen:v2:{{{pid}}}:e1:__project__:10",
    ):
        seen_redis.sadd(key, "h")
    seen_redis.set(CONTRACT_KEY, "1")
    seen_redis.set(EPOCH_KEY, "e1")
    assert legacy_seen_keys_present(seen_redis) is False
    seen_redis.sadd(f"traj:seen:{pid}:sess", "h")
    assert legacy_seen_keys_present(seen_redis) is True


def test_open_seen_redis_uses_the_collector_database():
    from tracer.services.trajectory_seen_contract import open_seen_redis

    with override_settings(REDIS_URL="redis://localhost:16379/5"):
        client = open_seen_redis()
    try:
        assert client.connection_pool.connection_kwargs["db"] == 0
    finally:
        client.close()


@pytest.mark.integration
def test_trajectory_audit_reports_zero_on_clean_fixture(store, monkeypatch):
    now = store.now_us()
    pid, key, tool = str(uuid.uuid4()), _h(), _h()
    store.add_items(
        [
            (pid, "sess", key, now - 6 * HOUR_US),
            (pid, "__project__", tool, now - DAY_US),
        ]
    )
    store.add_spans(
        [
            {
                "project": pid,
                "created_us": now - 5 * HOUR_US,
                "scope": "sess",
                "inputs": [key],
                "tools": ["p:" + tool],
            }
        ]
    )
    monkeypatch.setattr(
        lifecycle, "open_executor", lambda: (store.execute, lambda: None)
    )
    out = io.StringIO()
    call_command("trajectory_audit", stdout=out)
    report = json.loads(out.getvalue())
    assert report["status"] == "ok"
    assert report["spans_sampled"] == 1
    assert report["references"] == 2
    assert report["violations"] == 0


@pytest.mark.integration
def test_trajectory_audit_exits_nonzero_on_violation(store, monkeypatch):
    now = store.now_us()
    store.add_spans(
        [
            {
                "project": str(uuid.uuid4()),
                "created_us": now - 5 * HOUR_US,
                "scope": "sess",
                "inputs": [_h()],
            }
        ]
    )
    monkeypatch.setattr(
        lifecycle, "open_executor", lambda: (store.execute, lambda: None)
    )
    with pytest.raises(CommandError):
        call_command(
            "trajectory_audit", "--since", "2000-01-01T00:00:00", stdout=io.StringIO()
        )
    assert not store.deletes()


# ---------------------------------------------------------------------------
# GC (LC-1)
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_gc_property_never_deletes_referenced_items(store):
    rng = random.Random(4347)
    now = store.now_us()
    projects = [str(uuid.uuid4()) for _ in range(6)]
    items, spans = [], []
    referenced: set[tuple[str, str, str]] = set()
    oldest_span: dict[str, int] = {}
    for index, pid in enumerate(projects):
        pool = [("__project__", _h()) for _ in range(3)]
        pool += [(f"sess-{index}-{n}", _h()) for n in range(8)]
        for _ in range(0 if index == 0 else rng.randint(3, 12)):
            created = now - rng.randint(HOUR_US, 20 * DAY_US)
            oldest_span[pid] = min(oldest_span.get(pid, created), created)
            refs = rng.sample(pool, rng.randint(1, 4))
            scope = next((s for s, _ in refs if s != "__project__"), "sess-x")
            refs = [(s, h) for s, h in refs if s in ("__project__", scope)]
            spans.append(
                {
                    "project": pid,
                    "created_us": created,
                    "scope": scope,
                    "inputs": [h for s, h in refs if s == scope],
                    "tools": ["p:" + h for s, h in refs if s == "__project__"],
                }
            )
            for s, h in refs:
                referenced.add((pid, s, h))
                # A copy within the collector's reuse bound of this span.
                items.append((pid, s, h, created - rng.randint(0, 47 * HOUR_US)))
                if rng.random() < 0.5:  # plus an older copy of the same key
                    older = created - rng.randint(3 * DAY_US, 30 * DAY_US)
                    items.append((pid, s, h, max(older, now - 30 * DAY_US)))
        for _ in range(rng.randint(2, 6)):  # orphans
            items.append(
                (pid, f"gone-{index}", _h(), now - rng.randint(0, 30 * DAY_US))
            )
    store.add_items(items)
    store.add_spans(spans)

    with GC_ON:
        report = _gc(store)
    assert report["status"] == "ok", report

    remaining = store.items()
    present_keys = {(p, s, h) for p, s, h, _ in remaining}
    assert referenced <= present_keys, "GC deleted an item a present span references"
    expected = set()
    for p, s, h, t in items:
        cut = min(oldest_span.get(p, now), now) - MARGIN_US
        if t >= cut:
            expected.add((p, s, h, t))
    # Every deleted copy was older than its project's cut; nothing else went.
    assert remaining == expected
    assert report["delete_statements"] >= 1
    assert len(items) > len({(p, s, h) for p, s, h, _ in items})  # many copies


def test_gc_caps_delete_statements_per_run(monkeypatch):
    """A bucket with many candidate projects cannot issue unbounded mutations."""
    monkeypatch.setattr(lifecycle, "PROJECTS_PER_STATEMENT", 1)
    monkeypatch.setattr(lifecycle, "MAX_DELETE_STATEMENTS_PER_RUN", 2)
    now = 1_000 * DAY_US
    projects = [str(uuid.uuid4()) for _ in range(5)]
    statements = []

    def execute(sql, params=None):
        statements.append(sql)
        if "FROM trajectory_items WHERE _partition_id" in sql:
            return [(p, now - 10 * DAY_US) for p in projects]
        return []  # no span rows

    report = lifecycle.GcReport(status="ok")
    lifecycle._gc_bucket(execute, 0, now_us=now, report=report)
    assert report.delete_statements == 2
    assert report.capped is True
    assert sum(s.lstrip().startswith("DELETE") for s in statements) == 2


@pytest.mark.integration
def test_gc_lookahead_horizon_with_items_older_than_the_lookahead(store):
    """Items far older than PROBE_LOOKAHEAD, and different horizons in one probe.

    One bucket, so all four projects share a DELETE and one oldest-span probe
    whose bound is the largest horizon (now, from ``fresh``).
    """
    now = store.now_us()
    bucket = 13
    deep = store.project_in_bucket(bucket)
    fresh = store.project_in_bucket(bucket, exclude={deep})
    spanned = store.project_in_bucket(bucket, exclude={deep, fresh})
    held = store.project_in_bucket(bucket, exclude={deep, fresh, spanned})
    k_deep, k_spanned, k_held = _h(), _h(), _h()
    kept = {
        # deep: oldest item 100 d, spans only 5 d old. Its horizon is
        # 100 d - 72 h - 30 d = 67 d ago, below now, so its cut is 70 d ago.
        (deep, "s", _h(), now - 69 * DAY_US),
        (deep, "s", k_deep, now - 6 * DAY_US),
        # spanned: oldest span 50 d, so its cut is 53 d ago.
        (spanned, "s", k_spanned, now - 52 * DAY_US),
        # held: a span 2 d after its oldest item; nothing of it can go.
        (held, "s", k_held, now - 40 * DAY_US),
    }
    gone = {
        (deep, "s", _h(), now - 100 * DAY_US),
        (fresh, "s", _h(), now - 10 * DAY_US),
        (spanned, "s", _h(), now - 60 * DAY_US),
        (spanned, "s", _h(), now - 54 * DAY_US),
    }
    store.add_items(list(kept | gone))
    store.add_spans(
        [
            {
                "project": deep,
                "created_us": now - 5 * DAY_US,
                "scope": "s",
                "inputs": [k_deep],
            },
            {
                "project": spanned,
                "created_us": now - 50 * DAY_US,
                "scope": "s",
                "inputs": [k_spanned],
            },
            {
                "project": held,
                "created_us": now - 38 * DAY_US,
                "scope": "s",
                "inputs": [k_held],
            },
        ]
    )
    with GC_ON:
        report = _gc(store)
    assert report["status"] == "ok", report
    assert report["projects_held"] == 1
    assert report["projects_cut"] == 3
    assert report["delete_statements"] == 1
    assert store.items() == kept
    probes = [sql for sql in store.statements if "min(created_at)" in sql]
    assert len(probes) == 1


@pytest.mark.integration
def test_gc_skips_the_span_probe_when_every_project_is_held(store):
    """A span within GC_MARGIN of the oldest item: one LIMIT 1 read, no scan."""
    now = store.now_us()
    pid, key = str(uuid.uuid4()), _h()
    store.add_items([(pid, "s", key, now - 10 * DAY_US)])
    store.add_spans(
        [
            {
                "project": pid,
                "created_us": now - 9 * DAY_US,
                "scope": "s",
                "inputs": [key],
            }
        ]
    )
    with GC_ON:
        report = _gc(store)
    assert report["status"] == "ok"
    assert report["projects_held"] == 1 and report["projects_cut"] == 0
    assert not [sql for sql in store.statements if "min(created_at)" in sql]
    assert not store.deletes()
    assert len(store.items(pid)) == 1


@pytest.mark.integration
def test_gc_deletes_old_copy_keeps_refreshed_copy(store):
    now = store.now_us()
    pid, scope, key = str(uuid.uuid4()), "sess", _h()
    store.add_items(
        [
            (pid, scope, key, now - 10 * DAY_US, "same"),
            (pid, scope, key, now - HOUR_US, "same"),
        ]
    )
    store.add_spans(
        [
            {
                "project": pid,
                "created_us": now - 2 * HOUR_US,
                "scope": scope,
                "inputs": [key],
            }
        ]
    )
    assert len(store.items(pid)) == 2  # both copies are stored
    with GC_ON:
        report = _gc(store)
    assert report["status"] == "ok"
    assert report["delete_statements"] == 1
    assert {t for *_, t in store.items(pid)} == {now - HOUR_US}
    store.client.execute("OPTIMIZE TABLE trajectory_items FINAL")
    rows = store.client.execute(
        "SELECT count(), argMax(content, persisted_at) FROM trajectory_items "
        "WHERE project_id = toUUID(%(p)s)",
        {"p": pid},
    )
    assert rows == [(1, "same")]
    assert {t for *_, t in store.items(pid)} == {now - HOUR_US}


@pytest.mark.integration
def test_gc_removes_items_of_project_without_spans_only_after_margin(store):
    now = store.now_us()
    pid = str(uuid.uuid4())
    old, recent = _h(), _h()
    store.add_items(
        [(pid, "s", old, now - 80 * HOUR_US), (pid, "s", recent, now - 60 * HOUR_US)]
    )
    with GC_ON:
        _gc(store)
    assert {h for _, _, h, _ in store.items(pid)} == {recent}


@pytest.mark.integration
def test_gc_issues_one_delete_per_bucket_and_none_when_empty(store):
    now = store.now_us()
    a = store.project_in_bucket(3)
    b = store.project_in_bucket(3, exclude={a})
    c = store.project_in_bucket(11)
    store.add_items([(p, "s", _h(), now - 9 * DAY_US) for p in (a, b, c, a)])
    with GC_ON:
        first = _gc(store)
    assert first["delete_statements"] == 2
    deletes = store.deletes()
    assert len(deletes) == 2
    assert all("lightweight_deletes_sync = 2" in sql for sql in deletes)
    assert all("now" not in sql.lower() for sql in deletes)
    store.statements.clear()
    with GC_ON:
        second = _gc(store)
    assert second["delete_statements"] == 0
    assert not store.deletes()


@pytest.mark.integration
def test_gc_leaves_other_projects_in_same_bucket_untouched(store):
    now = store.now_us()
    gone = store.project_in_bucket(7)
    kept = store.project_in_bucket(7, exclude={gone})
    store.add_items([(gone, "s", _h(), now - 9 * DAY_US)])
    # kept's oldest span is 30 days old, so its cut is 33 days back.
    key = _h()
    store.add_spans(
        [
            {
                "project": kept,
                "created_us": now - 30 * DAY_US,
                "scope": "s",
                "inputs": [key],
            },
            {
                "project": kept,
                "created_us": now - 3 * HOUR_US,
                "scope": "t",
                "encoding": "inline",
            },
        ]
    )
    kept_items = {
        (kept, "s", key, now - 31 * DAY_US),
        (kept, "s", _h(), now - 32 * DAY_US),
    }
    store.add_items(list(kept_items))
    with GC_ON:
        _gc(store)
    assert store.items(gone) == set()
    assert store.items(kept) == kept_items


@pytest.mark.integration
@pytest.mark.parametrize("read_flag", [False, True], ids=["read-off", "read-on"])
def test_gc_runs_with_read_flag_off_and_on(store, read_flag):
    pid = str(uuid.uuid4())
    store.add_items([(pid, "s", _h(), store.now_us() - 9 * DAY_US)])
    with GC_ON, override_settings(FI_TRAJECTORY_STORE_READ_ENABLED=read_flag):
        assert _gc(store)["status"] == "ok"
    assert store.items(pid) == set()


@pytest.mark.integration
def test_gc_noop_without_trajectory_items():
    with _store(PRE040_FILES) as pre040:
        with GC_ON:
            report = _gc(pre040)
        assert report["status"] == "absent"
        assert not pre040.deletes()
        assert not pre040.masks()
        assert (
            lifecycle.erase_project_items(str(uuid.uuid4()), execute=pre040.execute)[
                "status"
            ]
            == "absent"
        )


@pytest.mark.integration
def test_apply_deleted_mask_reclaims_partition(store):
    now = store.now_us()
    pid = store.project_in_bucket(5)
    store.add_items([(pid, "s", _h(), now - 9 * DAY_US) for _ in range(3)])
    store.add_items([(store.project_in_bucket(5), "s", _h(), now - HOUR_US)])
    with GC_ON:
        report = _gc(store)
    assert report["masked_partitions"] >= 1
    assert "5" not in store.masked_partitions()
    assert store.items(pid) == set()


# Defined by .ci/clickhouse-test-config.xml: one shard, one replica (this server).
CI_CLUSTER = "traj_ci_single_replica"


def _cluster_hosts(client, cluster: str) -> int:
    rows = client.execute(
        "SELECT count() FROM system.clusters WHERE cluster = %(c)s AND is_local",
        {"c": cluster},
    )
    return int(rows[0][0]) if rows else 0


def _create_replicated_tables(client, database: str) -> None:
    path = f"/clickhouse/tables/{{shard}}/{database}"
    client.execute(
        "CREATE TABLE spans (project_id UUID, id String, trace_id String, "
        "start_time DateTime64(6, 'UTC'), "
        "created_at DateTime64(6, 'UTC') DEFAULT now64(6, 'UTC'), "
        "item_scope_id String DEFAULT '', "
        "input_item_hashes Array(String) DEFAULT [], "
        "output_item_hashes Array(String) DEFAULT [], "
        "tool_def_hashes Array(String) DEFAULT [], "
        "payload_encoding LowCardinality(String) DEFAULT 'inline', "
        "payload_envelope String DEFAULT '', is_deleted UInt8 DEFAULT 0, "
        "_version UInt64 DEFAULT 1) "
        f"ENGINE = ReplicatedReplacingMergeTree('{path}/spans', '{{replica}}', "
        "_version, is_deleted) ORDER BY (project_id, id)"
    )
    client.execute(
        "CREATE TABLE trajectory_items (project_id UUID, scope_id String, "
        "item_hash FixedString(32), item_type LowCardinality(String) DEFAULT 'raw', "
        "role LowCardinality(String) DEFAULT '', content String, "
        "persisted_at DateTime64(6, 'UTC') DEFAULT now64(6, 'UTC')) "
        f"ENGINE = ReplicatedReplacingMergeTree('{path}/trajectory_items', "
        "'{replica}', persisted_at) "
        "PARTITION BY cityHash64(project_id) % 32 "
        "ORDER BY (project_id, scope_id, item_hash)"
    )


@pytest.mark.integration
def test_gc_on_replicated_schema():
    """Replicated tables (Keeper path private to this database).

    The CI ClickHouse defines CI_CLUSTER, and this test fails without it, so
    the replicated path always runs: the replica health check, the
    all-replica oldest-span probe, and erasure's all-replica counts.
    """
    with _ch_test_owned_database("test_traj_lc_repl_") as database:
        with _ch_test_native_client(database=database) as client:
            assert _cluster_hosts(client, CI_CLUSTER) == 1, (
                f"{CI_CLUSTER} is missing from system.clusters; it comes from "
                ".ci/clickhouse-test-config.xml"
            )
            _create_replicated_tables(client, database)
            store = Store(client, database)
            now = store.now_us()
            pid, held, key = str(uuid.uuid4()), str(uuid.uuid4()), _h()
            store.add_items(
                [(pid, "s", _h(), now - 9 * DAY_US), (held, "s", key, now - 9 * DAY_US)]
            )
            # live: a recent span and its item, so the audit's span sample and
            # item lookup both run on the replicated path.
            live, live_key = str(uuid.uuid4()), _h()
            store.add_items([(live, "sess", live_key, now - 2 * HOUR_US)])
            # held's span is within GC_MARGIN of its oldest item: nothing goes.
            store.add_spans(
                [
                    {
                        "project": held,
                        "created_us": now - 9 * DAY_US + HOUR_US,
                        "scope": "s",
                        "inputs": [key],
                    },
                    {
                        "project": live,
                        "created_us": now - HOUR_US,
                        "scope": "sess",
                        "inputs": [live_key],
                    },
                ]
            )
            with GC_ON, override_settings(FI_TRAJECTORY_CH_CLUSTER=CI_CLUSTER):
                topology = lifecycle.resolve_topology(store.execute)
                report = _gc(store)
            assert topology == lifecycle.Topology(
                replicated=True, cluster=CI_CLUSTER, hosts=1, database=database
            )
            assert report["status"] == "ok", report
            assert report["audit"]["references"] == 1, report
            assert report["audit"]["violations"] == 0
            audit_reads = [
                sql
                for sql in store.statements
                if "payload_encoding = 'items_v1'" in sql or "max(persisted_at)" in sql
            ]
            assert audit_reads
            assert all("clusterAllReplicas" in sql for sql in audit_reads)
            assert any("max(persisted_at)" in sql for sql in audit_reads)
            assert report["projects_held"] == 1
            assert store.items(pid) == set()
            assert len(store.items(held)) == 1
            assert any(
                "clusterAllReplicas" in sql and "system.replicas" in sql
                for sql in store.statements
            )
            assert any(
                "clusterAllReplicas" in sql and "min(created_at)" in sql
                for sql in store.statements
            )

            gone = str(uuid.uuid4())
            store.add_items([(gone, "s", _h(), now - HOUR_US) for _ in range(2)])
            store.statements.clear()
            with override_settings(FI_TRAJECTORY_CH_CLUSTER=CI_CLUSTER):
                erased = lifecycle.erase_project_items(gone, execute=store.execute)
                receipt = lifecycle.verify_project_erased(gone, execute=store.execute)
            assert erased["rows"] == 2 and erased["delete_statements"] == 1
            first_delete = next(
                i for i, sql in enumerate(store.statements) if sql.startswith("DELETE")
            )
            # The row count that decides whether to delete reads every replica.
            assert any(
                "clusterAllReplicas" in sql for sql in store.statements[:first_delete]
            )
            assert receipt["all_replicas"] is True
            assert store.items(gone) == set()


def _replicated_fake(*, replicas=2, clusters=None, health=(4, 0), statements=None):
    """A fake ClickHouse that reports replicated spans/trajectory_items."""
    from tracer.services.clickhouse.v2.trajectory_hydration import (
        TRAJECTORY_SPAN_COLUMNS,
    )

    clusters = clusters or [("prod", 1, 2, ["a:9000", "b:9000"])]
    log = statements if statements is not None else []

    def execute(sql, params=None):
        log.append(sql)
        if "engine = 'Distributed'" in sql:
            return []
        if "FROM system.tables" in sql and "count()" in sql:
            return [(1,)]
        if "FROM system.columns" in sql:
            return [(name,) for name in TRAJECTORY_SPAN_COLUMNS]
        if "name, engine FROM system.tables" in sql:
            return [
                ("spans", "ReplicatedReplacingMergeTree"),
                ("trajectory_items", "ReplicatedReplacingMergeTree"),
            ]
        if "max(total_replicas)" in sql:
            return [(replicas,)]
        if "FROM system.clusters" in sql:
            return list(clusters)
        if sql == "SELECT currentDatabase()":
            return [("db",)]
        if "system.replicas" in sql and "clusterAllReplicas" in sql:
            return [health]
        raise AssertionError(f"unexpected statement: {sql}")

    return execute


@pytest.mark.parametrize(
    ("health", "reason"),
    [((4, 1), "replica_unhealthy"), ((3, 0), "replica_missing")],
    ids=["unhealthy", "missing"],
)
def test_gc_refuses_while_a_replica_is_behind_or_missing(health, reason):
    """A lagging or rebuilt replica could hide span rows from GC's reads."""
    statements = []
    redis_client = MagicMock()
    execute = _replicated_fake(health=health, statements=statements)
    with GC_ON:
        report = lifecycle.trajectory_items_gc(
            execute=execute, redis_client=redis_client
        )
    assert report["status"] == "refused" and report["reason"] == reason
    assert not [sql for sql in statements if sql.lstrip().startswith("DELETE")]
    assert not redis_client.mock_calls


def test_replica_health_passes_only_when_every_replica_is_current():
    execute = _replicated_fake(health=(4, 0))
    with override_settings(FI_TRAJECTORY_CH_CLUSTER=""):
        topology = lifecycle.resolve_topology(execute)
    assert topology.hosts == 2 and topology.database == "db"
    assert lifecycle.replica_health_refusal(execute, topology) is None
    local = lifecycle.Topology(replicated=False)
    assert lifecycle.replica_health_refusal(execute, local) is None


def test_configured_cluster_must_cover_every_replica():
    execute = _replicated_fake(replicas=3)
    with override_settings(FI_TRAJECTORY_CH_CLUSTER="prod"):
        topology = lifecycle.resolve_topology(execute)
    assert topology.refusal == "cluster_replica_mismatch"


def test_cluster_discovery_refuses_clusters_over_different_hosts():
    execute = _replicated_fake(
        clusters=[
            ("alpha", 1, 2, ["a:9000", "c:9000"]),
            ("prod", 1, 2, ["a:9000", "b:9000"]),
        ]
    )
    with override_settings(FI_TRAJECTORY_CH_CLUSTER=""):
        assert lifecycle.resolve_topology(execute).refusal == "cluster_ambiguous"
    same_hosts = _replicated_fake(
        clusters=[
            ("alpha", 1, 2, ["a:9000", "b:9000"]),
            ("prod", 1, 2, ["a:9000", "b:9000"]),
        ]
    )
    with override_settings(FI_TRAJECTORY_CH_CLUSTER=""):
        assert lifecycle.resolve_topology(same_hosts).cluster == "alpha"


def test_gc_audit_reads_every_replica():
    """A violating span on another replica latches GC.

    The connected replica has not fetched the span yet (its local read finds
    nothing); only the all-replica read sees it, and its item is missing.
    """
    now = 1_900_000_000_000_000
    pid, key = str(uuid.uuid4()), _h()
    statements: list[str] = []
    base = _replicated_fake(statements=statements)

    def execute(sql, params=None):
        params = params or {}
        if "now64(6, 'UTC')" in sql:
            return [(now,)]
        if "payload_encoding = 'items_v1'" in sql and "LIMIT %(limit)s" in sql:
            statements.append(sql)
            if "clusterAllReplicas" in sql and params.get("bucket") == 0:
                return [(pid, "sess", now - 2 * HOUR_US, [key])]
            return []
        if "max(persisted_at)" in sql:
            statements.append(sql)
            return []
        return base(sql, params)

    seen = Contract()
    with GC_ON, override_settings(FI_TRAJECTORY_CH_CLUSTER="prod"):
        report = lifecycle.trajectory_items_gc(execute=execute, redis_client=seen)
        audit = lifecycle.run_audit(execute=execute)
    assert report["status"] == "latched", report
    assert report["reason"] == "reference_violation"
    assert report["audit"]["missing"] == 1
    assert GC_LATCH_KEY in seen.values
    assert not [sql for sql in statements if sql.lstrip().startswith("DELETE")]
    assert audit["status"] == "violations" and audit["missing"] == 1


def test_trajectory_audit_keeps_latch_when_audit_refused(monkeypatch):
    """A refused audit read nothing, so it cannot clear the latch."""
    seen = Contract(keys=[GC_LATCH_KEY])
    monkeypatch.setattr(lifecycle, "open_seen_redis", lambda: seen)
    monkeypatch.setattr(
        "tracer.management.commands.trajectory_audit.run_audit",
        lambda since_us=None: {"status": "refused", "reason": "cluster_unknown"},
    )
    with pytest.raises(CommandError, match="stays set"):
        call_command("trajectory_audit", "--clear-gc-latch", stdout=io.StringIO())
    assert GC_LATCH_KEY in seen.values
    with pytest.raises(CommandError, match="refused"):
        call_command("trajectory_audit", stdout=io.StringIO())


def test_gc_refuses_a_distributed_facade():
    def execute(sql, params=None):
        if "engine = 'Distributed'" in sql:
            return [("spans_all", "Distributed('c', 'db', 'spans', rand())")]
        raise AssertionError(sql)

    assert lifecycle.resolve_topology(execute).refusal == "distributed_table"


def test_reuse_cap_matches_collector_constant():
    """GC's margin rests on the collector's reuse cap.

    When the bounded seen store (seen store v2, SeenMaxReuseAge in
    fi-collector/pkg/trajectory) is present, its cap must equal the Python
    constant, and it must use the same epoch and contract keys and the key
    layout that erasure and the seen-store reset remove. Without it no
    collector writes the contract key, so GC can never delete: that is what
    this test pins in that case. The collector config cap (MaxReuseAgeCap)
    must equal the constant whenever it exists.
    """
    assert GC_MARGIN == MAX_REUSE_AGE_CAP + IN_FLIGHT_MARGIN == timedelta(hours=72)
    package = REPO_ROOT / "fi-collector/pkg/trajectory"
    source = "\n".join(
        p.read_text()
        for p in sorted(package.glob("*.go"))
        if not p.name.endswith("_test.go")
    )

    def hours(name):
        found = re.search(rf"\b{name}\s*=\s*(\d+)\s*\*\s*time\.Hour", source)
        return timedelta(hours=int(found.group(1))) if found else None

    config_cap = hours("MaxReuseAgeCap")
    if config_cap is not None:
        assert config_cap == MAX_REUSE_AGE_CAP
    seen_cap = hours("SeenMaxReuseAge")
    if seen_cap is not None:
        assert seen_cap == MAX_REUSE_AGE_CAP
        assert f'"{CONTRACT_KEY}"' in source
        assert f'"{EPOCH_KEY}"' in source
        for name, prefix in (
            ("seenKeyPrefix", SEEN_V2_KEY_PREFIX),
            ("seenLegacyPrefix", SEEN_KEY_PREFIX),
        ):
            assert re.search(rf'\b{name}\s*=\s*"{re.escape(prefix)}"', source), name
        # Project id right after the prefix, so project_seen_key_patterns
        # ("traj:seen:v2:<pid>:*") matches every key of one project.
        assert 'return seenKeyPrefix + projectID + ":" +' in source
        assert re.search(
            r"return \[\]string\{seenKeyPrefix \+ p \+ \":\*\", "
            r"seenLegacyPrefix \+ p \+ \":\*\"\}",
            source,
        )
        return
    go_sources = (REPO_ROOT / "fi-collector").rglob("*.go")
    assert not any(CONTRACT_KEY in p.read_text(errors="ignore") for p in go_sources)


# ---------------------------------------------------------------------------
# Erasure (LC-3)
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.django_db
def test_erasure_waits_while_spans_exist(store, organization, workspace, monkeypatch):
    from model_hub.models.ai_model import AIModel
    from tracer.models.project import Project

    gone = str(uuid.uuid4())  # absent from Postgres
    store.add_spans(
        [{"project": gone, "created_us": store.now_us(), "encoding": "inline"}]
    )
    with pytest.raises(lifecycle.TrajectoryErasurePending, match="spans_present"):
        lifecycle.erasure_gate(gone, execute=store.execute)

    live = Project.objects.create(
        organization=organization,
        workspace=workspace,
        name=f"lc-{uuid.uuid4()}",
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="observe",
    )
    with pytest.raises(lifecycle.TrajectoryErasurePending, match="project_not_deleted"):
        lifecycle.erasure_gate(str(live.pk), execute=store.execute)
    Project._base_manager.filter(pk=live.pk).update(deleted=True)
    assert lifecycle.erasure_gate(str(live.pk), execute=store.execute)["spans"] == 0

    monkeypatch.setattr(
        lifecycle, "open_executor", lambda: (store.execute, lambda: None)
    )
    requested = []
    monkeypatch.setattr(
        "tracer.management.commands.trajectory_erase.request_trajectory_erasure",
        requested.append,
    )
    with pytest.raises(CommandError, match="Refusing"):
        call_command("trajectory_erase", "--project", gone, stdout=io.StringIO())
    assert requested == []


@pytest.mark.integration
@override_settings(
    FI_TRAJECTORY_GC_DELETES_ENABLED=False, FI_TRAJECTORY_STORE_READ_ENABLED=False
)
def test_erasure_deletes_all_copies_and_applies_mask(store):
    """Not gated on the GC switch or the read flag."""
    now = store.now_us()
    pid = store.project_in_bucket(9)
    neighbour = store.project_in_bucket(9, exclude={pid})
    store.add_items(
        [(pid, "__project__", _h(), now), (pid, "s", _h(), now - DAY_US)]
        + [(pid, "s", _h(), now - HOUR_US) for _ in range(2)]
    )
    store.add_items([(neighbour, "s", _h(), now)])
    result = lifecycle.erase_project_items(pid, execute=store.execute)
    assert result["rows"] == 4
    assert result["delete_statements"] == 1
    assert result["masked_partitions"] == 1
    assert store.items(pid) == set()
    assert len(store.items(neighbour)) == 1
    assert "9" not in store.masked_partitions()


@pytest.mark.integration
def test_erasure_rerun_is_idempotent(store):
    pid = str(uuid.uuid4())
    store.add_items([(pid, "s", _h(), store.now_us())])
    lifecycle.erase_project_items(pid, execute=store.execute)
    store.statements.clear()
    again = lifecycle.erase_project_items(pid, execute=store.execute)
    assert again["rows"] == 0 and again["delete_statements"] == 0
    assert not store.deletes() and not store.masks()
    receipt = lifecycle.verify_project_erased(pid, execute=store.execute)
    assert receipt == {**receipt, "project_id": pid, "spans": 0, "items": 0}


@pytest.mark.integration
def test_erasure_verify_refuses_while_rows_remain(store):
    pid = str(uuid.uuid4())
    store.add_items([(pid, "s", _h(), store.now_us())])
    with pytest.raises(lifecycle.TrajectoryErasurePending, match="rows_present"):
        lifecycle.verify_project_erased(pid, execute=store.execute)


@pytest.mark.integration
def test_erasure_clears_only_target_project_seen_keys(seen_redis):
    target, other = str(uuid.uuid4()), str(uuid.uuid4())
    target_keys = [
        f"traj:seen:v2:{target}:epoch1:sess:10",
        f"traj:seen:v2:{{{target}}}:epoch1:__project__:10",
        f"traj:seen:{target}:sess",
    ]
    other_keys = [f"traj:seen:v2:{other}:epoch1:sess:10", f"traj:seen:{other}:sess"]
    for key in target_keys + other_keys:
        seen_redis.sadd(key, "h")
    seen_redis.set(EPOCH_KEY, "epoch1")
    seen_redis.set(CONTRACT_KEY, "1")
    assert clear_project_seen_keys(seen_redis, target) == len(target_keys)
    assert not any(seen_redis.exists(k) for k in target_keys)
    assert all(seen_redis.exists(k) for k in other_keys)
    assert seen_redis.get(EPOCH_KEY) == "epoch1"
    assert seen_redis.exists(CONTRACT_KEY)
    with pytest.raises(ValueError):
        clear_project_seen_keys(seen_redis, "*")


def test_request_trajectory_erasure_joins_a_running_erasure(monkeypatch):
    from temporalio.common import WorkflowIDConflictPolicy, WorkflowIDReusePolicy

    from tfc.temporal import trajectory_erasure

    calls = []
    monkeypatch.setattr(
        "tfc.temporal.common.client.start_workflow_sync",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )
    pid = str(uuid.uuid4())
    with override_settings(FI_TRAJECTORY_ERASURE_INGEST_WAIT_SECONDS=5000):
        ids = lifecycle.request_trajectory_erasure([pid.upper(), pid])
    assert ids == [f"trajectory-erasure-{pid}"]
    ((args, kwargs),) = calls
    assert args[0] is trajectory_erasure.TrajectoryErasureWorkflow
    assert args[1] == trajectory_erasure.TrajectoryErasureInput(
        project_id=pid, ingest_wait_seconds=5000
    )
    assert args[2:] == (f"trajectory-erasure-{pid}", "tasks_l")
    # Never cancel or terminate an erasure that is already running.
    assert kwargs["cancel_existing"] is False
    assert kwargs["id_conflict_policy"] == WorkflowIDConflictPolicy.USE_EXISTING
    assert kwargs["id_reuse_policy"] == WorkflowIDReusePolicy.ALLOW_DUPLICATE


def test_erasure_ingest_wait_never_drops_below_the_collector_cache(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "tfc.temporal.common.client.start_workflow_sync",
        lambda *args, **kwargs: calls.append(args[1]),
    )
    with override_settings(FI_TRAJECTORY_ERASURE_INGEST_WAIT_SECONDS=60):
        lifecycle.request_trajectory_erasure([str(uuid.uuid4())])
    assert calls[0].ingest_wait_seconds == lifecycle.ERASURE_MIN_INGEST_WAIT_SECONDS
    assert lifecycle.ERASURE_MIN_INGEST_WAIT_SECONDS == 300 + 3600 + 60


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(None, 3960), ("", 3960), ("7200", 7200), ("60", 3960), ("1h", 3960)],
)
def test_erasure_ingest_wait_setting_parses_without_failing_boot(raw, expected):
    from tfc.settings.settings import _trajectory_erasure_ingest_wait_seconds

    assert _trajectory_erasure_ingest_wait_seconds(raw) == expected


@pytest.mark.django_db
def test_trajectory_erase_command_starts_workflow_when_gate_passes(monkeypatch):
    from tracer.management.commands import trajectory_erase

    pid = str(uuid.uuid4())
    started = []
    monkeypatch.setattr(trajectory_erase, "erasure_gate", lambda p: {"spans": 0})
    monkeypatch.setattr(
        trajectory_erase,
        "request_trajectory_erasure",
        lambda ps: started.extend(ps) or [f"trajectory-erasure-{p}" for p in ps],
    )
    out = io.StringIO()
    call_command("trajectory_erase", "--project", pid, stdout=out)
    assert started == [pid]
    assert f"trajectory-erasure-{pid}" in out.getvalue()


# ---------------------------------------------------------------------------
# Redis fixture (private database index on the test Redis)
# ---------------------------------------------------------------------------


@pytest.fixture
def seen_redis():
    import redis

    base = os.environ.get("REDIS_URL", "redis://localhost:16379/0")
    url = base.rsplit("/", 1)[0] + "/13"
    client = redis.Redis.from_url(url, decode_responses=True, socket_timeout=5)
    try:
        client.ping()
    except Exception as exc:
        pytest.skip(f"test Redis unavailable: {exc!r}")
    client.flushdb()
    try:
        yield client
    finally:
        client.flushdb()
        client.close()
