"""Managed-credit failures expose only fixed diagnostics, never supplied values.

Cloud billing and the gateway are synthetic stand-ins. The marker is generated
at runtime; failures report outcome booleans, not its value or captured records.
"""

import logging
import secrets
from importlib import import_module
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

import httpx
import pytest
from django.urls import resolve

from agentcc.services.gateway_client import GatewayClientError
from agentcc.tests import test_managed_credits_api as wallet
from agentcc.tests.test_managed_credits_api import (
    BASE,
    CHECKOUT_BODY,
    _install_checkout,
)
from agentcc.views import managed_credits as views
from tfc.logging.checkout_format import SESSION_ID as CHECKOUT_SESSION_ID

pytestmark = pytest.mark.integration

# Reuse the wallet suite's synthetic boundaries and authenticated clients.
admin_client = wallet.admin_client
gateway = wallet.gateway
ledger = wallet.ledger
make_client = wallet.make_client
other_org = wallet.other_org
staff_client = wallet.staff_client


@pytest.fixture(params=["credential", "checkout-session"])
def supplied(request):
    # Synthetic values are generated only at runtime, never service credentials.
    if request.param == "credential":
        return "sk_test_" + secrets.token_urlsafe(32)
    # A checkout-session-like marker that never has the status route's
    # identifier shape (cs_test_ plus at least 24 letters or digits): its
    # 16-character tail keeps every draw another shape, answered 404 at the
    # route, and outside the output redaction's identifier pattern, so the
    # value-free checks below depend on the diagnostics alone.
    marker = "cs_test_" + secrets.token_hex(8)
    assert not CHECKOUT_SESSION_ID.search(marker)
    return marker


@pytest.fixture
def session():
    # A synthetic Stripe Checkout Session identifier. The status route answers
    # only cs_test_ or cs_live_ and at least 24 letters or digits; any other
    # shape (such as the supplied marker) is a 404 before the view runs.
    return "cs_test_" + secrets.token_hex(16)


@pytest.fixture
def diagnostics(caplog):
    caplog.set_level(logging.WARNING)
    return caplog


def _absent(supplied, *values):
    # Do not let assertion rewriting print the synthetic credential in a red run.
    present = any(supplied in str(value) for value in values)
    assert present is False, "supplied value reached an observable surface"


def _records_without_values(diagnostics, supplied, event, error_type, org=None):
    records = [r for r in diagnostics.records if event in r.getMessage()]
    assert len(records) == 1
    record = records[0]
    _absent(
        supplied,
        record.getMessage(),
        vars(record),
        logging.Formatter().format(record),
        record.exc_text,
    )
    assert record.exc_info is None
    assert record.exc_text is None
    assert isinstance(record.msg, dict)
    assert record.msg["exception_type"] == error_type
    assert record.msg["reason"]
    assert "exception" not in record.msg
    assert "exc_info" not in record.msg
    assert "session_id" not in record.msg
    if org is not None:
        assert record.msg["org_id"] == str(org.pk)


@pytest.fixture
def pending_landing_ledger():
    """Only the adapted landing cases use this private-free read boundary.

    Run the real checkout_credited org/kind/session query both before and
    after the refused fulfilment. No ledger write API is supplied, and no
    verification, binding, authorization or fulfilment guard is replaced.
    """
    entries = MagicMock(spec_set=["filter"])
    filtered = MagicMock(spec_set=["exists"])
    filtered.exists.return_value = False
    entries.filter.return_value = filtered
    service = SimpleNamespace(
        Kind=SimpleNamespace(TOPUP="topup"),
        ledger_entries=MagicMock(return_value=entries),
    )
    with patch.object(views, "_ledger", return_value=service) as load:
        yield SimpleNamespace(
            load=load, service=service, entries=entries, filtered=filtered
        )


def _pending_ledger_reads(ledger, organization, session):
    ledger.load.assert_called_once_with()
    scoped = ledger.service.ledger_entries.call_args_list == [call(organization)] * 2
    assert scoped, "both ledger reads must use the requesting organization"
    filtered = (
        ledger.entries.filter.call_args_list
        == [call(kind=ledger.service.Kind.TOPUP, stripe_ref=session)] * 2
    )
    assert filtered, "both ledger reads must use the top-up kind and exact session"
    assert ledger.filtered.exists.call_count == 2


