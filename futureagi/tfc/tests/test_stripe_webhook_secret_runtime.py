"""The running settings never hold an empty Stripe webhook signing secret.

MUST-FIX G: STRIPE_WEBHOOK_SECRET used to fall back to "" when the mode's
secret was unset, so an event signed with the empty key verified. These checks
read only the configured django.conf.settings and the settings source: they
import no new module, so on the base they fail for the defect itself.

The forged event is checked twice: with the standard library (any receiver
that compares the HMAC) and with the stripe SDK's verify_header. Some stripe
releases refuse an empty secret inside verify_header and others do not, so the
standard-library check is what makes this independent of the SDK release.

Values are synthetic or the process's own refusal value; failures report
fixed messages, never a value.
"""

import ast
import hashlib
import hmac
import os
import secrets
import subprocess
import sys
import time
from pathlib import Path

import pytest
import stripe
from django.conf import settings

pytestmark = pytest.mark.unit

SETTINGS_SOURCE = Path(__file__).resolve().parents[1] / "settings" / "settings.py"
BACKEND = Path(__file__).resolve().parents[2]
# Names that would legitimately change the resolved values in this process.
STRIPE_NAMES = (
    "STRIPE_SECRET_KEY",
    "WEBHOOK_SECRET_LIVE",
    "WEBHOOK_SECRET_TEST",
    "BILLING_LEDGER_ACCEPT_LIVE",
    "BILLING_LEDGER_ACCEPT_LIVE_OWNER_GO",
)
ENV_READERS = {"getenv", "get"}


def _require(condition, message):
    # A fixed message only: never a compared value.
    if not condition:
        pytest.fail(message, pytrace=False)


def _stripe_names_unset():
    return not any((os.environ.get(name) or "").strip() for name in STRIPE_NAMES)


def _hmac_hex(key, timestamp, payload):
    return hmac.new(
        key.encode("utf-8"), f"{timestamp}.{payload}".encode(), hashlib.sha256
    ).hexdigest()


# --- T1.6 ---------------------------------------------------------------------


def test_runtime_settings_never_empty_and_refuse_live():
    secret = getattr(settings, "STRIPE_WEBHOOK_SECRET", None)
    alias = getattr(settings, "WEBHOOK_SECRET", None)
    _require(isinstance(secret, str), "STRIPE_WEBHOOK_SECRET is not a string")
    _require(secret.strip() != "", "STRIPE_WEBHOOK_SECRET is empty")
    _require(secret == alias, "WEBHOOK_SECRET is not the same resolved value")

    configured = getattr(settings, "STRIPE_WEBHOOK_SECRET_CONFIGURED", None)
    accept_live = getattr(settings, "BILLING_LEDGER_ACCEPT_LIVE", None)
    owner_go = getattr(settings, "BILLING_LEDGER_ACCEPT_LIVE_OWNER_GO", None)
    _require(isinstance(configured, bool), "STRIPE_WEBHOOK_SECRET_CONFIGURED unset")
    _require(isinstance(accept_live, bool), "BILLING_LEDGER_ACCEPT_LIVE undefined")
    _require(isinstance(owner_go, str), "BILLING_LEDGER_ACCEPT_LIVE_OWNER_GO unset")
    if _stripe_names_unset():
        # The E2B/CI test env sets no Stripe name (plan step 4, S9).
        _require(configured is False, "an unset secret reads as configured")
        _require(accept_live is False, "live money accepted by default")
        _require(owner_go == "", "an owner-go reference without acceptance")


# --- T1.7 ---------------------------------------------------------------------


def _names_webhook_secret(node):
    return any(
        isinstance(inner, ast.Constant)
        and isinstance(inner.value, str)
        and inner.value.startswith("WEBHOOK_SECRET")
        for inner in ast.walk(node)
    )


def _default_argument(call):
    if len(call.args) >= 2:
        return call.args[1]
    for keyword in call.keywords:
        if keyword.arg == "default":
            return keyword.value
    return None


def _empty_webhook_fallbacks(tree):
    """Calls such as os.getenv("WEBHOOK_SECRET_..." or a conditional naming
    one, "") or os.environ.get(..., ""): an empty default for a secret."""
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        if name not in ENV_READERS or not _names_webhook_secret(node.args[0]):
            continue
        default = _default_argument(node)
        if isinstance(default, ast.Constant) and default.value in ("", None):
            found.append(node.lineno)
        elif default is None and name == "get":
            found.append(node.lineno)
    return found


