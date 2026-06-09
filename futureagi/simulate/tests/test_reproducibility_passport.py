import copy

import pytest

from model_hub.models.choices import DatasetSourceChoices, SourceChoices, StatusType
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.models.evals_metric import EvalTemplate
from model_hub.models.run_prompt import PromptTemplate, PromptVersion
from simulate.models import AgentDefinition, AgentVersion, RunTest, Scenarios
from simulate.models.eval_config import SimulateEvalConfig
from simulate.models.simulator_agent import SimulatorAgent
from simulate.models.test_execution import TestExecution as TestExecutionModel
from simulate.services.reproducibility_passport import (
    REDACTED,
    build_replay_plan,
    build_reproducibility_report,
    build_test_execution_passport,
    capture_reproducibility_snapshot,
    diff_passports,
    explain_passport_drift,
    explain_replay_input_drift,
    get_reproducibility_snapshots,
    stable_hash,
)


@pytest.fixture
def agent_definition(db, organization, workspace):
    return AgentDefinition.objects.create(
        agent_name="Checkout support agent",
        agent_type=AgentDefinition.AgentTypeChoices.VOICE,
        contact_number="+1234567890",
        inbound=True,
        description="Handles checkout and billing support calls",
        organization=organization,
        workspace=workspace,
        languages=["en"],
    )


@pytest.fixture
def agent_version(agent_definition):
    return agent_definition.create_version(
        description="Regression baseline",
        commit_message="Capture stable agent config",
        status=AgentVersion.StatusChoices.ACTIVE,
    )


@pytest.fixture
def simulator_agent(db, organization, workspace):
    return SimulatorAgent.objects.create(
        name="Impatient enterprise buyer",
        prompt="Push for a clear refund path and ask for escalation.",
        voice_provider="elevenlabs",
        voice_name="marissa",
        model="gpt-4",
        organization=organization,
        workspace=workspace,
    )


@pytest.fixture
def dataset_for_scenario(db, organization, user, workspace):
    dataset = Dataset.no_workspace_objects.create(
        name="Billing regression scenarios",
        organization=organization,
        workspace=workspace,
        user=user,
        source=DatasetSourceChoices.SCENARIO.value,
    )
    column = Column.objects.create(
        dataset=dataset,
        name="situation",
        data_type="text",
        source=SourceChoices.OTHERS.value,
    )
    dataset.column_order = [str(column.id)]
    dataset.save()

    row = Row.objects.create(dataset=dataset, order=0)
    Cell.objects.create(
        dataset=dataset,
        column=column,
        row=row,
        value="Customer was billed twice.",
    )
    return dataset


@pytest.fixture
def scenario(db, organization, workspace, dataset_for_scenario, agent_definition):
    return Scenarios.objects.create(
        name="Duplicate billing call",
        description="Customer requests urgent refund",
        source="Customer: I was charged twice and need a refund today.",
        scenario_type=Scenarios.ScenarioTypes.DATASET,
        organization=organization,
        workspace=workspace,
        dataset=dataset_for_scenario,
        agent_definition=agent_definition,
        status=StatusType.COMPLETED.value,
    )


@pytest.fixture
def run_test(db, organization, workspace, agent_definition, scenario, simulator_agent):
    run_test = RunTest.objects.create(
        name="Billing voice regression",
        description="Checks refund escalation behavior",
        agent_definition=agent_definition,
        simulator_agent=simulator_agent,
        organization=organization,
        workspace=workspace,
    )
    run_test.scenarios.add(scenario)
    return run_test


@pytest.fixture
def test_execution(db, run_test, simulator_agent, agent_definition):
    return TestExecutionModel.objects.create(
        run_test=run_test,
        status=TestExecutionModel.ExecutionStatus.PENDING,
        total_scenarios=1,
        total_calls=3,
        simulator_agent=simulator_agent,
        agent_definition=agent_definition,
    )