@pytest.mark.parametrize("failure", [ValueError, RuntimeError, views.DatabaseError])
def test_landing_refusal_logs_only_class_reason_and_org(
    admin_client,
    organization,
    supplied,
    session,
    diagnostics,
    failure,
    pending_landing_ledger,
):
    from agentcc.services import checkout_verification

    # A bound session Stripe says is paid reaches fulfil (MUST-FIX G).
    wallet._bind_checkout(organization, session)
    fulfil = MagicMock(side_effect=failure(session))
    with (
        _install_checkout(MagicMock(), fulfil=fulfil),
        patch.object(
            checkout_verification,
            "read_checkout_session",
            return_value=wallet._paid_checkout_read(session),
        ) as read,
    ):
        response = admin_client.get(f"{BASE}checkout/{session}/")
        # Another shape names no checkout session: 404, no fulfilment.
        other_shape = admin_client.get(f"{BASE}checkout/{supplied}/")
    # A worker's first request resolves URLs inside the stand-in. Its exit
    # must not evict the real modules while Django retains their callbacks:
    # later string-target mocks must patch these same globals, in any order.
    create = resolve("/agentcc/api-keys/").func.cls.create
    assert create.__globals__ is vars(import_module(create.__module__))
    assert create.__globals__["auth_bridge"] is import_module(
        "agentcc.services.auth_bridge"
    )
    bulk_get = resolve("/agentcc/org-configs/bulk/").func.cls.get
    assert bulk_get.__globals__ is vars(import_module(bulk_get.__module__))
    assert response.status_code == 200
    assert response.json()["result"]["credited"] is False
    assert fulfil.call_count == 1
    same_fulfilment = fulfil.call_args == call(organization, session)
    assert same_fulfilment, "fulfilment must retain the organization and session"
    same_read = read.call_args_list == [call(session)]
    assert same_read, "the bound paid session must be verified exactly once"
    _pending_ledger_reads(pending_landing_ledger, organization, session)
    assert other_shape.status_code == 404
    _absent(supplied, other_shape.content.decode())
    _records_without_values(
        diagnostics,
        session,
        "managed_credits_landing_fulfil_refused",
        failure.__name__,
        organization,
    )


def test_pending_landing_response_never_echoes_unverified_session(
    admin_client, supplied, session, diagnostics
):
    with _install_checkout(
        MagicMock(), fulfil=MagicMock(side_effect=ValueError(session))
    ):
        response = admin_client.get(f"{BASE}checkout/{session}/")
        # Another shape names no checkout session: 404, never echoed.
        other_shape = admin_client.get(f"{BASE}checkout/{supplied}/")
    assert response.status_code == 200
    _absent(session, response.content.decode())
    assert response.json()["result"] == {
        "session_id": None,
        "status": "pending",
        "credited": False,
    }
    assert other_shape.status_code == 404
    _absent(supplied, other_shape.content.decode())


def test_landing_module_failure_is_value_free(
    admin_client, organization, supplied, session, diagnostics, pending_landing_ledger
):
    from agentcc.services import checkout_verification

    # A bound session Stripe says is paid reaches fulfil (MUST-FIX G).
    wallet._bind_checkout(organization, session)
    with (
        patch.object(views, "_checkout", side_effect=RuntimeError(session)) as checkout,
        patch.object(
            checkout_verification,
            "read_checkout_session",
            return_value=wallet._paid_checkout_read(session),
        ) as read,
    ):
        response = admin_client.get(f"{BASE}checkout/{session}/")
        # Another shape names no checkout session: 404 before the view.
        other_shape = admin_client.get(f"{BASE}checkout/{supplied}/")
    assert response.status_code == 200
    _absent(session, response.content.decode())
    assert other_shape.status_code == 404
    assert checkout.call_count == 1
    assert response.json()["result"] == {
        "session_id": None,
        "status": "pending",
        "credited": False,
    }
    same_read = read.call_args_list == [call(session)]
    assert same_read, "the bound paid session must be verified exactly once"
    _pending_ledger_reads(pending_landing_ledger, organization, session)
    _records_without_values(
        diagnostics,
        session,
        "managed_credits_landing_fulfil_refused",
        "RuntimeError",
        organization,
    )


