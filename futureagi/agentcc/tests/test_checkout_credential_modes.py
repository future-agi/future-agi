"""Behavioral RED on the release base; no round-1 implementation imports.

Keys are visibly synthetic, generated here, and only sent to stand-ins. Fresh
settings imports return booleans only. No ImportError counts as defect proof.
"""

import json
import secrets
from unittest.mock import MagicMock, patch

import pytest

from agentcc.tests import test_managed_credits_api as wallet
from agentcc.tests.test_managed_credits_api import (
    BASE,
    CHECKOUT_BODY,
    _install_checkout,
)
from agentcc.views import managed_credits as views
from tfc.tests.test_stripe_webhook_secret_runtime import _load_settings

pytestmark = pytest.mark.integration
admin_client = wallet.admin_client
make_client = wallet.make_client


def _require(condition, message):
    if not condition:
        pytest.fail(message, pytrace=False)


def _key(family, mode):
    return f"{family}_{mode}_SYNTHETIC_NOT_A_CREDENTIAL_" + secrets.token_hex(8)


def _session(mode):
    return f"cs_{mode}_SyntheticNeverHosted" + secrets.token_hex(16)


def _opened(session):
    return {
        "checkout_url": "https://checkout.example.test/" + session,
        "session_id": session,
    }


def _load_mode(key, owner_go=False):
    env = {
        "STRIPE_SECRET_KEY": key,
        "WEBHOOK_SECRET_TEST": "whsec_SYNTHETIC_TEST_NOT_A_SECRET",
        "WEBHOOK_SECRET_LIVE": "whsec_SYNTHETIC_LIVE_NOT_A_SECRET",
    }
    if owner_go:
        env |= {
            "BILLING_LEDGER_ACCEPT_LIVE": "true",
            "BILLING_LEDGER_ACCEPT_LIVE_OWNER_GO": "synthetic-owner-go/not-live",
        }
    loaded = _load_settings(
        "__import__('json').dumps([s.STRIPE_LIVE, "
        "getattr(s, 'BILLING_LEDGER_ACCEPT_LIVE', False), "
        "s.STRIPE_WEBHOOK_SECRET == os.environ['WEBHOOK_SECRET_LIVE']])",
        **env,
    )
    _require(loaded.returncode == 0, "synthetic configured settings failed to load")
    _require(key not in loaded.stdout + loaded.stderr, "settings printed a credential")
    try:
        return json.loads(loaded.stdout.splitlines()[-1])
    except (ValueError, IndexError):
        pytest.fail("settings probe did not return mode booleans", pytrace=False)


@pytest.mark.parametrize("family", ["sk", "rk"])
def test_settings_to_post_live_without_owner_go_never_calls_create(
    admin_client, settings, family
):
    key = _key(family, "live")
    live, accepted, live_secret = _load_mode(key)
    settings.STRIPE_SECRET_KEY = key
    settings.STRIPE_LIVE = live
    settings.BILLING_LEDGER_ACCEPT_LIVE = accepted
    create = MagicMock(return_value=_opened(_session("live")))
    with _install_checkout(create):
        response = admin_client.post(f"{BASE}checkout/", CHECKOUT_BODY, format="json")
    # First assertion reproduces the money defect, not just the mode predicate.
    _require(
        create.call_count == 0, "live checkout reached create_session without owner go"
    )
    assert response.status_code == 503
    assert response.json()["code"] == "billing_unavailable"
    assert "checkout_url" not in response.content.decode()
    assert (live, accepted, live_secret) == (True, False, True)


@pytest.mark.parametrize("family", ["sk", "rk"])
@pytest.mark.parametrize("mode", ["test", "live"])
def test_settings_to_post_supported_modes_bind_correctly(
    admin_client, organization, settings, family, mode
):
    key = _key(family, mode)
    live, accepted, live_secret = _load_mode(key, owner_go=mode == "live")
    settings.STRIPE_SECRET_KEY = key
    settings.STRIPE_LIVE = live
    settings.BILLING_LEDGER_ACCEPT_LIVE = accepted
    session = _session(mode)
    create = MagicMock(return_value=_opened(session))
    with _install_checkout(create):
        response = admin_client.post(f"{BASE}checkout/", CHECKOUT_BODY, format="json")
    assert response.status_code == 200
    assert create.call_count == 1
    assert live is (mode == "live")
    assert live_secret is live
    from agentcc.models import AgentccCheckoutBinding

    binding = AgentccCheckoutBinding.objects.get(organization=organization)
    assert binding.livemode is live


