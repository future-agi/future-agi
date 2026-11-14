"""Real save/read/run endpoint contracts against isolated PostgreSQL fixtures."""

from unittest.mock import patch

import httpx
import pytest
from model_hub.models.api_key import ApiKey
from model_hub.models.evals_metric import EvalTemplate

pytestmark = [pytest.mark.requires_ee, pytest.mark.django_db(transaction=True)]
MODEL = "decision/openai/gpt-6-luna"


def test_playground_uses_binding_rubric_and_openai_key_without_mutating_template(
    auth_client, user, monkeypatch
):
    monkeypatch.setenv("AGENTCC_GATEWAY_INTERNAL_URL", "http://gateway.test:8080")
    monkeypatch.setenv("AGENTCC_INTERNAL_API_KEY", "synthetic-internal-key")
    ApiKey.objects.create(
        provider="openai", organization=user.organization, key="synthetic-openai-key"
    )
    created = auth_client.post(
        "/model-hub/eval-templates/create-v2/",
        {
            "name": "decision-runtime-rubric",
            "instructions": "Judge {{input}}",
            "model": MODEL,
            "output_type": "percentage",
            "jev_mapping": {"score": {"levels": ["old bad", "old good"]}},
        },
        format="json",
    )
    assert created.status_code == 200, created.data
    identifier = created.data["result"]["id"]
    answer = {
        "model": "gpt-6-luna",
        "answers": [
            {
                "name": "q1",
                "type": "score",
                "score": 1.5,
                "confidence": 0.5,
                "probabilities": [
                    {"value": 0, "label": "0", "probability": 0.1},
                    {"value": 1, "label": "1", "probability": 0.3},
                    {"value": 2, "label": "2", "probability": 0.6},
                ],
            }
        ],
        "usage": {"input_tokens": 100, "output_tokens": 0},
    }
    with patch("httpx.post", return_value=httpx.Response(200, json=answer)) as post:
        result = auth_client.post(
            "/model-hub/eval-playground/",
            {
                "template_id": identifier,
                "model": MODEL,
                "mapping": {"input": "Synthetic reply"},
                "config": {
                    "run_config": {
                        "model": MODEL,
                        "output_type": "score",
                        "jev_mapping": {"score": {"levels": ["bad", "fair", "good"]}},
                    }
                },
            },
            format="json",
        )
    assert result.status_code == 200, result.data
    post.assert_called_once()
    sent = post.call_args.kwargs
    assert post.call_args.args[0].endswith("/v1/decisions")
    assert sent["headers"]["x-agentcc-provider-key"] == "synthetic-openai-key"
    assert [
        level["description"] for level in sent["json"]["questions"][0]["levels"]
    ] == ["bad", "fair", "good"]
    assert EvalTemplate.objects.get(id=identifier).config["jev_mapping"]["score"][
        "levels"
    ] == ["old bad", "old good"]


def test_missing_openrouter_key_is_clear_and_makes_no_provider_call(auth_client):
    model = "decision/openrouter/morph/reflex"
    created = auth_client.post(
        "/model-hub/eval-templates/create-v2/",
        {
            "name": "decision-missing-key",
            "instructions": "Judge {{input}}",
            "model": model,
            "output_type": "pass_fail",
            "jev_mapping": {},
        },
        format="json",
    )
    assert created.status_code == 200, created.data
    with patch("httpx.post") as post:
        result = auth_client.post(
            "/model-hub/eval-playground/",
            {
                "template_id": created.data["result"]["id"],
                "model": model,
                "mapping": {"input": "Synthetic reply"},
                "config": {},
            },
            format="json",
        )
    assert result.status_code == 400, result.data
    assert result.data["code"] == "DECISION_KEY_MISSING"
    assert "OpenRouter" in str(result.data)
    post.assert_not_called()


def test_model_list_decisions_returns_grouped_connection_contract(auth_client):
    result = auth_client.get(
        "/model-hub/api/models_list/", {"model_type": "decision", "limit": 100}
    )
    assert result.status_code == 200, result.data
    assert result.data["count"] == 35
    assert len(result.data["results"]) == 35
    assert result.data["next"] is None
    assert all(
        row["type"] == "decision" and row["connections"]
        for row in result.data["results"]
    )
