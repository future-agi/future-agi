"""OpenAPI must describe the JSON extras the SDK already accepts."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from drf_yasg import openapi
from drf_yasg.inspectors import SwaggerAutoSchema
from rest_framework.views import APIView

from sdk.serializers.contracts import (
    ConfigureEvaluationsRequestSerializer,
    SDKStandaloneEvalInputSerializer,
    SDKStandaloneEvalRequestSerializer,
)
from sdk.serializers.evaluations import EvalTemplate
from tracer.models.external_eval_config import ExternalEvalConfig, PlatformChoices

JSON_EXTRAS = ["scalar", 7, 2.5, True, None, ["item", 3], {"nested": "value"}]


def _schema(serializer):
    components = openapi.ReferenceResolver(openapi.SCHEMA_DEFINITIONS, force_init=True)
    inspector = SwaggerAutoSchema(APIView(), "/", "POST", components, None, {})
    return openapi.resolve_ref(inspector.serializer_to_schema(serializer), components)


@pytest.fixture
def eval_template(monkeypatch):
    template = SimpleNamespace(owner="custom", config={})
    monkeypatch.setattr(
        EvalTemplate.no_workspace_objects, "get", Mock(return_value=template)
    )
    return template


@pytest.mark.parametrize("extra", JSON_EXTRAS)
def test_configure_schema_matches_accepted_json_extras(extra, eval_template):
    request = SimpleNamespace(user=SimpleNamespace(organization="organization"))
    serializer = ConfigureEvaluationsRequestSerializer(
        data={
            "eval_config": {"eval_templates": "example", "inputs": {}},
            "platform": "langfuse",
            "provider_extra": extra,
        },
        context={"request": request},
    )
    assert serializer.is_valid(), serializer.errors
    # The view reads credentials from the original data, not validated_data.
    assert serializer.initial_data["provider_extra"] == extra
    assert "provider_extra" not in serializer.validated_data
    schema = _schema(ConfigureEvaluationsRequestSerializer())
    assert schema["additionalProperties"] is True
    assert schema["properties"]["platform"]["type"] == "string"
    assert schema["required"] == ["eval_config", "platform"]


@pytest.mark.parametrize("extra", JSON_EXTRAS)
def test_standalone_schema_matches_ignored_json_extras(extra):
    serializer = SDKStandaloneEvalRequestSerializer(
        data={
            "inputs": [{"input": "hello", "max_tokens": 8, "extra": extra}],
            "config": {"1": {"threshold": 0.5}},
        }
    )
    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data["inputs"] == [{"input": "hello", "max_tokens": 8}]
    schema = _schema(SDKStandaloneEvalInputSerializer())
    assert schema["additionalProperties"] is True
    assert schema["properties"]["input"]["type"] == "string"
    assert schema["properties"]["max_tokens"]["type"] == "integer"
    assert schema["properties"]["max_tokens"]["minimum"] == 1


def test_configure_schema_tells_users_where_credentials_go():
    # Swagger UI renders nothing for additionalProperties: true, so the model
    # description is the only place the docs say credentials go top level.
    description = _schema(ConfigureEvaluationsRequestSerializer())["description"]
    assert "top level of this request" in description
    required = ExternalEvalConfig.get_required_credentials(PlatformChoices.LANGFUSE)
    assert required
    for credential in required:
        assert credential in description


def test_standalone_input_schema_describes_ignored_extras():
    description = _schema(SDKStandaloneEvalInputSerializer())["description"]
    for declared in SDKStandaloneEvalInputSerializer().fields:
        assert declared in description
    assert "accepted and ignored" in description


def test_standalone_declared_fields_still_reject_invalid_values():
    serializer = SDKStandaloneEvalInputSerializer(
        data={"input": {}, "max_tokens": 0, "extra": "accepted"}
    )
    assert not serializer.is_valid()
    assert set(serializer.errors) == {"input", "max_tokens"}


def test_configure_view_preserves_provider_credentials(monkeypatch, eval_template):
    from model_hub.utils import jev_templates
    from sdk.views import evaluations

    monkeypatch.setattr(
        evaluations.EvalTemplate.objects, "get", Mock(return_value=eval_template)
    )
    monkeypatch.setattr(jev_templates, "validate_jev_binding", Mock(return_value=None))
    create = Mock()
    monkeypatch.setattr(evaluations.ExternalEvalConfig.objects, "create", create)
    credentials = {
        "langfuse_secret_key": "test-secret",
        "langfuse_public_key": "test-public",
        "langfuse_host": "https://example.invalid",
        "metadata": {"region": "test"},
        "optional": None,
        "retries": 3,
        "enabled": True,
        "labels": ["test"],
    }
    request = SimpleNamespace(
        method="POST",
        user=SimpleNamespace(organization="organization"),
        data={
            "eval_config": {"eval_templates": "example", "inputs": {}},
            "platform": "langfuse",
            "custom_eval_name": "example",
            **credentials,
        },
    )
    response = evaluations.ConfigureEvaluationsView().post(request)
    assert response.status_code == 200, response.data
    create.assert_called_once()
    assert create.call_args.kwargs["credentials"] == credentials
    # The model's provider credential checks also accept these existing values.
    evaluations.ExternalEvalConfig(platform="langfuse", credentials=credentials).clean()