@pytest.mark.parametrize(
    "failure", [GatewayClientError, httpx.HTTPError, KeyError, TypeError, ValueError]
)
def test_gateway_read_diagnostics_are_value_free(
    admin_client, gateway, supplied, diagnostics, failure, organization
):
    gateway.error = failure(supplied)
    response = admin_client.get(BASE)
    assert response.status_code == 200
    _absent(supplied, response.content.decode())
    _records_without_values(
        diagnostics,
        supplied,
        "managed_credits_gateway_read_failed",
        failure.__name__,
        organization,
    )


def test_csv_failure_diagnostics_are_value_free(supplied, diagnostics):
    def rows():
        yield ["one"]
        raise views.DatabaseError(supplied)

    response = views._csv_response("entries.csv", ["header"], rows())
    body = b"".join(response.streaming_content).decode()
    _absent(supplied, body)
    assert "export failed after 1 rows" in body
    _records_without_values(
        diagnostics, supplied, "managed_credits_csv_failed", "DatabaseError"
    )


@pytest.mark.parametrize("operation", ["grant", "adjust", "refund"])
def test_ledger_refusal_response_is_value_free(
    ledger, staff_client, supplied, diagnostics, operation
):
    body = {
        "grant": {"amount_usd": "1", "idempotency_key": supplied, "note": "test"},
        "adjust": {"amount_usd": "1", "idempotency_key": supplied, "reason": "test"},
        "refund": {"request_id": supplied, "reason": "test"},
    }[operation]
    with patch.object(
        ledger, operation, side_effect=ledger.ManagedCreditError(supplied)
    ):
        response = staff_client.post(f"{BASE}{operation}/", body, format="json")
    assert response.status_code == 400
    assert response.json()["code"] == "ledger_write_refused"
    _absent(supplied, response.content.decode(), diagnostics.text)


def test_checkout_amount_refusal_response_is_value_free(
    admin_client, supplied, diagnostics
):
    class TopupAmountOutOfRange(ValueError):
        pass

    with _install_checkout(
        MagicMock(side_effect=TopupAmountOutOfRange(supplied)), TopupAmountOutOfRange
    ):
        response = admin_client.post(f"{BASE}checkout/", CHECKOUT_BODY, format="json")
    assert response.status_code == 400
    assert response.json()["code"] == "topup_amount_out_of_range"
    _absent(supplied, response.content.decode(), diagnostics.text)


@pytest.mark.parametrize("operation", ["checkout", "landing"])
def test_unexpected_dependency_failure_is_a_value_free_503(
    admin_client, organization, supplied, diagnostics, operation
):
    # Keep the route fixed here: the marker belongs to the dependency's error,
    # not to Django's independent request/access logging.
    with (
        _install_checkout(MagicMock(side_effect=RuntimeError(supplied))),
        patch.object(
            views, "checkout_credited", side_effect=views.DatabaseError(supplied)
        ),
    ):
        if operation == "checkout":
            response = admin_client.post(
                f"{BASE}checkout/", CHECKOUT_BODY, format="json"
            )
            error_type = "RuntimeError"
        else:
            response = admin_client.get(f"{BASE}checkout/{wallet.SESSION_STATUS}/")
            error_type = "DatabaseError"
            # Another shape names no checkout session: 404 before the view.
            other_shape = admin_client.get(f"{BASE}checkout/cs_pending/")
            assert other_shape.status_code == 404
    assert response.status_code == 503
    assert response.json()["code"] == "billing_unavailable"
    _absent(supplied, response.content.decode())
    _records_without_values(
        diagnostics,
        supplied,
        "managed_credits_operation_failed",
        error_type,
        organization,
    )
