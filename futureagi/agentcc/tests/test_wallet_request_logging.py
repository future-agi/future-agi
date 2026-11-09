"""Main-owned request logging acceptance outcomes; synthetic values only.

Ruling 681 addendum 5: observed in what the configured sinks emit
(tfc.tests.checkout_sinks), not in the in-memory records.
"""

import secrets
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

import pytest
from django.conf import settings
from django.test import override_settings

from agentcc.tests import test_managed_credits_api as wallet
from agentcc.views import managed_credits as views
from tfc.tests.checkout_sinks import configured_sinks

pytestmark = pytest.mark.integration
admin_client = wallet.admin_client
member_client = wallet.member_client
make_client = wallet.make_client


@pytest.mark.parametrize("outcome", ["pending", "unavailable", "forbidden"])
def test_session_is_absent_from_every_sink_after_landing(
    admin_client, member_client, organization, outcome
):
    from agentcc.services import checkout_verification

    # A synthetic Stripe Checkout Session identifier, the route's only shape;
    # any other shape is answered 404 before the view runs.
    supplied = "cs_test_" + secrets.token_hex(16)
    # MUST-FIX G: only a session bound to the org that Stripe says is paid
    # reaches fulfilment, so bind it and stand in for the paid read.
    wallet._bind_checkout(organization, supplied)
    paid = wallet._paid_checkout_read(supplied)
    other = "sk_test_" + secrets.token_urlsafe(32)
    fulfil = MagicMock(side_effect=ValueError(supplied))
    credited = MagicMock(return_value=False)
    if outcome == "unavailable":
        credited.side_effect = views.DatabaseError(supplied)
    client = member_client if outcome == "forbidden" else admin_client
    with ExitStack() as stack:
        # Root (application loggers), django and django_structlog (request logs).
        sinks = {
            name or "root": stack.enter_context(configured_sinks(name))[1]
            for name in ("", "django", "django_structlog")
        }
        with (
            override_settings(
                MIDDLEWARE=[
                    *settings.MIDDLEWARE,
                    "django_structlog.middlewares.RequestMiddleware",
                ]
            ),
            wallet._install_checkout(MagicMock(), fulfil=fulfil),
            # _ledger runs before checkout_credited. Stand in for the loader
            # too, so an absent private module cannot mask the intended branch.
            patch.object(views, "_ledger", return_value=object()) as ledger,
            patch.object(views, "checkout_credited", credited),
            patch.object(
                checkout_verification, "read_checkout_session", return_value=paid
            ) as read,
        ):
            response = client.get(f"{wallet.BASE}checkout/{supplied}/")
            other_shape = client.get(f"{wallet.BASE}checkout/{other}/")
    assert (
        response.status_code
        == {"pending": 200, "unavailable": 503, "forbidden": 403}[outcome]
    )
    assert other_shape.status_code == 404
    assert other not in other_shape.content.decode()
    if outcome == "pending":
        same_session = fulfil.call_args.args[1] == supplied
        assert same_session is True, "fulfilment must retain the supplied session"
        assert fulfil.call_count == 1
        assert read.call_count == 1
        assert ledger.call_count == 1
        assert credited.call_count == 2
        assert response.json()["result"] == {
            "session_id": None,
            "status": "pending",
            "credited": False,
        }
    elif outcome == "unavailable":
        # The 503 must originate in the intended credited-read failure, not
        # a missing module before that boundary (the previous false positive).
        assert ledger.call_count == 1
        assert credited.call_count == 1
        assert fulfil.call_count == 0
        assert read.call_count == 0
        assert response.json()["code"] == "billing_unavailable"
    else:
        assert ledger.call_count == 0
        assert credited.call_count == 0
        assert fulfil.call_count == 0
        assert read.call_count == 0
    request_sinks = sinks["django_structlog"]
    assert request_sinks.emitted() is True, "production request logging must be enabled"
    assert "request_started" in request_sinks.outputs()["console-production"]
    assert supplied not in response.content.decode()
    for name, observed in sinks.items():
        assert observed.leaks(supplied) == [], f"supplied value reached {name} sinks"
        assert observed.leaks(other) == [], f"other value reached {name} sinks"
        assert observed.structured(), "a production line is not one JSON document"
