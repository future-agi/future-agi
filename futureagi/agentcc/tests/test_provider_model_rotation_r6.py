"""R6 retained-model regressions: real HTTP/DB, synthetic secrets, no transport."""

import base64
from unittest.mock import patch

import pytest
from structlog.testing import capture_logs
from agentcc.models.provider_credential import AgentccProviderCredential
from agentcc.serializers.provider_credential import AgentccProviderCredentialSerializer
from agentcc.services.config_push import _assemble_providers
from agentcc.tests import test_provider_credential_api as api_fixtures
from agentcc.views import provider_credential as module
from integrations.services.credentials import CredentialManager

secondary_org_client = api_fixtures.secondary_org_client
secondary_org_context = api_fixtures.secondary_org_context

OLD = "alphaunit-7319-cedar"
NEW = "bravotest-4826-maple"
AUX = "violetreed-6250-finch"
SAFE = ["gpt-4o", "ft:gpt-3.5-turbo-0125:personal::8sNGh8Xd"]
CODE = "provider_credential_context_invalid"


def echoes(value):
    return [value, "route/" + value, base64.b64encode(value.encode()).decode()]


def stored_row(org, *, broken=False):
    row = AgentccProviderCredential(
        organization=org,
        provider_name="openai",
        api_format="openai",
        encrypted_credentials=(
            b"r6-unreadable-synthetic-cipher"
            if broken
            else CredentialManager.encrypt({"api_key": OLD})
        ),
        models_list=echoes(OLD) + echoes(NEW) + echoes(AUX) + SAFE,
        extra_config={"api_secret": AUX},
    )
    row.disable_auto_workspace_assignment = True
    row.save(force_insert=True)
    return row


def push_observer(org, observed):
    def push(_org):
        # Exercise the real publication assembler without making a network call.
        payload = _assemble_providers(str(org.pk))
        observed.append(payload["openai"]["models"])
        return True

    return push


def assert_reads(client, row, expected):
    for url in (
        f"/agentcc/provider-credentials/{row.pk}/",
        "/agentcc/provider-credentials/",
    ):
        response = client.get(url)
        assert response.status_code == 200
        result = response.json()["result"]
        item = result[0] if isinstance(result, list) else result
        assert item["models_list"] == expected
        for value in (OLD, NEW, AUX):
            assert value not in response.content.decode()
    hydrated = client.post(
        "/agentcc/provider-credentials/fetch_models/",
        {"provider_name": "openai", "saved_models_only": True},
        format="json",
    )
    assert hydrated.status_code == 200
    assert hydrated.json()["result"]["models"] == expected


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.parametrize("replace", [False, True])
def test_rotation_scrubs_both_keys_before_persist_push_and_reads(
    replace, secondary_org_client, secondary_org_context
):
    org, _ = secondary_org_context
    row = stored_row(org)
    observed = []
    with patch.object(
        module.AgentccProviderCredentialViewSet,
        "_push_config_to_gateway",
        side_effect=push_observer(org, observed),
    ):
        response = secondary_org_client.post(
            f"/agentcc/provider-credentials/{row.pk}/rotate/",
            {"credentials": {"api_key": NEW}, "replace_credentials": replace},
            format="json",
        )
    assert response.status_code == 200
    assert response.json()["result"]["models_list"] == SAFE
    assert response.json()["result"]["dropped_model_count"] == 9
    for value in (OLD, NEW, AUX):
        assert value not in response.content.decode()
    row.refresh_from_db()
    assert row.models_list == SAFE
    assert CredentialManager.decrypt(bytes(row.encrypted_credentials)) == {
        "api_key": NEW
    }
    assert observed == [SAFE]
    assert_reads(secondary_org_client, row, SAFE)


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.parametrize("replace", [False, True])
def test_unreadable_rotation_never_publishes_legacy_ids(
    replace, secondary_org_client, secondary_org_context
):
    org, _ = secondary_org_context
    row = stored_row(org, broken=True)
    before = bytes(row.encrypted_credentials)
    observed = []
    with patch.object(
        module.AgentccProviderCredentialViewSet,
        "_push_config_to_gateway",
        side_effect=push_observer(org, observed),
    ):
        response = secondary_org_client.post(
            f"/agentcc/provider-credentials/{row.pk}/rotate/",
            {"credentials": {"api_key": NEW}, "replace_credentials": replace},
            format="json",
        )
    row.refresh_from_db()
    if replace:
        # Explicit full replacement remains an admin recovery path, but it cannot
        # certify ANY legacy ID against the unreadable prior credential.
        assert response.status_code == 200
        result = response.json()["result"]
        assert result["models_list"] == []
        assert result["code"] == CODE
        assert "re-enter" in result["model_warning"].lower()
        assert result["dropped_model_count"] == 11
        assert row.models_list == []
        assert observed == [[]]
        assert CredentialManager.decrypt(bytes(row.encrypted_credentials)) == {
            "api_key": NEW
        }
        assert_reads(secondary_org_client, row, [])
    else:
        assert response.status_code == 400
        assert response.json()["code"] == CODE
        assert "replace_credentials=true" in response.json()["message"]
        assert bytes(row.encrypted_credentials) == before
        assert row.last_rotated_at is None
        assert observed == []
    for value in (OLD, NEW, AUX):
        assert value not in response.content.decode()


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.parametrize("detail", [False, True])
def test_unreadable_public_projection_is_typed_and_actionable(
    detail, secondary_org_client, secondary_org_context
):
    org, _ = secondary_org_context
    row = stored_row(org, broken=True)
    response = secondary_org_client.get(
        "/agentcc/provider-credentials/" + (f"{row.pk}/" if detail else "")
    )
    assert response.status_code == 200
    result = response.json()["result"]
    item = result if detail else result[0]
    assert item["models_list"] == []
    assert item["code"] == CODE
    assert "credential" in item["model_warning"].lower()


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.parametrize("method", ["patch", "put"])
def test_metadata_save_scrubs_retained_models_before_push(
    method, secondary_org_client, secondary_org_context
):
    org, _ = secondary_org_context
    row = stored_row(org)
    # The new-key candidate is not a secret until rotation; do not overfilter it.
    expected = echoes(NEW) + SAFE
    observed = []
    with patch.object(
        module.AgentccProviderCredentialViewSet,
        "_push_config_to_gateway",
        side_effect=push_observer(org, observed),
    ):
        response = getattr(secondary_org_client, method)(
            f"/agentcc/provider-credentials/{row.pk}/",
            {"display_name": "Renamed", "extra_config": {}},
            format="json",
        )
    assert response.status_code == 200
    assert response.json()["result"]["models_list"] == expected
    row.refresh_from_db()
    assert row.models_list == expected
    assert observed == [expected]
    assert response.json()["result"]["dropped_model_count"] == 6


