"""Unconfirmed shared static evictions are retried on a schedule (W4).

A shared bearer/API-key revoke, lapse or rotation records its gateway eviction
(``gateway_purge_pending``) and keeps it until the gateway confirms. These
tests pin that a failed eviction no longer waits for the org's next shared
credential change: ``agentcc-mcp-shared-evictions-retry`` retries it, at most
a bounded number of slots per tick, each under the org's config-write lock
(skipping an org a writer holds), and an eviction made after a config write
commits is serialized with the org's writers too.

The gateway admin API is faked at its seam
(``gateway_admin.mcp_credentials.purge``) and the config push at the gateway
client. Credential values are synthetic.
"""

import copy
import threading
import time
from unittest.mock import MagicMock, patch

import pytest
from django.db import connection, transaction

from accounts.models import Organization
from agentcc.models.mcp_credential import AgentccMCPCredential
from agentcc.models.org_config import AgentccOrgConfig
from agentcc.services import mcp_credentials as vault
from agentcc.services.org_config_writer import lock_org

SECRET = "test-w4-sweep-not-a-real-token"
SECRET_2 = "test-w4-sweep-rotated-not-a-real-token"
GITHUB_URL = "https://mcp.github.example.com/mcp"
RETRY_SCHEDULE = "agentcc-mcp-shared-evictions-retry"
LIFECYCLE = "agentcc.services.mcp_shared_credential_lifecycle"
JOIN_TIMEOUT = 30


def _tick():
    """Run one tick of the retry schedule the way the worker does: the
    activity its ScheduleConfig names, in the schedule's own module."""
    from tfc.temporal.schedules import agentcc_mcp_credentials as module

    schedules = {s.schedule_id: s for s in module.AGENTCC_MCP_CREDENTIAL_SCHEDULES}
    assert RETRY_SCHEDULE in schedules, (
        "no periodic retry of shared static evictions is scheduled"
    )
    activity = getattr(module, schedules[RETRY_SCHEDULE].activity_name)
    return activity._original_func()


@pytest.fixture
def pushes():
    client = MagicMock()
    sent = []
    client.set_org_config.side_effect = lambda org_id, payload: sent.append(
        (org_id, copy.deepcopy(payload))
    )
    with patch("agentcc.services.config_push.get_gateway_client", return_value=client):
        yield sent


class Gateway:
    """The gateway's POST /-/mcp/credentials/purge: records each call."""

    def __init__(self):
        self.calls = []
        self.up = True
        self.on_call = None
        self._lock = threading.Lock()

    def purge(self, org_id, server_id, user_id=None, *, revision=None):
        with self._lock:
            self.calls.append((org_id, server_id, user_id, revision))
        if self.on_call is not None:
            return self.on_call(org_id, server_id, user_id, revision)
        return self.up


@pytest.fixture
def gateway():
    fake = Gateway()
    with patch(
        "agentcc.services.gateway_admin.mcp_credentials.purge", side_effect=fake.purge
    ):
        yield fake


def _seed(org):
    return AgentccOrgConfig.no_workspace_objects.create(
        organization=org,
        version=1,
        is_active=True,
        mcp={"servers": {"github": {"url": GITHUB_URL, "transport": "http"}}},
    )


def _create(org, value=SECRET):
    return vault.create(org, "github", kind="bearer", value=value, actor=None)


def _pending_revoke(gateway, name):
    """An org whose shared github credential was revoked while the gateway
    refused the eviction."""
    org = Organization.objects.create(name=name)
    _seed(org)
    row = _create(org).credential
    gateway.up = False
    change = vault.revoke(row, None)
    gateway.up = True
    assert change.gateway_synced is False
    row.refresh_from_db()
    assert row.gateway_purge_pending is True
    return org, row


def _shared_calls(gateway, org):
    return [c for c in gateway.calls if c[0] == str(org.pk) and c[2] is None]


