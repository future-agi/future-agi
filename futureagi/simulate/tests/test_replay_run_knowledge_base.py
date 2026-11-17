"""Replay creation must retain each evaluation's selected knowledge base.

Exercise the real serializer, view, and ORM without invoking an evaluator.
Requests carry explicit scope while ambient workspace context is cleared.
"""

from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from rest_framework.test import APIRequestFactory, force_authenticate

from accounts.models import Organization
from accounts.models.workspace import Workspace
from model_hub.models.ai_model import AIModel
from model_hub.models.choices import DatasetSourceChoices, StatusType
from model_hub.models.develop_dataset import Dataset, KnowledgeBaseFile
from model_hub.models.evals_metric import EvalTemplate
from model_hub.utils.workspace_scope import knowledge_base_id_for
from model_hub.views.utils import evals
from simulate.models import AgentDefinition, RunTest, Scenarios, SimulateEvalConfig
from simulate.models.test_execution import CallExecution, TestExecution
from simulate.services import test_executor
from simulate.temporal.activities import xl
from simulate.views.run_test import CreateRunTestView
from tfc.middleware.workspace_context import workspace_context
from tracer.models.project import Project
from tracer.models.replay_session import ReplaySession, ReplaySessionStep

pytestmark = pytest.mark.django_db


@pytest.fixture
def replay_case(organization, workspace, user):
    agent = AgentDefinition.no_workspace_objects.create(
        agent_name="Replay knowledge base agent",
        agent_type=AgentDefinition.AgentTypeChoices.TEXT,
        inbound=False,
        description="Synthetic text agent for replay knowledge-base tests",
        organization=organization,
        workspace=workspace,
    )
    scenario = Scenarios.no_workspace_objects.create(
        name="Replay knowledge base scenario",
        source="Replay fixture",
        scenario_type=Scenarios.ScenarioTypes.DATASET,
        organization=organization,
        workspace=workspace,
        agent_definition=agent,
    )
    template = EvalTemplate.no_workspace_objects.create(
        name="replay_grounding",
        organization=organization,
        workspace=workspace,
        eval_type="llm",
        config={},
    )
    project = Project.no_workspace_objects.create(
        name="Replay knowledge base project",
        organization=organization,
        workspace=workspace,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="observe",
    )
    replay = ReplaySession.no_workspace_objects.create(
        project=project,
        replay_type="session",
        ids=[],
        current_step=ReplaySessionStep.GENERATING,
        agent_definition=agent,
        scenario=scenario,
    )
    kb = KnowledgeBaseFile.no_workspace_objects.create(
        name="Selected domain reference",
        organization=organization,
        workspace=workspace,
        status="completed",
    )
    return SimpleNamespace(
        organization=organization,
        workspace=workspace,
        user=user,
        agent=agent,
        scenario=scenario,
        template=template,
        replay=replay,
        kb=kb,
    )


def _evaluation(case, name, **overrides):
    return {
        "template_id": str(case.template.pk),
        "name": name,
        "mapping": {"text": "transcript"},
        **overrides,
    }


def _create_replay_run(case, evaluations):
    request = APIRequestFactory().post(
        "/simulate/run-tests/create/",
        {
            "name": "Replay with domain references",
            "agent_definition_id": str(case.agent.pk),
            "scenario_ids": [str(case.scenario.pk)],
            "replay_session_id": str(case.replay.pk),
            "evaluations_config": evaluations,
        },
        format="json",
    )
    request.organization = case.organization
    request.workspace = case.workspace
    force_authenticate(request, user=case.user)
    with workspace_context(None, organization=None, user=None):
        return CreateRunTestView.as_view()(request)


def _created_configs(case, response):
    assert response.status_code == 201, response.data
    case.replay.refresh_from_db()
    assert case.replay.current_step == ReplaySessionStep.COMPLETED
    assert str(case.replay.run_test_id) == str(response.data["id"])
    return SimulateEvalConfig.no_workspace_objects.filter(
        run_test_id=case.replay.run_test_id
    )


