"""Frozen checkout gateway binding; synthetic durable adapter, never a payment path."""

from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest
from django.db import connection, transaction
from rest_framework.test import APIClient

from agentcc.models.auto_reload_consent import AgentccAutoReloadConsent
from agentcc.services.auto_reload_consent import has_current_auto_reload_consent
from agentcc.tests import test_auto_reload_payment_binding as binding
from agentcc.tests import test_managed_credits_api as wallet
from agentcc.tests.auto_reload_checkout_double import (
    CheckoutDouble,
    CheckoutTriggerResult,
)
from agentcc.views import auto_reload as trigger_view
from agentcc.views import managed_credits as views

pytestmark = [pytest.mark.integration, pytest.mark.django_db(transaction=True)]
admin_client = wallet.admin_client
member_client = wallet.member_client
make_client = wallet.make_client
ledger = wallet.ledger
URL = wallet.BASE + "auto-reload/trigger/"
INTENT = "0698a07d-5a28-49fa-9d75-32f50be41d88"


@pytest.fixture
def ports(ledger, organization, admin_client):
    account = ledger.ManagedCreditAccount.objects.create(
        organization=organization,
        autoreload_enabled=True,
        autoreload_consent_version=binding.VERSION,
        autoreload_consent_at=datetime(2026, 11, 6, tzinfo=UTC),
        autoreload_threshold_micros=10_000_000,
        autoreload_amount_micros=25_000_000,
        autoreload_payment_method_id=binding.METHOD,
    )
    receipt = binding.record(account, admin_client)
    events = []
    double = CheckoutDouble(ledger, {binding.METHOD: organization.pk}, events)
    with connection.cursor() as cursor:
        cursor.execute(
            "CREATE TABLE test_gateway_checkout_outbox "
            "(intent_id varchar(255) PRIMARY KEY, payload text NOT NULL)"
        )
    try:
        with (
            patch.object(ledger, "request_push", return_value=None),
            patch.object(views, "_ledger", return_value=ledger),
            patch.object(views, "_checkout", return_value=double) as load_checkout,
            # The reload rules here are those of a build that runs auto-reload;
            # this release refuses turning it on (test_auto_reload_unavailable).
            patch.object(views, "AUTO_RELOAD_AVAILABLE", True, create=True),
        ):
            yield SimpleNamespace(
                account=account,
                receipt=receipt,
                checkout=double,
                load_checkout=load_checkout,
                events=events,
            )
    finally:
        with connection.cursor() as cursor:
            cursor.execute("DROP TABLE test_gateway_checkout_outbox")


def post(client, intent_id=INTENT):
    return client.post(URL, {"intent_id": intent_id}, format="json")


def test_locked_consent_check_precedes_exact_frozen_checkout_call(
    admin_client, organization, ports
):
    # SQL observation proves a real row-lock query, not a mocked manager chain.
    def observe_sql(execute, sql, params, many, context):
        result = execute(sql, params, many, context)
        if "FOR UPDATE" in sql and ports.account._meta.db_table in sql:
            ports.events.append("account_lock")
        return result

    def check_consent(account):
        assert connection.in_atomic_block
        assert ports.events == ["account_lock"]
        assert account.pk == ports.account.pk
        assert has_current_auto_reload_consent(account)
        ports.events.append("consent")
        return True

    with (
        connection.execute_wrapper(observe_sql),
        patch.object(
            trigger_view, "has_current_auto_reload_consent", side_effect=check_consent
        ),
    ):
        response = post(admin_client)
    assert response.status_code == 200, response.content
    assert response.json()["result"] == {"status": "queued"}
    assert ports.events == ["account_lock", "consent", "checkout"]
    assert ports.checkout.calls == [
        {
            "org_id": organization.pk,
            "consent_receipt_id": ports.receipt.pk,
            "intent_id": f"{organization.pk}:0",
            "amount_micros": ports.receipt.amount_micros,
            "payment_method_id": binding.METHOD,
            "consent_revision": 1,
        }
    ]
    assert len(ports.checkout.rows()) == 1
    assert not connection.in_atomic_block
    assert binding.METHOD not in response.content.decode()