def _call_name(node):
    if not isinstance(node, ast.Call):
        return None
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return None


def _assigned_names(target):
    if isinstance(target, ast.Name):
        return [target.id]
    if isinstance(target, ast.Tuple | ast.List):
        return [elt.id for elt in target.elts if isinstance(elt, ast.Name)]
    return []


def test_settings_source_has_no_empty_webhook_fallback():
    tree = ast.parse(SETTINGS_SOURCE.read_text(encoding="utf-8"))
    _require(
        _empty_webhook_fallbacks(tree) == [],
        "settings.py reads a WEBHOOK_SECRET_* variable with an empty default",
    )

    resolved, aliased = False, False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        names = [name for target in node.targets for name in _assigned_names(target)]
        if "STRIPE_WEBHOOK_SECRET" in names:
            resolved = resolved or (
                _call_name(node.value) == "resolve_stripe_webhook_secret"
            )
        if names == ["WEBHOOK_SECRET"]:
            aliased = (
                isinstance(node.value, ast.Name)
                and node.value.id == "STRIPE_WEBHOOK_SECRET"
            )
    _require(resolved, "STRIPE_WEBHOOK_SECRET is not resolve_stripe_webhook_secret's")
    _require(aliased, "WEBHOOK_SECRET is not the STRIPE_WEBHOOK_SECRET alias")


# --- T1.8 ---------------------------------------------------------------------


@pytest.mark.parametrize("name", ["STRIPE_WEBHOOK_SECRET", "WEBHOOK_SECRET"])
def test_runtime_secret_refuses_event_forged_with_empty_key(name):
    secret = getattr(settings, name, None)
    _require(isinstance(secret, str), f"{name} is not a string")
    payload = (
        '{"id": "evt_' + secrets.token_hex(12) + '", "object": "event", '
        '"type": "checkout.session.completed"}'
    )
    timestamp = int(time.time())
    forged = _hmac_hex("", timestamp, payload)
    header = f"t={timestamp},v1={forged}"

    # Any receiver comparing the HMAC itself: the runtime secret must not
    # sign like the empty key.
    same = hmac.compare_digest(_hmac_hex(secret, timestamp, payload), forged)
    _require(same is False, f"{name} signs like the empty key")

    # The stripe SDK's verification refuses the forged event as well.
    try:
        stripe.WebhookSignature.verify_header(payload, header, secret, tolerance=300)
        verified = True
    except stripe.SignatureVerificationError:
        verified = False
    _require(verified is False, f"an event forged with the empty key verified ({name})")


# --- T1.9 (supplementary): the real settings import ------------------------------


def _load_settings(probe, **env):
    """Import the settings module in a fresh interpreter with ``env`` added
    (values only in the child's environment) and print ``probe`` on it as
    ``s``. Probes print "loaded" or a boolean, never a value."""
    child_env = {k: v for k, v in os.environ.items() if k not in STRIPE_NAMES} | env
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


def _error_class(loaded):
    """The class of the child's final error (text before the first colon of
    stderr's last line): a name only, never the message."""
    lines = loaded.stderr.strip().splitlines()
    return lines[-1].split(":", 1)[0][:80] if lines else "none"


@pytest.mark.parametrize(
    "key_prefix, missing, other",
    [
        ("sk_test_", "WEBHOOK_SECRET_TEST", "WEBHOOK_SECRET_LIVE"),
        ("sk_live_", "WEBHOOK_SECRET_LIVE", "WEBHOOK_SECRET_TEST"),
        ("rk_test_", "WEBHOOK_SECRET_TEST", "WEBHOOK_SECRET_LIVE"),
        ("rk_live_", "WEBHOOK_SECRET_LIVE", "WEBHOOK_SECRET_TEST"),
    ],
    ids=["test-key", "live-key", "restricted-test", "restricted-live"],
)
def test_settings_with_a_stripe_key_and_no_secret_refuse_to_start(
    key_prefix, missing, other
):
    key, other_secret = _synthetic(key_prefix), _synthetic("whsec_")
    # The probe names no new setting: on a tree without the check the import
    # succeeds and prints "loaded".
    loaded = _load_settings("'loaded'", STRIPE_SECRET_KEY=key, **{other: other_secret})
    output = loaded.stdout + loaded.stderr
    _require(loaded.returncode != 0, "settings loaded with a Stripe key and no secret")
    _require(
        "ImproperlyConfigured" in output, "the refusal is not ImproperlyConfigured"
    )
    _require(missing in output, "the refusal does not name the missing variable")
    _require(key not in output, "the refusal echoes STRIPE_SECRET_KEY")
    _require(other_secret not in output, "the refusal echoes the other secret")


