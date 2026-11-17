"""W10-AUTH-ORG-U01: an AWS Marketplace buyer whose sign-up is refused for a
reason they can act on is told that reason, and a fixable one does not cost
them their setup link.

The onboarding code in the sign-up URL lives at most 60 seconds and the first
redeem spends it (CR-07). Every refusal after the redeem used to collapse to a
bare "Signup failed", and an email that already had an account also spent the
code, so the corrected retry came back as an expired link.

These tests pin:

- an email that already has an account is named, and the same code still
  finishes sign-up with a corrected email, inside its original 60 seconds;
- returning the code never extends its life and never brings back a login code;
- "this subscription already has an account" and an expired onboarding session
  reach the app as their own reasons, which the register view acts on.

The view runs DB-free with ``process_aws_onboarding`` faked, as the CR-07 tests
do, over a real isolated LocMemCache.
"""

from __future__ import annotations

import json
import time
import uuid
from contextlib import contextmanager
from unittest.mock import call, patch

import pytest
from django.core.cache import cache
from django.test import RequestFactory

from accounts.services import handoff_exchange
from accounts.services.handoff_exchange import (
    KIND_AWS_ONBOARDING,
    HandoffRefused,
    issue,
    redeem,
)

# synthetic test value
ORIGINAL_ONBOARDING = "FAKE-original-onboarding"
# synthetic test value
SYNTHETIC_ACCESS = "synthetic-access-not-a-credential"

EMAIL_TAKEN = "An account with this email already exists"
EMAIL_TAKEN_MESSAGE = (
    "An account with this email already exists. "
    "Sign in instead, or use a different email."
)
ALREADY_SET_UP = "This AWS Marketplace customer already has an account"
EXPIRED = "Invalid or expired onboarding token"


@pytest.fixture(autouse=True)
def isolated_handoff_cache(settings):
    settings.CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": f"aws-signup-recovery-{uuid.uuid4()}",
        }
    }
    cache.clear()
    yield
    cache.clear()


@contextmanager
def _expired_cache_clock(seconds_from_now):
    """Move LocMemCache's own clock; the expiry it stored used the real one."""
    target = time.time() + seconds_from_now
    with patch("django.core.cache.backends.locmem.time") as fake_time:
        fake_time.time.return_value = target
        yield


def _views():
    from accounts.views import aws_marketplace as views

    return views


def _signup(views, code, email="person@example.test"):
    body = json.dumps(
        {"onboarding_token": code, "email": email, "full_name": "Test Person"}
    )
    return views.aws_marketplace_signup(
        RequestFactory().post("/", body, content_type="application/json")
    )


def _onboarding_code():
    return issue(KIND_AWS_ONBOARDING, {"onboarding_token": ORIGINAL_ONBOARDING})


def test_email_with_an_account_is_named_and_the_corrected_retry_finishes():
    views = _views()
    code = _onboarding_code()
    with patch.object(
        views,
        "process_aws_onboarding",
        side_effect=[ValueError(EMAIL_TAKEN), "corrected@example.test"],
    ) as process:
        first = _signup(views, code, "person@example.test")
        second = _signup(views, code, "corrected@example.test")

    assert first.status_code == 400
    assert EMAIL_TAKEN_MESSAGE in str(first.data)
    assert "Signup failed" not in str(first.data)
    assert code not in str(first.data)
    assert second.status_code == 200
    assert process.call_args_list == [
        call(ORIGINAL_ONBOARDING, "person@example.test", "Test Person"),
        call(ORIGINAL_ONBOARDING, "corrected@example.test", "Test Person"),
    ]
    # A finished sign-up spends the code for good.
    with pytest.raises(HandoffRefused):
        redeem(code, KIND_AWS_ONBOARDING)


def test_returned_code_still_expires_at_its_original_deadline():
    views = _views()
    code = _onboarding_code()
    with patch.object(
        views, "process_aws_onboarding", side_effect=ValueError(EMAIL_TAKEN)
    ) as process:
        first = _signup(views, code)
        with _expired_cache_clock(handoff_exchange.HANDOFF_CODE_TTL_SECONDS + 1):
            late = _signup(views, code, "corrected@example.test")

    assert first.status_code == 400
    assert EMAIL_TAKEN_MESSAGE in str(first.data)
    assert late.status_code == 400
    assert EXPIRED in str(late.data)
    assert process.call_count == 1


def test_subscription_that_already_has_an_account_gets_that_reason():
    views = _views()
    code = _onboarding_code()
    with patch.object(
        views, "process_aws_onboarding", side_effect=ValueError(ALREADY_SET_UP)
    ) as process:
        response = _signup(views, code)
        retry = _signup(views, code)

    assert response.status_code == 400
    assert ALREADY_SET_UP in str(response.data)
    assert "Signup failed" not in str(response.data)
    # Nothing a new email could fix: this code stays spent.
    assert retry.status_code == 400
    assert EXPIRED in str(retry.data)
    assert process.call_count == 1


def test_expired_onboarding_session_is_reported_as_an_expired_link():
    views = _views()
    code = _onboarding_code()
    with patch.object(views, "process_aws_onboarding", side_effect=ValueError(EXPIRED)):
        response = _signup(views, code)

    assert response.status_code == 400
    assert EXPIRED in str(response.data)
    assert "Signup failed" not in str(response.data)


