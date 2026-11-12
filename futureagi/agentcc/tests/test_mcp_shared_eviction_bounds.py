"""A retry backlog cannot monopolize one org's config-write lock."""

import threading
import time
from unittest.mock import patch

import pytest
from django.db import transaction

from accounts.models import Organization
from agentcc.models.mcp_credential import AgentccMCPCredential as Credential
from agentcc.services import mcp_shared_credential_lifecycle as lifecycle
from agentcc.services.org_config_writer import lock_org, write_config_version
from agentcc.tests import test_mcp_shared_eviction_retry as fixtures
from agentcc.tests.test_mcp_shared_eviction_retry import _pending_revoke, _tick

gateway = fixtures.gateway
pushes = fixtures.pushes


def _backlog(gateway, name):
    org, row = _pending_revoke(gateway, name)
    for i in range(6):
        Credential.objects.create(
            organization=org,
            server_id=f"pending-{i}",
            kind="bearer",
            scope="shared",
            status="revoked",
            encrypted_secret=row.encrypted_secret,
            grant_revision=1,
            gateway_purge_pending=True,
        )
    return org, row


@pytest.mark.integration
def test_one_tick_caps_each_org_at_three_slots(db, pushes, gateway):
    org, _ = _backlog(gateway, "w4 per org cap")
    other, _ = _pending_revoke(gateway, "w4 other org gets a turn")
    gateway.calls.clear()
    with patch.object(lifecycle.random, "shuffle", lambda slots: None):
        result = _tick()
    assert sum(c[0] == str(org.pk) for c in gateway.calls) == 3, (
        "one org exceeded three slots per tick"
    )
    assert sum(c[0] == str(other.pk) for c in gateway.calls) == 1
    assert (result["slots"], result["deferred"]) == (4, 4)
    assert _tick()["slots"] == 3
    assert _tick()["slots"] == 1
    assert not Credential.objects.filter(gateway_purge_pending=True).exists()


@pytest.mark.integration
def test_in_request_retry_is_bounded_and_reports_deferred(db, pushes, gateway):
    org, row = _backlog(gateway, "w4 request cap")
    gateway.calls.clear()
    with transaction.atomic():
        lock_org(org.pk)
        synced = lifecycle.evict(row)
    assert len(gateway.calls) <= 4, "request retried an unbounded backlog"
    assert gateway.calls[0][1] == "github"
    assert synced is False, "deferred obligations reported as fully synced"
    assert (
        Credential.objects.filter(organization=org, gateway_purge_pending=True).count()
        == 3
    )
    assert _tick()["confirmed"] == 3


@pytest.mark.integration
def test_unsupported_revision_keeps_durable_obligation_until_capable_retry(db, pushes):
    from accounts.models import Organization
    from agentcc.services import mcp_credentials as vault
    from agentcc.services.gateway_client import GatewayClientError

    org = Organization.objects.create(name="w4 unsupported revision")
    fixtures._seed(org)
    row = fixtures._create(org).credential
    with patch(
        "agentcc.services.gateway_admin.mcp_credentials.GatewayAdminClient"
    ) as client:
        client.return_value.request.side_effect = GatewayClientError(
            "unsupported", status_code=501, error_code="revision_revocation_unsupported"
        )
        assert vault.revoke(row, None).gateway_synced is False
        row.refresh_from_db()
        assert row.gateway_purge_pending
        assert _tick()["pending"] == 1
        row.refresh_from_db()
        assert row.gateway_purge_pending
        client.return_value.request.side_effect = None
        assert _tick()["confirmed"] == 1
        row.refresh_from_db()
        assert not row.gateway_purge_pending


def _removal_backlog(count):
    org = Organization.objects.create(name="w4 after-commit retry cap")
    fixtures._seed(org)
    row = fixtures._create(org).credential
    Credential.objects.bulk_create(
        [
            Credential(
                organization=org,
                server_id=f"backlog-{i}",
                kind="bearer",
                scope="shared",
                status="revoked",
                encrypted_secret=row.encrypted_secret,
                grant_revision=1,
                gateway_purge_pending=True,
            )
            for i in range(count)
        ]
    )
    return org, row


@pytest.mark.integration
@pytest.mark.parametrize("confirmed", [False, True])
def test_after_commit_retry_respects_the_in_request_cap(
    db, pushes, gateway, django_capture_on_commit_callbacks, confirmed
):
    """Adopt the independent v8 regression; deferred work belongs to the schedule."""
    org, row = _removal_backlog(10)
    gateway.calls.clear()
    gateway.up = confirmed
    with django_capture_on_commit_callbacks(execute=True):
        write_config_version(org, {"mcp": {}}, mode="replace_sections")
        assert gateway.calls == []
    removed = [c for c in gateway.calls if c[1] == "github"]
    backlog = [c for c in gateway.calls if c[1].startswith("backlog-")]
    assert removed == [(str(org.pk), "github", None, row.grant_revision)]
    assert len(backlog) == lifecycle.REQUEST_RETRY_SLOT_LIMIT, (
        "after-commit retry exceeded the in-request cap"
    )
    assert gateway.calls[0] == removed[0], "backlog ran before the write's own slot"
    pending = Credential.objects.filter(organization=org, gateway_purge_pending=True)
    assert pending.count() == (7 if confirmed else 11)
    gateway.up = True
    for _ in range(4):
        _tick()
    assert not pending.exists(), "deferred obligations were not drained by the schedule"


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
def test_after_commit_slow_backlog_releases_the_config_write_lock(
    transactional_db, pushes, gateway
):
    """A slow unavailable gateway must not serialize a writer behind the backlog."""
    org, _ = _removal_backlog(60)
    in_purge = threading.Event()
    results, held_seconds = {}, []
    first_call = True

    def slow_unavailable(org_id, server_id, user_id, revision):
        nonlocal first_call
        if first_call:
            first_call = False
            in_purge.set()
            # Prove the competing writer really waits on this org's row lock.
            deadline = time.monotonic() + 10
            while not fixtures._a_backend_waits_on_this_connection():
                assert time.monotonic() < deadline, "config writer did not block"
                time.sleep(0.01)
        time.sleep(0.1)
        return False

    def removal():
        write_config_version(org, {"mcp": {}}, mode="replace_sections")
        return True

    def writer():
        assert in_purge.wait(10), "removal never started its after-commit eviction"
        started = time.monotonic()
        with transaction.atomic():
            lock_org(org.pk)
            held_seconds.append(time.monotonic() - started)
        return True

    gateway.calls.clear()
    gateway.on_call = slow_unavailable
    threads = [
        fixtures._start(removal, results, "removal"),
        fixtures._start(writer, results, "writer"),
    ]
    for thread in threads:
        thread.join(fixtures.JOIN_TIMEOUT)
    assert not any(thread.is_alive() for thread in threads)
    assert results == {"removal": ("ok", True), "writer": ("ok", True)}, results
    # Four 100 ms calls plus generous SQL/scheduling slack, not 61 calls.
    assert held_seconds[0] < 3, "after-commit backlog held the config lock too long"
    assert len(gateway.calls) == 1 + lifecycle.REQUEST_RETRY_SLOT_LIMIT
    assert gateway.calls[0][1] == "github"
    assert (
        Credential.objects.filter(organization=org, gateway_purge_pending=True).count()
        == 61
    )
