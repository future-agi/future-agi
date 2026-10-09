"""Grading a simulated call writes only the attachment's status.

Both graders (`TestExecutor._run_single_simulate_evaluation` and the Temporal
activity's `_run_single_evaluation`) run against real rows, with the real
`run_eval_func` and the real evaluator set-up. Only the evaluator class, the
usage helpers, field mapping, output formatting and input validation are
faked, so whatever the set-up writes into the settings it is handed is what
the grader holds when it saves the row.
"""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from django.conf import settings

import model_hub.views.utils.evals as evals_module
from model_hub.models.choices import DatasetSourceChoices, StatusType
from model_hub.models.develop_dataset import Dataset
from model_hub.models.evals_metric import EvalTemplate
from simulate.models import AgentDefinition, Scenarios
from simulate.models.agent_version import AgentVersion
from simulate.models.eval_config import SimulateEvalConfig
from simulate.models.run_test import RunTest
from simulate.models.simulator_agent import SimulatorAgent
from simulate.models.test_execution import CallExecution, TestExecution
from simulate.services import test_executor as executor_module
from simulate.temporal.activities import xl as xl_module
from tfc.constants.api_calls import APICallStatusChoices

TRANSCRIPT = "Hello. Yes, order 123 shipped."
ATTACHMENT_CONFIG = {"config": {}}


class JudgeUnavailable(RuntimeError):
    pass


@pytest.fixture
def agent_definition(db, organization, workspace):
    return AgentDefinition.objects.create(
        agent_name="Test Agent",
        agent_type=AgentDefinition.AgentTypeChoices.VOICE,
        contact_number="+15551230000",
        inbound=True,
        description="Test agent for grading writes",
        organization=organization,
        workspace=workspace,
        languages=["en"],
    )


@pytest.fixture
def agent_version(db, agent_definition, organization, workspace):
    return AgentVersion.objects.create(
        agent_definition=agent_definition,
        organization=organization,
        workspace=workspace,
        version_number=1,
        version_name="v1",
        configuration_snapshot={
            "description": "You are a helpful agent.",
            "assistant_id": "test-assistant-id",
        },
    )


@pytest.fixture
def simulator_agent(db, organization, workspace):
    return SimulatorAgent.objects.create(
        name="Test Simulator",
        prompt="You are a test simulator agent.",
        voice_provider="elevenlabs",
        voice_name="marissa",
        model="gpt-4",
        organization=organization,
        workspace=workspace,
    )


@pytest.fixture
def dataset_for_scenario(db, organization, user, workspace):
    dataset = Dataset.no_workspace_objects.create(
        name="Test Dataset",
        organization=organization,
        workspace=workspace,
        user=user,
        source=DatasetSourceChoices.SCENARIO.value,
    )
    return dataset


@pytest.fixture
def scenario(db, organization, workspace, dataset_for_scenario, agent_definition):
    return Scenarios.objects.create(
        name="Test Scenario",
        description="Test scenario",
        source="Test source",
        scenario_type=Scenarios.ScenarioTypes.DATASET,
        organization=organization,
        workspace=workspace,
        dataset=dataset_for_scenario,
        agent_definition=agent_definition,
        status=StatusType.COMPLETED.value,
    )


@pytest.fixture
def run_test(db, organization, workspace, agent_definition, scenario, simulator_agent):
    rt = RunTest.objects.create(
        name="Test Run",
        description="Test run",
        agent_definition=agent_definition,
        simulator_agent=simulator_agent,
        organization=organization,
        workspace=workspace,
    )
    rt.scenarios.add(scenario)
    return rt


@pytest.fixture
def test_execution(
    db, run_test, simulator_agent, agent_definition, agent_version, scenario
):
    return TestExecution.objects.create(
        run_test=run_test,
        status=TestExecution.ExecutionStatus.COMPLETED,
        total_scenarios=1,
        total_calls=1,
        simulator_agent=simulator_agent,
        agent_definition=agent_definition,
        agent_version=agent_version,
        scenario_ids=[str(scenario.id)],
    )


@pytest.fixture
def call_execution(db, test_execution, scenario, agent_version):
    return CallExecution.objects.create(
        test_execution=test_execution,
        scenario=scenario,
        phone_number="+15551230000",
        status=CallExecution.CallStatus.COMPLETED,
        agent_version=agent_version,
        recording_url="s3://bucket/rec.mp3",
        call_summary="Customer called about order 123.",
        ended_reason="customer-ended-call",
        duration_seconds=120,
        simulation_call_type=CallExecution.SimulationCallType.VOICE,
    )


