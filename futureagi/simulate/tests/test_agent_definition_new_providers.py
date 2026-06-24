"""Agent-definition support for the multi-provider roster (TH-5642).

Proves — as a regression guard, not an assertion — that the existing
AgentDefinition model + AgentDefinitionSerializer are already provider-agnostic:
an agent definition can be CREATED (validated + saved) for each new provider
(Deepgram / Agora / Bland / ElevenLabs voice, Retell chat) with no schema change.
The model's `provider` is a free-form CharField and `validate()` rejects no
provider — it only special-cases LiveKit and requires *some* way to reach the
agent (contact_number for SIP, or api_key+assistant_id for the web bridge).

If a future change reintroduces a hard provider allow-list on the write path
(the historical bug the ProviderSpec registry exists to prevent), these tests
fail — which is the point.
"""

import pytest
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory

from simulate.models import AgentDefinition
from simulate.serializers.agent_definition import AgentDefinitionSerializer


@pytest.fixture
def mock_request(user):
    request = APIRequestFactory().post("/simulate/agent-definitions/")
    drf_request = Request(request)
    drf_request._user = user
    drf_request._request.user = user
    return drf_request


def _base(provider, agent_type, **extra):
    data = {
        "agent_name": f"{provider} test agent",
        "agent_type": agent_type,
        "inbound": True,
        "description": f"Agent-under-test on {provider}",
        "provider": provider,
        "languages": ["en"],
    }
    data.update(extra)
    return data


# (provider, agent_type, reach-creds) — each new provider with a realistic way to
# be reached: SIP providers via contact_number, web-bridge providers via api_key+id.
NEW_PROVIDER_CASES = [
    ("deepgram", AgentDefinition.AgentTypeChoices.VOICE,
     {"api_key": "dg-key", "assistant_id": "dg-agent-1"}),
    ("elevenlabs", AgentDefinition.AgentTypeChoices.VOICE,
     {"api_key": "el-key", "assistant_id": "el-agent-1"}),
    ("agora", AgentDefinition.AgentTypeChoices.VOICE,
     {"contact_number": "+14155550101"}),
    ("bland", AgentDefinition.AgentTypeChoices.VOICE,
     {"contact_number": "+14155550102"}),
    ("retell", AgentDefinition.AgentTypeChoices.TEXT,
     {"api_key": "rt-key", "assistant_id": "agent_chat_1"}),
]


@pytest.mark.unit
@pytest.mark.django_db
@pytest.mark.parametrize("provider,agent_type,creds", NEW_PROVIDER_CASES)
def test_agent_definition_creatable_for_new_provider(
    provider, agent_type, creds, organization, workspace, mock_request
):
    data = _base(provider, agent_type, **creds)
    serializer = AgentDefinitionSerializer(
        data=data, context={"request": mock_request}
    )
    # The object-level validate() is exactly what would reject an unknown
    # provider — assert it does NOT.
    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data["provider"] == provider

    instance = serializer.save(organization=organization, workspace=workspace)
    assert instance.pk is not None
    assert instance.provider == provider
    assert instance.agent_type == agent_type


@pytest.mark.unit
@pytest.mark.django_db
def test_voice_agent_requires_a_provider(organization, workspace, mock_request):
    # The only provider constraint on voice agents is "present" — not membership
    # in a hard-coded allow-list.
    data = _base("", AgentDefinition.AgentTypeChoices.VOICE,
                 contact_number="+14155550103")
    serializer = AgentDefinitionSerializer(
        data=data, context={"request": mock_request}
    )
    assert not serializer.is_valid()
    assert "provider" in serializer.errors