@pytest.mark.integration
@pytest.mark.api
def test_unreadable_metadata_save_refuses_without_mutation_or_push(
    secondary_org_client, secondary_org_context
):
    org, _ = secondary_org_context
    row = stored_row(org, broken=True)
    with patch.object(
        module.AgentccProviderCredentialViewSet, "_push_config_to_gateway"
    ) as push:
        response = secondary_org_client.patch(
            f"/agentcc/provider-credentials/{row.pk}/",
            {"display_name": "Must not persist"},
            format="json",
        )
    assert response.status_code == 400
    assert response.json()["code"] == CODE
    row.refresh_from_db()
    assert row.display_name == ""
    push.assert_not_called()


@pytest.mark.integration
@pytest.mark.api
def test_every_push_projection_scrubs_retained_models(secondary_org_context):
    org, _ = secondary_org_context
    stored_row(org)
    payload = _assemble_providers(str(org.pk))
    assert payload["openai"]["models"] == echoes(NEW) + SAFE


@pytest.mark.integration
@pytest.mark.api
def test_push_projection_omits_non_dict_credentials_without_logging_secrets(
    secondary_org_context, caplog
):
    org, _ = secondary_org_context
    stored_row(org)
    canary = "synthetic-nondict-canary-prr009"
    invalid = AgentccProviderCredential(
        organization=org,
        provider_name="cohere",
        api_format="openai",
        encrypted_credentials=CredentialManager.encrypt(canary),
        models_list=["command-r"],
        extra_config={},
    )
    invalid.disable_auto_workspace_assignment = True
    invalid.save(force_insert=True)

    with capture_logs() as records:
        payload = _assemble_providers(str(org.pk))

    assert "cohere" not in payload
    assert "openai" in payload
    assert payload["openai"]["models"] == echoes(NEW) + SAFE
    assert any(
        record.get("event") == "provider_model_projection_refused"
        and record.get("code") == CODE
        and record.get("log_level") == "warning"
        for record in records
    )
    assert canary not in repr(records)
    assert canary not in repr([record.__dict__ for record in caplog.records])
    assert canary not in repr(payload)


@pytest.mark.integration
@pytest.mark.api
def test_direct_serializer_uses_full_credential_exclusion(secondary_org_context):
    org, _ = secondary_org_context
    row = stored_row(org)
    assert AgentccProviderCredentialSerializer(row).data["models_list"] == (
        echoes(NEW) + SAFE
    )
