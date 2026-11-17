"""P2 boundaries; HTTP refusal plus the read serializer shared by all actions."""

import copy
import importlib
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from agentcc.contracts.gateway_admin import ProviderConfig
from agentcc.models.provider_credential import AgentccProviderCredential
from agentcc.serializers.provider_credential import (
    AgentccProviderCredentialCreateSerializer,
    AgentccProviderCredentialSerializer,
    AgentccProviderCredentialUpdateSerializer,
)
from agentcc.services.config_push import field_input_names
from agentcc.services.provider_validation import (
    PROVIDER_CREDENTIAL_ALIASES,
    PROVIDER_CREDENTIAL_FIELDS,
)

ENDPOINT = "/agentcc/provider-credentials/"
FORBIDDEN_NAMES = sorted(
    set(PROVIDER_CREDENTIAL_ALIASES).union(
        *(
            field_input_names(ProviderConfig, name)
            for name in PROVIDER_CREDENTIAL_FIELDS
        )
    )
)


def _policy():
    try:
        return importlib.import_module("agentcc.services.provider_secret_policy")
    except ModuleNotFoundError:
        pytest.fail("The shared provider secret boundary policy is missing.")


@pytest.fixture
def synthetic_secret():
    return "synthetic-boundary-credential"


@pytest.fixture
def credential_io(synthetic_secret):
    # No real encryption material, provider or gateway is needed by these tests.
    with (
        patch(
            "integrations.services.credentials.CredentialManager.decrypt",
            return_value={"api_key": synthetic_secret},
        ) as decrypt,
        patch(
            "integrations.services.credentials.CredentialManager.encrypt",
            return_value=b"opaque-test-ciphertext",
        ) as encrypt,
        patch(
            "agentcc.views.provider_credential.AgentccProviderCredentialViewSet."
            "_push_config_to_gateway",
            return_value=True,
        ) as push,
    ):
        yield SimpleNamespace(decrypt=decrypt, encrypt=encrypt, push=push)


@pytest.fixture
def stored_provider(organization, credential_io):
    row = AgentccProviderCredential(
        organization=organization,
        provider_name="anthropic",
        encrypted_credentials=b"opaque-test-ciphertext",
        extra_config={"timeout": 30},
        models_list=["clean-model"],
    )
    row.disable_auto_workspace_assignment = True
    row.save(force_insert=True)
    return row


def _snapshot(row):
    return (
        AgentccProviderCredential.no_workspace_objects.filter(pk=row.pk)
        .values("encrypted_credentials", "extra_config", "models_list", "updated_at")
        .get()
    )


def _representations(row):
    # The pinned view calls this same serializer for create/update/retrieve and
    # many=True for list. HTTP endpoint integration is also assigned to P7.
    for action in ("create", "update", "list", "retrieve"):
        many = action == "list"
        data = AgentccProviderCredentialSerializer(
            [row] if many else row,
            many=many,
            context={"view": SimpleNamespace(action=action)},
        ).data
        yield data[0] if many else data


@pytest.mark.integration
def test_extra_config_secret_field_and_alias_matrix_refused_before_save(
    auth_client, stored_provider, credential_io, synthetic_secret, caplog
):
    before = _snapshot(stored_provider)
    count = AgentccProviderCredential.no_workspace_objects.count()
    variants = (
        None,
        "",
        synthetic_secret,
        7,
        True,
        {"nested": synthetic_secret},
        [synthetic_secret],
    )
    for name in FORBIDDEN_NAMES:
        for value in variants:
            extra_config = {name: value, "timeout": 45, "api_path_prefix": "/v1"}
            payload = {
                "provider_name": "openai",
                "credentials": {"api_key": synthetic_secret},
                "extra_config": extra_config,
            }
            for response in (
                auth_client.post(ENDPOINT, payload, format="json"),
                auth_client.patch(
                    f"{ENDPOINT}{stored_provider.pk}/",
                    {"extra_config": extra_config},
                    format="json",
                ),
            ):
                assert response.status_code == 400
                assert synthetic_secret not in response.content.decode()
            assert _snapshot(stored_provider) == before
            assert AgentccProviderCredential.no_workspace_objects.count() == count
    credential_io.encrypt.assert_not_called()
    credential_io.push.assert_not_called()
    assert synthetic_secret not in caplog.text

    permitted = {"timeout": 45, "api_path_prefix": "/v1", "apiPathPrefix": "/v1"}
    for serializer in (
        AgentccProviderCredentialCreateSerializer(
            data={
                "provider_name": "openai",
                "credentials": {"api_key": synthetic_secret},
                "extra_config": permitted,
            }
        ),
        AgentccProviderCredentialUpdateSerializer(
            stored_provider,
            data={"extra_config": permitted},
            partial=True,
        ),
    ):
        assert serializer.is_valid()
        assert serializer.validated_data["extra_config"] == permitted
    policy = _policy()
    assert policy.reject_secret_extra_config(permitted) == permitted
    assert policy.public_provider_settings(permitted) == permitted
    for name in FORBIDDEN_NAMES:
        for value in variants:
            for extra_config in ({name: value}, {"wrapper": [{name: value}]}):
                with pytest.raises(ValueError) as caught:
                    policy.reject_secret_extra_config(extra_config)
                assert synthetic_secret not in str(caught.value)
    assert synthetic_secret not in caplog.text


