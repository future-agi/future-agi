"""A raw SDK stderr debug mode must refuse before any request is made."""

from unittest.mock import Mock

import pytest
import stripe

from agentcc.services import checkout_verification

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("source", ["environment", "sdk-global"])
@pytest.mark.parametrize("level", ["debug", "info"])
def test_raw_sdk_logging_refuses_before_http(settings, monkeypatch, source, level):
    settings.STRIPE_SECRET_KEY = "rk_test_SYNTHETIC_NOT_A_CREDENTIAL"
    monkeypatch.delenv("STRIPE_LOG", raising=False)
    monkeypatch.setattr(stripe, "log", None, raising=False)
    if source == "environment":
        monkeypatch.setenv("STRIPE_LOG", level)
    else:
        monkeypatch.setattr(stripe, "log", level)
    factory = Mock()
    monkeypatch.setattr(checkout_verification, "_http_client", factory)
    with pytest.raises(checkout_verification.CheckoutReadRefused) as caught:
        checkout_verification.read_checkout_session(
            "cs_test_SyntheticRawLoggingSession0000"
        )
    assert caught.value.code == "stripe_raw_logging_enabled"
    assert caught.value.exception_type is None
    factory.assert_not_called()
