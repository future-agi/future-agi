"""PRR009 union controls keep c26 keyless and secret-policy serializers real."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from agentcc.serializers.provider_credential import (
    AgentccProviderCredentialCreateSerializer,
    AgentccProviderCredentialSerializer,
    AgentccProviderCredentialUpdateSerializer,
)
from agentcc.views import provider_credential as module


@pytest.mark.parametrize("api_format", ["systemone", "typesafe"])
@pytest.mark.parametrize("credentials", [{}, {"api_key": ""}, {"api_key": " \t"}])
def test_keyless_create_survives_blank_secret_omission(api_format, credentials):
    request = SimpleNamespace(
        data={
            "provider_name": "custom-systemone",
            "api_format": api_format,
            "base_url": "https://decider.example.com",
            "credentials": credentials,
            "models_list": ["strands-decider"],
        },
        organization=SimpleNamespace(id="synthetic-org", pk="synthetic-org"),
    )
    view = module.AgentccProviderCredentialViewSet()
    view.request = request
    view.action = "create"
    view.format_kwarg = None
    with (
        patch.object(module, "AgentccProviderCredential") as model,
        patch.object(module, "AgentccProviderCredentialSerializer") as output,
        patch.object(module, "ensure_provider_base_url_allowed"),
        patch.object(
            module.CredentialManager, "encrypt", return_value=b"synthetic-cipher"
        ) as encrypt,
        patch.object(view, "_push_config_to_gateway", return_value=True) as push,
    ):
        model.no_workspace_objects.filter.return_value.first.return_value = None
        output.return_value.data = {}
        response = view.create(request)
    assert response.status_code == 201
    encrypt.assert_called_once_with({})
    model.return_value.save.assert_called_once_with(force_insert=True)
    assert model.return_value.disable_auto_workspace_assignment is True
    push.assert_called_once_with(request.organization)


@pytest.mark.parametrize("api_format", ["systemone", "typesafe"])
def test_keyless_metadata_update_keeps_current_serializer_restrictions(api_format):
    instance = SimpleNamespace(
        provider_name="custom-systemone",
        api_format=api_format,
        base_url="https://decider.example.com",
        encrypted_credentials=b"synthetic-cipher",
    )
    with patch.object(module.CredentialManager, "decrypt", return_value={}):
        edit = AgentccProviderCredentialUpdateSerializer(
            instance, data={"models_list": ["new-decider"]}, partial=True
        )
        assert edit.is_valid(), edit.errors
        for change in (
            {"base_url": ""},
            {"base_url": "https://api.openai.com/v1"},
            {"api_format": "openai"},
        ):
            invalid = AgentccProviderCredentialUpdateSerializer(
                instance, data=change, partial=True
            )
            assert not invalid.is_valid()


@pytest.mark.parametrize("api_format", ["systemone", "typesafe"])
@pytest.mark.parametrize("credentials", [{}, {"api_key": ""}, {"api_key": " \t"}])
def test_known_vendor_cannot_use_keyless_exception(api_format, credentials):
    serializer = AgentccProviderCredentialCreateSerializer(
        data={
            "provider_name": "typesafe",
            "api_format": api_format,
            "base_url": "https://api.typesafe.ai",
            "credentials": credentials,
        }
    )
    assert not serializer.is_valid()
    assert "credentials" in serializer.errors


@pytest.mark.parametrize("api_format", ["systemone", "typesafe"])
def test_complete_rotation_uses_current_field_contract(api_format):
    instance = SimpleNamespace(
        provider_name="custom-systemone",
        organization=SimpleNamespace(id="synthetic-org", pk="synthetic-org"),
        api_format=api_format,
        base_url="https://decider.example.com",
        encrypted_credentials=b"unreadable-synthetic-cipher",
        models_list=["strands-decider"],
        extra_config={},
        save=MagicMock(),
    )
    request = SimpleNamespace(
        organization=instance.organization,
        data={
            "credentials": {"api_key": "synthetic-replacement-key"},
            "replace_credentials": True,
        },
    )
    view = module.AgentccProviderCredentialViewSet()
    with (
        patch.object(view, "get_object", return_value=instance),
        patch.object(module.transaction, "atomic"),
        patch.object(module, "lock_org"),
        patch.object(module, "AgentccProviderCredentialSerializer") as output,
        patch.object(
            module.CredentialManager, "decrypt", side_effect=ValueError("synthetic")
        ) as decrypt,
        patch.object(
            module.CredentialManager, "encrypt", return_value=b"new-synthetic-cipher"
        ) as encrypt,
        patch.object(view, "_push_config_to_gateway", return_value=True),
    ):
        output.return_value.data = {}
        response = view.rotate(request)
    assert response.status_code == 200
    decrypt.assert_called_once_with(b"unreadable-synthetic-cipher")
    assert instance.models_list == []
    assert response.data["result"]["code"] == "provider_credential_context_invalid"
    assert response.data["result"]["dropped_model_count"] == 1
    encrypt.assert_called_once_with({"api_key": "synthetic-replacement-key"})


@pytest.mark.parametrize("action", ["create", "update"])
def test_union_keeps_secret_extra_config_refusal(action):
    secret = "synthetic-union-secret"
    data = {"extra_config": {"wrapper": [{"apiKey": secret}]}}
    if action == "create":
        data.update(provider_name="openai", credentials={"api_key": secret})
        serializer = AgentccProviderCredentialCreateSerializer(data=data)
    else:
        serializer = AgentccProviderCredentialUpdateSerializer(data=data, partial=True)
    assert not serializer.is_valid()
    assert "extra_config" in serializer.errors
    assert secret not in str(serializer.errors)


def test_union_keeps_fixed_credential_redaction():
    instance = SimpleNamespace(encrypted_credentials=b"synthetic-cipher")
    with patch.object(
        module.CredentialManager,
        "decrypt",
        return_value={"api_key": "synthetic-union-secret", "aws_session_token": "7"},
    ):
        assert AgentccProviderCredentialSerializer().get_credentials(instance) == {
            "api_key": "****",
            "aws_session_token": "****",
        }