@pytest.mark.parametrize(
    "legacy_organization", [False, True], ids=["primary", "active-org"]
)
def test_replay_keeps_each_selected_knowledge_base(replay_case, legacy_organization):
    case = replay_case
    if legacy_organization:
        case.user.organization = Organization.objects.create(name="Legacy organization")
        case.user.save(update_fields=["organization"])
    second_kb = KnowledgeBaseFile.no_workspace_objects.create(
        name="Second domain reference",
        organization=case.organization,
        workspace=case.workspace,
    )
    response = _create_replay_run(
        case,
        [
            _evaluation(case, "first", kb_id=str(case.kb.pk)),
            _evaluation(case, "second", kb_id=str(second_kb.pk)),
            _evaluation(case, "omitted"),
            _evaluation(case, "null", kb_id=None),
        ],
    )
    configs = _created_configs(case, response)
    assert dict(configs.values_list("name", "kb_id_id")) == {
        "first": case.kb.pk,
        "second": second_kb.pk,
        "omitted": None,
        "null": None,
    }
    assert all(config.mapping == {"text": "transcript"} for config in configs)


def test_replay_accepts_default_workspace_legacy_knowledge_base(replay_case):
    case = replay_case
    assert case.workspace.is_default
    # Bypass insert-time workspace assignment to model a pre-workspace file.
    KnowledgeBaseFile.no_workspace_objects.filter(pk=case.kb.pk).update(workspace=None)
    response = _create_replay_run(
        case, [_evaluation(case, "legacy", kb_id=str(case.kb.pk))]
    )
    assert _created_configs(case, response).get().kb_id_id == case.kb.pk


@pytest.mark.parametrize("selection", [{}, {"kb_id": None}], ids=["omitted", "null"])
def test_replay_without_selected_knowledge_base_remains_supported(
    replay_case, selection
):
    response = _create_replay_run(
        replay_case, [_evaluation(replay_case, "ungrounded", **selection)]
    )
    assert _created_configs(replay_case, response).get().kb_id_id is None


@pytest.mark.parametrize(
    "unavailable", ["organization", "workspace", "deleted", "missing"]
)
def test_replay_rejects_unavailable_knowledge_base_atomically(replay_case, unavailable):
    case = replay_case
    kb_id = uuid4()
    if unavailable != "missing":
        organization = case.organization
        workspace = case.workspace
        if unavailable == "organization":
            organization = Organization.objects.create(
                name="Foreign knowledge base org"
            )
            workspace = Workspace.no_workspace_objects.create(
                name="Foreign organization workspace",
                organization=organization,
                created_by=case.user,
                is_default=False,
            )
        elif unavailable == "workspace":
            workspace = Workspace.no_workspace_objects.create(
                name="Other private workspace",
                organization=organization,
                created_by=case.user,
                is_default=False,
            )
        kb = KnowledgeBaseFile.no_workspace_objects.create(
            name="Unavailable reference",
            organization=organization,
            workspace=workspace,
        )
        kb_id = kb.pk
        if unavailable == "deleted":
            KnowledgeBaseFile.no_workspace_objects.filter(pk=kb.pk).update(deleted=True)
    before_runs = RunTest.no_workspace_objects.count()
    before_configs = SimulateEvalConfig.no_workspace_objects.count()
    response = _create_replay_run(
        case,
        [
            _evaluation(case, "valid-first", kb_id=str(case.kb.pk)),
            _evaluation(case, "invalid-second", kb_id=str(kb_id)),
        ],
    )
    assert response.status_code == 404, response.data
    assert RunTest.no_workspace_objects.count() == before_runs
    assert SimulateEvalConfig.no_workspace_objects.count() == before_configs
    case.replay.refresh_from_db()
    assert case.replay.run_test_id is None
    assert case.replay.current_step == ReplaySessionStep.GENERATING