def test_duplicate_intent_is_duplicate_and_leaves_one_durable_row(admin_client, ports):
    assert post(admin_client).json()["result"] == {"status": "queued"}
    first = ports.checkout.rows()
    response = post(admin_client)
    assert response.status_code == 200
    assert response.json()["result"] == {"status": "duplicate"}
    assert len(ports.checkout.calls) == 2
    assert len(first) == 1
    assert ports.checkout.rows() == first


@pytest.mark.parametrize(
    "invalid",
    [
        "missing",
        "method",
        "legacy",
        "amount",
        "threshold",
        "revision",
        "disabled",
        "actor",
    ],
)
def test_invalid_consent_never_loads_or_calls_checkout(admin_client, ports, invalid):
    account = ports.account
    if invalid == "missing":
        ports.receipt.delete()
    elif invalid == "legacy":
        AgentccAutoReloadConsent.objects.filter(pk=ports.receipt.pk).update(
            payment_method_id=""
        )
    elif invalid == "actor":
        AgentccAutoReloadConsent.objects.filter(pk=ports.receipt.pk).update(actor=None)
    else:
        field, value = {
            "method": ("autoreload_payment_method_id", "pm_synthetic_unconsented"),
            "amount": (
                "autoreload_amount_micros",
                account.autoreload_amount_micros + 1,
            ),
            "threshold": (
                "autoreload_threshold_micros",
                account.autoreload_threshold_micros + 1,
            ),
            "revision": ("autoreload_consent_version", "auto-reload-v2"),
            "disabled": ("autoreload_enabled", False),
        }[invalid]
        type(account).objects.filter(pk=account.pk).update(**{field: value})
    response = post(admin_client)
    assert response.status_code == 409, response.content
    assert response.json()["code"] == "auto_reload_consent_required"
    ports.load_checkout.assert_not_called()
    assert not ports.checkout.calls
    assert not ports.checkout.rows()


def test_committed_enabled_false_settings_produces_no_new_trigger(admin_client, ports):
    assert post(admin_client).status_code == 200
    prior = ports.checkout.rows()
    response = admin_client.put(
        wallet.BASE + "settings/",
        {"low_balance_threshold_usd": None, "auto_reload": {"enabled": False}},
        format="json",
    )
    assert response.status_code == 200, response.content
    ports.load_checkout.reset_mock()
    assert post(admin_client, str(uuid4())).status_code == 409
    ports.account.refresh_from_db()
    assert not ports.account.autoreload_enabled
    ports.load_checkout.assert_not_called()
    assert len(ports.checkout.calls) == 1
    assert ports.checkout.rows() == prior


def test_foreign_owned_method_is_refused_even_with_matching_receipt(
    admin_client, ports
):
    ports.checkout.owned_methods[binding.METHOD] = uuid4()
    response = post(admin_client)
    assert response.status_code == 409
    assert response.json()["code"] == "auto_reload_payment_method_mismatch"
    assert len(ports.checkout.calls) == 1
    assert not ports.checkout.rows()


@pytest.mark.parametrize(
    "reason",
    [
        "consent_missing",
        "consent_stale",
        "payment_method_mismatch",
        "amount_above_consent",
        "disabled",
    ],
)
def test_gateway_propagates_only_frozen_value_free_refusal_codes(
    admin_client, ports, reason
):
    with patch.object(
        ports.checkout,
        "request_auto_reload_checkout",
        return_value=CheckoutTriggerResult("refused", reason),
    ) as request:
        response = post(admin_client)
    assert response.status_code == 409
    assert response.json()["code"] == "auto_reload_" + reason
    request.assert_called_once()
    assert not ports.checkout.rows()
    assert binding.METHOD not in response.content.decode()


