"""Regression proofs for customer fences and the complete reconnect journal."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from importlib import import_module
from threading import Event
from unittest.mock import MagicMock, patch

import pytest
from django.core.management.base import CommandError
from django.db import connection, connections, transaction
from django.utils import timezone

from accounts.models import OrgApiKey
from accounts.models.retirement_manifest import RetirementManifest
from accounts.services.retirement_733 import run_retirement, unjournaled_connections
from accounts.services.retirement_rows import fingerprint, journal, reverse_rows
from agentcc.models import AgentccAPIKey
from agentcc.services.auth_bridge import revoke_key
from agentcc.services.gateway_client import GatewayClientError
from mcp_server.models import MCPConnection

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def schema_fence_for_model_only_suites(request, db):
    # The broad paired runner creates schemas with --nomigrations on both sides.
    # Install this migration's real SQL there, not a mocked signal/trigger. The
    # decisive focused and composed runs apply all migrations normally.
    if request.config.getoption("nomigrations", default=False):
        migration = import_module("accounts.migrations.0035_gateway_retirement_fence")
        with connection.cursor() as cursor:
            cursor.execute(migration.REVERSE)
            cursor.execute(migration.FORWARD)


@pytest.fixture
def gateway_key(user):
    return AgentccAPIKey._base_manager.create(
        organization=user.organization,
        user=user,
        name="console-batch-test-only-review",
        gateway_key_id="test_only_review_fence",
        status="active",
    )


@pytest.fixture
def forward(gateway_key):
    # Unit adapter boundary only. The scratch-container test uses real L4.
    def retire(adapter, *, dry_run):
        if not dry_run:
            with transaction.atomic():
                key = AgentccAPIKey._base_manager.select_for_update().get(
                    pk=gateway_key.pk
                )
                prior = {"status": key.status, "expires_at": None}
                revoke_key(key)
                adapter.record(key=key, prior_state=prior, confirmed=True)
        return {"selected": 1, "pending": []}

    with (
        patch(
            "accounts.services.retirement_733.gateway_retirement", return_value=retire
        ),
        patch(
            "accounts.services.retirement_733.repush_configs", return_value=(0, 0, {})
        ),
        patch(
            "agentcc.services.auth_bridge.get_gateway_client", return_value=MagicMock()
        ),
    ):
        yield run_retirement


@pytest.mark.parametrize("retry", [False, True])
@pytest.mark.parametrize("gateway_failed", [False, True])
def test_customer_revoke_after_retirement_is_never_reversed(
    gateway_key, forward, retry, gateway_failed
):
    forward()
    client = MagicMock()
    if gateway_failed:
        client.revoke_key.side_effect = GatewayClientError("test-only unavailable")
    with patch("agentcc.services.auth_bridge.get_gateway_client", return_value=client):
        revoke_key(gateway_key)
    if retry:
        forward()
    for dry_run in (True, False):
        result = reverse_rows(dry_run=dry_run)
        assert result["superseded"] == 1 and result["restored"] == 0
    gateway_key.refresh_from_db()
    assert gateway_key.status == "revoked"


def test_customer_fence_does_not_depend_on_clock_changes(gateway_key, forward):
    with patch("django.utils.timezone.now", return_value=timezone.now()):
        forward()
        revoke_key(gateway_key)
        forward()
        assert reverse_rows()["superseded"] == 1
    gateway_key.refresh_from_db()
    assert gateway_key.status == "revoked"


def test_customer_edit_after_retirement_survives_retry(gateway_key, forward):
    forward()
    gateway_key.name = "customer-renamed"
    gateway_key.save(update_fields=["name", "updated_at"])
    forward()
    assert reverse_rows()["superseded"] == 1
    gateway_key.refresh_from_db()
    assert gateway_key.name == "customer-renamed" and gateway_key.status == "revoked"


def test_retirement_retries_still_restore_original_state(gateway_key, forward):
    forward()
    forward()
    assert (
        RetirementManifest.objects.get(service="gateway").prior_state["status"]
        == "active"
    )
    assert reverse_rows()["restored"] == 1
    gateway_key.refresh_from_db()
    assert gateway_key.status == "active"
    forward()
    assert reverse_rows()["restored"] == 1


def test_unfenced_historical_gateway_journal_is_not_retroactively_trusted(
    gateway_key, forward
):
    forward()
    manifest = RetirementManifest.objects.get(service="gateway")
    manifest.applied_state.pop("customer_change_fence", None)
    manifest.save(update_fields=["applied_state"])
    forward()
    assert reverse_rows()["superseded"] == 1
    gateway_key.refresh_from_db()
    assert gateway_key.status == "revoked"


def test_retirement_exception_does_not_exempt_later_customer_write(
    gateway_key, forward
):
    forward()
    with patch(
        "accounts.services.retirement_733.gateway_retirement",
        return_value=MagicMock(side_effect=RuntimeError("test-only failure")),
    ):
        with pytest.raises(RuntimeError, match="test-only failure"):
            forward()
    revoke_key(gateway_key)
    assert reverse_rows()["superseded"] == 1


@pytest.mark.django_db(transaction=True)
def test_retirement_context_does_not_exempt_another_connection(gateway_key, forward):
    from accounts.services.retirement_fence import gateway_retirement_writes

    def customer_revoke():
        try:
            key = AgentccAPIKey._base_manager.get(pk=gateway_key.pk)
            revoke_key(key)
        finally:
            connections.close_all()

    forward()
    with gateway_retirement_writes(), ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(customer_revoke).result(timeout=10)
    assert reverse_rows()["superseded"] == 1


def test_direct_database_revoke_is_also_fenced(gateway_key, forward):
    forward()
    # Same value and no timestamp rewrite: model signals/fingerprints cannot
    # detect this customer action. The database trigger must record it.
    AgentccAPIKey._base_manager.filter(pk=gateway_key.pk).update(status="revoked")
    forward()
    assert reverse_rows()["superseded"] == 1
    gateway_key.refresh_from_db()
    assert gateway_key.status == "revoked"


@pytest.mark.django_db(transaction=True)
def test_concurrent_customer_commit_wins_over_reverse(gateway_key, forward):
    saved, release = Event(), Event()

    def customer():
        try:
            with transaction.atomic():
                AgentccAPIKey._base_manager.filter(pk=gateway_key.pk).update(
                    status="revoked"
                )
                saved.set()
                assert release.wait(timeout=10)
        finally:
            connections.close_all()

    def reverse():
        try:
            return reverse_rows()
        finally:
            connections.close_all()

    forward()
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending_customer = pool.submit(customer)
        try:
            assert saved.wait(timeout=10)
            pending_reverse = pool.submit(reverse)
        finally:
            release.set()
        pending_customer.result(timeout=10)
        result = pending_reverse.result(timeout=10)
    assert result["superseded"] == 1 and result["restored"] == 0
    gateway_key.refresh_from_db()
    assert gateway_key.status == "revoked"


def test_rolled_back_customer_action_does_not_supersede(gateway_key, forward):
    forward()
    with pytest.raises(RuntimeError), transaction.atomic():
        revoke_key(gateway_key)
        raise RuntimeError("customer transaction rolled back")
    assert reverse_rows()["restored"] == 1


@pytest.fixture
def reconnect_journal(user, workspace):
    key = OrgApiKey._base_manager.create(
        organization=user.organization,
        type="mcp",
        enabled=False,
        retired_reason="733",
        prior_enabled=True,
    )
    connection = MCPConnection._base_manager.create(
        user=user,
        organization=user.organization,
        workspace=workspace,
        api_key=key,
        is_active=False,
        reconnect_required=True,
    )
    with transaction.atomic():
        manifest = journal(
            "mcp",
            connection,
            "reconnect",
            {"is_active": True, "reconnect_required": False},
            fingerprint(connection, ("is_active", "reconnect_required")),
            confirmed=True,
        )
    return connection, manifest


@pytest.mark.parametrize(
    "damage",
    [
        "empty-applied",
        "unconfirmed",
        "missing-updated_at",
        "invalid-updated_at",
        "missing-deleted",
        "missing-user_id",
        "missing-organization_id",
        "missing-api_key_id",
        "wrong-is_active",
        "wrong-reconnect_required",
        "string-prior",
        "numeric-prior",
        "invalid-user_id",
        "numeric-deleted",
        "list-applied",
    ],
)
def test_incomplete_reconnect_journal_refuses_before_any_mutation(
    reconnect_journal, damage
):
    connection, manifest = reconnect_journal
    if damage == "empty-applied":
        manifest.applied_state = {}
    elif damage == "list-applied":
        manifest.applied_state = []
    elif damage == "unconfirmed":
        manifest.confirmed = False
    elif damage.startswith("missing-"):
        manifest.applied_state.pop(damage.removeprefix("missing-"))
    elif damage == "invalid-updated_at":
        manifest.applied_state["updated_at"] = "not-a-date"
    elif damage == "wrong-is_active":
        manifest.applied_state["is_active"] = True
    elif damage == "wrong-reconnect_required":
        manifest.applied_state["reconnect_required"] = False
    elif damage == "string-prior":
        manifest.prior_state["is_active"] = "true"
    elif damage == "numeric-prior":
        manifest.prior_state["is_active"] = 1
    elif damage == "invalid-user_id":
        manifest.applied_state["user_id"] = "not-an-id"
    elif damage == "numeric-deleted":
        manifest.applied_state["deleted"] = 0
    manifest.save()
    before = deepcopy(manifest.applied_state)
    assert unjournaled_connections() == 1
    with patch(
        "accounts.services.retirement_733.gateway_retirement", return_value=MagicMock()
    ):
        for reverse in (False, True):
            with pytest.raises(CommandError, match="prior state is unavailable for 1"):
                run_retirement(reverse=reverse)
    connection.refresh_from_db()
    manifest.refresh_from_db()
    assert connection.reconnect_required and not connection.is_active
    assert manifest.applied_state == before and manifest.reverted_at is None


def test_valid_but_customer_changed_connection_is_superseded_not_missing(
    reconnect_journal,
):
    connection, _ = reconnect_journal
    connection.save(update_fields=["updated_at"])
    assert unjournaled_connections() == 0
    assert reverse_rows()["superseded"] == 1
