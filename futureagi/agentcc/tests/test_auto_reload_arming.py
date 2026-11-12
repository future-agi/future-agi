"""Logical reload events, using real account locks and a synthetic checkout outbox."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event
from time import monotonic
from uuid import uuid4

import pytest
from django.db import close_old_connections, connection, connections, transaction

from agentcc.tests import test_auto_reload_trigger as trigger
from agentcc.tests.auto_reload_event_helpers import complete, credit
from tfc.constants.levels import Level

pytestmark = [pytest.mark.integration, pytest.mark.django_db(transaction=True)]
admin_client = trigger.admin_client
make_client = trigger.make_client
ledger = trigger.ledger
ports = trigger.ports


def state(ports):
    # Lazy import lets the r2 RED tree collect and reach the behavior assertion.
    from agentcc.models.auto_reload_arming import AgentccAutoReloadArming

    return AgentccAutoReloadArming.objects.get(
        organization_id=ports.account.organization_id
    )


def balance(ledger, organization, target):
    """Use the real ledger's public adjustment API in the isolated test DB."""
    difference = target - ledger.ledger_balance(organization).balance_micros
    if difference:
        ledger.adjust(
            organization,
            difference,
            idempotency_key=f"arming-test:{uuid4()}",
            actor=None,
            reason="synthetic threshold transition",
        )
    assert ledger.ledger_balance(organization).balance_micros == target


def test_further_debits_below_threshold_keep_one_queued_intent(
    admin_client, ports, ledger, organization
):
    balance(ledger, organization, 12_000_000)
    balance(ledger, organization, 9_000_000)
    first = trigger.post(admin_client)
    assert first.status_code == 200, first.content
    assert first.json()["result"] == {"status": "queued"}
    for target in (8_000_000, 7_000_000, 6_000_000):
        balance(ledger, organization, target)
        response = trigger.post(admin_client, str(uuid4()))
        assert response.status_code == 200, response.content
        assert response.json()["result"] == {"status": "duplicate"}
        assert len(ports.checkout.rows()) == 1
    assert state(ports).epoch == 0
    assert len({call["intent_id"] for call in ports.checkout.calls}) == 1


def test_observed_rearm_then_new_crossing_has_new_intent(
    admin_client, ports, ledger, organization
):
    assert trigger.post(admin_client).json()["result"] == {"status": "queued"}
    credit(ports, ledger, organization, 11_000_000)
    complete(ports, "credited")
    above = trigger.post(admin_client, str(uuid4()))
    assert above.status_code == 409, above.content
    assert above.json()["code"] == "auto_reload_threshold_not_reached"
    assert state(ports).epoch == 1
    assert not state(ports).below_threshold
    # Remaining above does not mint an epoch on each observation.
    assert trigger.post(admin_client).status_code == 409
    assert state(ports).epoch == 1
    assert len(ports.checkout.rows()) == 1
    balance(ledger, organization, 9_000_000)
    second = trigger.post(admin_client)  # even the original UUID has no authority
    assert second.status_code == 200, second.content
    assert second.json()["result"] == {"status": "queued"}
    assert trigger.post(admin_client, str(uuid4())).json()["result"] == {
        "status": "duplicate"
    }
    assert len(ports.checkout.rows()) == 2
    assert len({row["intent_id"] for row in ports.checkout.rows()}) == 2
    assert state(ports).epoch == 1


def test_exact_threshold_does_not_rearm(admin_client, ports, ledger, organization):
    assert trigger.post(admin_client).json()["result"] == {"status": "queued"}
    balance(ledger, organization, 10_000_000)
    response = trigger.post(admin_client, str(uuid4()))
    assert response.json()["result"] == {"status": "duplicate"}
    assert state(ports).epoch == 0
    assert len(ports.checkout.rows()) == 1


