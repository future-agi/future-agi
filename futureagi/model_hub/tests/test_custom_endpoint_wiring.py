"""Use the same safe transport for model validation, execution and readback."""

import time
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
import requests

from agentic_eval.core_evals.run_prompt.runprompt_handlers.handlers.custom_model_handler import (
    CustomModelHandler,
)
from model_hub.serializers.custom_models import CustomAIModelSerializer
from model_hub.services.custom_endpoint import CustomEndpointError
from model_hub.utils.utils import validate_model_working

ENDPOINT = "https://synthetic.example/v1/chat/completions?token=query-secret"
HEADERS = {"X-Short": "abc", "X-Key": "synthetic-header-secret"}


def handler():
    instance = object.__new__(CustomModelHandler)
    instance.context = SimpleNamespace(model="synthetic-model")
    instance._custom_model_config = {"endpoint_url": ENDPOINT, "headers": HEADERS}
    instance._calculate_token_costs = Mock(return_value=(0, 0, {"total_cost": 0}))
    return instance


def test_execution_uses_safe_transport_without_logging_url(caplog):
    caplog.set_level("INFO")
    response = requests.Response()
    response.status_code = 200
    response._content = b"synthetic-result"
    with patch(
        "agentic_eval.core_evals.run_prompt.runprompt_handlers.handlers.custom_model_handler.post_custom_endpoint",
        return_value=response,
    ) as post:
        payload = {"messages": [{"role": "user", "content": "synthetic-prompt"}]}
        result = handler()._make_custom_api_request(payload, time.time())
    post.assert_called_once_with(url=ENDPOINT, headers=HEADERS, payload=payload)
    assert result.response == "synthetic-result"
    assert ENDPOINT not in caplog.text
    assert "synthetic-header-secret" not in caplog.text


def test_execution_returns_safe_refusal_without_credentials(caplog):
    with patch(
        "agentic_eval.core_evals.run_prompt.runprompt_handlers.handlers.custom_model_handler.post_custom_endpoint",
        side_effect=CustomEndpointError(
            "Custom model endpoint request refused or unavailable."
        ),
    ):
        with pytest.raises(CustomEndpointError, match="refused or unavailable"):
            handler()._make_custom_api_request({}, time.time())
    assert ENDPOINT not in caplog.text
    assert "synthetic-header-secret" not in caplog.text


def test_validation_uses_safe_transport_and_preserves_configuration():
    config = {"api_base": ENDPOINT, "headers": dict(HEADERS), "custom_provider": True}
    response = requests.Response()
    response.status_code = 200
    response._content = b"synthetic-result"
    with patch(
        "model_hub.utils.utils.post_custom_endpoint", return_value=response
    ) as post:
        assert (
            validate_model_working("synthetic-model", {"config_json": config}, "custom")
            == "synthetic-result"
        )
    assert post.call_args.kwargs["url"] == ENDPOINT
    assert post.call_args.kwargs["headers"] == HEADERS
    assert config == {"api_base": ENDPOINT, "headers": HEADERS, "custom_provider": True}


def test_custom_readback_fully_redacts_short_and_nested_credentials():
    config = {
        "api_base": ENDPOINT,
        "headers": HEADERS,
        "other": ["xy", {"key": "z"}],
        "custom_provider": True,
    }
    instance = SimpleNamespace(
        provider="custom", key_config={"stored": True}, actual_json=config
    )
    assert CustomAIModelSerializer().get_config_json(instance) == {
        "api_base": "********",
        "headers": {"X-Short": "********", "X-Key": "********"},
        "other": ["********", {"key": "********"}],
        "custom_provider": True,
    }