@pytest.fixture
def transcript_data():
    return {
        "transcript": TRANSCRIPT,
        "voice_recording": "s3://bucket/rec.mp3",
        "assistant_recording": "s3://bucket/asst.mp3",
        "customer_recording": "s3://bucket/cust.mp3",
        "stereo_recording": "s3://bucket/stereo.mp3",
        "user_chat_transcript": "",
        "assistant_chat_transcript": "",
    }


@pytest.fixture
def template(db, organization):
    # No version is created: a default version's prompt would override the
    # template text and hide which text the grade actually used.
    return EvalTemplate.objects.create(
        name="grading-writes-status-only",
        organization=organization,
        eval_type="llm",
        config={
            "eval_type_id": "CustomPromptEvaluator",
            "rule_prompt": "v1",
            "output": "Pass/Fail",
            "config": {},
        },
    )


@pytest.fixture
def eval_config(db, template, run_test):
    # Starts at queued, not the model default `completed`, so a test that
    # reads `completed` back has observed the grade's write. The judge model
    # is a FutureAGI one so the set-up never looks up a provider key.
    return SimulateEvalConfig.objects.create(
        name="Grading writes",
        eval_template=template,
        run_test=run_test,
        # A literal, not ATTACHMENT_CONFIG: the marker tests mutate this very object.
        config={"config": {}},
        mapping={"transcript": "call.transcript"},
        model="turing_large",
        status=StatusType.QUEUED.value,
    )


@pytest.fixture
def fake_evaluator(monkeypatch):
    """Evaluator stand-in installed where `run_eval_func` resolves the class.

    Defined per test so `seen`, `on_run` and `fail` never leak between tests.
    """

    class FakeEvaluator:
        cost = {"total_cost": 0}
        token_usage = {}
        seen: list[dict] = []
        on_run = None
        fail = False

        def __init__(self, **kwargs):
            type(self).seen.append(dict(kwargs))

        def run(self, **_):
            if type(self).on_run is not None:
                type(self).on_run()
            if type(self).fail:
                raise JudgeUnavailable("judge unavailable")
            return SimpleNamespace(
                eval_results=[
                    {
                        "data": {"input": "hello"},
                        "failure": None,
                        "reason": "ok",
                        "runtime": 0.01,
                        "model": "turing_large",
                        "metrics": None,
                        "metadata": None,
                    }
                ]
            )

    monkeypatch.setattr(evals_module, "CustomPromptEvaluator", FakeEvaluator)
    return FakeEvaluator


@pytest.fixture(autouse=True)
def grading_patches(monkeypatch):
    def _fake_log_and_deduct(organization, api_call_type, config=None, **_kw):
        return SimpleNamespace(
            log_id="log-001",
            config=json.dumps(config or {}, default=str),
            status=APICallStatusChoices.PROCESSING.value,
            input_token_count=0,
            save=MagicMock(),
        )

    monkeypatch.setattr(
        evals_module, "log_and_deduct_cost_for_api_request", _fake_log_and_deduct
    )
    monkeypatch.setattr(
        "ee.usage.services.metering.check_usage",
        lambda *_args, **_kwargs: SimpleNamespace(allowed=True),
    )
    monkeypatch.setattr(
        "model_hub.views.utils.evals.EvaluationRunner.map_fields",
        lambda *_args, **_kwargs: {"transcript": TRANSCRIPT},
    )
    monkeypatch.setattr(
        "model_hub.views.utils.evals.EvaluationRunner.format_output",
        lambda *_args, **_kwargs: 0.9,
    )
    monkeypatch.setattr(
        "model_hub.utils.eval_input_validation.validate_eval_inputs",
        lambda template, run_kwargs, **_kw: (None, run_kwargs),
    )
    # Unpatched, these close the connection the test transaction runs on.
    monkeypatch.setattr(executor_module, "close_old_connections", lambda: None)
    monkeypatch.setattr(xl_module, "close_old_connections", lambda: None)
    # Usage events would retry a Temporal connect on every grade; not under test here.
    monkeypatch.setattr(settings, "USAGE_EVENTS_ENABLED", False)


def _grade_executor(eval_config, call_execution, transcript_data):
    return executor_module.TestExecutor()._run_single_simulate_evaluation(
        eval_config, call_execution, transcript_data
    )


def _grade_xl(eval_config, call_execution, transcript_data):
    return xl_module._run_single_evaluation(
        eval_config, call_execution, transcript_data
    )


def _reload(eval_config):
    return SimulateEvalConfig.objects.get(id=eval_config.id)


