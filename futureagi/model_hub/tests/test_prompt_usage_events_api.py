import logging
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from ee.usage.models.usage import APICallStatusChoices


logger = logging.getLogger("tests.usage_events")


@pytest.mark.django_db
def test_generate_prompt_api_emits_token_usage_properties(auth_client):
    call_log = SimpleNamespace(
        status=APICallStatusChoices.PROCESSING.value,
        log_id=uuid.uuid4(),
    )

    with (
        patch(
            "tfc.billing.boundary._EeBilling.log_and_deduct",
            return_value=call_log,
        ) as mock_log_cost,
        patch("model_hub.views.prompt_template.submit_with_retry") as mock_submit,
        patch("ee.usage.services.emitter.emit") as mock_emit,
    ):
        response = auth_client.post(
            "/model-hub/prompt-templates/generate-prompt/",
            {"statement": "Write a concise bug triage prompt for a JSON API."},
            format="json",
        )

        assert response.status_code == 200, response.content
        mock_log_cost.assert_called_once()
        mock_submit.assert_called_once()
        mock_emit.assert_called_once()

        event = mock_emit.call_args.args[0]
        logger.info("captured_prompt_usage_event %s", event.model_dump(mode="json"))

    props = event.properties
    assert event.event_type == "ai_prompt_creation"
    assert props["source"] == "run_prompt_gen"
    assert props["source_id"] == str(call_log.log_id)
    assert props["prompt_tokens"] > 0
    assert props["total_tokens"] == props["prompt_tokens"]


@pytest.mark.django_db
def test_improve_prompt_api_emits_token_usage_properties(auth_client):
    call_log = SimpleNamespace(
        status=APICallStatusChoices.PROCESSING.value,
        log_id=uuid.uuid4(),
    )

    with (
        patch("tfc.ee_gating.check_ee_feature"),
        patch(
            "tfc.billing.boundary._EeBilling.log_and_deduct",
            return_value=call_log,
        ) as mock_log_cost,
        patch("model_hub.views.prompt_template.submit_with_retry") as mock_submit,
        patch("ee.usage.services.emitter.emit") as mock_emit,
    ):
        response = auth_client.post(
            "/model-hub/prompt-templates/improve-prompt/",
            {
                "existing_prompt": "Summarize this support ticket: {{ticket}}",
                "improvement_requirements": "Make the output include severity and next action.",
            },
            format="json",
        )

        assert response.status_code == 200, response.content
        mock_log_cost.assert_called_once()
        mock_submit.assert_called_once()
        mock_emit.assert_called_once()

        event = mock_emit.call_args.args[0]
        logger.info("captured_prompt_usage_event %s", event.model_dump(mode="json"))

    props = event.properties
    assert event.event_type == "ai_prompt_improvement"
    assert props["source"] == "run_prompt_improve"
    assert props["source_id"] == str(call_log.log_id)
    assert props["prompt_tokens"] > 0
    assert props["total_tokens"] == props["prompt_tokens"]
