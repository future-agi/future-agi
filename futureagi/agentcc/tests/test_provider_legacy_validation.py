"""Tightening new credentials must not unpublish an already working provider."""

import socket
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from agentcc.serializers.provider_credential import (
    AgentccProviderCredentialCreateSerializer,
    unsupported_credential_keys,
)
from agentcc.services.config_push import _normalize_provider_credentials
from agentcc.services.url_safety import ensure_provider_base_url_allowed
from agentcc.views.gateway import _not_pushed_reason
from integrations.services.credentials import CredentialManager


@pytest.mark.parametrize(
    "url", ["http://127.0.0.1", "http://10.0.0.1", "http://[fe80::1%25missing-zone]"]
)
def test_hosted_literal_refusal_does_not_depend_on_dns(settings, url):
    settings.CLOUD_DEPLOYMENT = "US"
    with patch(
        "agentcc.services.url_safety.socket.getaddrinfo", side_effect=socket.gaierror
    ):
        with pytest.raises(ValueError):
            ensure_provider_base_url_allowed(url)


@pytest.mark.parametrize("value", [None, {}, [], 3, False])
def test_unknown_credential_key_reports_allowed_keys_regardless_of_value(value):
    serializer = AgentccProviderCredentialCreateSerializer(
        data={
            "provider_name": "openai",
            "credentials": {"api_key": "synthetic-key", "weight": value},
        }
    )
    assert not serializer.is_valid()
    error = str(serializer.errors["credentials"])
    assert "weight" in error and "Allowed credential keys" in error
    assert "aws_region" in error


def test_legacy_contract_fields_still_push_but_cannot_be_new_credentials():
    stored = {
        "api_key": "synthetic-key",
        "weight": "2",
        "base_url": "https://old.invalid",
    }
    assert unsupported_credential_keys("openai", stored) == ("base_url", "weight")
    assert _normalize_provider_credentials("openai", stored) == stored
    assert unsupported_credential_keys("openai", stored, for_save=False) == ()
    row = SimpleNamespace(
        provider_name="openai",
        encrypted_credentials=CredentialManager.encrypt(stored),
    )
    assert _not_pushed_reason(row) is None


def test_legacy_non_contract_fields_are_still_reported_as_not_pushed():
    stored = {"api_key": "synthetic-key", "organization_id": "not-a-provider-field"}
    assert unsupported_credential_keys("openai", stored, for_save=False) == (
        "organization_id",
    )