def _edit_config_in_memory(eval_config):
    def _edit():
        eval_config.config["marker"] = 1

    return _edit


@pytest.mark.django_db
class TestExecutorGradeWrites:
    def test_executor_grade_writes_status_only(
        self, eval_config, call_execution, transcript_data, fake_evaluator
    ):
        before = eval_config.updated_at

        _grade_executor(eval_config, call_execution, transcript_data)

        row = _reload(eval_config)
        assert row.config == ATTACHMENT_CONFIG
        assert row.status == StatusType.COMPLETED.value
        assert row.updated_at > before

    def test_executor_failed_grade_writes_status_only(
        self, eval_config, call_execution, transcript_data, fake_evaluator
    ):
        fake_evaluator.fail = True
        before = eval_config.updated_at

        with pytest.raises(JudgeUnavailable):
            _grade_executor(eval_config, call_execution, transcript_data)

        row = _reload(eval_config)
        assert row.status == StatusType.FAILED.value
        assert row.config == ATTACHMENT_CONFIG
        assert row.updated_at > before

    def test_executor_never_persists_in_memory_config_edits(
        self, eval_config, call_execution, transcript_data, fake_evaluator
    ):
        fake_evaluator.on_run = _edit_config_in_memory(eval_config)

        _grade_executor(eval_config, call_execution, transcript_data)

        assert eval_config.config["marker"] == 1
        row = _reload(eval_config)
        assert "marker" not in row.config
        assert row.status == StatusType.COMPLETED.value

    def test_executor_never_persists_in_memory_config_edits_on_failure(
        self, eval_config, call_execution, transcript_data, fake_evaluator
    ):
        fake_evaluator.on_run = _edit_config_in_memory(eval_config)
        fake_evaluator.fail = True

        with pytest.raises(JudgeUnavailable):
            _grade_executor(eval_config, call_execution, transcript_data)

        assert eval_config.config["marker"] == 1
        row = _reload(eval_config)
        assert "marker" not in row.config
        assert row.status == StatusType.FAILED.value


@pytest.mark.django_db
class TestXlGradeWrites:
    def test_xl_grade_writes_status_only(
        self, eval_config, call_execution, transcript_data, fake_evaluator
    ):
        before = eval_config.updated_at

        _grade_xl(eval_config, call_execution, transcript_data)

        row = _reload(eval_config)
        assert row.config == ATTACHMENT_CONFIG
        assert row.status == StatusType.COMPLETED.value
        assert row.updated_at > before

    def test_xl_failed_grade_writes_status_only(
        self, eval_config, call_execution, transcript_data, fake_evaluator
    ):
        fake_evaluator.fail = True
        before = eval_config.updated_at

        with pytest.raises(JudgeUnavailable):
            _grade_xl(eval_config, call_execution, transcript_data)

        row = _reload(eval_config)
        assert row.status == StatusType.FAILED.value
        assert row.config == ATTACHMENT_CONFIG
        assert row.updated_at > before

    def test_xl_never_persists_in_memory_config_edits(
        self, eval_config, call_execution, transcript_data, fake_evaluator
    ):
        fake_evaluator.on_run = _edit_config_in_memory(eval_config)

        _grade_xl(eval_config, call_execution, transcript_data)

        assert eval_config.config["marker"] == 1
        row = _reload(eval_config)
        assert "marker" not in row.config
        assert row.status == StatusType.COMPLETED.value

    def test_xl_never_persists_in_memory_config_edits_on_failure(
        self, eval_config, call_execution, transcript_data, fake_evaluator
    ):
        fake_evaluator.on_run = _edit_config_in_memory(eval_config)
        fake_evaluator.fail = True

        with pytest.raises(JudgeUnavailable):
            _grade_xl(eval_config, call_execution, transcript_data)

        assert eval_config.config["marker"] == 1
        row = _reload(eval_config)
        assert "marker" not in row.config
        assert row.status == StatusType.FAILED.value


@pytest.mark.django_db
class TestTemplateTextOnLaterGrades:
    def test_second_grade_uses_updated_template_text(
        self, template, eval_config, call_execution, transcript_data, fake_evaluator
    ):
        _grade_executor(eval_config, call_execution, transcript_data)
        assert fake_evaluator.seen[-1]["rule_prompt"] == "v1"

        template.config["rule_prompt"] = "v2"
        template.save()
        eval_config = SimulateEvalConfig.objects.get(id=eval_config.id)

        _grade_executor(eval_config, call_execution, transcript_data)
        assert fake_evaluator.seen[-1]["rule_prompt"] == "v2"
