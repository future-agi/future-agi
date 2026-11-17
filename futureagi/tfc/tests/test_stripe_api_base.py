"""Stripe calls can be pointed at a test API base (W20: stripe-mock walks).

STRIPE_API_BASE is unset in production, and then every Stripe call goes where
it always went: the SDK's https://api.stripe.com. A walk stack sets it to
stripe-mock (e2e/stack/docker-compose.stripe-mock.yml) and then every Stripe
call this repo makes goes there instead, through one helper
(``tfc.stripe_client``): StripeClient instances (the landing's Checkout
Session read) and the SDK's module-level resources (the
switch_stripe_collection_method command). A value that is not an http(s) URL
with a host is refused, naming the variable, never the value.

The installed SDK runs end to end against an in-test HTTPClient: no network,
no database. Keys are synthetic and built at runtime; failures report fixed
messages. The helper is imported inside each test, so a missing helper fails
each test instead of the collection.
"""

import importlib
import json
import logging
import os
import secrets
import subprocess
import sys
import types
from pathlib import Path
from urllib.parse import urlsplit

import pytest
import stripe
from django.core.exceptions import ImproperlyConfigured

pytestmark = pytest.mark.unit

MOCK_BASE = "http://stripe-mock:12111"
MOCK_HOST = "stripe-mock:12111"
STRIPE_HOST = "api.stripe.com"


def _require(condition, message):
    # A fixed message only: never a key or a compared value.
    if not condition:
        pytest.fail(message, pytrace=False)


def _helper():
    return importlib.import_module("tfc.stripe_client")


def _resolve(value):
    from tfc.settings.stripe_billing import resolve_stripe_api_base

    return resolve_stripe_api_base(value)


def _key():
    return "sk_test_" + "fi" + secrets.token_hex(8)


def _session():
    return "cs_test_" + secrets.token_hex(16)


def _session_body(session_id):
    return json.dumps(
        {
            "id": session_id,
            "object": "checkout.session",
            "mode": "payment",
            "status": "complete",
            "payment_status": "paid",
            "livemode": False,
            "currency": "usd",
            "amount_total": 2500,
        }
    )


def _payment_method_body(payment_method_id):
    return json.dumps(
        {"id": payment_method_id, "object": "payment_method", "type": "card"}
    )


class FakeStripeHTTP(stripe.HTTPClient):
    """Answers every request with one canned 200 body and records each URL.
    Never opens a connection."""

    name = "fi-test-canned"

    def __init__(self, body):
        super().__init__()
        self.body = body
        self.urls = []

    def request(self, method, url, headers, post_data=None, **kwargs):
        self.urls.append(url)
        return self.body, 200, {"request-id": "req_" + secrets.token_hex(8)}

    def request_stream(self, method, url, headers, post_data=None, **kwargs):
        raise AssertionError("no Stripe call here streams")

    def close(self):
        pass


@pytest.fixture
def sdk_globals(monkeypatch):
    """The SDK's module-level state, set to its defaults and restored after."""
    monkeypatch.setattr(stripe, "api_base", stripe.DEFAULT_API_BASE)
    monkeypatch.setattr(stripe, "api_key", _key())
    monkeypatch.setattr(stripe, "default_http_client", None)
    monkeypatch.setattr(stripe, "log", None)
    monkeypatch.delenv("STRIPE_LOG", raising=False)


def _host(url):
    parts = urlsplit(url)
    return parts.scheme, parts.netloc


# --- the setting ------------------------------------------------------------------


@pytest.mark.parametrize("value", [None, ""], ids=["none", "empty"])
def test_unset_base_resolves_to_stripe_default(value):
    _require(_resolve(value) == "", "an unset STRIPE_API_BASE is not empty")


@pytest.mark.parametrize(
    "value, resolved",
    [
        (MOCK_BASE, MOCK_BASE),
        (MOCK_BASE + "/", MOCK_BASE),
        ("https://stripe-mock.test", "https://stripe-mock.test"),
        ("http://127.0.0.1:12111", "http://127.0.0.1:12111"),
    ],
    ids=["http-port", "trailing-slash", "https", "ip"],
)
def test_http_base_with_a_host_is_accepted(value, resolved):
    _require(_resolve(value) == resolved, "a valid STRIPE_API_BASE was changed")


