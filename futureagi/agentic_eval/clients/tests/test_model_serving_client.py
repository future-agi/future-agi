from unittest.mock import Mock, patch

import pytest

from agentic_eval.clients.model_serving_client import (
    MODEL_SERVING_REQUEST_TIMEOUT_SECONDS,
    ModelServingClient,
)


@pytest.fixture(autouse=True)
def reset_model_serving_client_singleton():
    ModelServingClient._instance = None
    yield
    ModelServingClient._instance = None


def test_get_passes_default_timeout():
    response = Mock()
    response.json.return_value = {"ok": True}

    with patch(
        "agentic_eval.clients.model_serving_client.requests.get",
        return_value=response,
    ) as mock_get:
        client = ModelServingClient("https://model-serving.example")

        assert client.get("health", params={"check": "ready"}) == {"ok": True}

    mock_get.assert_called_once_with(
        "https://model-serving.example/health",
        params={"check": "ready"},
        headers=None,
        timeout=MODEL_SERVING_REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status.assert_called_once()


def test_post_passes_default_timeout():
    response = Mock()
    response.json.return_value = {"id": "completion-1"}

    with patch(
        "agentic_eval.clients.model_serving_client.requests.post",
        return_value=response,
    ) as mock_post:
        client = ModelServingClient("https://model-serving.example")

        result = client.post("generate", json={"prompt": "hello"})

    assert result == {"id": "completion-1"}
    mock_post.assert_called_once_with(
        "https://model-serving.example/generate",
        json={"prompt": "hello"},
        headers=None,
        timeout=MODEL_SERVING_REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status.assert_called_once()