@pytest.fixture
def prompt_run_test(db, organization, workspace, prompt_scenario, prompt_version):
    run_test = RunTest.objects.create(
        name="Prompt regression run",
        description="Replay customer onboarding failures",
        source_type=RunTest.SourceTypes.PROMPT,
        prompt_template=prompt_version.original_template,
        prompt_version=prompt_version,
        organization=organization,
        workspace=workspace,
        dataset_row_ids=["row-1", "row-2"],
        enable_tool_evaluation=True,
    )
    run_test.scenarios.add(prompt_scenario)
    return run_test


@pytest.fixture
def prompt_template(db, organization, workspace):
    return PromptTemplate.no_workspace_objects.create(
        name="Support copilot",
        organization=organization,
        workspace=workspace,
    )


@pytest.fixture
def prompt_version(db, prompt_template):
    return PromptVersion.no_workspace_objects.create(
        original_template=prompt_template,
        template_version="v3",
        commit_message="Tighten escalation behavior",
        prompt_config_snapshot={
            "messages": [
                {
                    "role": "system",
                    "content": "Escalate billing disputes after two failed attempts.",
                }
            ],
            "provider": "openai",
            "api_key": "sk-test",
        },
        variable_names={"customer_tier": "enterprise"},
        placeholders={"region": "us"},
    )


@pytest.fixture
def prompt_scenario(db, organization, workspace, dataset_for_scenario, prompt_version):
    return Scenarios.objects.create(
        name="Billing escalation",
        description="Customer asks for refund twice",
        source="Customer: I was charged twice and need this fixed today.",
        scenario_type=Scenarios.ScenarioTypes.DATASET,
        source_type=Scenarios.SourceTypes.PROMPT,
        organization=organization,
        workspace=workspace,
        dataset=dataset_for_scenario,
        prompt_template=prompt_version.original_template,
        prompt_version=prompt_version,
        metadata={"segment": "enterprise", "auth_token": "should-not-leak"},
    )