@pytest.mark.integration
class TestSharedEvictionRetrySchedule:
    def test_the_schedule_runs_every_minute(self):
        from tfc.temporal.schedules import ALL_SCHEDULES

        found = [s for s in ALL_SCHEDULES if s.schedule_id == RETRY_SCHEDULE]
        assert len(found) == 1, "no periodic retry of shared static evictions"
        assert found[0].interval_seconds == 60
        assert found[0].queue == "default"

    def test_a_failed_eviction_is_retried_without_another_change(
        self, db, pushes, gateway
    ):
        org, row = _pending_revoke(gateway, "w4 sweep retried")
        gateway.calls.clear()
        result = _tick()
        row.refresh_from_db()
        assert row.gateway_purge_pending is False
        assert _shared_calls(gateway, org) == [
            (str(org.pk), "github", None, row.grant_revision)
        ]
        assert result["confirmed"] == 1 and result["pending"] == 0

    def test_an_eviction_the_gateway_still_refuses_is_kept(self, db, pushes, gateway):
        org, row = _pending_revoke(gateway, "w4 sweep kept")
        gateway.up = False
        result = _tick()
        row.refresh_from_db()
        assert row.gateway_purge_pending is True
        assert result["pending"] == 1 and result["confirmed"] == 0
        # A later tick, once the gateway answers, confirms it.
        gateway.up = True
        assert _tick()["confirmed"] == 1
        row.refresh_from_db()
        assert row.gateway_purge_pending is False

    def test_a_tick_retries_at_most_its_slot_limit(
        self, db, pushes, gateway, monkeypatch
    ):
        # The schedule first, before patching the bounds its tick reads.
        assert _tick()["slots"] == 0
        rows = [_pending_revoke(gateway, f"w4 sweep bound {i}")[1] for i in range(3)]
        monkeypatch.setattr(f"{LIFECYCLE}.SWEEP_SLOT_LIMIT", 2)
        gateway.calls.clear()
        first = _tick()
        assert len(gateway.calls) == 2
        assert (first["slots"], first["deferred"]) == (2, 1)
        assert (
            sum(
                AgentccMCPCredential.objects.get(pk=r.pk).gateway_purge_pending
                for r in rows
            )
            == 1
        )
        second = _tick()
        assert (second["slots"], second["deferred"]) == (1, 0)
        assert not AgentccMCPCredential.objects.filter(
            pk__in=[r.pk for r in rows], gateway_purge_pending=True
        ).exists()

    def test_a_tick_starts_no_slot_past_its_time_budget(
        self, db, pushes, gateway, monkeypatch
    ):
        assert _tick()["slots"] == 0
        rows = [_pending_revoke(gateway, f"w4 sweep budget {i}")[1] for i in range(3)]
        now = [1000.0]
        monkeypatch.setattr(f"{LIFECYCLE}._now", lambda: now[0])
        monkeypatch.setattr(f"{LIFECYCLE}.SWEEP_BUDGET_SECONDS", 60)

        def slow(org_id, server_id, user_id, revision):
            # Each call spends the whole budget, as a gateway timing out would.
            now[0] += 61
            return True

        gateway.calls.clear()
        gateway.on_call = slow
        result = _tick()
        assert len(gateway.calls) == 1
        assert (result["slots"], result["deferred"]) == (1, 2)
        assert (
            AgentccMCPCredential.objects.filter(
                pk__in=[r.pk for r in rows], gateway_purge_pending=True
            ).count()
            == 2
        )

    def test_a_retried_rotation_never_revokes_the_active_secret(
        self, db, pushes, gateway
    ):
        org = Organization.objects.create(name="w4 sweep rotation")
        _seed(org)
        row = _create(org).credential
        before = row.grant_revision
        gateway.up = False
        assert vault.rotate(row, SECRET_2, None).gateway_synced is False
        gateway.up = True
        row.refresh_from_db()
        assert row.gateway_purge_pending is True
        gateway.calls.clear()
        _tick()
        row.refresh_from_db()
        assert row.status == "active" and row.grant_revision > before
        revisions = [c[3] for c in _shared_calls(gateway, org)]
        assert revisions and all(before <= r < row.grant_revision for r in revisions)
        assert row.gateway_purge_pending is False

    def test_a_personal_obligation_is_left_to_its_own_cleanup(
        self, user, pushes, gateway
    ):
        org = user.organization
        _seed(org)
        change = vault.create(
            org,
            "github",
            kind="bearer",
            value=SECRET,
            scope="user",
            user=user,
            actor=user,
        )
        gateway.up = False
        vault.revoke(change.credential, user)
        gateway.up = True
        personal = AgentccMCPCredential.objects.get(pk=change.credential.pk)
        assert personal.gateway_purge_pending is True
        gateway.calls.clear()
        result = _tick()
        assert gateway.calls == []
        assert result["slots"] == 0
        personal.refresh_from_db()
        assert personal.gateway_purge_pending is True


