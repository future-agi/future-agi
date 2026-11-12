"""Static RED/green predictions for the manual-trigger money contract.

Run only in an isolated PostgreSQL with the existing synthetic checkout-trigger adapter.
Rows here are queued reload intents, NOT proof of provider charges or credits.
ADDENDUM-1 keeps the endpoint enabled and uses threshold epochs, not cooldowns.
Superseded originals are archived in the packet's out/ record. No gate bypass.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Event
from time import monotonic
from unittest.mock import patch
from uuid import uuid4

import pytest
from django.db import close_old_connections, connection, connections

from accounts.models.audit_log import AuditLog
from agentcc.models.auto_reload_intent import AgentccAutoReloadIntent
from agentcc.tests import test_auto_reload_trigger as trigger
from agentcc.tests.auto_reload_event_helpers import complete, credit
from agentcc.tests.test_auto_reload_arming import balance
from tfc.constants.levels import Level

pytestmark = [pytest.mark.integration, pytest.mark.django_db(transaction=True)]
admin_client = trigger.admin_client
make_client = trigger.make_client
ledger = trigger.ledger
ports = trigger.ports


@pytest.mark.parametrize(
    "injected",
    [
        {"amount_micros": 1},
        {"amount_usd": "0.01"},
        {"currency": "EUR"},
        {"target": "synthetic-foreign-org"},
        {"organization_id": "00000000-0000-0000-0000-000000000001"},
        {"payment_method_id": "pm_synthetic_foreign"},
    ],
)
def test_p1_request_money_and_target_are_refused(admin_client, ports, injected):
    """Prediction: PASS at base and after fallback; zero queued charges."""
    response = admin_client.post(
        trigger.URL, {"intent_id": trigger.INTENT, **injected}, format="json"
    )
    assert response.status_code == 400, response.content
    ports.load_checkout.assert_not_called()
    assert not ports.checkout.calls
    assert not ports.checkout.rows()


def test_p1_reload_amount_is_exactly_the_recorded_configuration(
    admin_client, organization, ports
):
    """Prediction: PASS at base; enabled-path gate remains RED under fallback."""
    response = trigger.post(admin_client)
    assert response.status_code == 200, response.content
    assert response.json()["result"] == {"status": "queued"}
    [row] = ports.checkout.rows()
    assert row["org_id"] == str(organization.pk)
    assert row["consent_receipt_id"] == str(ports.receipt.pk)
    assert row["amount_micros"] == ports.account.autoreload_amount_micros
    assert row["amount_micros"] == ports.receipt.amount_micros == 25_000_000
    assert row["payment_method_id"] == ports.account.autoreload_payment_method_id


def test_p1_no_consent_refuses_without_a_queued_charge(admin_client, ports):
    """Prediction: PASS at base and after fallback; no adapter invocation."""
    ports.receipt.delete()
    response = trigger.post(admin_client)
    assert response.status_code == 409, response.content
    ports.load_checkout.assert_not_called()
    assert not ports.checkout.calls
    assert not ports.checkout.rows()


def test_p2_concurrent_triggers_and_retry_queue_exactly_once(
    admin_client, make_client, organization, ports
):
    """Prediction: PASS at base on PostgreSQL; RED while endpoint is disabled."""
    assert connection.vendor == "postgresql", "real row-lock proof requires PostgreSQL"
    second_client = make_client(organization, Level.ADMIN)
    inserted, lock_attempted, release = Event(), Event(), Event()
    pids = {}

    def pause_first_insert():
        inserted.set()
        assert release.wait(15), "first transaction was not released"

    def worker(client, name, request_id):
        close_old_connections()
        db = connections["default"]
        try:
            with db.cursor() as cursor:
                cursor.execute("SET statement_timeout = '20s'")
                cursor.execute("SET lock_timeout = '15s'")
                cursor.execute("SELECT pg_backend_pid()")
                pids[name] = cursor.fetchone()[0]

            def observe(execute, sql, params, many, context):
                if (
                    name == "second"
                    and "FOR UPDATE" in sql
                    and ports.account._meta.db_table in sql
                ):
                    lock_attempted.set()
                return execute(sql, params, many, context)

            with db.execute_wrapper(observe):
                return trigger.post(client, request_id)
        finally:
            db.close()

    ports.checkout.after_insert = pause_first_insert
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(worker, admin_client, "first", trigger.INTENT)
        try:
            assert inserted.wait(10), "first request did not queue a reload"
            second = executor.submit(worker, second_client, "second", str(uuid4()))
            assert lock_attempted.wait(10), "second request did not attempt account lock"
            deadline = monotonic() + 5
            while True:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_blocking_pids(%s)", [pids["second"]])
                    blockers = cursor.fetchone()[0]
                if pids["first"] in blockers:
                    break
                assert monotonic() < deadline, "real database blocking not observed"
                release.wait(0.01)
            assert not second.done()
        finally:
            release.set()
        responses = [first.result(timeout=20), second.result(timeout=20)]
    assert all(response.status_code == 200 for response in responses)
    assert sorted(response.json()["result"]["status"] for response in responses) == [
        "duplicate", "queued"
    ]
    first_rows = ports.checkout.rows()
    retry = trigger.post(admin_client, trigger.INTENT)
    assert retry.status_code == 200, retry.content
    assert retry.json()["result"] == {"status": "duplicate"}
    assert ports.checkout.rows() == first_rows
    assert len(first_rows) == 1
    assert len({call["intent_id"] for call in ports.checkout.calls}) == 1
    assert AgentccAutoReloadIntent.objects.filter(
        organization=organization, state="queued"
    ).count() == 1


def test_p3_threshold_epoch_deduplicates_and_rearms_without_cooldown(
    admin_client, organization, ledger, ports
):
    """Prediction: GREEN at base/head; new epochs need no elapsed-time gate."""
    now = datetime(2026, 11, 8, tzinfo=UTC)
    with patch("django.utils.timezone.now", return_value=now):
        first = trigger.post(admin_client)
        assert first.status_code == 200, first.content
        assert first.json()["result"] == {"status": "queued"}
        prior = ports.checkout.rows()
        response = trigger.post(admin_client, str(uuid4()))
        assert response.status_code == 200, response.content
        assert response.json()["result"] == {"status": "duplicate"}
        assert ports.checkout.rows() == prior
        assert len(prior) == 1
        credit(ports, ledger, organization, 11_000_000)
        complete(ports, "credited")
        calls = len(ports.checkout.calls)
        above = trigger.post(admin_client, str(uuid4()))
        assert above.status_code == 409, above.content
        assert above.json()["code"] == "auto_reload_threshold_not_reached"
        assert len(ports.checkout.calls) == calls
        assert ports.checkout.rows() == prior
        balance(ledger, organization, 9_000_000)
        second = trigger.post(admin_client)
        assert second.status_code == 200, second.content
        assert second.json()["result"] == {"status": "queued"}
        assert len(ports.checkout.rows()) == 2
        assert len({row["intent_id"] for row in ports.checkout.rows()}) == 2


def test_p4_exactly_one_audit_names_trigger_actor_not_consent_actor(
    admin_client, make_client, organization, ports
):
    """Prediction: FAIL at base (no audit); re-enable gate under fallback."""
    acting_client = make_client(organization, Level.ADMIN)
    actor = acting_client.handler._force_user
    assert actor.pk != ports.receipt.actor_id
    before = set(AuditLog.objects.filter(organization=organization).values_list(
        "pk", flat=True
    ))
    response = trigger.post(acting_client)
    assert response.status_code == 200, response.content
    created = AuditLog.objects.filter(organization=organization).exclude(pk__in=before)
    assert created.count() == 1
    [audit] = list(created)
    assert audit.actor_id == actor.pk
    [row] = ports.checkout.rows()
    intent = AgentccAutoReloadIntent.objects.get(
        pk=row["intent_id"], organization=organization
    )
    assert audit.action == "auto_reload.manual_trigger"
    assert audit.scope == "organization"
    assert audit.organization_id == organization.pk
    # The audit target is a UUID; the server intent key is an org:epoch string.
    assert audit.target_id == organization.pk
    assert audit.metadata["intent_id"] == intent.pk
    audit_fields = (
        "pk", "organization_id", "actor_id", "action", "scope", "target_id",
        "changes", "metadata", "created_at", "updated_at",
    )
    original_audit = created.values(*audit_fields).get()
    retry = trigger.post(acting_client)
    assert retry.status_code == 200, retry.content
    assert retry.json()["result"] == {"status": "duplicate"}
    # Never count the QuerySet whose result cache list(created) populated above.
    created = AuditLog.objects.filter(organization=organization).exclude(pk__in=before)
    assert created.count() == 1
    assert created.values(*audit_fields).get() == original_audit
    assert len(ports.checkout.rows()) == 1
    assert ports.checkout.rows() == [row]


def test_enabled_trigger_queues_through_ledger_and_checkout(admin_client, ports):
    """Prediction: GREEN at base/head; containment is not the feature contract."""
    with patch.object(trigger.views, "_ledger", return_value=ports.checkout.ledger) as load:
        response = trigger.post(admin_client)
    assert response.status_code == 200, response.content
    assert response.json()["result"] == {"status": "queued"}
    load.assert_called_once()
    ports.load_checkout.assert_called_once()
    assert len(ports.checkout.calls) == 1
    [row] = ports.checkout.rows()
    assert AgentccAutoReloadIntent.objects.get().pk == row["intent_id"]


def test_enabled_concurrent_clicks_and_retry_queue_one_event(
    admin_client, make_client, organization, ports
):
    """Prediction: GREEN at base/head; all callers share one server intent."""
    second_client = make_client(organization, Level.ADMIN)
    start = Event()

    def worker(client, request_id):
        close_old_connections()
        try:
            assert start.wait(10)
            return trigger.post(client, request_id)
        finally:
            connections["default"].close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(worker, admin_client, trigger.INTENT)
        second = executor.submit(worker, second_client, str(uuid4()))
        start.set()
        responses = [first.result(timeout=20), second.result(timeout=20)]
    responses.append(trigger.post(admin_client, trigger.INTENT))
    for response in responses:
        assert response.status_code == 200, response.content
    assert sorted(response.json()["result"]["status"] for response in responses) == [
        "duplicate", "duplicate", "queued"
    ]
    assert ports.load_checkout.call_count == 3
    assert len(ports.checkout.calls) == 3
    [row] = ports.checkout.rows()
    assert AgentccAutoReloadIntent.objects.get().pk == row["intent_id"]
