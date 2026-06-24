"""DB test for the server-side chat routing gate (TH-5642).

Proves `_server_side_chat_filter()` selects exactly the CallExecutions the platform
should drive server-side — prompt sims + agent_definition TEXT sims on an external
HOSTED chat provider (Retell) with an assistant_id — and excludes SDK-driven agents
(LiveKit), missing assistant_id, and voice agents, so SDK-push is never double-driven.
"""

import pytest

from model_hub.models.choices import StatusType
from simulate.models import AgentDefinition, CallExecution, RunTest, Scenarios
from simulate.models.test_execution import TestExecution

TEXT = AgentDefinition.AgentTypeChoices.TEXT
VOICE = AgentDefinition.AgentTypeChoices.VOICE


def _mk_call(organization, workspace, *, source_type, provider, agent_type=TEXT,
             assistant_id="agent_1", label=""):
    ad = AgentDefinition.objects.create(
        agent_name=f"AUT {label}",
        agent_type=agent_type,
        inbound=True,
        description="routing test agent",
        organization=organization,
        workspace=workspace,
        provider=provider,
        assistant_id=assistant_id,
        languages=["en"],
    )
    scenario = Scenarios.objects.create(
        name=f"Scenario {label}",
        description="routing test scenario",
        source="test",
        scenario_type=Scenarios.ScenarioTypes.DATASET,
        organization=organization,
        workspace=workspace,
        agent_definition=ad,
        status=StatusType.COMPLETED.value,
    )
    rt = RunTest.objects.create(
        name=f"Run {label}",
        description="routing test run",
        agent_definition=ad,
        organization=organization,
        workspace=workspace,
        source_type=source_type,
    )
    te = TestExecution.objects.create(
        run_test=rt,
        status=TestExecution.ExecutionStatus.RUNNING,
        total_scenarios=1,
        total_calls=1,
        agent_definition=ad,
    )
    return CallExecution.objects.create(
        test_execution=te,
        scenario=scenario,
        status=CallExecution.CallStatus.REGISTERED,
        simulation_call_type=CallExecution.SimulationCallType.TEXT,
    )


@pytest.mark.unit
@pytest.mark.django_db
def test_server_side_filter_selects_only_hosted_and_prompt(organization, workspace):
    from simulate.services.chat_agent_adapter_factory import server_side_chat_filter

    prompt = _mk_call(organization, workspace,
                      source_type=RunTest.SourceTypes.PROMPT, provider="vapi", label="prompt")
    retell_hosted = _mk_call(organization, workspace,
                             source_type=RunTest.SourceTypes.AGENT_DEFINITION,
                             provider="retell", label="retell")
    livekit_sdk = _mk_call(organization, workspace,
                           source_type=RunTest.SourceTypes.AGENT_DEFINITION,
                           provider="livekit", label="livekit")
    retell_no_id = _mk_call(organization, workspace,
                            source_type=RunTest.SourceTypes.AGENT_DEFINITION,
                            provider="retell", assistant_id="", label="retell-no-id")
    retell_voice = _mk_call(organization, workspace,
                            source_type=RunTest.SourceTypes.AGENT_DEFINITION,
                            provider="retell", agent_type=VOICE, label="retell-voice")

    selected = set(
        CallExecution.objects.filter(server_side_chat_filter()).values_list("id", flat=True)
    )

    # Server-side: prompt sims + hosted Retell TEXT with an assistant_id.
    assert prompt.id in selected
    assert retell_hosted.id in selected
    # SDK-push / not-hosted: excluded (never double-driven).
    assert livekit_sdk.id not in selected
    assert retell_no_id.id not in selected
    assert retell_voice.id not in selected
