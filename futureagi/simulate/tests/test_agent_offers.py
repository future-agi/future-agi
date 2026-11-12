"""A version made on the Agents page is offered to the environments using its
agent, which may then list and pick it; and the Retell key check the build runs."""

import pytest

from simulate.models import (
    AgentDefinition,
    HarnessAgentVersionRuntime,
    HostedHarnessJob,
)
from simulate.services.harness_agent_versions import (
    OFFERED_KEY,
    create_agent_version,
    lineage_version_ids,
    record_offers,
)
from simulate.services.harness_credential_probes import PROVIDER_PROBES
from simulate.tests.test_agent_overview_api import _environment

pytestmark = pytest.mark.django_db

Trigger = HarnessAgentVersionRuntime.Trigger
PAYLOAD = {
    "connector": "livekit",
    "config": {"agent_name": "worker-a", "target_system_prompt": "Be Riley."},
    "definition_owner": "environment",
}


@pytest.fixture(autouse=True)
def versioning_on(settings):
    settings.HARNESS_VERSIONING_ENABLED = True
    settings.HARNESS_BUNDLE_CERTIFY_ENABLED = False


@pytest.fixture
def agent(organization, workspace):
    return AgentDefinition.objects.create(
        agent_name="riley",
        agent_type=AgentDefinition.AgentTypeChoices.VOICE,
        inbound=True,
        organization=organization,
        workspace=workspace,
    )


def _version(agent, parent=None, prompt="Be Riley."):
    return create_agent_version(
        agent,
        parent=parent,
        env=None,
        source={"kind": "provider"} if parent is None else None,
        prompt=prompt,
        agent_payload={
            **PAYLOAD,
            "config": {**PAYLOAD["config"], "target_system_prompt": prompt},
        },
        trigger=Trigger.CONNECT if parent is None else Trigger.PROMPT,
    )


def _offered(version):
    return HarnessAgentVersionRuntime.no_workspace_objects.get(
        agent_version=version
    ).agent_payload.get(OFFERED_KEY)


class TestOffers:
    def test_an_offer_is_remembered_once(self, agent):
        version = _version(agent)

        record_offers(version.id, ["env-a", "env-b"])
        record_offers(version.id, ["env-b", "env-c"])

        assert _offered(version) == ["env-a", "env-b", "env-c"]

    def test_no_environments_leave_the_version_alone(self, agent):
        version = _version(agent)

        record_offers(version.id, [])

        assert _offered(version) is None

    def test_an_offered_version_joins_the_environments_list(self, agent):
        first = _version(agent)
        environment = _environment(agent, 5)
        HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
            agent_version=first
        )
        environment.refresh_from_db()
        second = _version(agent, parent=first, prompt="Be Riley, briefly.")

        assert lineage_version_ids(environment, agent.id) == {str(first.id)}

        record_offers(second.id, [environment.id])

        assert lineage_version_ids(environment, agent.id) == {
            str(first.id),
            str(second.id),
        }

    def test_another_environment_does_not_see_the_offer(self, agent):
        first = _version(agent)
        offered, other = _environment(agent, 5), _environment(agent, 6)
        HostedHarnessJob.no_workspace_objects.filter(
            id__in=[offered.id, other.id]
        ).update(agent_version=first)
        other.refresh_from_db()
        second = _version(agent, parent=first, prompt="Be Riley, briefly.")

        record_offers(second.id, [offered.id])

        assert str(second.id) not in lineage_version_ids(other, agent.id)


class TestRetellProbe:
    def test_the_key_is_checked_against_the_v2_agent_list(self):
        request = PROVIDER_PROBES["retell"].build({"RETELL_API_KEY": " key-1 "})

        assert request.method == "POST"
        assert request.url == "https://api.retellai.com/v2/list-agents"
        assert request.headers["Authorization"] == "Bearer key-1"