@pytest.mark.parametrize(
    "value",
    [
        "ftp://stripe-mock:12111",
        "file:///etc/hosts",
        "javascript:void(0)",
        "stripe-mock:12111",
        "//stripe-mock:12111",
        "http://",
        "http://:12111",
        "http://walk@stripe-mock:12111",
        "http://walk:" + secrets.token_hex(4) + "@stripe-mock:12111",
        "http://stripe-mock:12111/?mode=x",
        "http://stripe-mock:12111/#x",
        "http://stripe-mock:port",
        " http://stripe-mock:12111",
        "http://stripe mock:12111",
        12111,
        b"http://stripe-mock:12111",
    ],
    ids=[
        "ftp",
        "file",
        "javascript",
        "no-scheme",
        "scheme-relative",
        "no-host",
        "port-only",
        "user",
        "user-password",
        "query",
        "fragment",
        "bad-port",
        "whitespace-padded",
        "inner-space",
        "int",
        "bytes",
    ],
)
def test_base_that_is_not_http_to_a_host_is_refused(value):
    with pytest.raises(ImproperlyConfigured) as refusal:
        _resolve(value)
    message = str(refusal.value)
    _require("STRIPE_API_BASE" in message, "the refusal does not name the variable")
    shown = value.decode() if isinstance(value, bytes) else str(value)
    _require(shown.strip() not in message, "the refusal shows the value")


@pytest.mark.parametrize(
    "character", ["\x00", "\x1b", "\x7f"], ids=["nul", "escape", "delete"]
)
def test_base_with_a_control_character_in_the_host_is_refused(character):
    """urlsplit keeps a control character inside the host, so the check
    itself refuses one, with the same refusal as any other bad value."""
    with pytest.raises(ImproperlyConfigured) as reference:
        _resolve("ftp://stripe-mock:12111")
    with pytest.raises(ImproperlyConfigured) as refusal:
        _resolve(f"http://stripe{character}mock:12111")
    _require(
        str(refusal.value) == str(reference.value),
        "a control character got another refusal",
    )


# --- the helper: unset changes nothing ------------------------------------------


@pytest.mark.parametrize("value", ["", None], ids=["empty", "none"])
def test_unset_base_keeps_every_stripe_call_on_stripe(settings, sdk_globals, value):
    helper = _helper()
    settings.STRIPE_API_BASE = value
    _require(helper.stripe_api_base() is None, "an unset base is reported as set")

    session = _session()
    fake = FakeStripeHTTP(_session_body(session))
    client = helper.stripe_client(_key(), max_network_retries=0, http_client=fake)
    client.v1.checkout.sessions.retrieve(session)
    _require(
        _host(fake.urls[0]) == ("https", STRIPE_HOST),
        "an unset base moved StripeClient off api.stripe.com",
    )

    helper.configure_stripe_module()
    _require(
        stripe.api_base == stripe.DEFAULT_API_BASE,
        "an unset base changed stripe.api_base",
    )
    payment_method = "pm_" + secrets.token_hex(8)
    module_fake = FakeStripeHTTP(_payment_method_body(payment_method))
    stripe.default_http_client = module_fake
    stripe.PaymentMethod.retrieve(payment_method)
    _require(
        _host(module_fake.urls[0]) == ("https", STRIPE_HOST),
        "an unset base moved module-level calls off api.stripe.com",
    )


# --- the helper: a set base reaches both SDK surfaces ----------------------------


