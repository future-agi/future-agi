"""User-facing text for failed Jev runs: fixed per typed code, never the
exception's own text, and the same wherever the error surfaces."""

import pytest

from agentic_eval.core_evals.run_prompt.litellm_models import MissingProviderKeyError
from ee.jev.client import JevGatewayError
from ee.jev.errors import describe
from ee.jev.mapping import JevMappingError, check_input_size
from ee.jev.validation import JevResponseError
from sdk.utils.evaluations import StandaloneEvaluationError
from tfc.utils.error_codes import get_specific_error_message, get_usage_error_code

pytestmark = pytest.mark.requires_ee

KEY_INVALID = (
    "TypeSafe rejected the API key saved under Settings → AI Providers. "
    "Update the key and try again."
)
KEY_MISSING = (
    "API key not configured for typesafe. Please add your API key in settings."
)


@pytest.mark.parametrize(
    "error,code,message",
    [
        (JevGatewayError("JEV_KEY_MISSING"), "JEV_KEY_MISSING", KEY_MISSING),
        (MissingProviderKeyError("typesafe"), "JEV_KEY_MISSING", KEY_MISSING),
        (JevGatewayError("JEV_KEY_INVALID"), "JEV_KEY_INVALID", KEY_INVALID),
        (
            JevGatewayError("JEV_RATE_LIMITED", True, "3"),
            "JEV_RATE_LIMITED",
            "TypeSafe's rate limit was reached. Try again shortly.",
        ),
        (
            JevGatewayError("JEV_TIMEOUT", True),
            "JEV_TIMEOUT",
            "TypeSafe didn't respond in time. Try again.",
        ),
        (
            JevGatewayError("JEV_PROVIDER_ERROR", True),
            "JEV_PROVIDER_ERROR",
            "TypeSafe couldn't complete this evaluation. Try again.",
        ),
        (
            JevGatewayError(),
            "JEV_PROVIDER_ERROR",
            "TypeSafe couldn't complete this evaluation.",
        ),
        (
            JevResponseError("JEV_MODEL_MISMATCH"),
            "JEV_MODEL_MISMATCH",
            "TypeSafe couldn't complete this evaluation.",
        ),
        (
            JevMappingError(
                "JEV_MODEL_UNKNOWN", "model", "Model 'jev-2.0.0' is not supported."
            ),
            "JEV_MODEL_UNKNOWN",
            "Model 'jev-2.0.0' is not supported.",
        ),
        (
            JevMappingError(
                "JEV_INPUT_UNSUPPORTED", "state", "Jev does not accept media inputs."
            ),
            "JEV_INPUT_UNSUPPORTED",
            "Jev does not accept media inputs.",
        ),
    ],
)
def test_each_code_has_fixed_text(error, code, message):
    described = describe(error)
    assert described["code"] == code
    assert described["message"] == message
    # Wrapped the way the SDK wraps the engine's exception.
    assert describe(StandaloneEvaluationError(error)) == described
    # Dataset cells and preview rows show the same text.
    assert get_specific_error_message(error) == message
    assert get_specific_error_message(StandaloneEvaluationError(error)) == message


def test_too_large_says_what_to_map_instead():
    """The playground and task logs showed "JEV_INPUT_TOO_LARGE: Jev input
    exceeds the configured size limit." for a session with a screenshot."""
    with pytest.raises(JevMappingError) as too_large:
        check_input_size({"state": "x" * 120001, "questions": {}})
    described = describe(too_large.value)
    assert described["code"] == "JEV_INPUT_TOO_LARGE"
    assert not described["message"].startswith("JEV_")
    assert '"transcript" instead of "traces"' in described["message"]
    assert get_specific_error_message(too_large.value) == described["message"]


def test_a_rejected_key_is_never_told_to_retry():
    assert "retry" not in describe(JevGatewayError("JEV_KEY_INVALID"))["message"]


def test_text_never_carries_the_exception_message():
    error = JevGatewayError("JEV_KEY_INVALID")
    error.args = ("org-key-CANARY internal-key-CANARY upstream body",)
    assert "CANARY" not in describe(error)["message"]
    assert "CANARY" not in get_specific_error_message(error)


def test_mapping_errors_name_their_field():
    error = JevMappingError("JEV_MODEL_UNKNOWN", "model", "Unsupported Jev model.")
    assert describe(error)["attr"] == "model"
    assert describe(error)["details"] == {"model": ["JEV_MODEL_UNKNOWN"]}
    assert describe(JevGatewayError("JEV_TIMEOUT", True))["attr"] is None
    assert describe(JevGatewayError("JEV_TIMEOUT", True))["details"] is None


@pytest.mark.parametrize(
    "code,field",
    [
        ("JEV_SCORE_LEVELS_REQUIRED", "jev_mapping.score.levels"),
        ("JEV_MULTI_CHOICE_UNSUPPORTED", "multi_choice"),
        ("JEV_FEW_SHOT_UNSUPPORTED", "few_shot_examples"),
    ],
)
def test_a_mapping_rule_is_reported_as_jev_mapping_invalid(code, field):
    """As the save and view-level checks report it: the envelope code is
    JEV_MAPPING_INVALID and the rule's own code is in details."""
    error = JevMappingError(code, field, "This configuration is not supported.")
    described = describe(error)
    assert described["code"] == "JEV_MAPPING_INVALID"
    assert described["details"] == {field: [code]}
    assert described["attr"] == field
    assert described["message"] == "This configuration is not supported."


@pytest.mark.parametrize(
    "error",
    [
        ValueError("plain validation"),
        RuntimeError("JEV_KEY_INVALID"),
        MissingProviderKeyError("openai"),
        StandaloneEvaluationError(ValueError("other")),
    ],
)
def test_other_errors_are_not_jev(error):
    assert describe(error) is None


def test_typesafe_rate_limit_is_not_a_plan_limit():
    """The dataset cell would otherwise show Future AGI's rate-limit text and
    the plan upgrade banner for TypeSafe's limit."""
    error = JevGatewayError("JEV_RATE_LIMITED", True)
    assert get_usage_error_code(error) is None
    assert get_specific_error_message(error) == (
        "TypeSafe's rate limit was reached. Try again shortly."
    )
    assert get_usage_error_code(ValueError("rate limit hit", "rate_limited")) == (
        "RATE_LIMITED"
    )