@pytest.mark.integration
def test_legacy_extra_config_never_serialized_or_forwarded(
    stored_provider, synthetic_secret, credential_io, caplog
):
    harmless = {"timeout": 45, "apiPathPrefix": "/v1"}
    stored_provider.extra_config = {
        **dict.fromkeys(FORBIDDEN_NAMES, synthetic_secret),
        **harmless,
        "unknown": {"apiKey": synthetic_secret},
    }
    stored_provider.models_list = [
        synthetic_secret,
        f"before-{synthetic_secret}-after",
        "clean-model",
        None,
        7,
    ]
    stored_provider.save(update_fields=["extra_config", "models_list"])
    before = _snapshot(stored_provider)
    for data in _representations(stored_provider):
        assert data["extra_config"] == harmless
        assert not set(FORBIDDEN_NAMES).intersection(data["extra_config"])
        assert data["models_list"] == ["clean-model"]
        assert synthetic_secret not in json.dumps(data, default=str)
    projected = _policy().public_provider_settings(stored_provider.extra_config)
    assert projected == harmless
    assert not set(FORBIDDEN_NAMES).intersection(projected)
    assert synthetic_secret not in json.dumps(projected)
    assert _snapshot(stored_provider) == before
    assert stored_provider.extra_config == before["extra_config"]
    assert stored_provider.models_list == before["models_list"]
    credential_io.encrypt.assert_not_called()
    credential_io.push.assert_not_called()
    assert synthetic_secret not in caplog.text


@pytest.mark.integration
def test_all_credential_lengths_and_legacy_types_are_fixed_redacted(
    stored_provider, credential_io, synthetic_secret, caplog
):
    secret_len_1, secret_len_2, secret_len_3, secret_len_4 = "7", "78", "789", "7890"
    secret_len_5, long_secret = "78901", synthetic_secret
    empty_secret, null_legacy = "", None
    nested_legacy = {"nested": [synthetic_secret]}
    before = _snapshot(stored_provider)
    for value in (
        secret_len_1,
        secret_len_2,
        secret_len_3,
        secret_len_4,
        secret_len_5,
        long_secret,
        empty_secret,
        null_legacy,
        nested_legacy,
        7,
        False,
    ):
        credentials = dict.fromkeys(FORBIDDEN_NAMES, value)
        credential_io.decrypt.return_value = credentials
        for data in _representations(stored_provider):
            assert data["credentials"] == dict.fromkeys(FORBIDDEN_NAMES, "****")
            assert all(item != value for item in data["credentials"].values())
            assert synthetic_secret not in json.dumps(data, default=str)
            # Exact marker equality above forbids every original prefix/suffix.
        assert credential_io.decrypt.return_value == credentials
    assert _snapshot(stored_provider) == before
    assert synthetic_secret not in caplog.text