def test_settings_with_a_stripe_key_and_its_secret_load_configured():
    secret = _synthetic("whsec_")
    loaded = _load_settings(
        "s.STRIPE_WEBHOOK_SECRET_CONFIGURED is True "
        "and s.STRIPE_WEBHOOK_SECRET == os.environ['WEBHOOK_SECRET_TEST'] "
        "and s.WEBHOOK_SECRET == s.STRIPE_WEBHOOK_SECRET",
        STRIPE_SECRET_KEY=_synthetic("sk_test_"),
        WEBHOOK_SECRET_TEST=secret,
    )
    _require(
        loaded.returncode == 0,
        "settings refused a configured Stripe secret: " + _error_class(loaded),
    )
    _require(
        loaded.stdout.splitlines()[-1:] == ["True"],
        "the configured secret is not the resolved value",
    )
    _require(secret not in loaded.stdout + loaded.stderr, "the secret was printed")


@pytest.mark.parametrize("value", ["yes", "1", "on"])
def test_settings_with_a_malformed_accept_live_refuse_to_start(value):
    marker = value + secrets.token_hex(4)
    # The probe names no new setting: on a tree without the check the import
    # succeeds and prints "loaded".
    loaded = _load_settings("'loaded'", BILLING_LEDGER_ACCEPT_LIVE=marker)
    output = loaded.stdout + loaded.stderr
    _require(loaded.returncode != 0, "settings loaded a malformed accept-live value")
    _require(
        "ImproperlyConfigured" in output, "the refusal is not ImproperlyConfigured"
    )
    _require("BILLING_LEDGER_ACCEPT_LIVE" in output, "the refusal names no variable")
    _require(marker not in output, "the refusal echoes the value")


@pytest.mark.parametrize("family", ["sk", "rk"])
@pytest.mark.parametrize("mode", ["test", "live"])
def test_settings_mode_and_webhook_secret_follow_credential_family(family, mode):
    key = _synthetic(f"{family}_{mode}_")
    loaded = _load_settings(
        f"s.STRIPE_LIVE is {mode == 'live'} and "
        f"s.STRIPE_WEBHOOK_SECRET == os.environ['WEBHOOK_SECRET_{mode.upper()}']",
        STRIPE_SECRET_KEY=key,
        WEBHOOK_SECRET_TEST="whsec_SYNTHETIC_TEST_NOT_A_SECRET",
        WEBHOOK_SECRET_LIVE="whsec_SYNTHETIC_LIVE_NOT_A_SECRET",
    )
    _require(
        loaded.returncode == 0, "settings refused a supported synthetic credential"
    )
    _require(
        loaded.stdout.splitlines()[-1:] == ["True"],
        "credential mode selected the wrong webhook secret",
    )
    _require(key not in loaded.stdout + loaded.stderr, "settings printed a credential")


@pytest.mark.parametrize("kind", ["unknown", "publishable", "truncated", "whitespace"])
def test_settings_unrecognized_credential_refuses_to_start(kind):
    key = {
        "unknown": "SYNTHETIC_UNKNOWN_CREDENTIAL",
        "publishable": "pk_live_SYNTHETIC_NOT_A_SERVER_CREDENTIAL",
        "truncated": "rk_live_",
        "whitespace": " " + _synthetic("sk_live_"),
    }[kind]
    loaded = _load_settings(
        "'loaded'",
        STRIPE_SECRET_KEY=key,
        WEBHOOK_SECRET_TEST="whsec_SYNTHETIC_TEST_NOT_A_SECRET",
        WEBHOOK_SECRET_LIVE="whsec_SYNTHETIC_LIVE_NOT_A_SECRET",
    )
    output = loaded.stdout + loaded.stderr
    _require(loaded.returncode != 0, "settings accepted an unrecognized credential")
    _require(
        "ImproperlyConfigured" in output,
        "credential refusal is not ImproperlyConfigured",
    )
    _require("STRIPE_SECRET_KEY" in output, "credential refusal names no setting")
    _require(key not in output, "credential refusal printed its value")
