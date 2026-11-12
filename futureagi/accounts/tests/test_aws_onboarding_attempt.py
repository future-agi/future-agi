"""AWS registration exchanges its redirect before waiting for human input."""

import json
import time
import uuid
from unittest.mock import patch

import pytest
from django.core.cache import cache
from django.test import RequestFactory

from accounts.services.handoff_exchange import HandoffRefused, issue, redeem
from accounts.views import aws_marketplace as views


@pytest.fixture(autouse=True)
def isolated_onboarding(settings):
    settings.CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": f"aws-form-{uuid.uuid4()}",
        }
    }
    settings.APP_BASE_URL = "https://app.example.test"
    cache.clear()
    yield
    cache.clear()


def start_attempt():
    original = "synthetic-purchase-session"
    cache.set(
        f"aws_onboard:{original}",
        {"customer_identifier": "synthetic-customer", "product_code": "test-product"},
        timeout=900,
    )
    code = issue("aws_marketplace_onboarding", {"onboarding_token": original})
    response = views.AWSOnboardingExchangeView.as_view()(
        RequestFactory().post(
            "/accounts/aws-marketplace/onboarding-exchange/",
            json.dumps({"code": code}),
            content_type="application/json",
            HTTP_ORIGIN="https://app.example.test",
        )
    )
    assert response.status_code == 200
    assert response["Cache-Control"] == "no-store"
    assert response["Referrer-Policy"] == "no-referrer"
    attempt = response.data["onboarding_attempt"]
    assert attempt not in (code, original)
    with pytest.raises(HandoffRefused):
        redeem(code, "aws_marketplace_onboarding")
    return attempt


def signup(attempt, email="person@example.test"):
    return views.aws_marketplace_signup(
        RequestFactory().post(
            "/",
            json.dumps(
                {"onboarding_token": attempt, "email": email, "full_name": "Test Buyer"}
            ),
            content_type="application/json",
        )
    )


def test_form_can_submit_after_redirect_code_expires():
    attempt = start_attempt()
    later = time.time() + 61
    with patch("time.time", return_value=later), patch.object(
        views, "process_aws_onboarding", return_value="person@example.test"
    ) as process:
        assert signup(attempt).status_code == 200
        assert signup(attempt).status_code == 400
    process.assert_called_once_with(
        "synthetic-purchase-session", "person@example.test", "Test Buyer"
    )


def test_correcting_existing_email_reuses_form_attempt():
    attempt = start_attempt()
    with patch.object(
        views,
        "process_aws_onboarding",
        side_effect=[ValueError("An account with this email already exists"), "new@example.test"],
    ) as process:
        rejected = signup(attempt, "existing@example.test")
        assert rejected.status_code == 400
        assert "An account with this email already exists" in str(rejected.data)
        assert signup(attempt, "new@example.test").status_code == 200
        assert signup(attempt, "new@example.test").status_code == 400
    assert process.call_count == 2


def test_attempt_does_not_extend_purchase_session():
    attempt = start_attempt()
    cache.delete("aws_onboard:synthetic-purchase-session")
    with patch.object(views, "process_aws_onboarding") as process:
        response = signup(attempt)
    assert response.status_code == 400
    assert "Invalid or expired onboarding token" in str(response.data)
    process.assert_not_called()


def test_attempt_expires_after_fifteen_minutes():
    attempt = start_attempt()
    later = time.time() + 901
    with patch("time.time", return_value=later), patch.object(
        views, "process_aws_onboarding"
    ) as process:
        assert signup(attempt).status_code == 400
    process.assert_not_called()


def test_concurrent_submit_cannot_process_the_same_attempt_twice():
    attempt = start_attempt()
    competing_responses = []

    def process(*_args):
        competing_responses.append(signup(attempt))
        return "person@example.test"

    with patch.object(views, "process_aws_onboarding", side_effect=process) as process_mock:
        assert signup(attempt).status_code == 200
    assert competing_responses[0].status_code == 400
    process_mock.assert_called_once()