def test_model_identifier_sanitizer_handles_short_and_embedded_secrets(
    synthetic_secret, caplog
):
    sanitize = _policy().safe_model_identifiers
    valid_model_id = "clean-model"
    for submitted_secret in ("7", "78", "789", "7890", "78901", synthetic_secret):
        values = [
            submitted_secret,
            f"{submitted_secret}-suffix",
            f"prefix-{submitted_secret}",
            f"middle-{submitted_secret}-end",
            {"id": submitted_secret},
            None,
            7,
            "",
            "****",
            valid_model_id,
        ]
        before = copy.deepcopy(values)
        assert sanitize(values, [submitted_secret]) == [valid_model_id]
        assert values == before
        google_values = [f"models/{v}" for v in values if isinstance(v, str)]
        output = sanitize(
            google_values,
            [submitted_secret],
            normalize=lambda value: value.removeprefix("models/"),
        )
        assert output == [valid_model_id]
        assert submitted_secret not in json.dumps(output)
        assert "****" not in output
    # Check the raw identifier before a normalizer can erase the secret.
    assert sanitize(
        ["models/clean-model", valid_model_id],
        ["models/"],
        normalize=lambda value: value.removeprefix("models/"),
    ) == [valid_model_id]
    # Check again when normalization assembles a secret not present contiguously.
    assert sanitize(
        ["7-8-9", valid_model_id],
        ["789"],
        normalize=lambda value: value.replace("7-8-9", "789"),
    ) == [valid_model_id]
    assert sanitize([valid_model_id], ["", None, 7]) == [valid_model_id]
    assert sanitize([valid_model_id], synthetic_secret) == [valid_model_id]
    assert sanitize([synthetic_secret], {"legacy": [synthetic_secret]}) == []
    assert sanitize([synthetic_secret], {"api_key": synthetic_secret}.values()) == []
    assert sanitize([synthetic_secret], iter([synthetic_secret])) == []
    for malformed in (None, 7, synthetic_secret, {"id": synthetic_secret}):
        assert sanitize(malformed, [synthetic_secret]) == []
    assert synthetic_secret not in caplog.text


@pytest.mark.integration
def test_legacy_model_projection_fails_closed_without_usable_credentials(
    stored_provider, credential_io, synthetic_secret
):
    stored_provider.models_list = [synthetic_secret, "clean-model"]
    for malformed in (None, [], synthetic_secret, 7):
        credential_io.decrypt.return_value = malformed
        for data in _representations(stored_provider):
            assert data["credentials"] == {}
            assert data["models_list"] == []
    credential_io.decrypt.side_effect = ValueError(synthetic_secret)
    for data in _representations(stored_provider):
        assert data["credentials"] == {}
        assert data["models_list"] == []
        assert synthetic_secret not in json.dumps(data, default=str)


def test_public_settings_drop_unknown_and_malformed_values_without_mutation(
    synthetic_secret,
):
    policy = _policy()
    value = {
        "timeout": {"apiKey": synthetic_secret},
        "weight": [synthetic_secret],
        "apiPathPrefix": {"nested": synthetic_secret},
        "maxConcurrent": True,
        "models": [synthetic_secret],
        "unknown": synthetic_secret,
        "connPoolSize": 20,
        "enabled": False,
        "dataRegion": "eu",
    }
    before = copy.deepcopy(value)
    assert policy.public_provider_settings(value) == {
        "connPoolSize": 20,
        "enabled": False,
        "dataRegion": "eu",
    }
    assert value == before
    for malformed in (None, [], synthetic_secret, 7):
        assert policy.public_provider_settings(malformed) == {}
        with pytest.raises(ValueError):
            policy.reject_secret_extra_config(malformed)


@pytest.mark.integration
def test_legacy_plaintext_alias_also_filters_model_identifiers(
    stored_provider, credential_io, synthetic_secret
):
    credential_io.decrypt.return_value = {"api_key": "different-synthetic-credential"}
    stored_provider.extra_config = {"wrapper": [{"awsSessionToken": synthetic_secret}]}
    stored_provider.models_list = [synthetic_secret, "clean-model"]
    stored_provider.save(update_fields=["extra_config", "models_list"])
    before = _snapshot(stored_provider)
    for data in _representations(stored_provider):
        assert data["extra_config"] == {}
        assert data["models_list"] == ["clean-model"]
        assert synthetic_secret not in json.dumps(data, default=str)
    assert _snapshot(stored_provider) == before
