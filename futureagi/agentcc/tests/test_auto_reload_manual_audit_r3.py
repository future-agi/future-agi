"""Manual-trigger accountability; isolated PostgreSQL, synthetic checkout trigger only.

Positive audit and audit-failure cases are behavioral RED before the view fix.
Refusal/scheduled cases preserve existing behavior. No real payment proof.
"""

from unittest.mock import patch
from uuid import uuid4

import pytest
from django.db import connections

from accounts.models.audit_log import AuditLog
from agentcc.models.auto_reload_arming import AgentccAutoReloadArming
from agentcc.models.auto_reload_intent import AgentccAutoReloadIntent
from agentcc.tests import test_auto_reload_trigger as trigger
from agentcc.tests import test_auto_reload_trigger_money_r1 as money
from agentcc.tests.test_auto_reload_arming import balance
from tfc.constants.levels import Level

pytestmark = [pytest.mark.integration, pytest.mark.django_db(transaction=True)]
admin_client = trigger.admin_client
make_client = trigger.make_client
ledger = trigger.ledger
ports = trigger.ports
ACTION = "auto_reload.manual_trigger"
AUDIT_FIELDS = (
    "pk", "organization_id", "actor_id", "action", "scope", "target_id",
    "changes", "metadata", "created_at", "updated_at",
)


def audits(organization):
    # Every call creates a fresh, primary-bound QuerySet (no evaluated cache).
    return AuditLog.no_workspace_objects.using("default").filter(
        organization=organization, action=ACTION
    )


@pytest.mark.parametrize("second_admin", [False, True], ids=["consent-actor", "other-admin"])
def test_accepted_trigger_audits_acting_user_and_intent_once(
    admin_client, make_client, organization, ports, second_admin
):
    client = make_client(organization, Level.ADMIN) if second_admin else admin_client
    actor = client.handler._force_user
    assert (actor.pk != ports.receipt.actor_id) is second_admin
    before = set(AuditLog.objects.filter(organization=organization).values_list(
        "pk", flat=True
    ))
    response = trigger.post(client)
    assert response.status_code == 200, response.content
    assert response.json()["result"] == {"status": "queued"}
    created = AuditLog.objects.filter(organization=organization).exclude(pk__in=before)
    assert created.count() == 1
    audit = created.get()
    [row] = ports.checkout.rows()
    intent = AgentccAutoReloadIntent.objects.get(
        pk=row["intent_id"], organization=organization
    )
    assert audit.action == ACTION
    assert audit.scope == "organization"
    assert audit.organization_id == organization.pk
    assert audit.target_id == organization.pk  # UUID target; intent is an org:epoch key.
    assert audit.metadata == {"intent_id": intent.pk}
    assert audit.actor_id == actor.pk
    assert audit.changes == {}
    assert audit._state.db == intent._state.db == "default"
    assert audits(organization).count() == 1
    ports.receipt.refresh_from_db()
    assert ports.receipt.actor_id == admin_client.handler._force_user.pk


@pytest.mark.parametrize("new_attempt", [False, True], ids=["same-retry", "new-click"])
def test_duplicate_writes_no_second_audit_or_mutation(
    admin_client, make_client, organization, ports, new_attempt
):
    response = trigger.post(admin_client)
    assert response.status_code == 200, response.content
    assert response.json()["result"] == {"status": "queued"}
    assert audits(organization).count() == 1
    original = audits(organization).values(*AUDIT_FIELDS).get()
    rows = ports.checkout.rows()
    # Another admin must not overwrite the original event's acting user.
    client = make_client(organization, Level.ADMIN) if new_attempt else admin_client
    attempt = str(uuid4()) if new_attempt else trigger.INTENT
    retry = trigger.post(client, attempt)
    assert retry.status_code == 200, retry.content
    assert retry.json()["result"] == {"status": "duplicate"}
    assert audits(organization).count() == 1
    assert audits(organization).values(*AUDIT_FIELDS).get() == original
    assert ports.checkout.rows() == rows
    assert len(rows) == 1
    assert AgentccAutoReloadIntent.objects.filter(organization=organization).count() == 1