def test_balance_writer_observation_survives_an_unobserved_new_crossing(
    admin_client, ports, ledger, organization
):
    """Trusted credit/outcome hooks qualify the join, not the protected writer."""
    assert trigger.post(admin_client).json()["result"] == {"status": "queued"}
    _, _, _, observed = credit(ports, ledger, organization, 11_000_000)
    assert observed.epoch == 1
    complete(ports, "credited")
    # No HTTP observation above threshold. Both hooks must be called in the
    # authoritative transaction; an observed level alone is no longer authority.
    balance(ledger, organization, 9_000_000)
    assert trigger.post(admin_client, str(uuid4())).json()["result"] == {
        "status": "queued"
    }
    assert len(ports.checkout.rows()) == 2


def test_observer_refuses_outside_transaction(ports):
    from agentcc.services.auto_reload_arming import observe_locked_auto_reload_balance

    assert not connection.in_atomic_block
    with pytest.raises(RuntimeError, match="locked account transaction"):
        observe_locked_auto_reload_balance(ports.account, balance_micros=0)


def test_observed_rearm_rolls_back_with_balance_transaction(
    admin_client, ports, ledger, organization
):
    assert trigger.post(admin_client).json()["result"] == {"status": "queued"}
    with pytest.raises(RuntimeError, match="synthetic abort"):
        with transaction.atomic():
            credit(ports, ledger, organization, 11_000_000)
            assert state(ports).epoch == 1
            raise RuntimeError("synthetic abort")
    assert state(ports).epoch == 0
    assert state(ports).below_threshold
    assert trigger.post(admin_client, str(uuid4())).json()["result"] == {
        "status": "duplicate"
    }
    assert len(ports.checkout.rows()) == 1


def test_unsupported_terms_revision_is_not_a_rearm_or_trigger(admin_client, ports):
    assert trigger.post(admin_client).json()["result"] == {"status": "queued"}
    type(ports.account).objects.filter(pk=ports.account.pk).update(
        autoreload_consent_version="auto-reload-v2"
    )
    response = trigger.post(admin_client, str(uuid4()))
    assert response.status_code == 409
    assert response.json()["code"] == "auto_reload_consent_required"
    assert len(ports.checkout.rows()) == 1
    assert state(ports).epoch == 0


def test_concurrent_distinct_uuids_wait_on_account_lock_and_queue_once(
    admin_client, ports, make_client, organization
):
    assert connection.vendor == "postgresql", "FOR UPDATE evidence requires PostgreSQL"
    second_client = make_client(organization, Level.ADMIN)
    inserted, second_lock_attempted, release = Event(), Event(), Event()
    pids = {}

    def pause_after_insert():
        inserted.set()
        assert release.wait(15), "test did not release first transaction"

    def worker(client, name):
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
                    second_lock_attempted.set()
                return execute(sql, params, many, context)

            with db.execute_wrapper(observe):
                return trigger.post(client, str(uuid4()))
        finally:
            db.close()

    ports.checkout.after_insert = pause_after_insert
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(worker, admin_client, "first")
        try:
            assert inserted.wait(10), "first request never wrote an outbox row"
            second = executor.submit(worker, second_client, "second")
            assert second_lock_attempted.wait(10), "second request never tried row lock"
            deadline = monotonic() + 5
            while True:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_blocking_pids(%s)", [pids["second"]])
                    blockers = cursor.fetchone()[0]
                if pids["first"] in blockers:
                    break
                assert monotonic() < deadline, "no real account-lock blocking observed"
                release.wait(0.01)
            assert not second.done()
        finally:
            release.set()
        responses = [first.result(timeout=20), second.result(timeout=20)]
    assert all(response.status_code == 200 for response in responses)
    assert sorted(response.json()["result"]["status"] for response in responses) == [
        "duplicate",
        "queued",
    ]
    assert len(ports.checkout.rows()) == 1
    assert len({call["intent_id"] for call in ports.checkout.calls}) == 1
    assert state(ports).epoch == 0

    from agentcc.models.auto_reload_intent import AgentccAutoReloadIntent

    assert (
        AgentccAutoReloadIntent.objects.filter(
            organization=organization, state="queued"
        ).count()
        == 1
    )