@pytest.mark.parametrize("execution_path", ["temporal", "legacy"])
@pytest.mark.parametrize("selection", ["selected", "omitted", "null"])
def test_replay_execution_passes_selected_uuid_to_adapter(
    replay_case, monkeypatch, execution_path, selection
):
    """Real create/reload/execute path, with only the judge boundary stubbed.

    The boundary uses the same scoped UUID resolver as the real adapter, so
    an ordinary named FK object cannot masquerade as a valid selection.
    """
    case = replay_case
    dataset = Dataset.no_workspace_objects.create(
        name="Replay transcript scenario",
        organization=case.organization,
        workspace=case.workspace,
        user=case.user,
        source=DatasetSourceChoices.SCENARIO.value,
        column_order=[],
    )
    case.scenario.dataset = dataset
    case.scenario.save(update_fields=["dataset"])
    selection_config = {}
    expected_kb_id = None
    if selection == "selected":
        assert str(case.kb) == "Selected domain reference"
        selection_config["kb_id"] = str(case.kb.pk)
        expected_kb_id = case.kb.pk
    elif selection == "null":
        selection_config["kb_id"] = None
    response = _create_replay_run(
        case, [_evaluation(case, "execution reference", **selection_config)]
    )
    # Reload from storage, as workers do; do not substitute an FK test double.
    config = _created_configs(case, response).get()
    config.status = StatusType.PROCESSING.value
    config.save(update_fields=["status"])
    execution = TestExecution.no_workspace_objects.create(
        run_test=config.run_test,
        agent_definition=case.agent,
        scenario_ids=[str(case.scenario.pk)],
        total_scenarios=1,
        total_calls=1,
        status=TestExecution.ExecutionStatus.COMPLETED,
    )
    call = CallExecution.no_workspace_objects.create(
        test_execution=execution,
        scenario=case.scenario,
        simulation_call_type=CallExecution.SimulationCallType.TEXT,
        status=CallExecution.CallStatus.COMPLETED,
    )
    transcript = "Customer: Is this supported? Agent: Yes, per the domain reference."
    adapter_calls = []
    result = {"output": "8", "reason": "Reference evaluated", "output_type": "score"}

    def evaluate_without_provider(**kwargs):
        adapter_calls.append(kwargs)
        assert kwargs["kb_id"] == expected_kb_id
        if expected_kb_id is not None:
            assert isinstance(kwargs["kb_id"], UUID)
            assert (
                knowledge_base_id_for(
                    kwargs["org"], kwargs["workspace"], kwargs["kb_id"]
                )
                == expected_kb_id
            )
        return result.copy()

    monkeypatch.setattr(evals, "run_eval_func", evaluate_without_provider)
    monkeypatch.setattr(test_executor, "run_eval_func", evaluate_without_provider)
    # Worker cleanup must not close pytest-django's enclosing transaction.
    monkeypatch.setattr(xl, "close_old_connections", lambda: None)
    monkeypatch.setattr(test_executor, "close_old_connections", lambda: None)
    with workspace_context(None, organization=None, user=None):
        if execution_path == "temporal":
            xl._run_single_evaluation(config, call, {"transcript": transcript})
        else:
            executor = test_executor.TestExecutor(initialize_voice_service=False)
            executor._run_single_simulate_evaluation(
                config, call, {"transcript": transcript}
            )

    assert len(adapter_calls) == 1
    assert adapter_calls[0]["org"].pk == case.organization.pk
    assert adapter_calls[0]["workspace"].pk == case.workspace.pk
    assert adapter_calls[0]["mappings"] == {"text": transcript}
    config.refresh_from_db()
    call.refresh_from_db()
    assert config.kb_id_id == expected_kb_id
    assert config.status == StatusType.COMPLETED.value
    assert call.eval_outputs[str(config.pk)]["output"] == result["output"]
    assert call.eval_outputs[str(config.pk)]["reason"] == result["reason"]
    assert "error" not in call.eval_outputs[str(config.pk)]