@pytest.mark.parametrize("refusal", ["consent", "above-threshold", "payment-method"])
def test_409_trigger_creates_no_manual_audit_or_intent(
    admin_client, organization, ledger, ports, refusal
):
    if refusal == "consent":
        ports.receipt.delete()
        code = "auto_reload_consent_required"
    elif refusal == "above-threshold":
        balance(ledger, organization, 11_000_000)
        code = "auto_reload_threshold_not_reached"
    else:
        ports.checkout.owned_methods[trigger.binding.METHOD] = uuid4()
        code = "auto_reload_payment_method_mismatch"
    response = trigger.post(admin_client)
    assert response.status_code == 409, response.content
    assert response.json()["code"] == code
    assert audits(organization).count() == 0
    assert not AgentccAutoReloadIntent.objects.filter(organization=organization).exists()
    assert not ports.checkout.rows()


@pytest.mark.parametrize("persist_first", [False, True], ids=["audit-insert-fails", "after-audit-insert"])
def test_audit_failure_rolls_back_intent_outbox_and_audit(
    admin_client, organization, ports, persist_first
):
    save = AuditLog.save
    observed = {}

    def fail(audit, *args, **kwargs):
        if audit.action != ACTION:
            return save(audit, *args, **kwargs)
        using = kwargs.get("using")
        intent = AgentccAutoReloadIntent.objects.using("default").get(
            organization=organization
        )
        observed.update(
            using=using,
            in_transaction=connections["default"].in_atomic_block,
            intent_id=intent.pk,
            rows=ports.checkout.rows(),
        )
        if persist_first:
            save(audit, *args, **kwargs)
            observed["persisted"] = audits(organization).count()
        raise RuntimeError("synthetic private audit failure")

    with patch.object(AuditLog, "save", new=fail):
        response = trigger.post(admin_client)
    assert response.status_code == 503, response.content
    assert response.json()["code"] == "billing_unavailable"
    assert "synthetic private audit failure" not in response.content.decode()
    assert observed["using"] == "default"
    assert observed["in_transaction"]
    assert observed["intent_id"] == f"{organization.pk}:0"
    assert len(observed["rows"]) == 1
    if persist_first:
        assert observed["persisted"] == 1
    assert audits(organization).count() == 0
    assert not AgentccAutoReloadIntent.objects.filter(organization=organization).exists()
    assert not AgentccAutoReloadArming.objects.filter(organization=organization).exists()
    assert not ports.checkout.rows()
    # A rolled-back attempt must not poison a retry or advance its epoch.
    retry = trigger.post(admin_client)
    assert retry.status_code == 200, retry.content
    assert retry.json()["result"] == {"status": "queued"}
    assert audits(organization).count() == 1
    assert audits(organization).get().metadata["intent_id"] == observed["intent_id"]
    assert len(ports.checkout.rows()) == 1


def test_adapter_failure_after_intent_writes_no_manual_audit(
    admin_client, organization, ports
):
    observed = {}

    def fail():
        observed["intent"] = AgentccAutoReloadIntent.objects.get(
            organization=organization
        ).pk
        observed["rows"] = ports.checkout.rows()
        raise RuntimeError("synthetic outbox failure")

    ports.checkout.after_insert = fail
    response = trigger.post(admin_client)
    assert response.status_code == 503, response.content
    assert observed["intent"] == f"{organization.pk}:0"
    assert len(observed["rows"]) == 1
    assert audits(organization).count() == 0
    assert not AgentccAutoReloadIntent.objects.filter(organization=organization).exists()
    assert not ports.checkout.rows()


def test_postgresql_contending_triggers_leave_one_acting_user_audit(
    admin_client, make_client, organization, ports
):
    # Reuse the unchanged r1 two-connection/pg_blocking_pids proof, including retry.
    money.test_p2_concurrent_triggers_and_retry_queue_exactly_once(
        admin_client, make_client, organization, ports
    )
    assert audits(organization).count() == 1
    audit = audits(organization).get()
    assert audit.actor_id == admin_client.handler._force_user.pk
    assert audit.target_id == organization.pk
    assert audit.metadata == {"intent_id": ports.checkout.rows()[0]["intent_id"]}


def test_scheduled_claim_writes_no_manual_trigger_audit(organization, ledger, ports):
    # Real existing public claim seam, as pinned by test_auto_reload_consent.
    # Do not inspect/change its protected implementation or call a payment provider.
    type(ports.account).objects.filter(pk=ports.account.pk).update(
        autoreload_daily_count_cap=2,
        autoreload_daily_amount_cap_micros=50_000_000,
    )
    claim = ledger.claim_auto_topup(organization)
    assert claim is not None
    assert claim.amount_micros == ports.receipt.amount_micros == 25_000_000
    assert ledger.claim_auto_topup(organization) is None
    assert audits(organization).count() == 0
    ports.load_checkout.assert_not_called()
    assert not ports.checkout.calls
    assert not ports.checkout.rows()
