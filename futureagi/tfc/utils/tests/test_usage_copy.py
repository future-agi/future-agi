"""PROPOSED copy: preserve legacy classifications without a purchase claim."""

import pytest

from tfc.utils.error_codes import (
    get_error_for_api_status,
    get_error_message,
    get_specific_error_message,
    get_usage_error_code,
)

MESSAGE = (
    "This request could not be completed. Try again later; "
    "if it keeps happening, contact your administrator."
)
LEGACY_MESSAGE = "Insufficient credits. Please recharge your account."

pytestmark = pytest.mark.unit


def test_registered_message_is_neutral_and_api_status_is_unchanged():
    assert get_error_message("INSUFFICIENT_CREDITS") == MESSAGE
    assert get_error_for_api_status("insufficient_credits") == MESSAGE
    assert get_error_for_api_status("rate_limited") == get_error_message(
        "RATE_LIMIT_REACHED"
    )
    assert get_error_for_api_status("other") == "API call not allowed: other"


@pytest.mark.parametrize(
    "text",
    [
        LEGACY_MESSAGE,
        "Insufficient credits",
        "INSUFFICIENT CREDITS",
        "insufficient_credits",
        MESSAGE,
    ],
)
@pytest.mark.parametrize("kind", [Exception, ValueError, str])
def test_new_and_legacy_messages_keep_the_same_normalization_and_code(text, kind):
    error = kind(text)
    assert get_specific_error_message(error) == MESSAGE
    assert get_usage_error_code(error) == "FREE_TIER_LIMIT"


@pytest.mark.parametrize("text", [LEGACY_MESSAGE, MESSAGE, MESSAGE.upper()])
def test_wrapped_messages_and_status_arguments_keep_the_same_classification(text):
    for error in [
        Exception(f"Request failed: {text}"),
        ValueError("Request failed", text),
    ]:
        assert get_specific_error_message(error) == MESSAGE
        assert get_usage_error_code(error) == "FREE_TIER_LIMIT"


@pytest.mark.parametrize("text", [LEGACY_MESSAGE, MESSAGE])
def test_rate_limit_status_still_takes_precedence(text):
    error = ValueError(text, "rate_limited")
    assert get_specific_error_message(error) == get_error_message("RATE_LIMIT_REACHED")
    assert get_usage_error_code(error) == "RATE_LIMITED"


def test_broad_insufficient_mapping_does_not_invent_a_credit_classification():
    error = Exception("insufficient input data")
    assert get_specific_error_message(error) == MESSAGE
    assert get_usage_error_code(error) is None


@pytest.mark.parametrize("text", [LEGACY_MESSAGE, MESSAGE, "Provider refusal"])
def test_llm_passthrough_is_unchanged(text):
    assert get_specific_error_message(Exception(text), is_llm_error=True) == text


def test_unrelated_errors_keep_existing_fallbacks():
    assert get_specific_error_message(Exception("Other failure")) == get_error_message(
        "FAILED_TO_PROCESS_EVALUATION"
    )
    assert (
        get_specific_error_message(ValueError("Invalid context format"))
        == "Invalid context format"
    )
    assert get_usage_error_code(Exception("Other failure")) is None
