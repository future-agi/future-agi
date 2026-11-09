"""The checkout status route serves Stripe Checkout Session identifiers only.

An identifier is cs_test_ or cs_live_ followed by at least 24 ASCII letters or
digits. Any other value is answered 404 before the view authenticates the
caller, checks permissions, runs its handler or logs anything, so a value of
another shape never reaches the view.

Percent-encoded unreserved characters are the same characters (RFC 3986
section 2.3): Django decodes the path once before routing, so a single-encoded
identifier is served as the identifier itself. A value that is still encoded
after that decoding (double percent-encoding, JSON escapes) is another shape.
All values are synthetic.
"""

from contextlib import ExitStack
from unittest.mock import MagicMock, patch
from urllib.parse import unquote

import pytest
from rest_framework.test import APIClient

from agentcc.tests import test_managed_credits_api as wallet
from agentcc.views import managed_credits as views

pytestmark = pytest.mark.integration
admin_client = wallet.admin_client
member_client = wallet.member_client
make_client = wallet.make_client

ROUTE = wallet.BASE + "checkout/"
TAIL = "SyntheticRouteShape0123456789"
TAIL_24 = "A1b2C3d4E5f6G7h8I9j0K1l2"

CONFORMING = {
    "test-mode": "cs_test_" + TAIL,
    "live-mode": "cs_live_" + TAIL,
    "tail-24": "cs_test_" + TAIL_24,
}

# The raw path segment as sent; the view would receive unquote(segment).
SHORT = {
    "tail-23": "cs_test_" + TAIL_24[:23],
    "tail-1": "cs_test_1",
    "prefix-only": "cs_test_",
    "bare": "cs_1",
}
MALFORMED = {
    "other-mode": "cs_prod_" + TAIL,
    "upper-case-prefix": "CS_TEST_" + TAIL,
    "hyphen-separator": "cs_test-" + TAIL,
    "no-mode": "cs_" + TAIL,
    "other-object": "sk_test_" + TAIL,
    "hyphen-in-tail": "cs_test_" + TAIL + "-x",
    "underscore-in-tail": "cs_test_" + TAIL + "_x",
    "leading-text": "x" + "cs_test_" + TAIL,
    "trailing-newline": "cs_test_" + TAIL + "%0A",
    "space-in-tail": "cs_test_" + TAIL + "%20x",
    "non-ascii-digit": "cs_test_" + TAIL + "%D9%A3",
    "fullwidth-letter": "cs_test_" + TAIL + "%EF%BC%A1",
}
ENCODED = {
    "percent-double": "cs%255Ftest%255F" + TAIL,
    "percent-double-tail": "cs_test_" + TAIL + "%2541",
    "json-escape": "cs%5Cu005ftest%5Cu005f" + TAIL,
    "json-escape-tail": "cs_test_" + TAIL + "%5Cu0041",
}
NON_CONFORMING = {**SHORT, **MALFORMED, **ENCODED}


@pytest.fixture
def view_body():
    """Every part of the status view's handler, and its logger, as mocks."""
    mocks = {
        "_ledger": MagicMock(),
        "checkout_credited": MagicMock(return_value=True),
        "_fulfil_on_landing": MagicMock(),
        "logger": MagicMock(),
    }
    with ExitStack() as stack:
        for name, mock in mocks.items():
            stack.enter_context(patch.object(views, name, mock))
        yield mocks


def _untouched(view_body):
    return {name: mock.mock_calls for name, mock in view_body.items()}


@pytest.mark.parametrize("method", ["get", "post"])
@pytest.mark.parametrize("label", sorted(NON_CONFORMING))
def test_other_shapes_are_not_found_before_the_view(
    admin_client, view_body, label, method
):
    segment = NON_CONFORMING[label]
    response = getattr(admin_client, method)(ROUTE + segment + "/")
    assert response.status_code == 404, (label, response.status_code)
    assert response.json()["code"] == "checkout_session_not_found"
    assert _untouched(view_body) == {name: [] for name in view_body}
    body = response.content.decode()
    assert unquote(segment) not in body and segment not in body


@pytest.mark.parametrize("caller", ["anonymous", "member"])
@pytest.mark.parametrize("label", ["tail-23", "other-mode", "percent-double"])
def test_other_shapes_are_not_found_before_authentication_or_permissions(
    member_client, view_body, label, caller
):
    client = APIClient() if caller == "anonymous" else member_client
    response = client.get(ROUTE + NON_CONFORMING[label] + "/")
    assert response.status_code == 404, (caller, response.status_code)
    assert response.json()["code"] == "checkout_session_not_found"
    assert _untouched(view_body) == {name: [] for name in view_body}


@pytest.mark.parametrize("label", sorted(CONFORMING))
def test_session_identifiers_reach_the_view(admin_client, view_body, label):
    session = CONFORMING[label]
    response = admin_client.get(ROUTE + session + "/")
    assert response.status_code == 200, response.content
    assert response.json()["result"] == {
        "session_id": session,
        "status": "complete",
        "credited": True,
    }
    assert view_body["checkout_credited"].call_args.args[2] == session


@pytest.mark.parametrize("caller", ["anonymous", "member"])
def test_session_identifiers_keep_authentication_and_permissions(
    member_client, view_body, caller
):
    client = APIClient() if caller == "anonymous" else member_client
    response = client.get(ROUTE + CONFORMING["test-mode"] + "/")
    assert response.status_code in ((401, 403) if caller == "anonymous" else (403,))
    view_body["checkout_credited"].assert_not_called()


@pytest.mark.parametrize(
    "segment",
    ["cs%5Ftest%5F" + TAIL, "%63s_test_" + TAIL, "cs_test_" + TAIL[:-1] + "%39"],
    ids=["separators", "first-letter", "last-digit"],
)
def test_single_encoded_identifier_is_the_identifier(admin_client, view_body, segment):
    session = unquote(segment)
    assert session in CONFORMING.values()
    response = admin_client.get(ROUTE + segment + "/")
    assert response.status_code == 200, response.content
    assert view_body["checkout_credited"].call_args.args[2] == session


def test_route_and_output_redaction_share_one_identifier_shape():
    from tfc.logging import checkout_format

    assert views.CHECKOUT_SESSION_ID is checkout_format.SESSION_ID
    assert checkout_format.SESSION_ID.pattern == "cs_(?:test|live)_[A-Za-z0-9]{24,}"
    for session in CONFORMING.values():
        assert checkout_format.SESSION_ID.fullmatch(session)
    for segment in NON_CONFORMING.values():
        assert checkout_format.SESSION_ID.fullmatch(unquote(segment)) is None