def test_set_base_is_used_by_stripe_client(settings, sdk_globals, caplog):
    caplog.set_level(logging.DEBUG)
    helper = _helper()
    settings.STRIPE_API_BASE = MOCK_BASE
    _require(helper.stripe_api_base() == MOCK_BASE, "the set base is not reported")

    key = _key()
    session = _session()
    fake = FakeStripeHTTP(_session_body(session))
    client = helper.stripe_client(key, max_network_retries=0, http_client=fake)
    client.v1.checkout.sessions.retrieve(session)

    assert len(fake.urls) == 1
    _require(
        _host(fake.urls[0]) == ("http", MOCK_HOST),
        "StripeClient did not use STRIPE_API_BASE",
    )
    _require(
        urlsplit(fake.urls[0]).path == f"/v1/checkout/sessions/{session}",
        "StripeClient did not keep the API path under the base",
    )
    _require(
        not any(key in str(vars(record)) for record in caplog.records),
        "a log record carries the key",
    )


def test_set_base_is_used_by_module_level_calls(settings, sdk_globals):
    helper = _helper()
    settings.STRIPE_API_BASE = MOCK_BASE
    helper.configure_stripe_module()
    _require(stripe.api_base == MOCK_BASE, "stripe.api_base is not the set base")

    payment_method = "pm_" + secrets.token_hex(8)
    fake = FakeStripeHTTP(_payment_method_body(payment_method))
    stripe.default_http_client = fake
    stripe.PaymentMethod.retrieve(payment_method)

    assert len(fake.urls) == 1
    _require(
        _host(fake.urls[0]) == ("http", MOCK_HOST),
        "a module-level call did not use STRIPE_API_BASE",
    )
    _require(
        urlsplit(fake.urls[0]).path == f"/v1/payment_methods/{payment_method}",
        "a module-level call did not keep the API path under the base",
    )


def test_invalid_base_in_settings_is_refused_at_call_time(settings, sdk_globals):
    """A settings module that skipped the startup check still cannot send a
    Stripe call to an unchecked destination."""
    helper = _helper()
    settings.STRIPE_API_BASE = "ftp://stripe-mock:12111"
    with pytest.raises(ImproperlyConfigured):
        helper.stripe_api_base()
    with pytest.raises(ImproperlyConfigured):
        helper.stripe_client(_key(), max_network_retries=0)
    with pytest.raises(ImproperlyConfigured):
        helper.configure_stripe_module()
    _require(
        stripe.api_base == stripe.DEFAULT_API_BASE,
        "a refused base changed stripe.api_base",
    )


# --- the call sites route through the helper --------------------------------------


@pytest.mark.parametrize(
    "base, expected",
    [("", ("https", STRIPE_HOST)), (MOCK_BASE, ("http", MOCK_HOST))],
    ids=["unset", "stripe-mock"],
)
def test_checkout_read_goes_to_the_configured_base(
    settings, sdk_globals, monkeypatch, base, expected
):
    from agentcc.services import checkout_verification

    settings.STRIPE_SECRET_KEY = _key()
    settings.STRIPE_API_BASE = base
    session = _session()
    fake = FakeStripeHTTP(_session_body(session))
    monkeypatch.setattr(checkout_verification, "_http_client", lambda: fake)

    state = checkout_verification.read_checkout_session(session)

    _require(state.payment_status == "paid", "the read did not parse the session")
    assert len(fake.urls) == 1
    _require(_host(fake.urls[0]) == expected, "the read went to another base")


def test_checkout_read_with_an_invalid_base_sends_nothing(
    settings, sdk_globals, monkeypatch
):
    from agentcc.services import checkout_verification

    settings.STRIPE_SECRET_KEY = _key()
    settings.STRIPE_API_BASE = "ftp://stripe-mock:12111"
    session = _session()
    fake = FakeStripeHTTP(_session_body(session))
    monkeypatch.setattr(checkout_verification, "_http_client", lambda: fake)

    with pytest.raises(checkout_verification.CheckoutReadRefused) as refusal:
        checkout_verification.read_checkout_session(session)

    assert refusal.value.code == "stripe_read_failed"
    assert refusal.value.exception_type == "ImproperlyConfigured"
    _require(fake.urls == [], "a request was sent to a refused base")


class _Stop(Exception):
    """Ends the command at its first Stripe step."""


