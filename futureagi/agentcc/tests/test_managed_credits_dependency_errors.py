"""Dependency refusals stay value-free after authenticated wallet operations.

Request validation and permission errors retain their framework semantics.
All external boundaries are synthetic; red runs assert booleans, not values.
"""

import logging
import secrets
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest
from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.http import Http404
from rest_framework.exceptions import (
    APIException,
    NotAuthenticated,
    PermissionDenied,
    ValidationError,
)

from agentcc.tests import test_managed_credits_api as wallet
from agentcc.views import managed_credits as views

pytestmark = pytest.mark.integration
admin_client = wallet.admin_client
member_client = wallet.member_client
staff_client = wallet.staff_client
make_client = wallet.make_client
ledger = wallet.ledger


@pytest.fixture
def supplied():
    return "sk_test_" + secrets.token_urlsafe(32)


def absent(supplied, *values):
    observed = any(supplied in str(value) for value in values)
    assert observed is False, "supplied value reached an observable surface"


@contextmanager
def all_records(caplog):
    caplog.set_level(logging.INFO)
    # Production loggers do not all propagate to the root pytest handler.
    loggers = [logging.getLogger(name) for name in ("django", "django_structlog")]
    for logger in loggers:
        logger.addHandler(caplog.handler)
    try:
        yield
    finally:
        for logger in loggers:
            logger.removeHandler(caplog.handler)


OPERATIONS = [
    ("get", "", "credit_summary", None),
    ("get", "entries/", "credit_entries", None),
    ("get", "statement/", "credit_statement", {"month": "2026-10"}),
    ("post", "checkout/", "checkout", wallet.CHECKOUT_BODY),
    ("get", f"checkout/{wallet.SESSION_STATUS}/", "checkout_credited", None),
    ("get", "settings/", "get_account", None),
    (
        "put",
        "settings/",
        "save_settings",
        {"low_balance_threshold_usd": "1", "auto_reload": {"enabled": False}},
    ),
    (
        "post",
        "grant/",
        "grant",
        {"amount_usd": "1", "idempotency_key": "test", "note": "test"},
    ),
    (
        "post",
        "adjust/",
        "adjust",
        {"amount_usd": "1", "idempotency_key": "test", "reason": "test"},
    ),
    ("post", "refund/", "refund_spend", {"request_id": "test", "reason": "test"}),
]


@pytest.mark.parametrize("method,route,boundary,body", OPERATIONS)
@pytest.mark.parametrize(
    "failure",
    [
        APIException,
        ValidationError,
        NotAuthenticated,
        PermissionDenied,
        Http404,
        DjangoPermissionDenied,
    ],
)
def test_dependency_failure_has_fixed_response_and_value_free_records(
    admin_client,
    staff_client,
    ledger,
    supplied,
    caplog,
    method,
    route,
    boundary,
    body,
    failure,
):
    dependency = MagicMock(side_effect=failure(supplied))
    if boundary == "checkout":
        replacement = wallet._install_checkout(dependency)
    else:
        target = ledger if boundary in ("get_account", "grant", "adjust") else views
        replacement = patch.object(target, boundary, dependency)
    client = staff_client if route in ("grant/", "adjust/", "refund/") else admin_client
    with (
        replacement,
        all_records(caplog),
        patch.object(views, "set_rollback", create=True) as rollback,
    ):
        response = getattr(client, method)(wallet.BASE + route, body, format="json")
        if boundary == "checkout_credited":
            # Another shape names no checkout session: 404 before the view.
            other_shape = client.get(wallet.BASE + "checkout/cs_synthetic/")
    if boundary == "checkout_credited":
        assert other_shape.status_code == 404
    assert dependency.call_count == 1
    absent(
        supplied,
        response.content.decode(),
        [vars(record) for record in caplog.records],
        [logging.Formatter().format(record) for record in caplog.records],
    )
    assert response.status_code == 503
    assert response.json()["code"] == "billing_unavailable"
    assert (
        response.json()["result"]
        == "The managed credit operation could not be completed."
    )
    assert rollback.called is True
    records = [
        record
        for record in caplog.records
        if "managed_credits_operation_failed" in record.getMessage()
    ]
    assert len(records) == 1
    assert records[0].msg["exception_type"] == failure.__name__
    assert records[0].exc_info is None


def test_request_validation_still_returns_400_before_dependency_call(admin_client):
    checkout = MagicMock()
    with wallet._install_checkout(checkout):
        response = admin_client.post(
            wallet.BASE + "checkout/",
            {**wallet.CHECKOUT_BODY, "amount_usd": "invalid"},
            format="json",
        )
    assert response.status_code == 400
    assert checkout.call_count == 0


def test_permission_refusal_still_returns_403_before_dependency_call(member_client):
    checkout = MagicMock()
    with wallet._install_checkout(checkout):
        response = member_client.post(
            wallet.BASE + "checkout/", wallet.CHECKOUT_BODY, format="json"
        )
    assert response.status_code == 403
    assert checkout.call_count == 0