def test_restore_returns_only_onboarding_codes_and_never_extends_them():
    login = issue("sso", {"access": SYNTHETIC_ACCESS, "refresh": None})
    login_payload, login_deadline = handoff_exchange.redeem_with_deadline(login, "sso")
    with pytest.raises(ValueError):
        handoff_exchange.restore(login, "sso", login_payload, login_deadline)
    with pytest.raises(HandoffRefused):
        redeem(login, "sso")

    issued_at = time.time()
    code = _onboarding_code()
    payload, deadline = handoff_exchange.redeem_with_deadline(code, KIND_AWS_ONBOARDING)
    assert payload == {"onboarding_token": ORIGINAL_ONBOARDING}
    assert deadline <= issued_at + handoff_exchange.HANDOFF_CODE_TTL_SECONDS + 1
    assert handoff_exchange.restore(code, KIND_AWS_ONBOARDING, payload, deadline)
    # A live record is never overwritten.
    assert not handoff_exchange.restore(
        code, KIND_AWS_ONBOARDING, payload, deadline + 600
    )
    assert redeem(code, KIND_AWS_ONBOARDING) == payload
    # A deadline that has passed is never put back.
    assert not handoff_exchange.restore(
        code, KIND_AWS_ONBOARDING, payload, time.time() - 1
    )
    with pytest.raises(HandoffRefused):
        redeem(code, KIND_AWS_ONBOARDING)


# Head-55 repair: the 60-second redirect code is exchanged when the register
# page opens (W10-AUTH-ORG-L02) for a form attempt that lives as long as the
# purchase session, so the buyer is not timed out while typing.

APP_ORIGIN = "https://app.example.test"


@pytest.fixture
def app_origin(settings):
    settings.APP_BASE_URL = APP_ORIGIN


def _purchase_session():
    cache.set(
        f"aws_onboard:{ORIGINAL_ONBOARDING}",
        {"customer_identifier": "synthetic-customer", "product_code": "test-product"},
        timeout=15 * 60,
    )


def _exchange(views, code):
    return views.AWSOnboardingExchangeView.as_view()(
        RequestFactory().post(
            "/accounts/aws-marketplace/onboarding-exchange/",
            json.dumps({"code": code}),
            content_type="application/json",
            HTTP_ORIGIN=APP_ORIGIN,
        )
    )


def _form_attempt(views):
    _purchase_session()
    code = _onboarding_code()
    response = _exchange(views, code)
    assert response.status_code == 200
    attempt = response.data["onboarding_attempt"]
    assert attempt not in (code, ORIGINAL_ONBOARDING)
    return code, attempt


def _seconds_later(seconds):
    return patch("time.time", return_value=time.time() + seconds)


def test_form_submitted_61_seconds_after_arrival_finishes_sign_up(app_origin):
    views = _views()
    _code, attempt = _form_attempt(views)
    # The same buyer without an exchange: the bare redirect code is gone.
    bare_code = _onboarding_code()
    with (
        _seconds_later(handoff_exchange.HANDOFF_CODE_TTL_SECONDS + 1),
        patch.object(
            views, "process_aws_onboarding", return_value="person@example.test"
        ) as process,
    ):
        late_bare = _signup(views, bare_code)
        late_form = _signup(views, attempt)

    assert late_bare.status_code == 400
    assert EXPIRED in str(late_bare.data)
    assert late_form.status_code == 200
    assert "Signup failed" not in str(late_form.data)
    process.assert_called_once_with(
        ORIGINAL_ONBOARDING, "person@example.test", "Test Person"
    )


def test_form_attempt_is_single_use(app_origin):
    views = _views()
    code, attempt = _form_attempt(views)
    # The redirect code was spent by the exchange: a second tab cannot mint
    # another attempt from it.
    assert _exchange(views, code).status_code == 400
    with patch.object(
        views, "process_aws_onboarding", return_value="person@example.test"
    ) as process:
        first = _signup(views, attempt)
        again = _signup(views, attempt, "other@example.test")

    assert first.status_code == 200
    assert again.status_code == 400
    assert EXPIRED in str(again.data)
    assert attempt not in str(again.data)
    assert process.call_count == 1


def test_expired_form_attempt_is_reported_as_an_expired_link(app_origin):
    from accounts.services import aws_onboarding_attempt

    views = _views()
    _code, attempt = _form_attempt(views)
    with (
        _seconds_later(aws_onboarding_attempt.ATTEMPT_TTL_SECONDS + 1),
        patch.object(views, "process_aws_onboarding") as process,
    ):
        response = _signup(views, attempt)

    assert response.status_code == 400
    assert EXPIRED in str(response.data)
    assert "Signup failed" not in str(response.data)
    process.assert_not_called()


def test_email_with_an_account_keeps_the_form_attempt_after_60_seconds(app_origin):
    views = _views()
    _code, attempt = _form_attempt(views)
    with (
        _seconds_later(handoff_exchange.HANDOFF_CODE_TTL_SECONDS + 1),
        patch.object(
            views,
            "process_aws_onboarding",
            side_effect=[ValueError(EMAIL_TAKEN), "corrected@example.test"],
        ) as process,
    ):
        taken = _signup(views, attempt, "person@example.test")
        corrected = _signup(views, attempt, "corrected@example.test")
        spent = _signup(views, attempt, "corrected@example.test")

    assert taken.status_code == 400
    assert EMAIL_TAKEN_MESSAGE in str(taken.data)
    assert corrected.status_code == 200
    assert spent.status_code == 400
    assert EXPIRED in str(spent.data)
    assert process.call_count == 2


def test_subscription_with_an_account_spends_the_form_attempt(app_origin):
    views = _views()
    _code, attempt = _form_attempt(views)
    with patch.object(
        views, "process_aws_onboarding", side_effect=ValueError(ALREADY_SET_UP)
    ) as process:
        response = _signup(views, attempt)
        retry = _signup(views, attempt)

    assert response.status_code == 400
    assert ALREADY_SET_UP in str(response.data)
    assert "Signup failed" not in str(response.data)
    assert retry.status_code == 400
    assert EXPIRED in str(retry.data)
    assert process.call_count == 1
