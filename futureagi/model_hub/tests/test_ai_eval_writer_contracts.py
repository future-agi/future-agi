from unittest.mock import MagicMock, patch

import pytest
from rest_framework import status


def _assert_field_error(response, field_name):
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert field_name in response.json()["details"]


def _patch_llm(content):
    """Patch the in-house LLM wrapper so _get_completion_content returns `content`.

    The service routes through agentic_eval.core.llm.llm.LLM (the Agentcc
    gateway wrapper), not litellm directly — so the mock targets the wrapper.
    """
    return patch(
        "agentic_eval.core.llm.llm.LLM",
        return_value=MagicMock(
            _get_completion_content=MagicMock(return_value=content)
        ),
    )


@pytest.mark.django_db
class TestAIEvalWriterContracts:
    def test_rejects_unknown_request_fields(self, auth_client):
        response = auth_client.post(
            "/model-hub/ai-eval-writer/",
            {
                "description": "Check whether the response is helpful",
                "output_format": "prompt",
                "outputFormat": "legacy camel alias",
            },
            format="json",
        )

        _assert_field_error(response, "outputFormat")

    def test_rejects_invalid_output_format_before_model_call(self, auth_client):
        response = auth_client.post(
            "/model-hub/ai-eval-writer/",
            {
                "description": "Check whether the response is helpful",
                "output_format": "json",
            },
            format="json",
        )

        _assert_field_error(response, "output_format")


@pytest.mark.django_db
class TestAIEvalWriterGeneration:
    """Behavior of the generation path (litellm mocked — no network)."""

    def test_test_data_format_returns_parsed_object(self, auth_client):
        # test_data is parsed backend-side and returned under a typed
        # `test_data` object key — not a raw string under `prompt`.
        with _patch_llm('{"output": "Revenue grew 40%.", "context": "Flat YoY."}'):
            response = auth_client.post(
                "/model-hub/ai-eval-writer/",
                {
                    "description": "Generate a failing case for variables: output, context",
                    "output_format": "test_data",
                },
                format="json",
            )

        assert response.status_code == status.HTTP_200_OK
        result = response.json()["result"]
        assert result["test_data"] == {
            "output": "Revenue grew 40%.",
            "context": "Flat YoY.",
        }
        assert result.get("prompt") is None  # not under the misleading key

    def test_messages_format_returns_parsed_array(self, auth_client):
        # messages is parsed backend-side and returned as a typed `messages`
        # array (the frontend no longer JSON.parses a string).
        msgs = '[{"role": "system", "content": "Judge politeness."}, {"role": "user", "content": "{{output}}"}]'
        with _patch_llm(msgs):
            response = auth_client.post(
                "/model-hub/ai-eval-writer/",
                {"description": "judge politeness", "output_format": "messages"},
                format="json",
            )

        assert response.status_code == status.HTTP_200_OK
        result = response.json()["result"]
        assert result["messages"] == [
            {"role": "system", "content": "Judge politeness."},
            {"role": "user", "content": "{{output}}"},
        ]

    def test_strips_markdown_fence_then_parses(self, auth_client):
        fenced = '```json\n{"output": "x", "context": "y"}\n```'
        with _patch_llm(fenced):
            response = auth_client.post(
                "/model-hub/ai-eval-writer/",
                {"description": "test data please", "output_format": "test_data"},
                format="json",
            )

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["result"]["test_data"] == {"output": "x", "context": "y"}

    def test_malformed_model_json_surfaces_as_5xx(self, auth_client):
        # The model returned non-JSON for a format that requires it — a model
        # failure, so 5xx, not a 200 with a broken payload.
        with _patch_llm("not json at all"):
            response = auth_client.post(
                "/model-hub/ai-eval-writer/",
                {"description": "test data please", "output_format": "test_data"},
                format="json",
            )

        assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR

    def test_prompt_format_still_works(self, auth_client):
        # Regression guard for the pre-existing default format after adding the
        # new branch.
        generated = "You are an expert evaluator. Check {{output}} against {{ground_truth}}."
        with _patch_llm(generated):
            response = auth_client.post(
                "/model-hub/ai-eval-writer/",
                {"description": "check factual accuracy"},
                format="json",
            )

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["result"]["prompt"] == generated

    def test_llm_failure_surfaces_as_5xx(self, auth_client):
        # An upstream model/gateway failure is not the client's fault: it must
        # surface as 5xx (not 400, not a swallowed 200) so monitoring can tell
        # "model is down" from "bad input".
        failing_llm = MagicMock(
            _get_completion_content=MagicMock(side_effect=RuntimeError("model down"))
        )
        with patch("agentic_eval.core.llm.llm.LLM", return_value=failing_llm):
            response = auth_client.post(
                "/model-hub/ai-eval-writer/",
                {"description": "anything", "output_format": "test_data"},
                format="json",
            )

        assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