@pytest.mark.parametrize(
    "change,reason",
    [
        ({"consent_revision": 2}, "consent_stale"),
        ({"amount_micros": 25_000_001}, "amount_above_consent"),
        ({"payment_method_id": "pm_synthetic_foreign"}, "payment_method_mismatch"),
        (
            {"consent_receipt_id": UUID("00000000-0000-0000-0000-000000000001")},
            "consent_missing",
        ),
    ],
)
def test_adapter_double_contract_refuses_mismatched_inputs(
    organization, ports, change, reason
):
    """Executable contract for replacement; NOT proof of the real adapter."""
    values = {
        "org_id": organization.pk,
        "consent_receipt_id": ports.receipt.pk,
        "intent_id": f"{organization.pk}:0",
        "amount_micros": ports.receipt.amount_micros,
        "payment_method_id": binding.METHOD,
        "consent_revision": 1,
    }
    values.update(change)
    with transaction.atomic():
        type(ports.account).objects.select_for_update().get(pk=ports.account.pk)
        result = ports.checkout.request_auto_reload_checkout(**values)
    assert result == CheckoutTriggerResult("refused", reason)
    assert not ports.checkout.rows()


def test_revoked_receipt_never_queues_even_as_duplicate(admin_client, ports):
    assert post(admin_client).status_code == 200
    type(ports.account).objects.filter(pk=ports.account.pk).update(
        autoreload_enabled=False
    )
    response = post(admin_client)
    assert response.status_code == 409
    assert len(ports.checkout.calls) == 1
    assert len(ports.checkout.rows()) == 1


def test_outbox_write_rolls_back_with_gateway_failure(admin_client, ports):
    def fail_after_insert():
        raise RuntimeError("synthetic-private-detail")

    ports.checkout.after_insert = fail_after_insert
    response = post(admin_client)
    assert response.status_code == 503
    assert len(ports.checkout.calls) == 1
    assert not ports.checkout.rows()
    assert "synthetic-private-detail" not in response.content.decode()
    from agentcc.models.auto_reload_arming import AgentccAutoReloadArming

    assert not AgentccAutoReloadArming.objects.exists()


@pytest.mark.parametrize(
    "bad_result",
    [
        None,
        CheckoutTriggerResult("paid"),
        CheckoutTriggerResult("refused", "synthetic-private-detail"),
        replace(CheckoutTriggerResult("queued"), reason="synthetic-private-detail"),
    ],
)
def test_invalid_adapter_result_rolls_back_and_is_not_leaked(
    admin_client, ports, bad_result
):
    original = ports.checkout.request_auto_reload_checkout

    def malformed(**kwargs):
        original(**kwargs)
        return bad_result

    with patch.object(
        ports.checkout, "request_auto_reload_checkout", side_effect=malformed
    ):
        response = post(admin_client)
    assert response.status_code == 503
    assert not ports.checkout.rows()
    assert "synthetic-private-detail" not in response.content.decode()


@pytest.mark.parametrize("caller", ["member", "anonymous"])
def test_trigger_retains_org_admin_permissions(member_client, ports, caller):
    client = member_client if caller == "member" else APIClient()
    assert post(client).status_code in (401, 403)
    ports.load_checkout.assert_not_called()


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"intent_id": "not-a-uuid"},
        {"intent_id": INTENT, "amount_micros": 25_000_001},
        {"intent_id": INTENT, "payment_method_id": "pm_synthetic_foreign"},
        {"intent_id": INTENT, "consent_revision": 2},
        {"intent_id": INTENT, "consent_receipt_id": INTENT},
        {"intent_id": INTENT, "organization_id": INTENT},
    ],
)
def test_trigger_accepts_attempt_id_only(admin_client, ports, payload):
    assert admin_client.post(URL, payload, format="json").status_code == 400
    ports.load_checkout.assert_not_called()


def test_trigger_is_not_a_get_side_effect(admin_client, ports):
    assert admin_client.get(URL).status_code == 405
    ports.load_checkout.assert_not_called()


def test_missing_adapter_is_explicit_unavailability(admin_client, ports):
    ports.load_checkout.return_value = SimpleNamespace()
    response = post(admin_client)
    assert response.status_code == 503
    assert response.json()["code"] == "billing_unavailable"


def test_trigger_refuses_uncommitted_outer_transaction(admin_client, ports):
    with transaction.atomic():
        response = post(admin_client)
    assert response.status_code == 503
    ports.load_checkout.assert_not_called()
    assert not ports.checkout.rows()