@pytest.mark.parametrize("family", ["sk", "rk"])
def test_current_live_key_cannot_hide_behind_stale_test_boolean(
    admin_client, settings, family
):
    settings.STRIPE_SECRET_KEY = _key(family, "live")
    settings.STRIPE_LIVE = False
    settings.BILLING_LEDGER_ACCEPT_LIVE = False
    create = MagicMock(return_value=_opened(_session("live")))
    with _install_checkout(create):
        response = admin_client.post(f"{BASE}checkout/", CHECKOUT_BODY, format="json")
    _require(create.call_count == 0, "stale mode boolean opened live checkout")
    assert response.status_code == 503


@pytest.mark.parametrize("bad", ["unknown", "pk_live", "sk_live", "rk_live", "rk_test"])
def test_unknown_or_truncated_credential_refuses_post(admin_client, settings, bad):
    # No key values in parametrization ids or assertion diagnostics.
    key = "SYNTHETIC_UNKNOWN_" + secrets.token_hex(8) if bad == "unknown" else bad + "_"
    settings.STRIPE_SECRET_KEY = key
    settings.STRIPE_LIVE = False
    settings.BILLING_LEDGER_ACCEPT_LIVE = True
    create = MagicMock(return_value=_opened(_session("test")))
    with _install_checkout(create):
        response = admin_client.post(f"{BASE}checkout/", CHECKOUT_BODY, format="json")
    _require(create.call_count == 0, "unrecognized credential reached create_session")
    assert response.status_code == 503
    _require(key not in response.content.decode(), "refusal printed a credential")


@pytest.mark.parametrize(
    "mode, returned", [("test", "live"), ("live", "test"), ("test", "invalid")]
)
def test_returned_session_must_match_validated_key_mode(
    admin_client, organization, settings, mode, returned
):
    settings.STRIPE_SECRET_KEY = _key("rk", mode)
    settings.STRIPE_LIVE = mode == "live"
    settings.BILLING_LEDGER_ACCEPT_LIVE = True
    session = _session(returned) if returned != "invalid" else "synthetic-invalid-id"
    create = MagicMock(return_value=_opened(session))
    with _install_checkout(create):
        response = admin_client.post(f"{BASE}checkout/", CHECKOUT_BODY, format="json")
    assert create.call_count == 1
    assert response.status_code == 503
    assert "checkout_url" not in response.content.decode()
    _require(
        session not in response.content.decode(),
        "refusal exposed the mismatched session",
    )
    from agentcc.models import AgentccCheckoutBinding

    assert not AgentccCheckoutBinding.objects.filter(organization=organization).exists()


def test_unbound_landing_does_not_call_fulfil_behavioral_red(admin_client, settings):
    settings.STRIPE_LIVE = False
    settings.STRIPE_SECRET_KEY = None
    fulfil = MagicMock()
    with (
        _install_checkout(MagicMock(), fulfil=fulfil),
        patch.object(views, "_ledger", return_value=object()),
        patch.object(views, "checkout_credited", return_value=False),
    ):
        response = admin_client.get(f"{BASE}checkout/{_session('test')}/")
    assert response.status_code == 200
    _require(fulfil.call_count == 0, "unbound session reached fulfil")
    assert response.json()["result"] == {
        "session_id": None,
        "status": "pending",
        "credited": False,
    }


def test_unpaid_landing_does_not_call_fulfil_behavioral_red(
    admin_client, organization, settings, monkeypatch
):
    import hashlib
    from types import SimpleNamespace

    from django.apps import apps

    settings.STRIPE_LIVE = False
    settings.STRIPE_SECRET_KEY = None
    session = _session("test")
    try:
        binding_model = apps.get_model("prism", "AgentccCheckoutBinding")
    except LookupError:
        binding_model = None
    if binding_model is not None:
        binding_model.objects.create(
            organization=organization,
            session_digest=hashlib.sha256(session.encode()).hexdigest(),
            amount_micros=25_000_000,
            currency="usd",
            livemode=False,
        )
    verification = getattr(views, "checkout_verification", None)
    if verification is not None:
        monkeypatch.setattr(
            verification,
            "read_checkout_session",
            lambda _: SimpleNamespace(
                id=session,
                mode="payment",
                status="complete",
                payment_status="unpaid",
                livemode=False,
                currency="usd",
                amount_total=2500,
            ),
        )
    fulfil = MagicMock()
    with (
        _install_checkout(MagicMock(), fulfil=fulfil),
        patch.object(views, "_ledger", return_value=object()),
        patch.object(views, "checkout_credited", return_value=False),
    ):
        response = admin_client.get(f"{BASE}checkout/{session}/")
    assert response.status_code == 200
    _require(fulfil.call_count == 0, "unpaid session reached fulfil")
    assert response.json()["result"]["credited"] is False