def _stub_stripe_service(seen):
    class StripeService:
        @staticmethod
        def _is_live():
            seen.append(stripe.api_base)
            raise _Stop

    module = types.ModuleType("ee.cloud.billing.stripe_service")
    module.StripeService = StripeService
    return module


@pytest.mark.requires_ee
@pytest.mark.parametrize(
    "base, expected",
    [("", stripe.DEFAULT_API_BASE), (MOCK_BASE, MOCK_BASE)],
    ids=["unset", "stripe-mock"],
)
def test_switch_command_points_module_calls_at_the_base(
    settings, sdk_globals, monkeypatch, base, expected
):
    """The command's module-level Stripe calls (PaymentMethod.retrieve,
    Subscription.modify) use the base from its first Stripe step on. Cloud
    billing's StripeService is not in this repo: a stub stops the command
    there, before any query runs."""
    from ee.usage.management.commands.switch_stripe_collection_method import (
        Command,
    )

    seen = []
    monkeypatch.setitem(
        sys.modules, "ee.cloud.billing.stripe_service", _stub_stripe_service(seen)
    )
    settings.STRIPE_API_BASE = base

    with pytest.raises(_Stop):
        Command().handle(org_id="", dry_run=True)

    _require(seen == [expected], "the command's Stripe calls use another base")


# A test API base must never receive a live key (money review W20SM-MONEY-01):
# a deployment with both set is misconfigured, and the landing read would
# otherwise send its live key to that host and trust its answer.


def test_api_base_with_a_live_key_is_refused_at_settings_time():
    from tfc.settings.stripe_billing import refuse_api_base_with_live

    with pytest.raises(ImproperlyConfigured) as refused:
        refuse_api_base_with_live(MOCK_BASE, True)
    _require("STRIPE_API_BASE" in str(refused.value), "the refusal names the variable")
    _require(MOCK_BASE not in str(refused.value), "the refusal never echoes the value")
    refuse_api_base_with_live("", True)  # unset with a live key: unchanged
    refuse_api_base_with_live(MOCK_BASE, False)  # test or empty key: unchanged


def test_api_base_with_a_live_key_sends_nothing(settings, sdk_globals):
    from tfc.stripe_client import stripe_api_base, stripe_client

    settings.STRIPE_API_BASE = MOCK_BASE
    settings.STRIPE_LIVE = True
    with pytest.raises(ImproperlyConfigured):
        stripe_api_base()
    with pytest.raises(ImproperlyConfigured):
        stripe_client(_key())


@pytest.mark.parametrize("key_prefix", ["sk_live_", "rk_live_"])
def test_api_base_with_a_live_key_passed_to_stripe_client_sends_nothing(
    settings, sdk_globals, key_prefix
):
    """The call-time guard checks the key stripe_client is handed, not only
    STRIPE_LIVE: with the flag False (a settings override, say) a live key
    still sends nothing to a test API base. With the base unset the same key
    goes to Stripe as before."""
    helper = _helper()
    settings.STRIPE_API_BASE = MOCK_BASE
    settings.STRIPE_LIVE = False
    key = _synthetic(key_prefix)
    session = _session()
    fake = FakeStripeHTTP(_session_body(session))
    refusal = ""
    try:
        client = helper.stripe_client(key, max_network_retries=0, http_client=fake)
        client.v1.checkout.sessions.retrieve(session)
    except ImproperlyConfigured as refused:
        refusal = str(refused)
    _require(fake.urls == [], "a live key was sent to a test API base")
    _require("STRIPE_API_BASE" in refusal, "the refusal does not name the variable")
    _require(MOCK_BASE not in refusal, "the refusal echoes the API base")
    _require(key not in refusal, "the refusal echoes the key")

    settings.STRIPE_API_BASE = ""
    client = helper.stripe_client(key, max_network_retries=0, http_client=fake)
    client.v1.checkout.sessions.retrieve(session)
    _require(
        [_host(url) for url in fake.urls] == [("https", STRIPE_HOST)],
        "an unset base moved a live-key call off api.stripe.com",
    )


