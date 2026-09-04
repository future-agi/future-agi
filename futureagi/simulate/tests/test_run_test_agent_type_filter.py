"""
API tests for the ``agent_type`` (Voice / Chat) filter on
GET /simulate/run-tests/ (RunTestListView).

The frontend Run Simulation table sends ``agent_type=voice`` or
``agent_type=text``. Chat/text must also include prompt-based sims, which
have no linked agent_definition. See issue #1387.
"""

import pytest
from rest_framework import status

from simulate.models import AgentDefinition, RunTest


@pytest.fixture
def voice_agent(db, organization, workspace):
    return AgentDefinition.objects.create(
        agent_name="Voice Agent",
        agent_type=AgentDefinition.AgentTypeChoices.VOICE,
        contact_number="+12345678901",
        inbound=True,
        organization=organization,
        workspace=workspace,
        languages=["en"],
    )


@pytest.fixture
def text_agent(db, organization, workspace):
    return AgentDefinition.objects.create(
        agent_name="Text Agent",
        agent_type=AgentDefinition.AgentTypeChoices.TEXT,
        inbound=True,
        organization=organization,
        workspace=workspace,
        languages=["en"],
    )


@pytest.fixture
def voice_run_test(db, organization, workspace, voice_agent):
    return RunTest.objects.create(
        name="Voice sim",
        organization=organization,
        workspace=workspace,
        agent_definition=voice_agent,
        source_type=RunTest.SourceTypes.AGENT_DEFINITION,
    )


@pytest.fixture
def text_run_test(db, organization, workspace, text_agent):
    return RunTest.objects.create(
        name="Text sim",
        organization=organization,
        workspace=workspace,
        agent_definition=text_agent,
        source_type=RunTest.SourceTypes.AGENT_DEFINITION,
    )


@pytest.fixture
def prompt_run_test(db, organization, workspace):
    # Prompt-based sim: no agent_definition, source_type=PROMPT.
    return RunTest.objects.create(
        name="Prompt sim",
        organization=organization,
        workspace=workspace,
        source_type=RunTest.SourceTypes.PROMPT,
    )


def _ids(response):
    return {r["id"] for r in response.json()["results"]}


@pytest.mark.integration
@pytest.mark.api
class TestRunTestAgentTypeFilter:
    def test_voice_returns_only_voice(
        self, auth_client, voice_run_test, text_run_test, prompt_run_test
    ):
        response = auth_client.get("/simulate/run-tests/?agent_type=voice")

        assert response.status_code == status.HTTP_200_OK, response.content
        ids = _ids(response)
        assert str(voice_run_test.id) in ids
        assert str(text_run_test.id) not in ids
        assert str(prompt_run_test.id) not in ids

    def test_text_includes_text_agent_and_prompt_sims(
        self, auth_client, voice_run_test, text_run_test, prompt_run_test
    ):
        response = auth_client.get("/simulate/run-tests/?agent_type=text")

        assert response.status_code == status.HTTP_200_OK, response.content
        ids = _ids(response)
        assert str(text_run_test.id) in ids
        # Prompt-based sims have no agent_definition -> treated as chat/text.
        assert str(prompt_run_test.id) in ids
        assert str(voice_run_test.id) not in ids

    def test_no_agent_type_returns_all(
        self, auth_client, voice_run_test, text_run_test, prompt_run_test
    ):
        response = auth_client.get("/simulate/run-tests/")

        assert response.status_code == status.HTTP_200_OK, response.content
        ids = _ids(response)
        assert {
            str(voice_run_test.id),
            str(text_run_test.id),
            str(prompt_run_test.id),
        } <= ids

    def test_agent_type_combines_with_search(
        self, auth_client, voice_run_test, text_run_test
    ):
        # Search matches both by name substring "sim"; agent_type narrows to voice.
        response = auth_client.get(
            "/simulate/run-tests/?agent_type=voice&search=sim"
        )

        assert response.status_code == status.HTTP_200_OK, response.content
        ids = _ids(response)
        assert str(voice_run_test.id) in ids
        assert str(text_run_test.id) not in ids

    def test_invalid_agent_type_is_rejected(self, auth_client):
        response = auth_client.get("/simulate/run-tests/?agent_type=bogus")

        assert response.status_code == status.HTTP_400_BAD_REQUEST, response.content
