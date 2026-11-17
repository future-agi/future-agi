"""Exact checkout primitive types; equality alone accepts bools and integral floats."""

from uuid import UUID

import pytest
from django.db import transaction

from agentcc.tests import test_auto_reload_trigger as trigger

pytestmark = [pytest.mark.integration, pytest.mark.django_db(transaction=True)]
admin_client = trigger.admin_client
make_client = trigger.make_client
ledger = trigger.ledger
ports = trigger.ports


@pytest.mark.parametrize(
    "field,bad_value",
    [
        ("org_id", "00000000-0000-0000-0000-000000000001"),
        ("consent_receipt_id", "00000000-0000-0000-0000-000000000001"),
        ("intent_id", UUID("00000000-0000-0000-0000-000000000001")),
        ("amount_micros", True),
        ("amount_micros", 25_000_000.0),
        ("payment_method_id", 1),
        ("consent_revision", True),
        ("consent_revision", 1.0),
    ],
)
def test_double_rejects_non_exact_boundary_types(organization, ports, field, bad_value):
    values = {
        "org_id": organization.pk,
        "consent_receipt_id": ports.receipt.pk,
        "intent_id": f"{organization.pk}:0",
        "amount_micros": 25_000_000,
        "payment_method_id": trigger.binding.METHOD,
        "consent_revision": 1,
    }
    values[field] = bad_value
    with transaction.atomic():
        type(ports.account).objects.select_for_update().get(pk=ports.account.pk)
        with pytest.raises(AssertionError, match="checkout: exact primitive types"):
            ports.checkout.request_auto_reload_checkout(**values)
    assert not ports.checkout.calls
    assert not ports.checkout.rows()


def test_gateway_supplies_exact_boundary_types(admin_client, ports):
    response = trigger.post(admin_client)
    assert response.status_code == 200, response.content
    call = ports.checkout.calls[0]
    assert tuple(
        type(call[name])
        for name in (
            "org_id",
            "consent_receipt_id",
            "intent_id",
            "amount_micros",
            "payment_method_id",
            "consent_revision",
        )
    ) == (UUID, UUID, str, int, str, int)