# The guard runs when the settings module is imported (settings.py, after
# STRIPE_LIVE and STRIPE_API_BASE resolve). These load the real settings in a
# fresh interpreter, so removing that call fails the first test (money read
# W20-STRIPE-GUARD-READ-r1, P2). Values exist only in the child's
# environment; probes print a boolean or "loaded", never a value.

BACKEND = Path(__file__).resolve().parents[2]
# Names that change what the settings resolve; the child gets only the ones a
# test passes.
_SETTINGS_NAMES = (
    "STRIPE_API_BASE",
    "STRIPE_SECRET_KEY",
    "WEBHOOK_SECRET_LIVE",
    "WEBHOOK_SECRET_TEST",
    "BILLING_LEDGER_ACCEPT_LIVE",
    "BILLING_LEDGER_ACCEPT_LIVE_OWNER_GO",
)


def _load_settings(probe, **env):
    child_env = {k: v for k, v in os.environ.items() if k not in _SETTINGS_NAMES} | env
    return subprocess.run(
        [
            sys.executable,
            "-c",
            "import importlib, os; "
            "s = importlib.import_module(os.environ['DJANGO_SETTINGS_MODULE']); "
            f"print({probe})",
        ],
        env=child_env,
        cwd=BACKEND,
        capture_output=True,
        text=True,
        timeout=120,
    )


def _synthetic(prefix):
    return prefix + "SYNTHETIC_NOT_A_SECRET_" + secrets.token_hex(8)


def _secrets_for(key_prefix):
    return {
        "STRIPE_SECRET_KEY": _synthetic(key_prefix),
        "WEBHOOK_SECRET_TEST": "whsec_SYNTHETIC_TEST_NOT_A_SECRET",
        "WEBHOOK_SECRET_LIVE": "whsec_SYNTHETIC_LIVE_NOT_A_SECRET",
    }


@pytest.mark.parametrize("key_prefix", ["sk_live_", "rk_live_"])
def test_settings_with_api_base_and_a_live_key_refuse_to_start(key_prefix):
    env = _secrets_for(key_prefix)
    # The probe names no new setting: with the guard's call removed the
    # import succeeds and prints "loaded".
    loaded = _load_settings("'loaded'", STRIPE_API_BASE=MOCK_BASE, **env)
    output = loaded.stdout + loaded.stderr
    _require(loaded.returncode != 0, "settings loaded a test API base with a live key")
    _require(
        "ImproperlyConfigured" in output, "the refusal is not ImproperlyConfigured"
    )
    _require("STRIPE_API_BASE" in output, "the refusal does not name the variable")
    _require(MOCK_BASE not in output, "the refusal echoes the API base")
    _require(env["STRIPE_SECRET_KEY"] not in output, "the refusal echoes the key")


@pytest.mark.parametrize("key_prefix", ["sk_test_", "rk_test_"])
def test_settings_with_api_base_and_a_test_key_load_it(key_prefix):
    loaded = _load_settings(
        f"s.STRIPE_LIVE is False and s.STRIPE_API_BASE == {MOCK_BASE!r}",
        STRIPE_API_BASE=MOCK_BASE + "/",
        **_secrets_for(key_prefix),
    )
    _require(loaded.returncode == 0, "settings refused a test API base with a test key")
    _require(
        loaded.stdout.splitlines()[-1:] == ["True"],
        "the test API base is not the resolved value",
    )


@pytest.mark.parametrize("base", [None, ""])
@pytest.mark.parametrize("key_prefix", ["sk_live_", "sk_test_"])
def test_settings_without_api_base_load_as_production(base, key_prefix):
    env = _secrets_for(key_prefix)
    if base is not None:
        env["STRIPE_API_BASE"] = base
    loaded = _load_settings("s.STRIPE_API_BASE == ''", **env)
    _require(loaded.returncode == 0, "settings refused a deployment with no API base")
    _require(
        loaded.stdout.splitlines()[-1:] == ["True"],
        "an unset API base did not resolve to Stripe's own",
    )
