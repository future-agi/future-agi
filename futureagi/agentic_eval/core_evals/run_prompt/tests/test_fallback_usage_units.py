"""A malformed optional unit must not replace usable token costs."""

import pytest
from litellm import ModelResponse

from agentcc.services.log_ingestion import _compute_cost_details
from agentic_eval.core_evals.fi_utils.token_count_helper import calculate_total_cost
from agentic_eval.core_evals.run_prompt.runprompt_handlers.utils.response_formatter import (
    ResponseFormatter,
)

TOKENS = {"prompt_tokens": 2000, "completion_tokens": 250, "total_tokens": 2250}
ESTIMATE = (0.00045, "default")


def amount(cost):
    return cost["total_cost"], cost["pricing_source"]


@pytest.mark.parametrize("model", ["openai/tts-1", "gemini-1.5-pro", "unknown-model"])
@pytest.mark.parametrize(
    "field",
    [
        "images_generated",
        "audio_seconds",
        "num_images",
        "input_characters",
        "output_characters",
    ],
)
@pytest.mark.parametrize(
    "bad", [None, -1, True, "unknown", [], {}, float("nan"), float("inf"), 10**400]
)
def test_malformed_optional_unit_keeps_valid_tokens(model, field, bad):
    # Reproduce a missing/rejected character rate beside an unrelated field.
    metadata = {"input_characters": "unknown", "output_characters": 70, field: bad}
    usage = {**TOKENS, **metadata}
    response = ModelResponse(
        model=model,
        choices=[{"message": {"role": "assistant", "content": "Synthetic reply"}}],
        usage=usage,
    )
    reply, info = ResponseFormatter.format_llm_response(response, model, 0)
    assert reply == "Synthetic reply"
    assert amount(info["metadata"]["cost"]) == ESTIMATE
    assert amount(calculate_total_cost(model, usage)) == ESTIMATE
    assert (
        amount(ResponseFormatter.build_streaming_metadata(response, 0)["cost"])
        == ESTIMATE
    )
    assert (
        amount(_compute_cost_details({"model": model, **TOKENS, "metadata": metadata}))
        == ESTIMATE
    )


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("images_generated", "0", ESTIMATE),
        ("audio_seconds", "0", ESTIMATE),
        ("images_generated", 0.5, ESTIMATE),
        ("input_characters", 0.5, ESTIMATE),
        ("input_characters", 0, (0.0, "available_models")),
        ("input_characters", 2000, (0.03, "available_models")),
        ("images_generated", 2, (0.08, "default")),
        ("audio_seconds", 0.5, (0.00005, "default")),
    ],
)
def test_usable_units_and_genuine_zero(field, value, expected):
    model = "openai/tts-1"
    usage = {**TOKENS, field: value}
    response = ModelResponse(model=model, usage=usage)
    assert amount(calculate_total_cost(model, usage)) == expected
    assert (
        amount(ResponseFormatter.build_streaming_metadata(response, 0)["cost"])
        == expected
    )
    assert (
        amount(
            _compute_cost_details(
                {"model": model, **TOKENS, "metadata": {field: value}}
            )
        )
        == expected
    )


@pytest.mark.parametrize(
    ("model", "field"),
    [("dall-e-3", "images_generated"), ("whisper-1", "audio_seconds")],
)
def test_invalid_billed_unit_is_not_a_catalog_or_custom_charge(model, field):
    usage = {**TOKENS, field: None}
    assert amount(calculate_total_cost(model, usage)) == ESTIMATE
    assert (
        amount(
            _compute_cost_details({"model": model, **TOKENS, "metadata": {field: None}})
        )
        == ESTIMATE
    )
    fallback = (
        {"per_image": 0.1} if field == "images_generated" else {"input_per_minute": 0.1}
    )
    assert amount(calculate_total_cost("openai/tts-1", usage, fallback)) == ESTIMATE
    assert amount(
        calculate_total_cost("openai/tts-1", {**TOKENS, field: 0}, fallback)
    ) == (0, "fallback")


@pytest.mark.parametrize("config", [{}, {"n": None}], ids=["omitted", "null"])
def test_image_handler_prices_a_null_count_as_one_image(config):
    # A null `n` asks for the provider default (one image). It must not reach
    # the calculator as a rejected count and turn a generated image into $0.
    from unittest.mock import Mock, patch

    from agentic_eval.core_evals.run_prompt.runprompt_handlers import (
        ModelHandlerContext,
    )
    from agentic_eval.core_evals.run_prompt.runprompt_handlers.handlers import (
        ImageHandler,
    )

    image = Mock(url="https://example.com/synthetic.png", b64_json=None)
    handler = ImageHandler(
        ModelHandlerContext(
            model="dall-e-3",
            messages=[{"role": "user", "content": "A synthetic test image"}],
            organization_id="00000000-0000-0000-0000-000000000000",
            api_key="test-key",
            run_prompt_config={"quality": "standard", **config},
        )
    )
    handler_module = (
        "agentic_eval.core_evals.run_prompt.runprompt_handlers.handlers.image_handler"
    )
    with (
        patch("litellm.image_generation", return_value=Mock(data=[image])) as gen,
        patch(f"{handler_module}.upload_image_to_s3", return_value=image.url),
    ):
        response = handler.execute_sync()
    assert gen.call_args.kwargs["n"] == 1
    one_image = calculate_total_cost(
        "dall-e-3",
        {"prompt_tokens": 0, "completion_tokens": 0, "images_generated": 1},
    )
    assert amount(response.metadata["cost"]) == amount(one_image)
    assert amount(one_image)[1] == "available_models"