# --- Serialized with the org's config writers ---------------------------------


def _run(target, results, name):
    try:
        results[name] = ("ok", target())
    except Exception as e:  # recorded for the assertion message
        results[name] = ("error", f"{type(e).__name__}: {e}")
    finally:
        connection.close()


def _start(target, results, name):
    thread = threading.Thread(target=_run, args=(target, results, name), name=name)
    thread.start()
    return thread


def _a_backend_waits_on_this_connection():
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM pg_locks"
            " WHERE NOT granted AND pg_backend_pid() = ANY(pg_blocking_pids(pid))"
        )
        return cursor.fetchone()[0] > 0


@pytest.fixture
def committed_org(transactional_db):
    """Committed rows, so the other thread's connection sees them; the
    transactional test flushes them afterwards."""
    org = Organization.objects.create(name="w4 sweep org lock")
    _seed(org)
    return org


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
class TestSharedEvictionRetryHoldsTheOrgLock:
    def test_a_tick_skips_an_org_a_config_writer_holds(
        self, committed_org, pushes, gateway
    ):
        # The schedule must exist before the race starts.
        assert _tick()["slots"] == 0
        org = committed_org
        row = _create(org).credential
        gateway.up = False
        vault.revoke(row, None)
        gateway.up = True
        held, release, results = threading.Event(), threading.Event(), {}

        def writer():
            with transaction.atomic():
                lock_org(org.pk)
                held.set()
                return release.wait(JOIN_TIMEOUT)

        thread = _start(writer, results, "writer")
        try:
            assert held.wait(JOIN_TIMEOUT)
            gateway.calls.clear()
            started = time.monotonic()
            result = _tick()
            # Never waits on the writer: the org is skipped this tick.
            assert time.monotonic() - started < JOIN_TIMEOUT / 2
            assert result["skipped_locked"] == 1 and result["slots"] == 0
            assert gateway.calls == []
            assert AgentccMCPCredential.objects.get(pk=row.pk).gateway_purge_pending
        finally:
            release.set()
            thread.join(JOIN_TIMEOUT)
        assert results == {"writer": ("ok", True)}, results
        assert _tick()["confirmed"] == 1
        assert not AgentccMCPCredential.objects.get(pk=row.pk).gateway_purge_pending

    def test_an_eviction_after_commit_cannot_clear_a_newer_obligation(
        self, committed_org, pushes, gateway
    ):
        """A removed server's eviction runs once its write commits. While it
        waits on the gateway, a writer grants and revokes the slot again and
        the gateway refuses that newer eviction: the older confirmation must
        not clear it. Held under the org lock, the writer waits for it; the
        unserialized one lets the writer in and then clears its obligation."""
        org = committed_org
        _create(org)
        in_purge, writer_done, results = threading.Event(), threading.Event(), {}
        newer = {}

        def on_call(org_id, server_id, user_id, revision):
            name = threading.current_thread().name
            if name == "removal":
                in_purge.set()
                deadline = time.monotonic() + JOIN_TIMEOUT
                while not (
                    writer_done.is_set() or _a_backend_waits_on_this_connection()
                ):
                    if time.monotonic() > deadline:
                        raise AssertionError("the writer neither waited nor finished")
                    time.sleep(0.02)
                return True
            # The writer's own eviction of the newer grant is refused.
            return False

        def removal():
            with transaction.atomic():
                lock_org(org.pk)
                vault.revoke_server_credentials(org, "github")
            return True

        def regrant():
            assert in_purge.wait(JOIN_TIMEOUT)
            try:
                row = _create(org, SECRET_2).credential
                newer["pk"] = row.pk
                return vault.revoke(row, None).gateway_synced
            finally:
                writer_done.set()

        gateway.on_call = on_call
        threads = [
            _start(removal, results, "removal"),
            _start(regrant, results, "writer"),
        ]
        for thread in threads:
            thread.join(JOIN_TIMEOUT)
        assert results == {"removal": ("ok", True), "writer": ("ok", False)}, results
        assert AgentccMCPCredential.objects.get(pk=newer["pk"]).gateway_purge_pending, (
            "an older eviction cleared a newer unconfirmed one"
        )
