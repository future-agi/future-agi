"""Public AWS regions must not erase Bedrock ARNs."""

import base64
from unittest.mock import patch

import pytest

from agentcc.services.provider_model_safety import (
    InvalidProviderCredentialContext,
    credential_context_strings,
    filter_provider_model_ids,
)
from agentcc.tests import test_provider_credential_api as api_fixtures

secondary_org_client = api_fixtures.secondary_org_client
secondary_org_context = api_fixtures.secondary_org_context

REGION = "us-east-1"
PROFILE_ARN = (
    "arn:aws:bedrock:us-east-1:123456789012:inference-profile/"
    "us.anthropic.claude-3-5-sonnet-20241022-v2:0"
)
# Deliberately synthetic, nonoverlapping credential values; no live AWS material.
AWS_CREDENTIALS = {
    "aws_access_key_id": "cedar-harbor-7913-oriole",
    "aws_secret_access_key": "willow-meadow-2357-wren",
    "aws_session_token": "violet-canyon-8642-tern",
}


@pytest.mark.parametrize("region_field", ["aws_region", "region"])
@pytest.mark.parametrize("with_credentials", [False, True])
def test_region_metadata_preserves_manual_bedrock_arn(region_field, with_credentials):
    credentials = dict(AWS_CREDENTIALS) if with_credentials else {}
    credentials[region_field] = REGION
    echoes = [
        echo
        for value in AWS_CREDENTIALS.values()
        for echo in (value, base64.b64encode(value.encode()).decode())
    ] if with_credentials else []
    result = filter_provider_model_ids(
        [PROFILE_ARN, *echoes, PROFILE_ARN],
        (credentials, {}, []),
        provider_name="bedrock",
        api_format="bedrock",
        enforce_discovery_grammar=False,
    )
    assert result == {
        "models": [PROFILE_ARN, PROFILE_ARN],
        "dropped_model_count": len(echoes),
    }
    assert credentials[region_field] == REGION
    # Other consumers of the validator still receive every context string.
    assert REGION in credential_context_strings(credentials)


@pytest.mark.parametrize(
    "secret_field",
    ["api_key", "aws_access_key_id", "aws_secret_access_key", "aws_session_token"],
)
def test_actual_credentials_equal_to_region_are_still_excluded(secret_field):
    assert filter_provider_model_ids(
        [PROFILE_ARN],
        {"aws_region": REGION, secret_field: REGION},
        enforce_discovery_grammar=False,
    ) == {"models": [], "dropped_model_count": 1}


@pytest.mark.parametrize(
    "context",
    [
        REGION,
        [REGION],
        {"unknown_field": REGION},
        {"aws_region": {"api_key": REGION}},
        {"region": [REGION]},
    ],
)
def test_region_exemption_is_only_for_named_scalar_metadata(context):
    assert filter_provider_model_ids(
        [PROFILE_ARN], context, enforce_discovery_grammar=False
    ) == {"models": [], "dropped_model_count": 1}


@pytest.mark.parametrize("region_field", ["aws_region", "region"])
@pytest.mark.parametrize("bad", [None, False, 42, b"synthetic", "\ud800", "x" * 16385])
def test_region_metadata_still_validates_context(region_field, bad):
    with pytest.raises(InvalidProviderCredentialContext):
        filter_provider_model_ids(
            [], {region_field: bad}, enforce_discovery_grammar=False
        )


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.parametrize("saved", [False, True])
def test_bedrock_arn_survives_dialog_save_projection_and_publication(
    saved, secondary_org_client, secondary_org_context
):
    from agentcc.models.provider_credential import AgentccProviderCredential
    from agentcc.services.config_push import _assemble_providers
    from agentcc.views import gateway
    from integrations.services.credentials import CredentialManager

    org, _ = secondary_org_context
    credentials = {**AWS_CREDENTIALS, "aws_region": REGION}
    if saved:
        row = AgentccProviderCredential(
            organization=org,
            provider_name="bedrock",
            api_format="bedrock",
            encrypted_credentials=CredentialManager.encrypt(credentials),
            models_list=[PROFILE_ARN],
            extra_config={},
        )
        row.disable_auto_workspace_assignment = True
        row.save(force_insert=True)

    config = {"api_format": "bedrock", "models": [PROFILE_ARN]}
    if not saved:
        config.update(credentials)
    published = []

    def push(_org):
        # Exercise the real config builder without any gateway transport.
        published.append(_assemble_providers(str(org.pk)))
        return True

    with patch.object(
        gateway.AgentccGatewayViewSet, "_push_current_config", side_effect=push
    ):
        response = secondary_org_client.post(
            "/agentcc/gateways/default/update-provider/",
            {"name": "bedrock", "config": config},
            format="json",
        )
    assert response.status_code == 200
    assert response.json()["result"].get("dropped_model_count", 0) == 0
    row = AgentccProviderCredential.no_workspace_objects.get(
        organization=org, provider_name="bedrock", deleted=False
    )
    assert row.models_list == [PROFILE_ARN]
    assert CredentialManager.decrypt(bytes(row.encrypted_credentials)) == credentials
    assert published[0]["bedrock"]["models"] == [PROFILE_ARN]

    for url in (
        "/agentcc/provider-credentials/",
        f"/agentcc/provider-credentials/{row.pk}/",
    ):
        read = secondary_org_client.get(url)
        assert read.status_code == 200
        result = read.json()["result"]
        item = result[0] if isinstance(result, list) else result
        assert item["models_list"] == [PROFILE_ARN]

    hydrated = secondary_org_client.post(
        "/agentcc/provider-credentials/fetch_models/",
        {"provider_name": "bedrock", "saved_models_only": True},
        format="json",
    )
    assert hydrated.status_code == 200
    assert hydrated.json()["result"]["models"] == [PROFILE_ARN]