@pytest.mark.unit
class TestReproducibilityPassport:
    def test_builds_stable_passport_for_agent_simulation(
        self,
        test_execution,
        run_test,
        scenario,
        agent_definition,
        agent_version,
        organization,
    ):
        agent_definition.provider = "vapi"
        agent_definition.model = "gpt-4o-mini"
        agent_definition.model_details = {"temperature": 0.2, "api_key": "secret"}
        agent_definition.save()
        test_execution.agent_version = agent_version
        test_execution.scenario_ids = [str(scenario.id)]
        test_execution.execution_metadata = {
            "workflow_id": "wf-123",
            "column_order": ["overall_score", "latency_ms"],
            "provider_token": "secret",
        }
        test_execution.save()

        eval_template = EvalTemplate.objects.create(
            name="Task completion",
            criteria="Check if the agent resolved the billing dispute.",
            config={"rubric": "binary", "api_secret": "hidden"},
            organization=organization,
        )
        eval_config = SimulateEvalConfig.objects.create(
            name="Resolution eval",
            eval_template=eval_template,
            run_test=run_test,
            config={"threshold": 0.8},
            mapping={"transcript": "call_transcript"},
            filters={"channel": "voice"},
            error_localizer=True,
            model="turing_flash",
        )

        passport = build_test_execution_passport(test_execution)
        rebuilt = build_test_execution_passport(test_execution)

        assert passport == rebuilt
        assert passport["passport_hash"] == rebuilt["passport_hash"]
        assert set(passport["section_hashes"]) == {
            "execution",
            "run_test",
            "agent",
            "prompt",
            "scenarios",
            "eval_configs",
            "execution_options",
        }
        assert set(passport["input_section_hashes"]) == {
            "run_test",
            "agent",
            "prompt",
            "scenarios",
            "eval_configs",
            "execution_options",
        }
        assert set(passport["runtime_section_hashes"]) == {"execution"}
        assert passport["agent"]["agent_definition"]["model"] == "gpt-4o-mini"
        assert (
            passport["agent"]["agent_definition"]["model_details"]["api_key"]
            == REDACTED
        )
        assert (
            passport["execution_options"]["execution_metadata"]["provider_token"]
            == REDACTED
        )
        assert passport["scenarios"][0]["source_hash"] == stable_hash(scenario.source)
        assert passport["eval_configs"][0]["id"] == str(eval_config.id)
        assert (
            passport["eval_configs"][0]["eval_template"]["config"]["api_secret"]
            == REDACTED
        )

        replay_plan = build_replay_plan(test_execution)

        assert replay_plan["can_replay"] is True
        assert replay_plan["replay_inputs"]["agent_version_id"] == str(agent_version.id)
        assert replay_plan["replay_inputs"]["scenario_ids"] == [str(scenario.id)]
        assert replay_plan["replay_inputs"]["eval_config_ids"] == [str(eval_config.id)]
        assert replay_plan["baseline"]["passport_hash"] == passport["passport_hash"]
        assert (
            replay_plan["baseline"]["input_fingerprint"]
            == passport["input_fingerprint"]
        )

        test_execution.status = TestExecutionModel.ExecutionStatus.COMPLETED
        test_execution.completed_calls = 3
        test_execution.save()
        completed_passport = build_test_execution_passport(test_execution)
        completed_plan = build_replay_plan(test_execution)

        assert completed_passport["passport_hash"] != passport["passport_hash"]
        assert (
            completed_passport["runtime_fingerprint"]
            != passport["runtime_fingerprint"]
        )
        assert (
            completed_passport["input_fingerprint"] == passport["input_fingerprint"]
        )
        assert completed_plan["replay_key"] == replay_plan["replay_key"]
        assert explain_replay_input_drift(passport, completed_passport) == {
            "has_drift": False,
            "highest_severity": None,
            "changed_sections": [],
            "changes": [],
            "before_input_fingerprint": passport["input_fingerprint"],
            "after_input_fingerprint": completed_passport["input_fingerprint"],
        }

    def test_prompt_passport_captures_prompt_snapshot_hash(
        self,
        prompt_run_test,
        prompt_scenario,
        prompt_version,
        simulator_agent,
    ):
        execution = TestExecutionModel.objects.create(
            run_test=prompt_run_test,
            status=TestExecutionModel.ExecutionStatus.COMPLETED,
            total_scenarios=1,
            total_calls=1,
            completed_calls=1,
            scenario_ids=[str(prompt_scenario.id)],
            simulator_agent=simulator_agent,
        )

        passport = build_test_execution_passport(execution)

        assert passport["run_test"]["source_type"] == RunTest.SourceTypes.PROMPT
        assert passport["prompt"]["prompt_version_id"] == str(prompt_version.id)
        assert passport["prompt"]["config_snapshot_hash"] == stable_hash(
            prompt_version.prompt_config_snapshot
        )
        assert passport["prompt"]["config_snapshot"]["api_key"] == REDACTED
        assert passport["agent"]["simulator_agent"]["prompt_hash"] == stable_hash(
            simulator_agent.prompt
        )
        assert passport["scenarios"][0]["metadata"]["auth_token"] == REDACTED

    def test_diff_passports_reports_changed_sections(self, test_execution):
        before = build_test_execution_passport(test_execution)
        after = copy.deepcopy(before)
        after["section_hashes"]["eval_configs"] = stable_hash(
            {"new_eval": "hallucination_regression"}
        )
        after["passport_hash"] = stable_hash(after["section_hashes"])

        diff = diff_passports(before, after)

        assert diff.has_drift is True
        assert diff.changed_sections == ["eval_configs"]
        assert diff.as_dict()["has_drift"] is True

        drift = explain_passport_drift(before, after)

        assert drift["highest_severity"] == "blocker"
        assert drift["changes"] == [
            {
                "section": "eval_configs",
                "severity": "blocker",
                "reason": "The eval templates, configs, mappings, or filters changed.",
                "before_hash": before["section_hashes"]["eval_configs"],
                "after_hash": after["section_hashes"]["eval_configs"],
            }
        ]

    def test_replay_plan_flags_unpinned_agent_versions(self, test_execution, scenario):
        test_execution.scenario_ids = [str(scenario.id)]
        test_execution.save()

        replay_plan = build_replay_plan(test_execution)

        assert replay_plan["can_replay"] is True
        assert replay_plan["issues"] == [
            {
                "severity": "warning",
                "code": "missing_agent_version_snapshot",
                "section": "agent",
                "message": "Agent-definition simulation has no pinned agent version.",
                "remediation": "Create or attach an agent version snapshot for reruns.",
            },
            {
                "severity": "warning",
                "code": "missing_eval_configs",
                "section": "eval_configs",
                "message": "No eval configs are attached to the run.",
                "remediation": "Attach eval configs before comparing eval outcomes.",
            },
        ]

    def test_replay_plan_blocks_prompt_runs_without_prompt_version(
        self,
        db,
        organization,
        workspace,
        prompt_scenario,
    ):
        run_test = RunTest.objects.create(
            name="Unpinned prompt run",
            source_type=RunTest.SourceTypes.PROMPT,
            organization=organization,
            workspace=workspace,
        )
        run_test.scenarios.add(prompt_scenario)
        execution = TestExecutionModel.objects.create(
            run_test=run_test,
            status=TestExecutionModel.ExecutionStatus.PENDING,
            total_scenarios=1,
            total_calls=1,
            scenario_ids=[str(prompt_scenario.id)],
        )

        replay_plan = build_replay_plan(execution)

        assert replay_plan["can_replay"] is False
        assert replay_plan["issues"][0] == {
            "severity": "blocker",
            "code": "missing_prompt_version",
            "section": "prompt",
            "message": "Prompt simulation does not have a pinned prompt version.",
            "remediation": "Attach a prompt version before treating reruns as exact.",
        }

    def test_snapshots_do_not_poison_input_fingerprint(
        self,
        test_execution,
        scenario,
        agent_version,
    ):
        test_execution.agent_version = agent_version
        test_execution.scenario_ids = [str(scenario.id)]
        test_execution.execution_metadata = {
            "column_order": ["overall_score"],
            "active_rerun_workflow_id": "wf-before",
        }
        test_execution.save()

        before = build_test_execution_passport(test_execution)
        start_snapshot = capture_reproducibility_snapshot(test_execution, "start")
        test_execution.refresh_from_db()
        after_start = build_test_execution_passport(test_execution)
        completion_snapshot = capture_reproducibility_snapshot(
            test_execution,
            "completion",
        )
        test_execution.refresh_from_db()
        after_completion = build_test_execution_passport(test_execution)

        assert start_snapshot["input_fingerprint"] == before["input_fingerprint"]
        assert (
            completion_snapshot["input_fingerprint"]
            == before["input_fingerprint"]
        )
        assert after_start["input_fingerprint"] == before["input_fingerprint"]
        assert after_completion["input_fingerprint"] == before["input_fingerprint"]
        assert set(get_reproducibility_snapshots(test_execution)) == {
            "start",
            "completion",
        }

    def test_report_includes_preflight_drift_and_score_change_diagnosis(
        self,
        test_execution,
        scenario,
        agent_version,
    ):
        test_execution.agent_version = agent_version
        test_execution.scenario_ids = [str(scenario.id)]
        test_execution.save()
        capture_reproducibility_snapshot(test_execution, "start")

        test_execution.execution_metadata = {
            **test_execution.execution_metadata,
            "column_order": ["overall_score", "latency_ms"],
        }
        test_execution.save()

        report = build_reproducibility_report(test_execution)

        assert report["has_start_snapshot"] is True
        assert report["has_completion_snapshot"] is False
        assert report["preflight"]["status"] == "warning"
        assert report["drift"]["input_from_start"]["changed_sections"] == [
            "execution_options"
        ]
        assert (
            report["score_change_diagnosis"]["classification"]
            == "replay_inputs_changed"
        )
