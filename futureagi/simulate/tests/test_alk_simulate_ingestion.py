"""Integration tests for the ALK sim ingestion surface.

Covers the full external-runner flow end to end against the real DB:
  start test execution -> batch call executions -> ingest result
plus recording upload and the backend-owned derivations (conversation
metrics, duration backfill, token usage, CSAT preservation).

External side effects are patched at the service boundary: Temporal
dispatch (evals / CSAT / monitor), the websocket notification, and the
object-storage upload. Everything else — metric computation, DB writes,
the API envelope — runs for real.
"""

import importlib
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from model_hub.models.choices import StatusType
from model_hub.models.evals_metric import EvalTemplate
from simulate.models import (
    AgentDefinition,
    RunTest,
    Scenarios,
    SimulatorAgent,
    SimulateEvalConfig,
)
from simulate.models.test_execution import (
    CallExecution,
    CallTranscript,
)
from simulate.models.test_execution import TestExecution as SimTestExecution

ALK_BASE = "/simulate/api/alk-simulate"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def agent_definition(db, organization, workspace):
    return AgentDefinition.objects.create(
        agent_name="ALK Ingestion Agent",
        agent_type=AgentDefinition.AgentTypeChoices.VOICE,
        contact_number="+12813716796",
        inbound=True,
        description="Agent under test for ALK ingestion",
        organization=organization,
        workspace=workspace,
        languages=["en"],
    )


@pytest.fixture
def simulator_agent(db, organization, workspace):
    return SimulatorAgent.objects.create(
        name="ALK Simulator",
        prompt="You are a customer.",
        voice_provider="livekit",
        voice_name="alk-simulator",
        model="gpt-4o",
        initial_message="Hi!",
        organization=organization,
        workspace=workspace,
    )


@pytest.fixture
def scenario(db, organization, workspace, agent_definition, simulator_agent):
    return Scenarios.objects.create(
        name="ALK Ingestion Scenario",
        description="Scenario for ALK ingestion tests",
        source="test",
        scenario_type=Scenarios.ScenarioTypes.DATASET,
        organization=organization,
        workspace=workspace,
        agent_definition=agent_definition,
        simulator_agent=simulator_agent,
        status=StatusType.COMPLETED.value,
    )


@pytest.fixture
def run_test(db, organization, workspace, agent_definition, scenario, simulator_agent):
    rt = RunTest.objects.create(
        name="ALK Ingestion Run Test",
        description="Run for ALK ingestion tests",
        agent_definition=agent_definition,
        simulator_agent=simulator_agent,
        organization=organization,
        workspace=workspace,
    )
    rt.scenarios.add(scenario)
    return rt


@pytest.fixture(autouse=True)
def _patch_side_effects():
    """No-op the async dispatch + websocket so the service runs inline."""
    # The string targets below are attribute paths; make sure the module is
    # imported even when a -k selection runs only tests that never import it.
    importlib.import_module("simulate.services.alk_simulate_ingestion")
    targets = (
        "simulate.services.alk_simulate_ingestion.notify_simulation_update",
        "simulate.services.test_executor._run_simulate_evaluations_task.apply_async",
    )
    with patch(targets[0]), patch(targets[1]):
        with (
            patch(
                "simulate.tasks.chat_sim.monitor_test_execution_for_chat.apply_async"
            ),
            patch("simulate.tasks.alk_sim.calculate_alk_voice_csat_score.apply_async"),
        ):
            yield


def _transcript_payload():
    """A short two-turn transcript with real speech offsets (ms)."""
    return [
        {
            "speaker_role": "user",
            "content": "Hi, my package is late. Can you check the status?",
            "start_time_ms": 0,
            "end_time_ms": 5000,
        },
        {
            "speaker_role": "assistant",
            "content": "Of course, let me look that up for you right away.",
            "start_time_ms": 6000,
            "end_time_ms": 11000,
        },
        {
            "speaker_role": "user",
            "content": "Thank you, I appreciate it.",
            "start_time_ms": 12000,
            "end_time_ms": 15000,
        },
    ]


def _start_and_batch(auth_client, run_test):
    """Helper: start a test execution and allocate its call executions."""
    start = auth_client.post(
        f"{ALK_BASE}/run-tests/{run_test.id}/test-executions/",
        {},
        format="json",
    )
    assert start.status_code == 200, start.content
    test_execution_id = start.json()["result"]["test_execution_id"]

    batch = auth_client.post(
        f"{ALK_BASE}/test-executions/{test_execution_id}/batch/",
        {},
        format="json",
    )
    assert batch.status_code == 200, batch.content
    call_ids = batch.json()["result"]["call_execution_ids"]
    return test_execution_id, call_ids


# ---------------------------------------------------------------------------
# provision (SDK-first RunTest + scenario-of-record)
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
class TestProvisionRunTest:
    """Stand up a chat RunTest + scenario-of-record from SDK personas (no async
    generation), then confirm it feeds the normal ingestion flow."""

    def _provision(self, auth_client, **body):
        return auth_client.post(f"{ALK_BASE}/run-tests/provision/", body, format="json")

    def test_provision_creates_text_agent_scenario_and_run_test(self, auth_client):
        resp = self._provision(
            auth_client,
            name="sdk-e2e",
            personas=[
                {
                    "name": "Sam",
                    "scenario_name": "Resolve a late refund",
                    "situation": "refund please",
                    "outcome": "refunded",
                }
            ],
        )
        assert resp.status_code == 200, resp.content
        result = resp.json()["result"]
        assert len(result["scenario_ids"]) == 1

        run_test = RunTest.objects.get(id=result["run_test_id"])
        assert (
            run_test.agent_definition.agent_type
            == AgentDefinition.AgentTypeChoices.TEXT
        )
        assert run_test.scenarios.count() == 1
        scenario = Scenarios.objects.get(id=result["scenario_ids"][0])
        assert scenario.name == "Resolve a late refund"
        assert not scenario.name.startswith(run_test.name)
        assert scenario.status == StatusType.COMPLETED.value
        assert scenario.metadata["persona"]["name"] == "Sam"

        # A real 1-row persona dataset backs the scenario so it renders with a
        # row and the {{persona}}/{{situation}} placeholders resolve.
        from model_hub.models.develop_dataset import Cell, Row

        assert scenario.dataset_id is not None
        rows = Row.objects.filter(dataset=scenario.dataset)
        assert rows.count() == 1
        cell_values = {
            c.column.name: c.value
            for c in Cell.objects.filter(row=rows.first()).select_related("column")
        }
        assert cell_values["situation"] == "refund please"
        assert cell_values["outcome"] == "refunded"
        assert json.loads(cell_values["persona"])["name"] == "Sam"

    def test_provision_voice_preserves_voice_call_type(self, auth_client):
        resp = self._provision(
            auth_client,
            name="sdk-voice-e2e",
            modality="voice",
            personas=[{"name": "Avery", "situation": "book a ride"}],
        )
        assert resp.status_code == 200, resp.content
        run_test = RunTest.objects.get(id=resp.json()["result"]["run_test_id"])
        assert (
            run_test.agent_definition.agent_type
            == AgentDefinition.AgentTypeChoices.VOICE
        )

        _test_execution_id, call_ids = _start_and_batch(auth_client, run_test)
        call = CallExecution.objects.get(id=call_ids[0])
        assert call.simulation_call_type == CallExecution.SimulationCallType.VOICE

    def test_provision_accepts_alk_chat_alias_as_text(self, auth_client):
        resp = self._provision(
            auth_client,
            name="sdk-chat-e2e",
            modality="chat",
            personas=[{"name": "Mina", "situation": "check account status"}],
        )
        assert resp.status_code == 200, resp.content
        run_test = RunTest.objects.get(id=resp.json()["result"]["run_test_id"])
        assert (
            run_test.agent_definition.agent_type
            == AgentDefinition.AgentTypeChoices.TEXT
        )

        _test_execution_id, call_ids = _start_and_batch(auth_client, run_test)
        call = CallExecution.objects.get(id=call_ids[0])
        assert call.simulation_call_type == CallExecution.SimulationCallType.TEXT

    def test_provisioned_run_test_batches_one_call_per_persona(self, auth_client):
        resp = self._provision(
            auth_client,
            name="sdk-e2e-batch",
            personas=[{"name": "Morgan", "situation": "late delivery"}],
        )
        run_test = RunTest.objects.get(id=resp.json()["result"]["run_test_id"])
        _te_id, call_ids = _start_and_batch(auth_client, run_test)
        assert len(call_ids) == 1

    def test_provision_reuses_existing_scenario(self, auth_client, scenario):
        before = Scenarios.objects.count()
        resp = self._provision(
            auth_client, name="sdk-reuse", scenario_ids=[str(scenario.id)]
        )
        assert resp.status_code == 200, resp.content
        result = resp.json()["result"]
        assert result["scenario_ids"] == [str(scenario.id)]
        # No scenario fabricated — the existing one is attached as-is.
        assert Scenarios.objects.count() == before

        run_test = RunTest.objects.get(id=result["run_test_id"])
        assert list(run_test.scenarios.values_list("id", flat=True)) == [scenario.id]
        # Run-test-level simulator agent set from the scenario so batch never
        # writes simulator_agent back onto the shared scenario.
        assert run_test.simulator_agent_id == scenario.simulator_agent_id
        # Chat run test carries a fresh TEXT agent, not the scenario's VOICE one.
        assert (
            run_test.agent_definition.agent_type
            == AgentDefinition.AgentTypeChoices.TEXT
        )

    def test_provision_rejects_both_personas_and_scenario_ids(
        self, auth_client, scenario
    ):
        resp = self._provision(
            auth_client,
            name="both",
            personas=[{"name": "x"}],
            scenario_ids=[str(scenario.id)],
        )
        assert resp.status_code == 400, resp.content

    def test_provision_rejects_neither_personas_nor_scenario_ids(self, auth_client):
        resp = self._provision(auth_client, name="neither")
        assert resp.status_code == 400, resp.content

    def test_provision_reuse_rejects_missing_scenario(self, auth_client):
        import uuid as _uuid

        resp = self._provision(
            auth_client, name="missing", scenario_ids=[str(_uuid.uuid4())]
        )
        assert resp.status_code == 400, resp.content

    def test_provision_rejects_voice_agent_definition(
        self, auth_client, agent_definition
    ):
        # `agent_definition` fixture is VOICE — provisioning must refuse it so it
        # cannot bypass the voice entitlement gate.
        resp = self._provision(
            auth_client,
            name="voice-nope",
            personas=[{"name": "x"}],
            agent_definition_id=str(agent_definition.id),
        )
        assert resp.status_code == 400, resp.content


# ---------------------------------------------------------------------------
# start_test_execution
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
class TestStartTestExecution:
    def test_creates_execution_inheriting_run_test(
        self, auth_client, run_test, scenario
    ):
        resp = auth_client.post(
            f"{ALK_BASE}/run-tests/{run_test.id}/test-executions/",
            {},
            format="json",
        )
        assert resp.status_code == 200, resp.content
        result = resp.json()["result"]
        assert result["run_test_id"] == str(run_test.id)
        assert result["total_scenarios"] == 1
        assert str(scenario.id) in result["scenario_ids"]

        te = SimTestExecution.objects.get(id=result["test_execution_id"])
        # Inherits agent_definition + scenarios from the run test, no orchestration.
        assert te.agent_definition_id == run_test.agent_definition_id
        assert te.scenario_ids == [str(scenario.id)]
        assert te.status == SimTestExecution.ExecutionStatus.PENDING

    def test_unknown_run_test_returns_404(self, auth_client):
        unknown = "00000000-0000-4000-8000-0000deadbeef"
        resp = auth_client.post(
            f"{ALK_BASE}/run-tests/{unknown}/test-executions/", {}, format="json"
        )
        assert resp.status_code == 404
        assert resp.json()["status"] is False

    def test_scenario_selectors_preserve_runner_order_for_saved_run(
        self, auth_client, run_test, scenario
    ):
        scenario.metadata = {
            "origin": "alk_sdk_ingestion",
            "persona": {"scenario_key": "case-a", "persona": {"name": "A"}},
        }
        scenario.save(update_fields=["metadata"])
        second = Scenarios.objects.create(
            name="Second saved case",
            source="second",
            scenario_type=Scenarios.ScenarioTypes.DATASET,
            organization=run_test.organization,
            workspace=run_test.workspace,
            agent_definition=run_test.agent_definition,
            status=StatusType.COMPLETED.value,
            metadata={
                "origin": "alk_sdk_ingestion",
                "persona": {
                    "scenario_key": "case-b",
                    "persona": {"name": "B"},
                },
            },
        )
        run_test.scenarios.add(second)

        resp = auth_client.post(
            f"{ALK_BASE}/run-tests/{run_test.id}/test-executions/",
            {
                "scenario_selectors": [
                    {"scenario_key": "case-b", "persona_name": "B"},
                    {"scenario_key": "case-a", "persona_name": "A"},
                ]
            },
            format="json",
        )

        assert resp.status_code == 200, resp.content
        assert resp.json()["result"]["scenario_ids"] == [
            str(second.id),
            str(scenario.id),
        ]

    def test_scenario_not_on_run_test_returns_400(self, auth_client, run_test):
        other = "11111111-1111-4111-8111-111111111111"
        resp = auth_client.post(
            f"{ALK_BASE}/run-tests/{run_test.id}/test-executions/",
            {"scenario_ids": [other]},
            format="json",
        )
        assert resp.status_code == 400
        assert resp.json()["status"] is False


# ---------------------------------------------------------------------------
# batch
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
class TestBatchCreate:
    def test_batch_creates_pending_voice_call(self, auth_client, run_test, scenario):
        te_id, call_ids = _start_and_batch(auth_client, run_test)
        assert len(call_ids) == 1

        call = CallExecution.objects.get(id=call_ids[0])
        assert call.status == CallExecution.CallStatus.PENDING
        assert call.simulation_call_type == CallExecution.SimulationCallType.VOICE
        assert call.scenario_id == scenario.id
        assert call.call_metadata["external_runner"] == "alk"

    def test_batch_count_is_exact_and_has_more_is_accurate(
        self, auth_client, run_test, scenario
    ):
        second_scenario = Scenarios.objects.create(
            name="Second ALK Ingestion Scenario",
            description="Second scenario for ALK batch tests",
            source="test",
            scenario_type=Scenarios.ScenarioTypes.DATASET,
            organization=run_test.organization,
            workspace=run_test.workspace,
            agent_definition=run_test.agent_definition,
            simulator_agent=run_test.simulator_agent,
            status=StatusType.COMPLETED.value,
        )
        run_test.scenarios.add(second_scenario)

        start = auth_client.post(
            f"{ALK_BASE}/run-tests/{run_test.id}/test-executions/",
            {},
            format="json",
        )
        test_execution_id = start.json()["result"]["test_execution_id"]

        first = auth_client.post(
            f"{ALK_BASE}/test-executions/{test_execution_id}/batch/",
            {"count": 1},
            format="json",
        )
        assert first.status_code == 200, first.content
        assert len(first.json()["result"]["call_execution_ids"]) == 1
        assert first.json()["result"]["has_more"] is True

        second = auth_client.post(
            f"{ALK_BASE}/test-executions/{test_execution_id}/batch/",
            {"count": 1},
            format="json",
        )
        assert second.status_code == 200, second.content
        assert len(second.json()["result"]["call_execution_ids"]) == 1
        assert second.json()["result"]["has_more"] is False

    def test_batch_rejects_non_positive_count(self, auth_client, run_test):
        start = auth_client.post(
            f"{ALK_BASE}/run-tests/{run_test.id}/test-executions/",
            {},
            format="json",
        )
        test_execution_id = start.json()["result"]["test_execution_id"]

        response = auth_client.post(
            f"{ALK_BASE}/test-executions/{test_execution_id}/batch/",
            {"count": 0},
            format="json",
        )
        assert response.status_code == 400

    def test_second_batch_has_nothing_to_create(self, auth_client, run_test):
        te_id, _ = _start_and_batch(auth_client, run_test)
        second = auth_client.post(
            f"{ALK_BASE}/test-executions/{te_id}/batch/", {}, format="json"
        )
        assert second.status_code == 400
        assert second.json()["status"] is False

    def test_hosted_execute_precreates_rows_and_batch_adopts_them(
        self, auth_client, run_test, scenario
    ):
        from simulate.views.run_test import RunTestExecutionView

        view = RunTestExecutionView()

        def assert_rows_visible_before_dispatch(**_kwargs):
            execution = SimTestExecution.objects.get(run_test=run_test)
            assert execution.calls.count() == 1
            assert execution.calls.get().status == CallExecution.CallStatus.PENDING
            return f"sim-runner-{execution.id}"

        with (
            patch.object(view, "_hosted_runner_mode", return_value="voice_webrtc"),
            patch(
                "simulate.temporal.client.start_simulation_runner_workflow",
                side_effect=assert_rows_visible_before_dispatch,
            ),
        ):
            result = view._execute_with_hosted_runner(
                run_test=run_test,
                scenario_ids=[str(scenario.id)],
                simulator_id=None,
            )

        execution = SimTestExecution.objects.get(id=result["execution_id"])
        precreated_id = str(execution.calls.get().id)
        assert result["total_calls"] == 1
        assert execution.total_calls == 1

        batch = auth_client.post(
            f"{ALK_BASE}/test-executions/{execution.id}/batch/", {}, format="json"
        )

        assert batch.status_code == 200, batch.content
        assert batch.json()["result"]["call_execution_ids"] == [precreated_id]
        assert execution.calls.count() == 1
        adopted_call = execution.calls.get()
        assert adopted_call.call_metadata["alk_batch_claimed"] is True

    def test_batch_readopts_reset_row_after_rerun(self, auth_client, run_test):
        """A hosted rerun clears call_metadata to {}; the batch must re-adopt the
        PENDING row (absent claimed-flag == unclaimed), not 400."""
        te_id, call_ids = _start_and_batch(auth_client, run_test)
        call = CallExecution.objects.get(id=call_ids[0])
        call.status = CallExecution.CallStatus.PENDING
        call.call_metadata = {}
        call.save(update_fields=["status", "call_metadata"])

        second = auth_client.post(
            f"{ALK_BASE}/test-executions/{te_id}/batch/", {}, format="json"
        )
        assert second.status_code == 200, second.content
        assert second.json()["result"]["call_execution_ids"] == [str(call.id)]


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
class TestInternalServiceIngestion:
    def test_internal_service_batches_and_ingests_precreated_execution(
        self, auth_client, api_client, run_test
    ):
        from django.test import override_settings

        start = auth_client.post(
            f"{ALK_BASE}/run-tests/{run_test.id}/test-executions/",
            {},
            format="json",
        )
        test_execution_id = start.json()["result"]["test_execution_id"]
        api_client.credentials(
            HTTP_AUTHORIZATION="Bearer hosted-runner-internal-secret"
        )

        with override_settings(INTERNAL_API_SECRET="hosted-runner-internal-secret"):
            batch = api_client.post(
                f"{ALK_BASE}/test-executions/{test_execution_id}/batch/",
                {},
                format="json",
            )
            assert batch.status_code == 200, batch.content
            call_id = batch.json()["result"]["call_execution_ids"][0]

            result = api_client.patch(
                f"{ALK_BASE}/call-executions/{call_id}/result/",
                {"status": "completed", "transcript": _transcript_payload()},
                format="json",
            )

        assert result.status_code == 200, result.content
        call = CallExecution.objects.get(id=call_id)
        assert call.status == CallExecution.CallStatus.COMPLETED
        assert call.test_execution.run_test.organization_id == run_test.organization_id

    def test_internal_service_cannot_create_execution(self, api_client, run_test):
        from django.test import override_settings

        api_client.credentials(
            HTTP_AUTHORIZATION="Bearer hosted-runner-internal-secret"
        )
        with override_settings(INTERNAL_API_SECRET="hosted-runner-internal-secret"):
            response = api_client.post(
                f"{ALK_BASE}/run-tests/{run_test.id}/test-executions/",
                {},
                format="json",
            )

        assert response.status_code == 404

    def test_wrong_internal_secret_is_rejected(self, api_client, run_test):
        from simulate.services.alk_simulate_ingestion import (
            create_alk_sim_test_execution,
        )

        test_execution = create_alk_sim_test_execution(run_test)
        api_client.credentials(HTTP_AUTHORIZATION="Bearer wrong-secret")
        response = api_client.post(
            f"{ALK_BASE}/test-executions/{test_execution.id}/batch/",
            {},
            format="json",
        )

        assert response.status_code == 403


@pytest.mark.integration
@pytest.mark.django_db
class TestMixedResultRollup:
    def test_failed_calls_do_not_block_completed_call_evaluations(
        self, auth_client, run_test
    ):
        from simulate.services.test_executor import TestExecutor

        test_execution_id, call_ids = _start_and_batch(auth_client, run_test)
        test_execution = SimTestExecution.objects.get(id=test_execution_id)
        completed_call = CallExecution.objects.get(id=call_ids[0])
        completed_call.status = CallExecution.CallStatus.COMPLETED
        completed_call.call_metadata = {"eval_started": True, "eval_completed": True}
        completed_call.save(update_fields=["status", "call_metadata"])
        CallExecution.objects.create(
            test_execution=test_execution,
            scenario=completed_call.scenario,
            status=CallExecution.CallStatus.FAILED,
            simulation_call_type=completed_call.simulation_call_type,
            call_metadata={},
        )
        test_execution.status = SimTestExecution.ExecutionStatus.EVALUATING
        test_execution.save(update_fields=["status"])

        TestExecutor(
            initialize_voice_service=False
        )._check_and_update_test_execution_completion(test_execution.id)

        test_execution.refresh_from_db()
        assert test_execution.status == SimTestExecution.ExecutionStatus.COMPLETED
        assert test_execution.completed_at is not None
        assert test_execution.total_calls == 2
        assert test_execution.completed_calls == 1
        assert test_execution.failed_calls == 1


# ---------------------------------------------------------------------------
# result ingest — metrics, duration, tokens, csat
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
class TestResultIngest:
    def test_sealed_result_retry_is_idempotent_and_conflicting_digest_is_rejected(
        self, auth_client, run_test
    ):
        _, call_ids = _start_and_batch(auth_client, run_test)
        endpoint = f"{ALK_BASE}/call-executions/{call_ids[0]}/result/"
        body = {
            "status": "completed",
            "transcript": _transcript_payload(),
            "result_digest": "sha256:" + "a" * 64,
            "artifact_manifest_digest": "sha256:" + "b" * 64,
        }

        first = auth_client.patch(endpoint, body, format="json")
        retry = auth_client.patch(endpoint, body, format="json")
        conflict = auth_client.patch(
            endpoint,
            {**body, "result_digest": "sha256:" + "c" * 64},
            format="json",
        )

        assert first.status_code == 200, first.content
        assert retry.status_code == 200, retry.content
        assert conflict.status_code == 400
        assert "conflicts" in str(conflict.json()).lower()
        call = CallExecution.objects.get(id=call_ids[0])
        assert call.call_metadata["alk_result_digest"] == "sha256:" + "a" * 64
        assert (
            call.call_metadata["alk_artifact_manifest_digest"] == "sha256:" + "b" * 64
        )

    def test_harness_checks_complete_external_parent_without_platform_eval_wait(
        self, auth_client, run_test
    ):
        test_execution_id, call_ids = _start_and_batch(auth_client, run_test)
        resp = auth_client.patch(
            f"{ALK_BASE}/call-executions/{call_ids[0]}/result/",
            {
                "status": "completed",
                "transcript": _transcript_payload(),
                "call_metadata": {
                    "harness_evaluations": [{"name": "ride_booked", "passed": False}]
                },
            },
            format="json",
        )
        assert resp.status_code == 200, resp.content

        call = CallExecution.objects.get(id=call_ids[0])
        execution = SimTestExecution.objects.get(id=test_execution_id)
        assert call.call_metadata["eval_started"] is True
        assert call.call_metadata["eval_completed"] is True
        assert len(call.eval_outputs) == 1
        direct_result = next(iter(call.eval_outputs.values()))
        assert direct_result == {
            "name": "ride_booked",
            "output": "Failed",
            "output_type": "Pass/Fail",
            "reason": "",
            "status": "completed",
            "source": "harness",
            "kind": "checkpoint",
            "platform_template": "",
        }
        assert execution.status == SimTestExecution.ExecutionStatus.COMPLETED
        assert execution.completed_at is not None
        assert execution.total_calls == 1
        assert execution.completed_calls == 1
        assert execution.failed_calls == 0

    def test_platform_judgement_is_linked_to_run_eval_config_and_output(
        self, auth_client, run_test
    ):
        template = EvalTemplate.objects.create(
            name="alk-platform-check",
            organization=run_test.organization,
            workspace=run_test.workspace,
            owner="user",
            eval_type="llm",
            output_type_normalized="pass_fail",
        )
        _, call_ids = _start_and_batch(auth_client, run_test)

        resp = auth_client.patch(
            f"{ALK_BASE}/call-executions/{call_ids[0]}/result/",
            {
                "status": "completed",
                "transcript": _transcript_payload(),
                "call_metadata": {
                    "harness_evaluations": [
                        {
                            "name": "response_wording",
                            "kind": "eval",
                            "passed": True,
                            "reason": "matched the required response",
                            "platform_template": template.name,
                        }
                    ]
                },
            },
            format="json",
        )
        assert resp.status_code == 200, resp.content

        config = SimulateEvalConfig.objects.get(
            run_test=run_test,
            eval_template=template,
            name="response_wording",
        )
        call = CallExecution.objects.get(id=call_ids[0])
        assert call.eval_outputs[str(config.id)] == {
            "name": "response_wording",
            "output": "Passed",
            "output_type": "Pass/Fail",
            "reason": "matched the required response",
            "status": "completed",
            "source": "harness",
            "kind": "eval",
            "platform_template": template.name,
        }

    def test_ingest_computes_metrics_and_duration(self, auth_client, run_test):
        _, call_ids = _start_and_batch(auth_client, run_test)
        call_id = call_ids[0]

        resp = auth_client.patch(
            f"{ALK_BASE}/call-executions/{call_id}/result/",
            {"status": "completed", "transcript": _transcript_payload()},
            format="json",
        )
        assert resp.status_code == 200, resp.content

        call = CallExecution.objects.get(id=call_id)
        assert call.status == CallExecution.CallStatus.COMPLETED
        # Transcript persisted.
        assert CallTranscript.objects.filter(call_execution=call).count() == 3
        # Metrics recomputed server-side from the transcript.
        cmd = call.conversation_metrics_data or {}
        assert cmd.get("turn_count") == 1  # one assistant/bot turn
        assert cmd.get("message_count") == 3
        assert call.user_wpm is not None and call.user_wpm > 0
        # Duration backfilled from the last transcript offset (15000 ms).
        assert call.duration_seconds == 15

    def test_ingest_persists_stereo_recording_url_and_serializer_surfaces_it(
        self, auth_client, run_test
    ):
        """A LiveKit result PATCH carrying stereo_recording_url lands on the model
        and surfaces through CallExecutionDetailSerializer as recordings['stereo']."""
        from simulate.serializers.test_execution import (
            CallExecutionDetailSerializer,
        )

        _, call_ids = _start_and_batch(auth_client, run_test)
        call_id = call_ids[0]
        stereo_url = "https://cdn.example.com/stereo.wav"

        resp = auth_client.patch(
            f"{ALK_BASE}/call-executions/{call_id}/result/",
            {
                "status": "completed",
                "transcript": _transcript_payload(),
                "stereo_recording_url": stereo_url,
                "provider_call_data": {"livekit": {"room": "alk-room"}},
            },
            format="json",
        )
        assert resp.status_code == 200, resp.content

        call = CallExecution.objects.get(id=call_id)
        assert call.stereo_recording_url == stereo_url

        recordings = CallExecutionDetailSerializer(
            context={"detail_mode": True}
        ).get_recordings(call)
        assert recordings["stereo"] == stereo_url

    def test_voice_ingest_emits_voice_call_billing_once(self, auth_client, run_test):
        """A completed voice call charges once through TestExecutor._deduct_call_cost
        (the same path native voice uses to emit the VOICE_CALL usage event); a
        re-ingest of the same result must not double-charge."""
        _, call_ids = _start_and_batch(auth_client, run_test)
        call_id = call_ids[0]
        body = {"status": "completed", "transcript": _transcript_payload()}

        with patch(
            "simulate.services.test_executor.TestExecutor._deduct_call_cost"
        ) as deduct:
            resp = auth_client.patch(
                f"{ALK_BASE}/call-executions/{call_id}/result/", body, format="json"
            )
            assert resp.status_code == 200, resp.content
            assert deduct.call_count == 1
            assert str(deduct.call_args[0][0].id) == str(call_id)

            # Re-ingest of the same terminal result must not charge again.
            resp2 = auth_client.patch(
                f"{ALK_BASE}/call-executions/{call_id}/result/", body, format="json"
            )
            assert resp2.status_code == 200, resp2.content
            assert deduct.call_count == 1

        call = CallExecution.objects.get(id=call_id)
        assert (call.call_metadata or {}).get("cost_deducted") is True
        assert call.duration_seconds == 15

    def test_hosted_rerun_reset_clears_batch_claim_for_readoption(
        self, auth_client, run_test
    ):
        """The hosted rerun reset must clear call_metadata['alk_batch_claimed'] so
        /batch re-adopts the row. reset_to_default leaves it — which made hosted
        reruns 400 ('failed, no transcript'); the module-level reset clears it."""
        from simulate.services.alk_simulate_ingestion import (
            create_alk_sim_call_execution_batch,
        )
        from simulate.views.run_test import _clear_call_execution_data

        te_id, call_ids = _start_and_batch(auth_client, run_test)
        call = CallExecution.objects.get(id=call_ids[0])
        # First run finished: the row is terminal AND still claimed by /batch.
        assert (call.call_metadata or {}).get("alk_batch_claimed") is True
        call.status = CallExecution.CallStatus.COMPLETED
        call.save(update_fields=["status"])

        _clear_call_execution_data(call)

        call.refresh_from_db()
        assert call.status == CallExecution.CallStatus.PENDING
        assert "alk_batch_claimed" not in (call.call_metadata or {})

        # /batch now re-adopts the reset row instead of raising nothing-to-create.
        execution = SimTestExecution.objects.get(id=te_id)
        batch = create_alk_sim_call_execution_batch(execution)
        assert str(call.id) in [str(cid) for cid in batch.call_execution_ids]

    def test_ingest_writes_token_usage_from_provider_data(self, auth_client, run_test):
        _, call_ids = _start_and_batch(auth_client, run_test)
        call_id = call_ids[0]

        resp = auth_client.patch(
            f"{ALK_BASE}/call-executions/{call_id}/result/",
            {
                "status": "completed",
                "transcript": _transcript_payload(),
                "provider_call_data": {
                    "vapi": {
                        "usage": {
                            "llm": {
                                "prompt_tokens": 1200,
                                "completion_tokens": 450,
                                "total_tokens": 1650,
                            }
                        }
                    }
                },
                "costs": {"cost_cents": 16},
            },
            format="json",
        )
        assert resp.status_code == 200, resp.content

        call = CallExecution.objects.get(id=call_id)
        cmd = call.conversation_metrics_data or {}
        assert cmd.get("input_tokens") == 1200
        assert cmd.get("output_tokens") == 450
        assert cmd.get("total_tokens") == 1650
        assert call.cost_cents == 16

    def test_retell_total_only_tokens(self, auth_client, run_test):
        _, call_ids = _start_and_batch(auth_client, run_test)
        call_id = call_ids[0]

        resp = auth_client.patch(
            f"{ALK_BASE}/call-executions/{call_id}/result/",
            {
                "status": "completed",
                "transcript": _transcript_payload(),
                "provider_call_data": {
                    "retell": {"usage": {"llm": {"total_tokens": 1500}}}
                },
            },
            format="json",
        )
        assert resp.status_code == 200, resp.content

        cmd = CallExecution.objects.get(id=call_id).conversation_metrics_data or {}
        assert cmd.get("total_tokens") == 1500
        assert cmd.get("input_tokens") is None

    def test_reingest_preserves_csat(self, auth_client, run_test):
        _, call_ids = _start_and_batch(auth_client, run_test)
        call_id = call_ids[0]
        body = {"status": "completed", "transcript": _transcript_payload()}

        first = auth_client.patch(
            f"{ALK_BASE}/call-executions/{call_id}/result/", body, format="json"
        )
        assert first.status_code == 200

        # Simulate the async CSAT task having written a score.
        call = CallExecution.objects.get(id=call_id)
        cmd = dict(call.conversation_metrics_data or {})
        cmd["csat_score"] = 6.0
        call.conversation_metrics_data = cmd
        call.overall_score = 6.0
        call.save(update_fields=["conversation_metrics_data", "overall_score"])

        # A second idempotent ingest must not wipe csat_score.
        second = auth_client.patch(
            f"{ALK_BASE}/call-executions/{call_id}/result/", body, format="json"
        )
        assert second.status_code == 200
        cmd = CallExecution.objects.get(id=call_id).conversation_metrics_data or {}
        assert cmd.get("csat_score") == 6.0

    def test_unknown_call_returns_404(self, auth_client):
        unknown = "00000000-0000-4000-8000-0000deadbeef"
        resp = auth_client.patch(
            f"{ALK_BASE}/call-executions/{unknown}/result/",
            {"status": "completed"},
            format="json",
        )
        assert resp.status_code == 404
        assert resp.json()["status"] is False


# ---------------------------------------------------------------------------
# recording upload
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
class TestRecordingUpload:
    def test_multipart_upload_persists_recording(self, auth_client, run_test):
        _, call_ids = _start_and_batch(auth_client, run_test)
        call_id = call_ids[0]

        from unittest.mock import MagicMock

        from django.core.files.uploadedfile import SimpleUploadedFile

        audio = SimpleUploadedFile(
            "combined.wav", b"RIFFfakewavdata", content_type="audio/wav"
        )

        fake_client = MagicMock()
        with (
            patch(
                "simulate.services.alk_simulate_ingestion.get_storage_client",
                return_value=fake_client,
            ),
            patch(
                "simulate.services.alk_simulate_ingestion.get_object_url",
                return_value="https://storage.example.com/fi-content/alk-sim/recordings/x.wav",
            ),
        ):
            resp = auth_client.post(
                f"{ALK_BASE}/call-executions/{call_id}/recording/",
                {"file": audio, "filename": "combined.wav"},
                format="multipart",
            )

        assert resp.status_code == 200, resp.content
        result = resp.json()["result"]
        assert result["recording_url"].endswith(".wav")
        assert result["object_key"].startswith("alk-sim/recordings/")
        call = CallExecution.objects.get(id=call_id)
        assert call.recording_url == result["recording_url"]
        assert call.recording_available is True
        # Bytes were written to the upload bucket via the storage client.
        fake_client.put_object.assert_called_once()

    def test_recording_checksum_is_verified_and_duplicate_upload_is_idempotent(
        self, auth_client, run_test
    ):
        import hashlib
        from unittest.mock import MagicMock

        from django.core.files.uploadedfile import SimpleUploadedFile

        _, call_ids = _start_and_batch(auth_client, run_test)
        endpoint = f"{ALK_BASE}/call-executions/{call_ids[0]}/recording/"
        content = b"RIFFdurable-audio-evidence"
        digest = hashlib.sha256(content).hexdigest()
        fake_client = MagicMock()
        with (
            patch(
                "simulate.services.alk_simulate_ingestion.get_storage_client",
                return_value=fake_client,
            ),
            patch(
                "simulate.services.alk_simulate_ingestion.get_object_url",
                return_value="https://storage.example.com/recording.wav",
            ),
        ):
            first = auth_client.post(
                endpoint,
                {
                    "file": SimpleUploadedFile("call.wav", content),
                    "filename": "call.wav",
                    "sha256": digest,
                },
                format="multipart",
            )
            retry = auth_client.post(
                endpoint,
                {
                    "file": SimpleUploadedFile("call.wav", content),
                    "filename": "call.wav",
                    "sha256": "sha256:" + digest,
                },
                format="multipart",
            )

        assert first.status_code == retry.status_code == 200
        assert first.json()["result"] == retry.json()["result"]
        fake_client.put_object.assert_called_once()
        call = CallExecution.objects.get(id=call_ids[0])
        assert (
            call.call_metadata["alk_recording_artifacts"]["combined"]["sha256"]
            == digest
        )

        stereo_content = b"RIFFdifferent-stereo-evidence"
        stereo_digest = hashlib.sha256(stereo_content).hexdigest()
        with (
            patch(
                "simulate.services.alk_simulate_ingestion.get_storage_client",
                return_value=fake_client,
            ),
            patch(
                "simulate.services.alk_simulate_ingestion.get_object_url",
                return_value="https://storage.example.com/stereo.wav",
            ),
        ):
            stereo = auth_client.post(
                endpoint,
                {
                    "file": SimpleUploadedFile("stereo.wav", stereo_content),
                    "sha256": stereo_digest,
                    "kind": "stereo",
                },
                format="multipart",
            )
        assert stereo.status_code == 200
        call.refresh_from_db()
        assert call.stereo_recording_url == "https://storage.example.com/stereo.wav"
        assert (
            call.call_metadata["alk_recording_artifacts"]["stereo"]["sha256"]
            == stereo_digest
        )

        mismatch = auth_client.post(
            endpoint,
            {
                "file": SimpleUploadedFile("call.wav", b"different"),
                "sha256": digest,
            },
            format="multipart",
        )
        assert mismatch.status_code == 400
        assert "sha256" in str(mismatch.json()).lower()

    def test_missing_file_returns_400(self, auth_client, run_test):
        _, call_ids = _start_and_batch(auth_client, run_test)
        resp = auth_client.post(
            f"{ALK_BASE}/call-executions/{call_ids[0]}/recording/",
            {},
            format="multipart",
        )
        assert resp.status_code == 400
        assert resp.json()["status"] is False


# ---------------------------------------------------------------------------
# Hosted runner (chat / TEXT mode)
# ---------------------------------------------------------------------------


@pytest.fixture
def text_agent_definition(db, organization, workspace):
    return AgentDefinition.objects.create(
        agent_name="ALK Chat Agent",
        agent_type=AgentDefinition.AgentTypeChoices.TEXT,
        contact_number="",
        inbound=True,
        description="Chat agent under test for the hosted runner",
        organization=organization,
        workspace=workspace,
        languages=["en"],
    )


@pytest.fixture
def text_run_test(
    db, organization, workspace, text_agent_definition, scenario, simulator_agent
):
    rt = RunTest.objects.create(
        name="ALK Chat Run Test",
        description="Chat run for the hosted runner",
        agent_definition=text_agent_definition,
        simulator_agent=simulator_agent,
        organization=organization,
        workspace=workspace,
    )
    rt.scenarios.add(scenario)
    return rt


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
class TestTextModeIngestion:
    def test_batch_creates_text_call(self, auth_client, text_run_test, scenario):
        te_id, call_ids = _start_and_batch(auth_client, text_run_test)
        assert len(call_ids) == 1

        call = CallExecution.objects.get(id=call_ids[0])
        assert call.simulation_call_type == CallExecution.SimulationCallType.TEXT
        assert call.call_metadata["call_channel"] == "chat"
        assert call.call_metadata["external_runner"] == "alk"

    def test_text_result_ingest_writes_chat_messages(self, auth_client, text_run_test):
        from simulate.models.chat_message import ChatMessageModel

        _, call_ids = _start_and_batch(auth_client, text_run_test)
        resp = auth_client.patch(
            f"{ALK_BASE}/call-executions/{call_ids[0]}/result/",
            {"status": "completed", "transcript": _transcript_payload()},
            format="json",
        )
        assert resp.status_code == 200, resp.content

        call = CallExecution.objects.get(id=call_ids[0])
        assert call.status == CallExecution.CallStatus.COMPLETED
        # Chat runs render from ChatMessage (not voice CallTranscript).
        chat_rows = ChatMessageModel.objects.filter(call_execution=call)
        assert chat_rows.count() == 3
        assert CallTranscript.objects.filter(call_execution=call).count() == 0
        # turn_count = number of ASSISTANT rows (one agent turn in the fixture).
        cmd = call.conversation_metrics_data or {}
        assert cmd.get("turn_count") == 1

    def test_text_result_ingest_folds_tool_calls_into_agent_turn(
        self, auth_client, text_run_test
    ):
        from simulate.models.chat_message import ChatMessageModel

        _, call_ids = _start_and_batch(auth_client, text_run_test)
        transcript = [
            {"speaker_role": "user", "content": "My order A1 arrived damaged."},
            {
                "speaker_role": "tool_calls",
                "content": 'lookup_order({"order_id": "A1"})',
                "tool_calls": [
                    {
                        "id": "c1",
                        "name": "lookup_order",
                        "arguments": {"order_id": "A1"},
                    }
                ],
            },
            {
                "speaker_role": "tool_call_result",
                "content": "order A1: eligible for refund",
                "tool_call_id": "c1",
            },
            {"speaker_role": "assistant", "content": "You're eligible — refund done."},
        ]
        resp = auth_client.patch(
            f"{ALK_BASE}/call-executions/{call_ids[0]}/result/",
            {"status": "completed", "transcript": transcript},
            format="json",
        )
        assert resp.status_code == 200, resp.content

        call = CallExecution.objects.get(id=call_ids[0])
        rows = list(
            ChatMessageModel.objects.filter(call_execution=call).order_by("created_at")
        )
        # One exchange: 1 USER row + 1 folded ASSISTANT row (tool call + result +
        # final text). turn_count stays 1 (native exchange semantic).
        assert len(rows) == 2
        assistant = next(r for r in rows if r.role == "assistant")
        blob = json.dumps(assistant.content)
        assert "lookup_order" in blob
        assert "eligible for refund" in blob
        assert any(item.get("tool_calls") for item in assistant.content)
        assert (call.conversation_metrics_data or {}).get("turn_count") == 1


@pytest.mark.integration
@pytest.mark.django_db
class TestBuildRunnerJob:
    def test_builds_chat_job_for_text_run(self, text_run_test, scenario):
        from django.test import override_settings

        from simulate.services.alk_simulate_ingestion import (
            create_alk_sim_test_execution,
        )
        from simulate.services.hosted_runner import build_start_runner_job

        te = create_alk_sim_test_execution(text_run_test)

        with override_settings(
            ALK_RUNNER_DEFAULT_CHAT_TARGET="my_module:reply",
            ALK_RUNNER_API_URL="http://localhost:8000",
        ):
            job = build_start_runner_job(
                test_execution_id=str(te.id),
                run_test_id=str(text_run_test.id),
                scenario_ids=[str(scenario.id)],
                mode="chat",
            )

        assert job["schema_version"] == "futureagi.runner-job.v1"
        assert job["mode"] == "chat"
        assert job["spec"]["environment"]["adapter"] == "chat"
        # No provider server_url -> falls back to the configured callable target.
        assert job["spec"]["target"]["adapter"] == "callable"
        assert job["spec"]["target"]["config"]["target"] == "my_module:reply"
        assert len(job["spec"]["scenario"]["dataset"]) == 1
        # Sink points at the pre-created execution + carries secret refs only.
        assert job["sink"]["test_execution_id"] == str(te.id)
        assert job["sink"]["run_test_id"] == str(text_run_test.id)
        internal_ref = job["sink"]["secret_refs"]["internal_api_secret"]
        assert internal_ref == {
            "manager": "env",
            "key": "INTERNAL_API_SECRET",
            "purpose": "internal_api_secret",
        }


@pytest.mark.integration
@pytest.mark.django_db
class TestBuildVoiceRunnerJob:
    """#149 — the voice branch maps a platform VOICE run test to a voice job
    (VoiceRunConfig shape) with the transport derived from provider + phone."""

    @pytest.fixture(autouse=True)
    def _system_livekit(self, settings):
        # Every non-webrtc voice job runs the simulator on the platform (system)
        # LiveKit, so the build requires LIVEKIT_URL. CI leaves it unset
        # (settings default ""), so provide it here for the whole class.
        settings.LIVEKIT_URL = "wss://sim.livekit.test"

    def _voice_agent(
        self,
        organization,
        workspace,
        *,
        provider=None,
        phone="",
        inbound=True,
        assistant_id="",
        server_url="",
        agent_name="",
    ):
        agent = AgentDefinition.objects.create(
            agent_name="Voice Target",
            agent_type=AgentDefinition.AgentTypeChoices.VOICE,
            contact_number=phone,
            inbound=inbound,
            description="Voice agent under test",
            provider=provider,
            assistant_id=assistant_id,
            organization=organization,
            workspace=workspace,
            languages=["en"],
        )
        if provider in {"vapi", "retell", "livekit"}:
            from simulate.models.agent_definition import ProviderCredentials

            ProviderCredentials.objects.create(
                agent_definition=agent,
                provider_type=provider,
                api_key="secret-key-value",
                api_secret="secret-secret-value" if provider == "livekit" else "",
                assistant_id=assistant_id,
                server_url=server_url,
                agent_name=agent_name,
            )
        return agent

    def _run_test(self, organization, workspace, agent, simulator_agent, scenario):
        rt = RunTest.objects.create(
            name="Voice Run",
            description="voice",
            agent_definition=agent,
            simulator_agent=simulator_agent,
            organization=organization,
            workspace=workspace,
        )
        rt.scenarios.add(scenario)
        return rt

    def _scenario(self, organization, workspace, agent, simulator_agent):
        return Scenarios.objects.create(
            name="Voice Scenario",
            description="A late delivery.",
            source="test",
            scenario_type=Scenarios.ScenarioTypes.DATASET,
            organization=organization,
            workspace=workspace,
            agent_definition=agent,
            simulator_agent=simulator_agent,
            status=StatusType.COMPLETED.value,
        )

    def _build(self, organization, workspace, simulator_agent, agent):
        from simulate.services.alk_simulate_ingestion import (
            create_alk_sim_test_execution,
        )
        from simulate.services.hosted_runner import (
            build_start_runner_job,
            resolve_runner_mode,
        )

        scenario = self._scenario(organization, workspace, agent, simulator_agent)
        rt = self._run_test(organization, workspace, agent, simulator_agent, scenario)
        te = create_alk_sim_test_execution(rt)
        mode = resolve_runner_mode(agent)
        job = build_start_runner_job(
            test_execution_id=str(te.id),
            run_test_id=str(rt.id),
            scenario_ids=[str(scenario.id)],
            mode=mode,
        )
        return job, mode

    def _build_multi(self, organization, workspace, simulator_agent, agent, n):
        """Like ``_build`` but attaches ``n`` scenarios with no dataset rows,
        so the builder derives one persona per scenario (mirrors ``_build``'s
        single-scenario attach)."""
        from simulate.services.alk_simulate_ingestion import (
            create_alk_sim_test_execution,
        )
        from simulate.services.hosted_runner import (
            build_start_runner_job,
            resolve_runner_mode,
        )

        scenarios = [
            self._scenario(organization, workspace, agent, simulator_agent)
            for _ in range(n)
        ]
        rt = RunTest.objects.create(
            name="Voice Run",
            description="voice",
            agent_definition=agent,
            simulator_agent=simulator_agent,
            organization=organization,
            workspace=workspace,
        )
        rt.scenarios.add(*scenarios)
        te = create_alk_sim_test_execution(rt)
        mode = resolve_runner_mode(agent)
        job = build_start_runner_job(
            test_execution_id=str(te.id),
            run_test_id=str(rt.id),
            scenario_ids=[str(s.id) for s in scenarios],
            mode=mode,
        )
        return job, mode

    def test_resolve_runner_mode(self, organization, workspace):
        from simulate.services.hosted_runner import resolve_runner_mode

        web = self._voice_agent(organization, workspace, provider="vapi", phone="")
        phoned = self._voice_agent(
            organization, workspace, provider="vapi", phone="+15551234567"
        )
        assert resolve_runner_mode(web) == "voice_webrtc"
        assert resolve_runner_mode(phoned) == "voice_sip"

    def test_builds_vapi_websocket_job(self, organization, workspace, simulator_agent):
        agent = self._voice_agent(
            organization, workspace, provider="vapi", phone="", assistant_id="asst_123"
        )
        job, mode = self._build(organization, workspace, simulator_agent, agent)
        assert mode == "voice_webrtc"
        assert job["mode"] == "voice_webrtc"
        assert "spec" not in job
        adef = job["voice"]["agent_definition"]
        assert adef["transport"]["kind"] == "vapi_websocket"
        assert adef["target"] == {
            "provider": "vapi",
            "assistant_id": "asst_123",
            "api_key_env": "VAPI_API_KEY",
        }
        assert job["voice"]["params"]["record_audio"] is True
        # Provider secret resolves from ProviderCredentials, LiveKit from env.
        keys = {r["key"]: r for r in job["metadata"]["secret_env"]}
        assert keys["VAPI_API_KEY"]["manager"] == "provider_credentials"
        assert keys["LIVEKIT_API_KEY"]["manager"] == "env"
        assert job["metadata"]["run_id"] == job["sink"]["test_execution_id"]

    def test_builds_retell_webcall_job(self, organization, workspace, simulator_agent):
        agent = self._voice_agent(
            organization,
            workspace,
            provider="retell",
            phone="",
            assistant_id="agent_xyz",
        )
        job, mode = self._build(organization, workspace, simulator_agent, agent)
        adef = job["voice"]["agent_definition"]
        assert mode == "voice_webrtc"
        assert adef["transport"]["kind"] == "retell_webcall"
        assert adef["target"]["provider"] == "retell"
        assert adef["target"]["agent_id"] == "agent_xyz"
        assert adef["target"]["api_key_env"] == "RETELL_API_KEY"

    def test_builds_webrtc_job_uses_customer_livekit(
        self, organization, workspace, simulator_agent
    ):
        agent = self._voice_agent(
            organization,
            workspace,
            provider="livekit",
            phone="",
            server_url="wss://customer.livekit.cloud",
            agent_name="target-worker",
        )
        job, mode = self._build(organization, workspace, simulator_agent, agent)
        adef = job["voice"]["agent_definition"]
        assert mode == "voice_webrtc"
        assert adef["transport"] == {"kind": "webrtc"}
        assert adef["agent_name"] == "target-worker"
        rt = job["voice"]["livekit_runtime"]
        assert rt["url"] == "wss://customer.livekit.cloud"
        keys = {r["key"]: r for r in job["metadata"]["secret_env"]}
        assert keys["LIVEKIT_API_KEY"]["manager"] == "provider_credentials"
        assert keys["LIVEKIT_API_SECRET"]["field"] == "api_secret"

    def test_dataset_rows_expand_to_one_voice_case_each(
        self, organization, workspace, simulator_agent
    ):
        from model_hub.models.choices import DatasetSourceChoices, SourceChoices
        from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
        from simulate.services.alk_simulate_ingestion import (
            create_alk_sim_call_execution_batch,
            create_alk_sim_test_execution,
            precreate_alk_sim_call_executions,
        )
        from simulate.services.hosted_runner import build_start_runner_job

        agent = self._voice_agent(
            organization, workspace, provider="vapi", assistant_id="asst_123"
        )
        dataset = Dataset.no_workspace_objects.create(
            name="ten hosted cases",
            organization=organization,
            workspace=workspace,
            source=DatasetSourceChoices.SCENARIO.value,
        )
        columns = {
            name: Column.objects.create(
                dataset=dataset,
                name=name,
                data_type="persona" if name == "persona" else "text",
                source=SourceChoices.OTHERS.value,
            )
            for name in ("persona", "situation", "outcome", "branch_category")
        }
        for index in range(10):
            row = Row.objects.create(dataset=dataset, order=index)
            row_values = {
                "persona": str({"name": f"Customer {index}", "age_group": "25-35"}),
                "situation": f"Situation {index}",
                "outcome": f"Outcome {index}",
                "branch_category": f"Branch {index}",
            }
            for name, value in row_values.items():
                Cell.objects.create(
                    dataset=dataset, column=columns[name], row=row, value=value
                )

        scenario = self._scenario(organization, workspace, agent, simulator_agent)
        scenario.dataset = dataset
        scenario.save(update_fields=["dataset"])
        run_test = self._run_test(
            organization, workspace, agent, simulator_agent, scenario
        )
        execution = create_alk_sim_test_execution(run_test)
        precreated_ids = precreate_alk_sim_call_executions(execution)
        batch = create_alk_sim_call_execution_batch(execution)

        assert len(precreated_ids) == 10
        assert batch.call_execution_ids == precreated_ids
        assert execution.calls.count() == 10
        assert (
            execution.calls.filter(status=CallExecution.CallStatus.PENDING).count()
            == 10
        )

        job = build_start_runner_job(
            test_execution_id=str(execution.id),
            run_test_id=str(run_test.id),
            scenario_ids=[str(scenario.id)],
            mode="voice_webrtc",
        )

        cases = job["voice"]["scenario"]["dataset"]
        assert len(cases) == 10
        assert [case["persona"]["name"] for case in cases] == [
            f"Customer {index}" for index in range(10)
        ]
        assert cases[4]["situation"] == "Situation 4"
        assert cases[4]["outcome"] == "Outcome 4"
        assert cases[4]["persona"]["branch_category"] == "Branch 4"
        # max_seconds now derives from the simulator's call-duration ceiling
        # (>=120s), not the old flat 120s that cut real calls at ~2 minutes.
        params = job["voice"]["params"]
        assert params["max_seconds"] >= 120.0
        # D15: the child's own deadline (no parent cap to fit under anymore)
        # — the parent's timeout is derived FROM this, not compared against it.
        from simulate.services.hosted_runner import child_run_seconds

        deadline = (
            params["max_seconds"]
            + params["connect_timeout"]
            + params["readiness_timeout"]
            + params["cleanup_timeout"]
            + 60.0
        )
        assert child_run_seconds(params) == int(deadline)

    def test_rerun_scopes_job_to_selected_calls(
        self, organization, workspace, simulator_agent
    ):
        """A scoped rerun (call_execution_ids) builds ONLY those calls' cases, in
        canonical (scenario, row) order regardless of request order — so the
        SDK's positional case→row mapping still lines up with exactly the rows
        that ALK /batch re-adopts. An id outside the execution is rejected."""
        import uuid as _uuid

        from model_hub.models.choices import DatasetSourceChoices, SourceChoices
        from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
        from simulate.services.alk_simulate_ingestion import (
            create_alk_sim_call_execution_batch,
            create_alk_sim_test_execution,
            precreate_alk_sim_call_executions,
        )
        from simulate.services.hosted_runner import (
            HostedRunnerBuildError,
            build_start_runner_job,
        )

        agent = self._voice_agent(
            organization, workspace, provider="vapi", assistant_id="asst_123"
        )
        dataset = Dataset.no_workspace_objects.create(
            name="four hosted cases",
            organization=organization,
            workspace=workspace,
            source=DatasetSourceChoices.SCENARIO.value,
        )
        columns = {
            name: Column.objects.create(
                dataset=dataset,
                name=name,
                data_type="persona" if name == "persona" else "text",
                source=SourceChoices.OTHERS.value,
            )
            for name in ("persona", "situation", "outcome")
        }
        for index in range(4):
            row = Row.objects.create(dataset=dataset, order=index)
            for name, value in {
                "persona": str({"name": f"Customer {index}"}),
                "situation": f"Situation {index}",
                "outcome": f"Outcome {index}",
            }.items():
                Cell.objects.create(
                    dataset=dataset, column=columns[name], row=row, value=value
                )

        scenario = self._scenario(organization, workspace, agent, simulator_agent)
        scenario.dataset = dataset
        scenario.save(update_fields=["dataset"])
        run_test = self._run_test(
            organization, workspace, agent, simulator_agent, scenario
        )
        execution = create_alk_sim_test_execution(run_test)
        precreated_ids = precreate_alk_sim_call_executions(execution)
        create_alk_sim_call_execution_batch(execution)
        assert len(precreated_ids) == 4

        # Select rows 3 and 1 (out of 0..3), given out of order on purpose.
        selected = [precreated_ids[3], precreated_ids[1]]
        job = build_start_runner_job(
            test_execution_id=str(execution.id),
            run_test_id=str(run_test.id),
            scenario_ids=[str(scenario.id)],
            mode="voice_webrtc",
            call_execution_ids=selected,
        )
        cases = job["voice"]["scenario"]["dataset"]
        # Exactly the two selected cases, in canonical row order (1 then 3).
        assert [c["persona"]["name"] for c in cases] == ["Customer 1", "Customer 3"]

        # An id outside the execution is rejected (guards the positional map).
        with pytest.raises(HostedRunnerBuildError):
            build_start_runner_job(
                test_execution_id=str(execution.id),
                run_test_id=str(run_test.id),
                scenario_ids=[str(scenario.id)],
                mode="voice_webrtc",
                call_execution_ids=[str(_uuid.uuid4())],
            )

    def test_builds_sip_outbound_job(self, organization, workspace, simulator_agent):
        from django.test import override_settings

        agent = self._voice_agent(
            organization,
            workspace,
            provider="livekit",
            phone="+15551230000",
            inbound=True,
        )
        with override_settings(
            LIVEKIT_OUTBOUND_TRUNK_ID="ST_trunk", PSTN_CALLER_NUMBER="+15550009999"
        ):
            job, mode = self._build(organization, workspace, simulator_agent, agent)
        assert mode == "voice_sip"
        t = job["voice"]["agent_definition"]["transport"]
        assert t["kind"] == "sip_outbound"
        assert t["sip_trunk_id"] == "ST_trunk"
        assert t["sip_number"] == "+15550009999"
        assert t["sip_call_to"] == "+15551230000"

    def test_builds_sip_inbound_job_no_did_at_build(
        self, organization, workspace, simulator_agent
    ):
        # Outbound agent (inbound=False) dials the simulator DID -> sip_inbound;
        # the DID/dispatch rule are leased by the runner activity, not here.
        agent = self._voice_agent(
            organization,
            workspace,
            provider="vapi",
            phone="+15551230000",
            inbound=False,
        )
        job, mode = self._build(organization, workspace, simulator_agent, agent)
        assert mode == "voice_sip"
        t = job["voice"]["agent_definition"]["transport"]
        assert t["kind"] == "sip_inbound"
        assert "dispatch_rule_name" not in t
        assert t["inbound_call_originator"] == "vapi"
        assert job["voice"]["agent_definition"]["provider_evidence"] == {
            "provider": "vapi",
            "call_id_source": "originator_response",
            "poll_interval_seconds": 3,
            "poll_deadline_seconds": 45,
        }

    def test_builds_retell_sip_inbound_job(
        self, organization, workspace, simulator_agent
    ):
        agent = self._voice_agent(
            organization,
            workspace,
            provider="retell",
            phone="+14155550123",
            inbound=False,
            assistant_id="agent_xyz",
        )
        job, mode = self._build(organization, workspace, simulator_agent, agent)
        assert mode == "voice_sip"
        t = job["voice"]["agent_definition"]["transport"]
        assert t["kind"] == "sip_inbound"
        assert t["inbound_call_originator"] == "retell"
        assert t["originator_agent_id"] == "agent_xyz"
        assert t["originator_from_number"] == "+14155550123"
        assert job["voice"]["agent_definition"]["provider_evidence"] == {
            "provider": "retell",
            "call_id_source": "originator_response",
            "poll_interval_seconds": 3,
            "poll_deadline_seconds": 45,
        }
        secret_env = job["metadata"]["secret_env"]
        assert any(
            ref["key"] == "RETELL_API_KEY" and ref["manager"] == "provider_credentials"
            for ref in secret_env
        )

    def test_retell_sip_inbound_missing_agent_id_raises(
        self, organization, workspace, simulator_agent
    ):
        from simulate.services.hosted_runner import HostedRunnerBuildError

        agent = self._voice_agent(
            organization,
            workspace,
            provider="retell",
            phone="+14155550123",
            inbound=False,
            assistant_id="",
        )
        with pytest.raises(HostedRunnerBuildError) as excinfo:
            self._build(organization, workspace, simulator_agent, agent)
        assert (
            str(excinfo.value) == "voice runner job missing retell originator_agent_id"
        )

    def test_retell_sip_inbound_missing_number_raises(self, organization, workspace):
        # An empty contact_number resolves mode to voice_webrtc, not sip_inbound
        # (§7 D7), so the validator can't be reached through build_start_runner_job
        # with a blank number — exercise it directly the way _build_voice_job does.
        from simulate.models.agent_definition import ProviderCredentials
        from simulate.services.hosted_runner import (
            HostedRunnerBuildError,
            _voice_agent_definition,
        )

        agent = self._voice_agent(
            organization,
            workspace,
            provider="retell",
            phone="",
            inbound=False,
            assistant_id="agent_xyz",
        )
        credentials = ProviderCredentials.objects.get(agent_definition=agent)
        with pytest.raises(HostedRunnerBuildError) as excinfo:
            _voice_agent_definition(agent, "retell", "sip_inbound", credentials)
        assert (
            str(excinfo.value)
            == "voice runner job missing retell originator_from_number"
        )

    def test_retell_sip_inbound_malformed_number_raises(
        self, organization, workspace, simulator_agent
    ):
        from simulate.services.hosted_runner import HostedRunnerBuildError

        agent = self._voice_agent(
            organization,
            workspace,
            provider="retell",
            phone="4155550123",
            inbound=False,
            assistant_id="agent_xyz",
        )
        with pytest.raises(HostedRunnerBuildError) as excinfo:
            self._build(organization, workspace, simulator_agent, agent)
        assert (
            str(excinfo.value)
            == "voice runner job invalid retell originator_from_number"
        )

    @pytest.mark.parametrize(
        "number,valid",
        [
            ("+1234567", True),
            ("+123456", False),
            ("+01234567", False),
            ("4155550123", False),
            ("+14155550123\n", False),
        ],
    )
    def test_retell_originator_from_number_e164_boundaries(
        self, organization, workspace, simulator_agent, number, valid
    ):
        from simulate.services.hosted_runner import HostedRunnerBuildError

        agent = self._voice_agent(
            organization,
            workspace,
            provider="retell",
            phone=number,
            inbound=False,
            assistant_id="agent_xyz",
        )
        if valid:
            job, mode = self._build(organization, workspace, simulator_agent, agent)
            assert (
                job["voice"]["agent_definition"]["transport"]["originator_from_number"]
                == number
            )
        else:
            with pytest.raises(HostedRunnerBuildError):
                self._build(organization, workspace, simulator_agent, agent)

    def test_pinned_version_snapshot_wins_over_definition_in_full_build(
        self, organization, workspace, simulator_agent
    ):
        """Pins the run to an OLDER version and cuts a NEWER one afterward
        (mirrors an edit reaching the definition columns after a version was
        already pinned), so ``_agent_field``'s own versionless fallback to
        ``latest_version`` cannot coincidentally match the pin — with only
        one version ever created, that fallback would still find the right
        snapshot even if ``agent_version`` were dropped somewhere on the path
        from ``build_start_runner_job`` through to ``_voice_agent_definition``,
        masking a real wiring break.

        Credentials are created once with ``assistant_id=""`` and never
        resynced, so ``credentials.assistant_id`` stays falsy throughout and
        cannot mask either version's own ``assistant_id``."""
        from simulate.services.alk_simulate_ingestion import (
            create_alk_sim_test_execution,
        )
        from simulate.services.hosted_runner import (
            build_start_runner_job,
            resolve_runner_mode,
        )

        # Outbound target (inbound=False) -> sip_inbound transport, so Retell's
        # originator_from_number / originator_agent_id fields are populated.
        agent = self._voice_agent(
            organization,
            workspace,
            provider="retell",
            phone="+15559999999",
            inbound=False,
            assistant_id="",
        )
        agent.assistant_id = "snap_agent"
        agent.agent_name = "Snap Name"
        agent.description = "snap prompt"
        agent.save(update_fields=["assistant_id", "agent_name", "description"])
        version_pinned = agent.create_version(
            description="pinned version", commit_message="v1", status="active"
        )

        # A later edit reaches the definition columns directly (today's edit
        # endpoints do exactly this) and a new version is cut from it, so
        # latest_version now differs from the pin.
        agent.contact_number = "+15550000001"
        agent.assistant_id = "def_agent"
        agent.agent_name = "Def Name"
        agent.description = "def prompt"
        agent.save(
            update_fields=[
                "contact_number",
                "assistant_id",
                "agent_name",
                "description",
            ]
        )
        agent.create_version(
            description="later edit", commit_message="v2", status="active"
        )
        assert agent.latest_version.id != version_pinned.id

        scenario = self._scenario(organization, workspace, agent, simulator_agent)
        rt = self._run_test(organization, workspace, agent, simulator_agent, scenario)
        rt.agent_version = version_pinned
        rt.save(update_fields=["agent_version"])

        te = create_alk_sim_test_execution(rt)
        assert te.agent_version_id == version_pinned.id  # pin carried to the execution

        mode = resolve_runner_mode(agent, version_pinned)
        assert mode == "voice_sip"
        job = build_start_runner_job(
            test_execution_id=str(te.id),
            run_test_id=str(rt.id),
            scenario_ids=[str(scenario.id)],
            mode=mode,
        )
        transport = job["voice"]["agent_definition"]["transport"]
        assert transport["originator_from_number"] == "+15559999999"
        assert transport["originator_agent_id"] == "snap_agent"
        assert job["voice"]["agent_definition"]["name"] == "Snap Name"
        assert job["voice"]["agent_definition"]["system_prompt"] == "snap prompt"

    def test_pinned_version_snapshot_clears_phone_and_falls_back_to_webrtc(
        self, organization, workspace, simulator_agent
    ):
        """Same two-version wiring as the previous test, but for the
        mode/transport gate rather than the originator fields: the pinned
        version's snapshot silencing contact_number must win over a stale
        phone column, dropping the run to voice_webrtc/retell_webcall with no
        DID lease and no originator."""
        from simulate.services.alk_simulate_ingestion import (
            create_alk_sim_test_execution,
        )
        from simulate.services.hosted_runner import (
            build_start_runner_job,
            resolve_runner_mode,
        )

        agent = self._voice_agent(
            organization,
            workspace,
            provider="retell",
            phone="+15551234567",
            assistant_id="agent_xyz",
        )
        version = agent.create_version(
            description="pinned for hosted run",
            commit_message="clear phone",
            status="active",
        )
        version.configuration_snapshot = {
            **version.configuration_snapshot,
            "contact_number": "",
        }
        version.save(update_fields=["configuration_snapshot"])

        scenario = self._scenario(organization, workspace, agent, simulator_agent)
        rt = self._run_test(organization, workspace, agent, simulator_agent, scenario)
        rt.agent_version = version
        rt.save(update_fields=["agent_version"])

        te = create_alk_sim_test_execution(rt)
        mode = resolve_runner_mode(agent, version)
        assert mode == "voice_webrtc"
        job = build_start_runner_job(
            test_execution_id=str(te.id),
            run_test_id=str(rt.id),
            scenario_ids=[str(scenario.id)],
            mode=mode,
        )
        transport = job["voice"]["agent_definition"]["transport"]
        assert transport["kind"] == "retell_webcall"
        assert "inbound_call_originator" not in transport

    def _retell_originator_agent(self, organization, workspace):
        return self._voice_agent(
            organization,
            workspace,
            provider="retell",
            phone="+14155550123",
            inbound=False,
            assistant_id="agent_xyz",
        )

    def test_multi_scenario_sip_inbound_originator_refused_without_reuse(
        self, organization, workspace, simulator_agent
    ):
        from django.test import override_settings

        from simulate.services.hosted_runner import HostedRunnerBuildError

        agent = self._retell_originator_agent(organization, workspace)
        with override_settings(HOSTED_RUNNER_LEASED_ROOM_REUSE=False):
            with pytest.raises(HostedRunnerBuildError) as excinfo:
                self._build_multi(organization, workspace, simulator_agent, agent, 3)
        message = str(excinfo.value)
        assert "3 scenario rows" in message
        assert "select a single scenario row" in message
        assert "HOSTED_RUNNER_LEASED_ROOM_REUSE" in message

    def test_multi_scenario_sip_inbound_originator_builds_with_reuse_on(
        self, organization, workspace, simulator_agent
    ):
        from django.test import override_settings

        agent = self._retell_originator_agent(organization, workspace)
        with override_settings(HOSTED_RUNNER_LEASED_ROOM_REUSE=True):
            job, mode = self._build_multi(
                organization, workspace, simulator_agent, agent, 3
            )
        assert mode == "voice_sip"
        params = job["voice"]["params"]
        assert params["max_concurrency"] == 1
        # The budget follows the rows: each call keeps the full ceiling and the
        # cleanup carries the other calls plus one drain allowance per call.
        assert params["max_seconds"] == 30 * 60.0
        fixed_overhead = params["connect_timeout"] + params["readiness_timeout"] + 60.0
        leased_overhead = fixed_overhead + params["connect_timeout"]
        assert params["cleanup_timeout"] == (
            2 * params["max_seconds"] + 3 * leased_overhead - fixed_overhead + 30.0
        )

    def test_switch_off_ignores_env_true(
        self, organization, workspace, simulator_agent, monkeypatch
    ):
        from django.test import override_settings

        from simulate.services.hosted_runner import HostedRunnerBuildError

        agent = self._retell_originator_agent(organization, workspace)
        monkeypatch.setenv("HOSTED_RUNNER_LEASED_ROOM_REUSE", "true")
        with override_settings(HOSTED_RUNNER_LEASED_ROOM_REUSE=False):
            with pytest.raises(HostedRunnerBuildError):
                self._build_multi(organization, workspace, simulator_agent, agent, 3)

    def test_single_scenario_sip_inbound_originator_builds_with_reuse_off(
        self, organization, workspace, simulator_agent
    ):
        from django.test import override_settings

        agent = self._retell_originator_agent(organization, workspace)
        with override_settings(HOSTED_RUNNER_LEASED_ROOM_REUSE=False):
            job, mode = self._build(organization, workspace, simulator_agent, agent)
        assert mode == "voice_sip"

    def test_multi_scenario_sip_inbound_no_originator_builds_with_reuse_off(
        self, organization, workspace, simulator_agent
    ):
        from django.test import override_settings

        agent = self._voice_agent(
            organization,
            workspace,
            provider="livekit",
            phone="+15551230000",
            inbound=False,
        )
        with override_settings(HOSTED_RUNNER_LEASED_ROOM_REUSE=False):
            job, mode = self._build_multi(
                organization, workspace, simulator_agent, agent, 3
            )
        assert mode == "voice_sip"
        transport = job["voice"]["agent_definition"]["transport"]
        assert transport["kind"] == "sip_inbound"
        assert "inbound_call_originator" not in transport

    def test_multi_scenario_sip_outbound_builds_with_reuse_off(
        self, organization, workspace, simulator_agent
    ):
        from django.test import override_settings

        agent = self._voice_agent(
            organization,
            workspace,
            provider="livekit",
            phone="+15551230000",
            inbound=True,
        )
        with override_settings(
            HOSTED_RUNNER_LEASED_ROOM_REUSE=False,
            LIVEKIT_OUTBOUND_TRUNK_ID="ST_trunk",
            PSTN_CALLER_NUMBER="+15550009999",
        ):
            job, mode = self._build_multi(
                organization, workspace, simulator_agent, agent, 3
            )
        assert mode == "voice_sip"
        assert job["voice"]["agent_definition"]["transport"]["kind"] == "sip_outbound"

    def test_multi_scenario_web_transport_builds_with_reuse_off(
        self, organization, workspace, simulator_agent
    ):
        from django.test import override_settings

        agent = self._voice_agent(
            organization, workspace, provider="vapi", phone="", assistant_id="asst_123"
        )
        with override_settings(HOSTED_RUNNER_LEASED_ROOM_REUSE=False):
            job, mode = self._build_multi(
                organization, workspace, simulator_agent, agent, 3
            )
        assert mode == "voice_webrtc"

    def test_leased_room_budget_default_ceiling_n3(
        self, organization, workspace, simulator_agent
    ):
        # D15: max_seconds is the call ceiling, full stop — no per-case
        # shrink for however many rows are leased into one room.
        from django.test import override_settings

        agent = self._retell_originator_agent(organization, workspace)
        with override_settings(HOSTED_RUNNER_LEASED_ROOM_REUSE=True):
            job, _ = self._build_multi(
                organization, workspace, simulator_agent, agent, 3
            )
        assert job["voice"]["params"]["max_seconds"] == 1800.0

    def test_leased_room_budget_two_minute_ceiling_n3(
        self, organization, workspace, simulator_agent
    ):
        from django.test import override_settings

        simulator_agent.max_call_duration_in_minutes = 2
        simulator_agent.save(update_fields=["max_call_duration_in_minutes"])
        agent = self._retell_originator_agent(organization, workspace)
        with override_settings(HOSTED_RUNNER_LEASED_ROOM_REUSE=True):
            job, _ = self._build_multi(
                organization, workspace, simulator_agent, agent, 3
            )
        assert job["voice"]["params"]["max_seconds"] == 120.0

    def test_leased_room_budget_two_minute_ceiling_n6_builds(
        self, organization, workspace, simulator_agent
    ):
        from django.test import override_settings

        simulator_agent.max_call_duration_in_minutes = 2
        simulator_agent.save(update_fields=["max_call_duration_in_minutes"])
        agent = self._retell_originator_agent(organization, workspace)
        with override_settings(HOSTED_RUNNER_LEASED_ROOM_REUSE=True):
            job, _ = self._build_multi(
                organization, workspace, simulator_agent, agent, 6
            )
        assert job["voice"]["params"]["max_seconds"] == 120.0

    def test_leased_room_budget_two_minute_ceiling_n10_builds(
        self, organization, workspace, simulator_agent
    ):
        # D15: a row count that used to be refused now simply builds — the
        # child owns however long its own budget needs to be.
        from django.test import override_settings

        simulator_agent.max_call_duration_in_minutes = 2
        simulator_agent.save(update_fields=["max_call_duration_in_minutes"])
        agent = self._retell_originator_agent(organization, workspace)
        with override_settings(HOSTED_RUNNER_LEASED_ROOM_REUSE=True):
            job, _ = self._build_multi(
                organization, workspace, simulator_agent, agent, 10
            )
        assert job["voice"]["params"]["max_seconds"] == 120.0

    def _assert_leased_deadline_identity(self, job, case_count):
        """D15: pin ``cleanup_timeout`` and the derived child deadline from
        the budget constants (not by echoing the function's own output), so a
        regression that reverts to the old per-case shrink is caught here
        even though every other assertion in this class only pins
        ``max_seconds``."""
        from simulate.services.hosted_runner import child_run_seconds

        connect_timeout = 60.0
        readiness_timeout = 120.0
        base_cleanup = 30.0
        fixed_overhead = connect_timeout + readiness_timeout + 60.0
        leased_overhead = fixed_overhead + connect_timeout

        params = job["voice"]["params"]
        max_seconds = params["max_seconds"]
        cleanup_timeout = (
            (case_count - 1) * max_seconds
            + case_count * leased_overhead
            - fixed_overhead
            + base_cleanup
        )
        assert params["cleanup_timeout"] == cleanup_timeout
        run_seconds = child_run_seconds(params)
        expected = case_count * (max_seconds + leased_overhead) + base_cleanup
        assert run_seconds == expected

    def test_leased_room_budget_fits_every_case_n3(
        self, organization, workspace, simulator_agent
    ):
        from django.test import override_settings

        agent = self._retell_originator_agent(organization, workspace)
        with override_settings(HOSTED_RUNNER_LEASED_ROOM_REUSE=True):
            job, _ = self._build_multi(
                organization, workspace, simulator_agent, agent, 3
            )
        self._assert_leased_deadline_identity(job, 3)

    def test_leased_room_budget_fits_every_case_n5(
        self, organization, workspace, simulator_agent
    ):
        from django.test import override_settings

        agent = self._retell_originator_agent(organization, workspace)
        with override_settings(HOSTED_RUNNER_LEASED_ROOM_REUSE=True):
            job, _ = self._build_multi(
                organization, workspace, simulator_agent, agent, 5
            )
        self._assert_leased_deadline_identity(job, 5)

    def test_leased_room_budget_fits_every_case_n2(
        self, organization, workspace, simulator_agent
    ):
        # N=2 used to disagree with the N>=3 formula only because the old
        # max_cleanup clamp bound differently at low case counts; with the
        # clamp gone, the same identity now covers every N >= 2 uniformly.
        from django.test import override_settings

        agent = self._retell_originator_agent(organization, workspace)
        with override_settings(HOSTED_RUNNER_LEASED_ROOM_REUSE=True):
            job, _ = self._build_multi(
                organization, workspace, simulator_agent, agent, 2
            )
        assert job["voice"]["params"]["max_seconds"] == 1800.0
        self._assert_leased_deadline_identity(job, 2)

    def test_single_scenario_leased_budget_untouched_at_defaults(
        self, organization, workspace, simulator_agent
    ):
        # The single-case drain allowance lives in cleanup_timeout, not
        # max_seconds — unaffected by D15 either way.
        from django.test import override_settings

        from simulate.services.hosted_runner import child_run_seconds

        agent = self._retell_originator_agent(organization, workspace)
        with override_settings(HOSTED_RUNNER_LEASED_ROOM_REUSE=False):
            job, _ = self._build(organization, workspace, simulator_agent, agent)
        params = job["voice"]["params"]
        leased_overhead = 300.0

        assert params["max_seconds"] == 1800.0
        assert params["cleanup_timeout"] == 90.0
        run_seconds = child_run_seconds(params)
        need = params["max_seconds"] + leased_overhead + 30
        assert need == 2130.0
        assert run_seconds == 2130.0
        assert run_seconds == need

    def test_single_scenario_leased_budget_at_55_minute_ceiling(
        self, organization, workspace, simulator_agent
    ):
        # D15: the single-case clamp that used to cap max_seconds at 3180s is
        # gone — the full 55-minute ceiling (3300s) now passes through.
        from django.test import override_settings

        from simulate.services.hosted_runner import child_run_seconds

        simulator_agent.max_call_duration_in_minutes = 55
        simulator_agent.save(update_fields=["max_call_duration_in_minutes"])
        agent = self._retell_originator_agent(organization, workspace)
        with override_settings(HOSTED_RUNNER_LEASED_ROOM_REUSE=False):
            job, _ = self._build(organization, workspace, simulator_agent, agent)
        params = job["voice"]["params"]
        leased_overhead = 300.0

        assert params["max_seconds"] == 3300.0
        assert params["cleanup_timeout"] == 90.0
        run_seconds = child_run_seconds(params)
        need = params["max_seconds"] + leased_overhead + 30
        assert need == 3630.0
        assert run_seconds == 3630.0
        assert run_seconds == need

    def test_sip_outbound_multi_scenario_budget_untouched_n7(
        self, organization, workspace, simulator_agent
    ):
        from django.test import override_settings

        agent = self._voice_agent(
            organization,
            workspace,
            provider="livekit",
            phone="+15551230000",
            inbound=True,
        )
        with override_settings(
            HOSTED_RUNNER_LEASED_ROOM_REUSE=True,
            LIVEKIT_OUTBOUND_TRUNK_ID="ST_trunk",
            PSTN_CALLER_NUMBER="+15550009999",
        ):
            job, _ = self._build_multi(
                organization, workspace, simulator_agent, agent, 7
            )
        assert job["voice"]["agent_definition"]["transport"]["kind"] == "sip_outbound"
        # sip_outbound is untouched by D12 — today's value, unshortened.
        assert job["voice"]["params"]["max_seconds"] == 1800.0

    def test_no_originator_sip_inbound_budget_untouched_n7(
        self, organization, workspace, simulator_agent
    ):
        from django.test import override_settings

        agent = self._voice_agent(
            organization,
            workspace,
            provider="livekit",
            phone="+15551230000",
            inbound=False,
        )
        with override_settings(HOSTED_RUNNER_LEASED_ROOM_REUSE=True):
            job, _ = self._build_multi(
                organization, workspace, simulator_agent, agent, 7
            )
        transport = job["voice"]["agent_definition"]["transport"]
        assert transport["kind"] == "sip_inbound"
        assert "inbound_call_originator" not in transport
        # No-originator sip_inbound is untouched by D12 — today's value.
        assert job["voice"]["params"]["max_seconds"] == 1800.0


class TestHostedRunnerActivityHelpers:
    def test_default_voice_simulator_uses_openai(self, monkeypatch, settings):
        from simulate.services.hosted_runner import _voice_simulator_config

        settings.SIMULATOR_LLM_PROVIDER = ""
        settings.SIMULATOR_LLM_MODEL = ""
        monkeypatch.delenv("SIMULATOR_LLM_PROVIDER", raising=False)
        monkeypatch.delenv("SIMULATOR_LLM_MODEL", raising=False)

        simulator = _voice_simulator_config()

        assert simulator["llm"] == {
            "provider": "openai",
            "model": "gpt-4.1",
        }

    def test_voice_simulator_uses_single_dataset_language(self, settings):
        from simulate.services.hosted_runner import _voice_simulator_config

        settings.SIMULATOR_STT_LANGUAGE = ""
        settings.SIMULATOR_TTS_PROVIDER = ""
        settings.SIMULATOR_TTS_MODEL = ""

        simulator = _voice_simulator_config([{"persona": {"language": "arabic"}}])

        assert simulator["stt"]["language"] == "ar"
        # TTS is routed by language: a non-English persona uses the multilingual
        # streaming Gemini voice (Deepgram Aura-2 is English-only), so it's spoken
        # natively rather than as English gibberish.
        assert simulator["tts"]["provider"] == "gemini"
        assert simulator["tts"]["model"] == "gemini-3.1-flash-tts-preview"

        english = _voice_simulator_config([{"persona": {"language": "english"}}])
        assert english["tts"]["provider"] == "deepgram"

    def test_voice_conversation_direction_follows_agent_call_direction(self):
        from simulate.services.hosted_runner import _voice_params

        # Inbound target receives the call → the simulator (caller) opens
        # (simulator_first), matching native ee/voice first_message_mode.
        assert (
            _voice_params("webrtc", inbound=True)["conversation_direction"]
            == "simulator_first"
        )
        # Outbound target places the call → the target opens (agent_first).
        assert (
            _voice_params("webrtc", inbound=False)["conversation_direction"]
            == "agent_first"
        )
        # Retell has no per-call first-message control → pinned to simulator_first
        # in both directions (its outbound/target-opens case is unsupported).
        assert (
            _voice_params("retell_webcall", inbound=True)["conversation_direction"]
            == "simulator_first"
        )
        assert (
            _voice_params("retell_webcall", inbound=False)["conversation_direction"]
            == "simulator_first"
        )

    @pytest.mark.parametrize("case_count", [1, 3, 5, 20, 40])
    @pytest.mark.parametrize("max_call_minutes", [30, 2, 55])
    def test_leased_budget_never_shrinks_or_refuses(self, case_count, max_call_minutes):
        # D15: the builder no longer clamps against a parent cap or refuses a
        # row count — max_seconds always equals the call ceiling, and
        # cleanup_timeout/run_seconds follow the child's own deadline
        # identity, however large. 55 min is where the deleted max_seconds
        # clamp used to bind (3240s), so this sweep now catches that
        # regression without a DB-backed test.
        from simulate.services.hosted_runner import _voice_params, child_run_seconds

        params = _voice_params(
            "sip_inbound",
            inbound=False,
            case_count=case_count,
            max_concurrency=5,
            max_call_minutes=max_call_minutes,
            leased_room=True,
        )
        ceiling = float(max(120, max_call_minutes * 60))
        assert params["max_seconds"] == ceiling

        connect_timeout = 60.0
        readiness_timeout = 120.0
        base_cleanup = 30.0
        fixed_overhead = connect_timeout + readiness_timeout + 60.0
        leased_overhead = fixed_overhead + connect_timeout
        if case_count > 1:
            expected_cleanup = (
                (case_count - 1) * ceiling
                + case_count * leased_overhead
                - fixed_overhead
                + base_cleanup
            )
        else:
            # N = 1 form: the single-case drain allowance, no accumulation.
            expected_cleanup = base_cleanup + connect_timeout
        assert params["cleanup_timeout"] == expected_cleanup

        run_seconds = child_run_seconds(params)
        assert run_seconds == case_count * (ceiling + leased_overhead) + base_cleanup

    def test_child_run_seconds_rounds_a_fractional_sum_up(self):
        # A fractional term (cleanup_timeout=30.5) must round the parent's
        # derived deadline UP, never down — truncation could leave the
        # child's own budget a fraction of a second longer than what the
        # parent is willing to wait for it.
        from simulate.services.hosted_runner import child_run_seconds

        params = {
            "max_seconds": 1800.0,
            "connect_timeout": 60.0,
            "readiness_timeout": 120.0,
            "cleanup_timeout": 30.5,
        }
        # kit sum = 1800 + 60 + 120 + 30.5 + 60 = 2070.5
        assert child_run_seconds(params) == 2071

    def test_non_leased_web_budget_unclamped_n1_n2_matched_n7_grows(self):
        # N=1/N=2 never hit HEAD's cap, so both fields are byte-identical to
        # before; N=7 only changes cleanup_timeout — HEAD clamped it to 1470
        # (0.9 * the old 65-minute constant, minus overhead), the unclamped
        # identity now gives 2100. max_seconds is unaffected at every N since
        # a single case never needed the deleted clamp either.
        from simulate.services.hosted_runner import _voice_params

        for case_count, expected_cleanup in ((1, 30.0), (2, 30.0), (7, 2100.0)):
            params = _voice_params(
                "webrtc",
                inbound=True,
                case_count=case_count,
                max_concurrency=5,
                max_call_minutes=30,
                leased_room=False,
            )
            assert params["max_seconds"] == 1800.0
            assert params["cleanup_timeout"] == expected_cleanup

    def test_no_originator_sip_inbound_budget_unclamped_n1_matched_n7_grows(self):
        # Telephony without a leased room serialises every case
        # (effective_concurrency pinned to 1), so N=7's unclamped
        # cleanup_timeout (12450) grows far past HEAD's capped 1470 — the
        # child now gets the whole real drain instead of a truncated one.
        from simulate.services.hosted_runner import _voice_params

        for case_count, expected_cleanup in ((1, 30.0), (7, 12450.0)):
            params = _voice_params(
                "sip_inbound",
                inbound=False,
                case_count=case_count,
                max_concurrency=5,
                max_call_minutes=30,
                leased_room=False,
            )
            assert params["max_seconds"] == 1800.0
            assert params["cleanup_timeout"] == expected_cleanup

    def test_resolve_agent_inbound_prefers_version_snapshot(self):
        """Once the pinned version has a snapshot dict, it alone decides
        inbound/outbound — same whole-dict precedence as ``_agent_field``. A
        key it lacks means "unset" (defaults to inbound=True); the
        ``AgentDefinition.inbound`` column is NOT consulted in that case, only
        when the agent has no version at all (no snapshot dict). This stops a
        later definition-level toggle from reaching back and flipping the call
        direction of an older pinned version that predates it."""
        from types import SimpleNamespace

        from simulate.services.hosted_runner import _resolve_agent_inbound

        agent_def_outbound = SimpleNamespace(inbound=False)
        agent_def_inbound = SimpleNamespace(inbound=True)

        # Snapshot True overrides a stale outbound column (the bug we hit).
        version = SimpleNamespace(configuration_snapshot={"inbound": True})
        assert _resolve_agent_inbound(version, agent_def_outbound) is True

        # String "false" must not be truthy (bool("false") is True).
        version = SimpleNamespace(configuration_snapshot={"inbound": "false"})
        assert _resolve_agent_inbound(version, agent_def_inbound) is False
        version = SimpleNamespace(configuration_snapshot={"inbound": "true"})
        assert _resolve_agent_inbound(version, agent_def_outbound) is True

        # Snapshot missing the key → default inbound=True; the column is NOT
        # consulted (a versioned run must not see a later column edit).
        version = SimpleNamespace(configuration_snapshot={})
        assert _resolve_agent_inbound(version, agent_def_outbound) is True
        assert _resolve_agent_inbound(version, agent_def_inbound) is True

        # No version at all → column fallback (default inbound when absent).
        assert _resolve_agent_inbound(None, agent_def_outbound) is False
        assert _resolve_agent_inbound(None, SimpleNamespace()) is True

    """The DID pool is touched only for sip_inbound (mirrors _needs_phone)."""

    def test_inject_did_slot_only_for_sip_inbound(self):
        from simulate.temporal.activities.hosted_runner import (
            _child_environment,
            _inject_did_slot,
        )

        slot = {
            "did": "+15557654321",
            "dispatch_rule_name": "rule-1",
            "room_name": "sim-slot-01",
            "slot_id": "s1",
        }
        inbound = {
            "voice": {
                "agent_definition": {"transport": {"kind": "sip_inbound"}},
                "livekit_runtime": {"room_name": "hosted-{test_case_id}"},
                "params": {},
                "scenario": {"dataset": [{"persona": {"name": "Caller"}}]},
            },
            "metadata": {},
        }
        _inject_did_slot(inbound, slot)
        t = inbound["voice"]["agent_definition"]["transport"]
        assert t["dispatch_rule_name"] == "rule-1"
        assert inbound["metadata"]["leased_did"] == "+15557654321"
        # Never in voice.params — the SDK splats params as kwargs and has no
        # inbound_did parameter, so a stray key there raises TypeError.
        assert "inbound_did" not in inbound["voice"]["params"]
        assert _child_environment(inbound)["LIVEKIT_INBOUND_DID"] == "+15557654321"
        assert inbound["voice"]["livekit_runtime"] == {
            "room_name": "sim-slot-01",
            "room_name_verbatim": True,
        }

        outbound = {
            "voice": {
                "agent_definition": {
                    "transport": {"kind": "sip_outbound", "sip_call_to": "+1"}
                }
            }
        }
        _inject_did_slot(outbound, slot)
        # sip_outbound dials the target directly; never consumes a leased DID.
        assert (
            "dispatch_rule_name"
            not in (outbound["voice"]["agent_definition"]["transport"])
        )

    def test_inject_did_slot_pins_multi_row_originator_job(self):
        # The multi-scenario reuse population (an originator job): pinned
        # identically to the single-row case.
        from simulate.temporal.activities.hosted_runner import _inject_did_slot

        slot = {
            "did": "+15557654321",
            "dispatch_rule_name": "rule-1",
            "room_name": "sim-slot-01",
            "slot_id": "s1",
        }
        job = {
            "voice": {
                "agent_definition": {
                    "transport": {
                        "kind": "sip_inbound",
                        "inbound_call_originator": "retell",
                    }
                },
                "livekit_runtime": {"room_name": "hosted-{test_case_id}"},
                "params": {},
                "scenario": {
                    "dataset": [
                        {"persona": {"name": "A"}},
                        {"persona": {"name": "B"}},
                        {"persona": {"name": "C"}},
                    ]
                },
            },
            "metadata": {},
        }
        _inject_did_slot(job, slot)
        assert job["voice"]["livekit_runtime"] == {
            "room_name": "sim-slot-01",
            "room_name_verbatim": True,
        }

    def test_inject_did_slot_leaves_multi_row_no_originator_job_templated(self):
        # A multi-row job without an originator is not the reuse population —
        # the guard and D12 budget do not cover it, so it keeps the templated
        # runtime exactly as today.
        from simulate.temporal.activities.hosted_runner import _inject_did_slot

        slot = {
            "did": "+15557654321",
            "dispatch_rule_name": "rule-1",
            "room_name": "sim-slot-01",
            "slot_id": "s1",
        }
        job = {
            "voice": {
                "agent_definition": {"transport": {"kind": "sip_inbound"}},
                "livekit_runtime": {"room_name": "hosted-{test_case_id}"},
                "params": {},
                "scenario": {
                    "dataset": [
                        {"persona": {"name": "A"}},
                        {"persona": {"name": "B"}},
                        {"persona": {"name": "C"}},
                    ]
                },
            },
            "metadata": {},
        }
        _inject_did_slot(job, slot)
        assert job["voice"]["livekit_runtime"] == {"room_name": "hosted-{test_case_id}"}

    def test_inject_did_slot_pins_single_row_no_originator_job(self):
        # Today's behaviour: a single-row job is pinned even without an
        # originator.
        from simulate.temporal.activities.hosted_runner import _inject_did_slot

        slot = {
            "did": "+15557654321",
            "dispatch_rule_name": "rule-1",
            "room_name": "sim-slot-01",
            "slot_id": "s1",
        }
        job = {
            "voice": {
                "agent_definition": {"transport": {"kind": "sip_inbound"}},
                "livekit_runtime": {"room_name": "hosted-{test_case_id}"},
                "params": {},
                "scenario": {"dataset": [{"persona": {"name": "A"}}]},
            },
            "metadata": {},
        }
        _inject_did_slot(job, slot)
        assert job["voice"]["livekit_runtime"] == {
            "room_name": "sim-slot-01",
            "room_name_verbatim": True,
        }

    def test_inject_did_slot_treats_whitespace_number_as_absent(self):
        # Pin _inject_did_slot's OWN .strip() independently of the
        # guard's — a whitespace-only did in the leased slot must never reach
        # metadata.leased_did, even though the guard downstream would also
        # strip it if it somehow did.
        from simulate.temporal.activities.hosted_runner import _inject_did_slot

        job = {
            "voice": {
                "agent_definition": {"transport": {"kind": "sip_inbound"}},
                "params": {},
            },
            "metadata": {},
        }
        _inject_did_slot(job, {"slot_id": "s1", "dispatch_rule_name": "r1", "did": " "})

        assert "leased_did" not in job.get("metadata", {})

    def test_inject_did_slot_treats_whitespace_room_name_as_absent(self):
        # A whitespace-only room_name must not be pinned — the templated
        # runtime is left untouched exactly as when room_name is absent.
        from simulate.temporal.activities.hosted_runner import _inject_did_slot

        job = {
            "voice": {
                "agent_definition": {"transport": {"kind": "sip_inbound"}},
                "livekit_runtime": {"room_name": "hosted-{test_case_id}"},
                "params": {},
                "scenario": {"dataset": [{"persona": {"name": "A"}}]},
            },
            "metadata": {},
        }
        _inject_did_slot(
            job,
            {"slot_id": "s1", "dispatch_rule_name": "r1", "room_name": "   "},
        )

        assert job["voice"]["livekit_runtime"] == {"room_name": "hosted-{test_case_id}"}

    def test_inject_did_slot_strips_pinned_room_name(self):
        # The pinned value itself must be stripped, not just checked for
        # blankness — an unstripped pin will not match the pool rule's
        # destination downstream.
        from simulate.temporal.activities.hosted_runner import _inject_did_slot

        job = {
            "voice": {
                "agent_definition": {"transport": {"kind": "sip_inbound"}},
                "livekit_runtime": {"room_name": "hosted-{test_case_id}"},
                "params": {},
                "scenario": {"dataset": [{"persona": {"name": "A"}}]},
            },
            "metadata": {},
        }
        slot = {
            "slot_id": "s1",
            "dispatch_rule_name": "r1",
            "room_name": "  sim-slot-01 ",
        }
        _inject_did_slot(job, slot)

        assert job["voice"]["livekit_runtime"] == {
            "room_name": "sim-slot-01",
            "room_name_verbatim": True,
        }

    def test_inject_did_slot_tolerates_explicit_null_transport(self):
        # An explicit "transport": null must not raise AttributeError — the
        # helper returns without pinning or raising, matching the activity's
        # own "or {}" guard.
        from simulate.temporal.activities.hosted_runner import _inject_did_slot

        job = {
            "voice": {
                "agent_definition": {"transport": None},
                "params": {},
            },
            "metadata": {},
        }
        _inject_did_slot(
            job, {"slot_id": "s1", "dispatch_rule_name": "r1", "did": "+15557654321"}
        )

        assert "leased_did" not in job.get("metadata", {})
        assert "livekit_runtime" not in job["voice"]

    def test_inject_did_slot_tolerates_explicit_null_scenario(self):
        # C5a: an explicit "scenario": null must not raise — the room-name
        # sizing check falls back to an empty dataset instead.
        from simulate.temporal.activities.hosted_runner import _inject_did_slot

        job = {
            "voice": {
                "agent_definition": {"transport": {"kind": "sip_inbound"}},
                "params": {},
                "scenario": None,
            },
            "metadata": {},
        }
        _inject_did_slot(job, {"slot_id": "s1", "room_name": "sim-slot-01"})

        # No dataset to size against, so the single-row room pin never fires.
        assert "livekit_runtime" not in job["voice"]

    def test_inject_did_slot_tolerates_explicit_null_dataset(self):
        # C5a: an explicit "dataset": null (scenario present) must not raise.
        from simulate.temporal.activities.hosted_runner import _inject_did_slot

        job = {
            "voice": {
                "agent_definition": {"transport": {"kind": "sip_inbound"}},
                "params": {},
                "scenario": {"dataset": None},
            },
            "metadata": {},
        }
        _inject_did_slot(job, {"slot_id": "s1", "room_name": "sim-slot-01"})

        assert "livekit_runtime" not in job["voice"]

    def test_inject_did_slot_tolerates_explicit_null_metadata(self):
        # C5a: an explicit "metadata": null must not raise — setdefault only
        # fires when the key is absent, not when it is present but None.
        from simulate.temporal.activities.hosted_runner import _inject_did_slot

        job = {
            "voice": {
                "agent_definition": {"transport": {"kind": "sip_inbound"}},
                "params": {},
            },
            "metadata": None,
        }
        _inject_did_slot(job, {"slot_id": "s1", "did": "+15557654321"})

        assert job["metadata"]["leased_did"] == "+15557654321"

    def test_inject_did_slot_strips_pinned_did(self):
        # The stored DID must be the stripped value, not just checked for
        # blankness — the same fix already applied to room_name — or a
        # padded number reaches LIVEKIT_INBOUND_DID and metadata.leased_did
        # verbatim.
        from simulate.temporal.activities.hosted_runner import _inject_did_slot

        job = {
            "voice": {
                "agent_definition": {"transport": {"kind": "sip_inbound"}},
                "params": {},
            },
            "metadata": {},
        }
        _inject_did_slot(
            job,
            {"slot_id": "s1", "dispatch_rule_name": "r1", "did": " +15557654321 "},
        )

        assert job["metadata"]["leased_did"] == "+15557654321"

    def test_acquire_did_slot_none_without_script(self, monkeypatch):
        import asyncio

        from simulate.temporal.activities.hosted_runner import _acquire_did_slot

        monkeypatch.delenv("ALK_SIM_SLOT_LEASE_SCRIPT", raising=False)
        assert asyncio.run(_acquire_did_slot("job-1", 900)) is None

    def test_build_runner_job_fills_run_seconds_for_voice(self, monkeypatch):
        # D15: BuildRunnerJobOutput.run_seconds comes from the job's own
        # voice.params via child_run_seconds, not a shared parent cap.
        # No DB touch: build_start_runner_job and close_old_connections are
        # both stubbed, so _run_db's executor thread issues no query.
        import asyncio

        from simulate.temporal.activities import hosted_runner as hr
        from simulate.temporal.types.hosted_runner import BuildRunnerJobInput

        fake_job = {
            "job_id": "job-1",
            "mode": "voice_webrtc",
            "metadata": {"run_id": "run-1"},
            "voice": {
                "params": {
                    "max_seconds": 1800.0,
                    "connect_timeout": 60.0,
                    "readiness_timeout": 120.0,
                    "cleanup_timeout": 90.0,
                }
            },
        }
        monkeypatch.setattr(hr, "close_old_connections", lambda: None)
        monkeypatch.setattr(
            "simulate.services.hosted_runner.build_start_runner_job",
            lambda **kwargs: fake_job,
        )

        inp = BuildRunnerJobInput(
            test_execution_id="te-1",
            run_test_id="rt-1",
            scenario_ids=["s-1"],
            mode="voice_webrtc",
        )
        out = asyncio.run(hr.build_runner_job(inp))

        assert out.run_seconds == 2130  # 1800 + 60 + 120 + 90 + 60

    def test_build_runner_job_run_seconds_for_chat_uses_its_own_constant(
        self, monkeypatch
    ):
        # Chat carries no voice params, so it can't derive a child deadline
        # (D15 rule 7) — it falls back to the chat runner's own fixed budget.
        import asyncio

        from simulate.temporal.activities import hosted_runner as hr
        from simulate.temporal.constants import HOSTED_RUNNER_CHAT_TIMEOUT_SECONDS
        from simulate.temporal.types.hosted_runner import BuildRunnerJobInput

        fake_job = {
            "job_id": "job-2",
            "mode": "chat",
            "metadata": {"run_id": "run-2"},
            "spec": {},
        }
        monkeypatch.setattr(hr, "close_old_connections", lambda: None)
        monkeypatch.setattr(
            "simulate.services.hosted_runner.build_start_runner_job",
            lambda **kwargs: fake_job,
        )

        inp = BuildRunnerJobInput(
            test_execution_id="te-2",
            run_test_id="rt-2",
            scenario_ids=["s-2"],
            mode="chat",
        )
        out = asyncio.run(hr.build_runner_job(inp))

        assert out.run_seconds == HOSTED_RUNNER_CHAT_TIMEOUT_SECONDS

    def test_chat_mode_constant_matches_the_service_module_literal(self):
        # The workflow sandbox can't import the Django-backed services
        # module, so the "chat" literal is duplicated there on purpose
        # (constants.py:81-83) — pin the two copies together so they can't
        # drift apart silently.
        from simulate.services.hosted_runner import _CHAT_MODE
        from simulate.temporal.constants import HOSTED_RUNNER_CHAT_MODE

        assert HOSTED_RUNNER_CHAT_MODE == _CHAT_MODE

    def test_build_runner_job_voice_params_none_does_not_crash(self, monkeypatch):
        # child_run_seconds indexes into params via .get(); an explicit
        # "params": null on a voice job must fall back to {} (the kit's own
        # floor) instead of raising AttributeError/TypeError.
        import asyncio

        from simulate.temporal.activities import hosted_runner as hr
        from simulate.temporal.types.hosted_runner import BuildRunnerJobInput

        fake_job = {
            "job_id": "job-3",
            "mode": "voice_webrtc",
            "metadata": {"run_id": "run-3"},
            "voice": {"params": None},
        }
        monkeypatch.setattr(hr, "close_old_connections", lambda: None)
        monkeypatch.setattr(
            "simulate.services.hosted_runner.build_start_runner_job",
            lambda **kwargs: fake_job,
        )

        inp = BuildRunnerJobInput(
            test_execution_id="te-3",
            run_test_id="rt-3",
            scenario_ids=["s-3"],
            mode="voice_webrtc",
        )
        out = asyncio.run(hr.build_runner_job(inp))

        # 300 is child_run_seconds' own floor for an empty params dict.
        assert out.run_seconds == 300

    def test_run_seconds_decodes_to_none_without_the_field(self):
        # Pins the absent default: a payload recorded before this field
        # existed must still decode, yielding None rather than raising
        # TypeError — the loud-failure guard lives at the workflow/activity
        # call sites, not in the dataclass shape.
        from simulate.temporal.types.hosted_runner import (
            BuildRunnerJobOutput,
            RunHostedJobInput,
        )

        build_out = BuildRunnerJobOutput(
            job_id="j1", run_id="r1", mode="voice_sip", job_json="{}"
        )
        run_in = RunHostedJobInput(
            job_id="j1", run_id="r1", mode="voice_sip", job_json="{}"
        )
        assert build_out.run_seconds is None
        assert run_in.run_seconds is None

    def test_workflow_command_sequence_is_independent_of_run_seconds(self):
        # C9/D15: an older worker's replayed history already has
        # run_hosted_sdk_job scheduled next, so nothing between the build
        # and run activity calls may raise or branch which command gets
        # emitted on run_seconds — only the timeout value may vary.
        import ast
        import pathlib

        import simulate.temporal.workflows.simulation_runner_workflow as wf

        wf_path = pathlib.Path(wf.__file__)
        tree = ast.parse(wf_path.read_text(), filename=str(wf_path))

        def activity_name(node):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "execute_activity"
                and node.args
                and isinstance(node.args[0], ast.Constant)
            ):
                return node.args[0].value
            return None

        run_method = next(
            (
                n
                for n in ast.walk(tree)
                if isinstance(n, ast.AsyncFunctionDef) and n.name == "run"
            ),
            None,
        )
        assert run_method is not None, "SimulationRunnerWorkflow.run not found"

        try_node = next(n for n in ast.walk(run_method) if isinstance(n, ast.Try))
        body = try_node.body

        def index_containing(name):
            for i, stmt in enumerate(body):
                if any(activity_name(n) == name for n in ast.walk(stmt)):
                    return i
            return None

        build_idx = index_containing("build_runner_job")
        run_idx = index_containing("run_hosted_sdk_job")
        finalize_idx = index_containing("finalize_hosted_execution")
        assert build_idx is not None, "build_runner_job call not found"
        assert run_idx is not None, "run_hosted_sdk_job call not found"
        assert finalize_idx is not None, "finalize_hosted_execution call not found"
        assert build_idx < run_idx < finalize_idx, (
            "run() must call build, then run, then finalize in that order"
        )

        calls_before_finalize = [
            name
            for i, stmt in enumerate(body)
            if i < finalize_idx
            for name in (activity_name(n) for n in ast.walk(stmt))
            if name is not None
        ]
        assert calls_before_finalize == ["build_runner_job", "run_hosted_sdk_job"], (
            "exactly build_runner_job then run_hosted_sdk_job may execute "
            "before the finalize path"
        )

        def references_run_seconds(test):
            return any(
                (isinstance(n, ast.Attribute) and n.attr == "run_seconds")
                or (isinstance(n, ast.Name) and n.id == "run_seconds")
                for n in ast.walk(test)
            )

        between = body[build_idx + 1 : run_idx]

        raises = [
            n for stmt in between for n in ast.walk(stmt) if isinstance(n, ast.Raise)
        ]
        assert raises == [], (
            "no Raise may sit between build_runner_job and run_hosted_sdk_job"
        )

        run_seconds_ifs = [
            n
            for stmt in between
            for n in ast.walk(stmt)
            if isinstance(n, ast.If) and references_run_seconds(n.test)
        ]
        assert len(run_seconds_ifs) == 1, (
            "the only If testing run_seconds between build and run must be "
            "the timeout assignment's own branch"
        )

    def test_workflow_timeout_derives_from_run_seconds_not_a_shared_cap(self):
        # AST check (C9/D15): this is the ONLY guard on the rule, so it must
        # pin the expression actually feeding run_hosted_sdk_job's own
        # timeout keyword byte-for-byte — an expression that merely
        # *contains* the derivation lets a literal cap or a unit swap slip
        # through with the suite green.
        import ast
        import pathlib

        import simulate
        import simulate.temporal.workflows.simulation_runner_workflow as wf

        wf_path = pathlib.Path(wf.__file__)
        tree = ast.parse(wf_path.read_text(), filename=str(wf_path))

        def is_target_call(node):
            return (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "execute_activity"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and node.args[0].value == "run_hosted_sdk_job"
            )

        call = owner = None
        for func in ast.walk(tree):
            if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for node in ast.walk(func):
                if is_target_call(node):
                    call, owner = node, func
                    break
            if call is not None:
                break
        assert call is not None, "run_hosted_sdk_job execute_activity call not found"

        timeout_kw = next(
            kw for kw in call.keywords if kw.arg == "start_to_close_timeout"
        )
        expr = timeout_kw.value
        assert isinstance(expr, ast.Name), (
            "start_to_close_timeout must be fed by a local, not inlined"
        )
        timeout_name = expr.id

        def is_build_call(node):
            return (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "execute_activity"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and node.args[0].value == "build_runner_job"
            )

        # Bind the build result's own local name rather than hardcoding
        # "job", so a rename can't silently blind the checks below.
        build_assign = next(
            n
            for n in ast.walk(owner)
            if isinstance(n, ast.Assign)
            and any(is_build_call(x) for x in ast.walk(n.value))
        )
        assert len(build_assign.targets) == 1 and isinstance(
            build_assign.targets[0], ast.Name
        ), "build_runner_job result must be assigned to a single local"
        job_name = build_assign.targets[0].id

        # Locate the specific `if job.mode == HOSTED_RUNNER_CHAT_MODE` (or
        # `!=`) node, rather than trusting which physical branch reads
        # "if" vs "else" — the comparison operator says which arm is chat.
        def is_chat_mode_test(test):
            return (
                isinstance(test, ast.Compare)
                and isinstance(test.left, ast.Attribute)
                and test.left.attr == "mode"
                and isinstance(test.left.value, ast.Name)
                and test.left.value.id == job_name
                and len(test.ops) == 1
                and isinstance(test.ops[0], (ast.Eq, ast.NotEq))
                and len(test.comparators) == 1
                and isinstance(test.comparators[0], ast.Name)
                and test.comparators[0].id == "HOSTED_RUNNER_CHAT_MODE"
            )

        if_node = next(
            (
                n
                for n in ast.walk(owner)
                if isinstance(n, ast.If) and is_chat_mode_test(n.test)
            ),
            None,
        )
        assert if_node is not None, "no job.mode/HOSTED_RUNNER_CHAT_MODE if"

        is_eq = isinstance(if_node.test.ops[0], ast.Eq)
        chat_arm = if_node.body if is_eq else if_node.orelse
        other_top = if_node.orelse if is_eq else if_node.body
        assert chat_arm and other_top, "both if/else arms must be present"

        # The non-chat arm is a single nested If (an elif in source form):
        # a positive-budget branch and a placeholder branch, never a flat
        # unconditional assignment.
        assert len(other_top) == 1 and isinstance(other_top[0], ast.If), (
            "the non-chat arm must be a single nested If on run_seconds"
        )
        inner_if = other_top[0]

        def assigns_to(stmts, name):
            return [
                stmt.value
                for stmt in stmts
                if isinstance(stmt, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == name for t in stmt.targets)
            ]

        chat_assigns = assigns_to(chat_arm, timeout_name)
        positive_assigns = assigns_to(inner_if.body, timeout_name)
        placeholder_assigns = assigns_to(inner_if.orelse, timeout_name)
        assert len(chat_assigns) == 1, (
            f"chat arm must assign {timeout_name} exactly once"
        )
        assert len(positive_assigns) == 1, (
            f"the positive-budget branch must assign {timeout_name} once"
        )
        assert len(placeholder_assigns) == 1, (
            f"the placeholder branch must assign {timeout_name} exactly once"
        )

        def normalized_dump(node):
            # ast.dump ignores position info by default; re-parsing an
            # unparse of `node` gives a detached copy so renaming the job
            # local for comparison can't mutate the tree under test.
            copy = ast.parse(ast.unparse(node), mode="eval").body
            for n in ast.walk(copy):
                if isinstance(n, ast.Name) and n.id == job_name:
                    n.id = "job"
            return ast.dump(copy)

        def expr_dump(src):
            return ast.dump(ast.parse(src, mode="eval").body)

        expected_chat = expr_dump(
            "timedelta(seconds=HOSTED_RUNNER_CHAT_TIMEOUT_SECONDS)"
        )
        expected_voice = expr_dump(
            "timedelta(seconds=job.run_seconds + HOSTED_RUNNER_PARENT_SLACK_SECONDS)"
        )

        # Pins which arm runs, not just what each arm computes: the two
        # assertions above alone let a flipped comparison operator (or `<`
        # for `>`) send every voice run down the placeholder branch while
        # leaving both arm expressions untouched and the suite green.
        expected_predicate = expr_dump(
            "isinstance(job.run_seconds, (int, float)) and job.run_seconds > 0"
        )
        assert normalized_dump(inner_if.test) == expected_predicate, (
            "the elif predicate must be exactly isinstance(run_seconds, "
            "(int, float)) and run_seconds > 0"
        )

        assert normalized_dump(chat_assigns[0]) == expected_chat, (
            "chat arm must be exactly timedelta(seconds="
            "HOSTED_RUNNER_CHAT_TIMEOUT_SECONDS)"
        )
        assert normalized_dump(positive_assigns[0]) == expected_voice, (
            "positive-budget branch must be exactly timedelta(seconds="
            "run_seconds + HOSTED_RUNNER_PARENT_SLACK_SECONDS), not merely "
            "contain that sum"
        )
        assert normalized_dump(placeholder_assigns[0]) == expected_chat, (
            "placeholder branch must reuse the chat expression exactly, "
            "not a different constant"
        )

        # Exactly these three assignments to the name exist anywhere in the
        # function — catches a correct set of branches silently overwritten
        # by a later, unconditional reassignment of the same local.
        all_assigns = [
            stmt
            for stmt in ast.walk(owner)
            if isinstance(stmt, ast.Assign)
            and any(
                isinstance(t, ast.Name) and t.id == timeout_name for t in stmt.targets
            )
        ]
        assert len(all_assigns) == 3, (
            f"{timeout_name} must be assigned exactly once per branch and nowhere else"
        )

        def names_and_attrs(t):
            names = {n.id for n in ast.walk(t) if isinstance(n, ast.Name)}
            attrs = {n.attr for n in ast.walk(t) if isinstance(n, ast.Attribute)}
            return names | attrs

        simulate_root = pathlib.Path(simulate.__file__).parent
        offenders = [
            str(path)
            for path in simulate_root.rglob("*.py")
            if "HOSTED_RUNNER_MAX_DURATION_SECONDS"
            in names_and_attrs(ast.parse(path.read_text(), filename=str(path)))
        ]
        assert offenders == []

        # Two more keywords a mutant could drop silently: no run_seconds
        # means the activity always refuses a voice run; no heartbeat_timeout
        # means Temporal loses its only liveness signal on the long-lived
        # child. Neither is touched by the checks above.
        input_call = call.args[1]
        assert (
            isinstance(input_call, ast.Call)
            and isinstance(input_call.func, ast.Name)
            and input_call.func.id == "RunHostedJobInput"
        ), "run_hosted_sdk_job's 2nd arg must build RunHostedJobInput"

        run_seconds_kw = next(
            kw for kw in input_call.keywords if kw.arg == "run_seconds"
        )
        expected_run_seconds = expr_dump("job.run_seconds")
        assert normalized_dump(run_seconds_kw.value) == expected_run_seconds, (
            "run_seconds must be job.run_seconds verbatim"
        )

        heartbeat_kw = next(kw for kw in call.keywords if kw.arg == "heartbeat_timeout")
        expected_heartbeat = expr_dump("timedelta(seconds=60)")
        assert normalized_dump(heartbeat_kw.value) == expected_heartbeat, (
            "heartbeat_timeout must be exactly timedelta(seconds=60)"
        )

    def test_child_environment_maps_internal_sink_secret(self, monkeypatch):
        from simulate.temporal.activities.hosted_runner import _child_environment

        monkeypatch.setenv("INTERNAL_API_SECRET", "shared-service-secret")
        job = {
            "sink": {
                "secret_refs": {
                    "internal_api_secret": {
                        "manager": "env",
                        "key": "INTERNAL_API_SECRET",
                        "purpose": "internal_api_secret",
                    }
                }
            }
        }

        child_env = _child_environment(job)

        assert child_env["FI_INTERNAL_SUBMIT_SECRET"] == "shared-service-secret"

    def test_child_environment_denies_customer_provider_api_keys(self, monkeypatch):
        # Exact-key, not prefix: a chat job
        # declares no secret_env ref at all, so the ref-scoped scrub never
        # touches RETELL_API_KEY/VAPI_API_KEY — the exact-key deny in
        # _child_environment is what keeps them out of a job shape that has
        # no business seeing either provider's key.
        from simulate.temporal.activities.hosted_runner import _child_environment

        monkeypatch.setenv("RETELL_API_KEY", "platform-retell")
        monkeypatch.setenv("VAPI_API_KEY", "platform-vapi")

        job = {
            "spec": {"target": {"secret_refs": {}}},
            "sink": {"api_url": "http://localhost:8000"},
        }
        child_env = _child_environment(job)

        assert "RETELL_API_KEY" not in child_env
        assert "VAPI_API_KEY" not in child_env

    def test_child_environment_keeps_non_secret_provider_config(self, monkeypatch):
        # The deny is by exact key, not by RETELL_/VAPI_ prefix, so
        # non-secret config the SDK child reads straight from env — base
        # URLs, phone number ids, assistant ids — must still reach it.
        from simulate.temporal.activities.hosted_runner import _child_environment

        monkeypatch.setenv("VAPI_PHONE_NUMBER_ID", "+15551234567")
        monkeypatch.setenv("RETELL_API_BASE_URL", "https://api.retellai.com")

        job = {
            "spec": {"target": {"secret_refs": {}}},
            "sink": {"api_url": "http://localhost:8000"},
        }
        child_env = _child_environment(job)

        assert child_env["VAPI_PHONE_NUMBER_ID"] == "+15551234567"
        assert child_env["RETELL_API_BASE_URL"] == "https://api.retellai.com"

    def test_child_environment_still_inherits_livekit_system_key(self, monkeypatch):
        # Complement to the exact-key deny above: LiveKit's api_key_env is
        # None in _PROVIDER_PROFILES, so LIVEKIT_API_KEY never joins
        # _customer_provider_env_keys() — it is the platform's own runtime
        # var by design (C5) and must stay inherited when no ref scrubs it.
        from simulate.temporal.activities.hosted_runner import _child_environment

        monkeypatch.setenv("LIVEKIT_API_KEY", "system-lk-key")

        job = {
            "spec": {"target": {"secret_refs": {}}},
            "sink": {"api_url": "http://localhost:8000"},
        }
        child_env = _child_environment(job)

        assert child_env["LIVEKIT_API_KEY"] == "system-lk-key"

    def test_child_environment_pops_stale_inbound_did_without_lease(self, monkeypatch):
        # _child_environment only ever SETS LIVEKIT_INBOUND_DID when a DID
        # was leased; an inherited value from the worker process must not
        # leak into a job with no lease (defense-in-depth: the C5 guard
        # blocks every originator job without an injected DID before spawn,
        # but a non-originator sip_inbound job reaches the child regardless).
        from simulate.temporal.activities.hosted_runner import _child_environment

        monkeypatch.setenv("LIVEKIT_INBOUND_DID", "STALE")

        job_without_lease = {
            "spec": {"target": {"secret_refs": {}}},
            "sink": {"api_url": "http://localhost:8000"},
            "metadata": {},
        }
        assert "LIVEKIT_INBOUND_DID" not in _child_environment(job_without_lease)

        job_with_lease = {
            "spec": {"target": {"secret_refs": {}}},
            "sink": {"api_url": "http://localhost:8000"},
            "metadata": {"leased_did": "+15557654321"},
        }
        child_env = _child_environment(job_with_lease)
        assert child_env["LIVEKIT_INBOUND_DID"] == "+15557654321"

    def test_child_environment_denies_new_provider_key_via_profile_table(
        self, monkeypatch
    ):
        # Pin: a new provider profile with an api_key_env must join
        # the inheritance deny automatically, with no hand-maintained edit to
        # this module — the same guarantee _customer_provider_env_keys()
        # already gives the hoisted raise in _resolve_voice_secret_env.
        from simulate.services.hosted_runner import _PROVIDER_PROFILES
        from simulate.temporal.activities.hosted_runner import (
            _child_environment,
            _customer_provider_env_keys,
        )

        monkeypatch.setenv("ZZZ_API_KEY", "platform-zzz")
        _PROVIDER_PROFILES["zzz_synthetic"] = {
            "web_transport_kind": "zzz_websocket",
            "target_id_field": "assistant_id",
            "api_key_env": "ZZZ_API_KEY",
            "emits_web_evidence": True,
            "sip_inbound_originator": None,
            "sip_inbound_originator_fields": (),
        }
        _customer_provider_env_keys.cache_clear()
        try:
            job = {
                "spec": {"target": {"secret_refs": {}}},
                "sink": {"api_url": "http://localhost:8000"},
            }
            child_env = _child_environment(job)

            assert "ZZZ_API_KEY" not in child_env
        finally:
            del _PROVIDER_PROFILES["zzz_synthetic"]
            _customer_provider_env_keys.cache_clear()

    def test_waiting_for_child_slot_heartbeats(self, monkeypatch):
        import asyncio

        from simulate.temporal.activities import hosted_runner as hr

        semaphore = asyncio.Semaphore(0)
        heartbeats = []
        monkeypatch.setattr(hr, "_child_semaphore", semaphore)
        monkeypatch.setattr(hr, "_CHILD_SLOT_HEARTBEAT_SECONDS", 0.001)
        monkeypatch.setattr(hr.activity, "heartbeat", heartbeats.append)

        async def exercise():
            acquire = asyncio.create_task(hr._acquire_child_slot())
            await asyncio.sleep(0.01)
            semaphore.release()
            await acquire

        asyncio.run(exercise())

        assert "waiting_for_child_slot" in heartbeats

    def test_acquire_did_slot_uses_livekit_infra_contract(self, monkeypatch):
        import asyncio

        from simulate.temporal.activities import hosted_runner as hr
        from simulate.temporal.constants import HOSTED_RUNNER_PARENT_SLACK_SECONDS

        calls = []

        class LeaseProc:
            returncode = 0

            async def communicate(self):
                return (
                    b'{\n  "slot": "07",\n'
                    b'  "phone_number": "+15557654321",\n'
                    b'  "dispatch_rule_name": "sim-slot-07"\n}\n',
                    b"",
                )

        async def fake_exec(*args, **kwargs):
            calls.append(args)
            return LeaseProc()

        monkeypatch.setenv("ALK_SIM_SLOT_LEASE_SCRIPT", "/infra/lease_sim_slot.py")
        monkeypatch.setenv("ALK_RUNNER_PYTHON", "/venv/bin/python")
        monkeypatch.setattr(hr.asyncio, "create_subprocess_exec", fake_exec)

        slot = asyncio.run(hr._acquire_did_slot("job-123", 1200))

        assert calls == [
            (
                "/venv/bin/python",
                "/infra/lease_sim_slot.py",
                "acquire",
                "--run-id",
                "job-123",
                "--ttl",
                str(1200 + HOSTED_RUNNER_PARENT_SLACK_SECONDS),
            )
        ]
        assert slot["slot_id"] == "07"
        assert slot["did"] == "+15557654321"
        assert slot["run_id"] == "job-123"

    def test_acquire_did_slot_ttl_tracks_run_seconds(self, monkeypatch):
        # D15: the lease TTL follows the job's own run_seconds, not a shared
        # parent cap — check a second value past the one above.
        import asyncio

        from simulate.temporal.activities import hosted_runner as hr
        from simulate.temporal.constants import HOSTED_RUNNER_PARENT_SLACK_SECONDS

        calls = []

        class LeaseProc:
            returncode = 0

            async def communicate(self):
                return b'{\n  "slot": "01"\n}\n', b""

        async def fake_exec(*args, **kwargs):
            calls.append(args)
            return LeaseProc()

        monkeypatch.setenv("ALK_SIM_SLOT_LEASE_SCRIPT", "/infra/lease_sim_slot.py")
        monkeypatch.setenv("ALK_RUNNER_PYTHON", "/venv/bin/python")
        monkeypatch.setattr(hr.asyncio, "create_subprocess_exec", fake_exec)

        asyncio.run(hr._acquire_did_slot("job-456", 42030))

        assert calls[0][-1] == str(42030 + HOSTED_RUNNER_PARENT_SLACK_SECONDS)

    def test_release_did_slot_argv_carries_run_id_when_present(self, monkeypatch):
        import asyncio

        from simulate.temporal.activities import hosted_runner as hr

        calls = []

        class ReleaseProc:
            returncode = 0

            async def communicate(self):
                return (b'{"status": "ok"}', b"")

        async def fake_exec(*args, **kwargs):
            calls.append(args)
            return ReleaseProc()

        monkeypatch.setenv("ALK_SIM_SLOT_LEASE_SCRIPT", "/infra/lease_sim_slot.py")
        monkeypatch.setenv("ALK_RUNNER_PYTHON", "/venv/bin/python")
        monkeypatch.setattr(hr.asyncio, "create_subprocess_exec", fake_exec)

        asyncio.run(hr._release_did_slot({"slot_id": "07", "run_id": "job-123"}))
        assert calls == [
            (
                "/venv/bin/python",
                "/infra/lease_sim_slot.py",
                "release",
                "--slot",
                "07",
                "--run-id",
                "job-123",
            )
        ]

        calls.clear()
        asyncio.run(hr._release_did_slot({"slot_id": "07"}))
        assert calls == [
            (
                "/venv/bin/python",
                "/infra/lease_sim_slot.py",
                "release",
                "--slot",
                "07",
            )
        ]

    def test_release_did_slot_logs_warning_on_non_owner_error(self, monkeypatch):
        import asyncio

        from simulate.temporal.activities import hosted_runner as hr

        warnings = []

        class ReleaseProc:
            returncode = 1

            async def communicate(self):
                return (b'{"status": "error", "code": "not_owner"}', b"")

        async def fake_exec(*args, **kwargs):
            return ReleaseProc()

        monkeypatch.setenv("ALK_SIM_SLOT_LEASE_SCRIPT", "/infra/lease_sim_slot.py")
        monkeypatch.setenv("ALK_RUNNER_PYTHON", "/venv/bin/python")
        monkeypatch.setattr(hr.asyncio, "create_subprocess_exec", fake_exec)
        monkeypatch.setattr(hr.activity.logger, "warning", warnings.append)

        asyncio.run(hr._release_did_slot({"slot_id": "07", "run_id": "job-123"}))

        assert any("not_owner" in message for message in warnings)


class TestSimulationRunnerWorkflowReplay:
    # The AST tests above cannot see a command-sequence change reached
    # through indirection (e.g. a raise hoisted into a helper the walk
    # never visits): they read run()'s statement list, not its behavior.
    # A real replay against a recorded history is the only check that
    # would catch a hidden branch or an extra activity call regardless of
    # how it got there, so it is the guard of last resort on this
    # invariant, not a duplicate of the AST tests.

    @staticmethod
    def _payload(obj):
        import base64

        return {
            "metadata": {"encoding": base64.b64encode(b"json/plain").decode()},
            "data": base64.b64encode(json.dumps(obj).encode()).decode(),
        }

    @classmethod
    def _payloads(cls, *objs):
        return {"payloads": [cls._payload(o) for o in objs]}

    @classmethod
    def _build_history(cls, *, include_run_seconds):
        """A hand-built history: build_runner_job completes, then
        run_hosted_sdk_job is already scheduled/started/completed --
        matching what an older worker's history looks like whether or not
        the build result carried a run_seconds field."""
        task_queue = {"name": "simulation_runner", "kind": "TASK_QUEUE_KIND_NORMAL"}
        events = []

        def add(event_type, key, attrs):
            n = len(events) + 1
            events.append(
                {
                    "eventId": str(n),
                    "eventTime": "2026-09-04T00:00:00Z",
                    "eventType": event_type,
                    "taskId": "1",
                    key: attrs,
                }
            )
            return n

        started_input = {
            "test_execution_id": "te-1",
            "run_test_id": "rt-1",
            "org_id": "org-1",
            "scenario_ids": ["s-1"],
            "mode": "voice_sip",
            "simulator_id": None,
            "call_execution_ids": [],
        }
        add(
            "EVENT_TYPE_WORKFLOW_EXECUTION_STARTED",
            "workflowExecutionStartedEventAttributes",
            {
                "workflowType": {"name": "SimulationRunnerWorkflow"},
                "taskQueue": task_queue,
                "input": cls._payloads(started_input),
                "workflowTaskTimeout": "10s",
                "originalExecutionRunId": "run-1",
                "firstExecutionRunId": "run-1",
                "attempt": 1,
            },
        )

        def wft():
            s = add(
                "EVENT_TYPE_WORKFLOW_TASK_SCHEDULED",
                "workflowTaskScheduledEventAttributes",
                {
                    "taskQueue": task_queue,
                    "startToCloseTimeout": "10s",
                    "attempt": 1,
                },
            )
            st = add(
                "EVENT_TYPE_WORKFLOW_TASK_STARTED",
                "workflowTaskStartedEventAttributes",
                {
                    "scheduledEventId": str(s),
                    "identity": "old-worker",
                    "requestId": "r",
                },
            )
            return s, st

        def wft_complete(s, st):
            add(
                "EVENT_TYPE_WORKFLOW_TASK_COMPLETED",
                "workflowTaskCompletedEventAttributes",
                {
                    "scheduledEventId": str(s),
                    "startedEventId": str(st),
                    "identity": "old-worker",
                },
            )

        s, st = wft()
        wft_complete(s, st)

        build_scheduled = add(
            "EVENT_TYPE_ACTIVITY_TASK_SCHEDULED",
            "activityTaskScheduledEventAttributes",
            {
                "activityId": "1",
                "activityType": {"name": "build_runner_job"},
                "taskQueue": task_queue,
                "input": cls._payloads({}),
                "scheduleToCloseTimeout": "0s",
                "scheduleToStartTimeout": "0s",
                "startToCloseTimeout": "120s",
                "heartbeatTimeout": "0s",
                "workflowTaskCompletedEventId": "4",
            },
        )
        build_started = add(
            "EVENT_TYPE_ACTIVITY_TASK_STARTED",
            "activityTaskStartedEventAttributes",
            {
                "scheduledEventId": str(build_scheduled),
                "identity": "old-worker",
                "requestId": "r",
                "attempt": 1,
            },
        )
        # The two histories under test differ only in whether the recorded
        # build payload carries run_seconds.
        build_out = {
            "job_id": "j1",
            "job_json": "{}",
            "mode": "voice_sip",
            "run_id": "r1",
        }
        if include_run_seconds:
            build_out["run_seconds"] = 2130
        add(
            "EVENT_TYPE_ACTIVITY_TASK_COMPLETED",
            "activityTaskCompletedEventAttributes",
            {
                "scheduledEventId": str(build_scheduled),
                "startedEventId": str(build_started),
                "identity": "old-worker",
                "result": cls._payloads(build_out),
            },
        )

        s, st = wft()
        wft_complete(s, st)

        sdk_timeout = "2730s" if include_run_seconds else "3900s"
        sdk_scheduled = add(
            "EVENT_TYPE_ACTIVITY_TASK_SCHEDULED",
            "activityTaskScheduledEventAttributes",
            {
                "activityId": "2",
                "activityType": {"name": "run_hosted_sdk_job"},
                "taskQueue": task_queue,
                "input": cls._payloads({}),
                "scheduleToCloseTimeout": "0s",
                "scheduleToStartTimeout": "0s",
                "startToCloseTimeout": sdk_timeout,
                "heartbeatTimeout": "60s",
                "workflowTaskCompletedEventId": str(st + 1),
            },
        )
        sdk_started = add(
            "EVENT_TYPE_ACTIVITY_TASK_STARTED",
            "activityTaskStartedEventAttributes",
            {
                "scheduledEventId": str(sdk_scheduled),
                "identity": "old-worker",
                "requestId": "r",
                "attempt": 1,
            },
        )
        add(
            "EVENT_TYPE_ACTIVITY_TASK_COMPLETED",
            "activityTaskCompletedEventAttributes",
            {
                "scheduledEventId": str(sdk_scheduled),
                "startedEventId": str(sdk_started),
                "identity": "old-worker",
                "result": cls._payloads(
                    {
                        "phase": "completed",
                        "return_code": 0,
                        "report_hash": "h",
                        "submission_status": "submitted",
                        "detail": None,
                    }
                ),
            },
        )
        wft()
        return {"events": events}

    @classmethod
    def _replay(cls, *, include_run_seconds):
        import asyncio

        from temporalio.client import WorkflowHistory
        from temporalio.worker import Replayer, UnsandboxedWorkflowRunner

        from simulate.temporal.workflows.simulation_runner_workflow import (
            SimulationRunnerWorkflow,
        )

        history = cls._build_history(include_run_seconds=include_run_seconds)
        replayer = Replayer(
            workflows=[SimulationRunnerWorkflow],
            workflow_runner=UnsandboxedWorkflowRunner(),
        )

        async def _run():
            return await replayer.replay_workflow(
                WorkflowHistory.from_json("wf-1", json.dumps(history)),
                raise_on_replay_failure=False,
            )

        return asyncio.run(_run())

    def test_replay_pre_run_seconds_history_stays_deterministic(self):
        # A worker running the previous release recorded histories
        # with a four-field BuildRunnerJobOutput (no run_seconds) and
        # run_hosted_sdk_job already scheduled next; today's workflow must
        # still emit that exact same command against such a history.
        result = self._replay(include_run_seconds=False)
        assert result.replay_failure is None, result.replay_failure

    def test_replay_current_history_stays_deterministic(self):
        # Same shape, recorded by the current code: a five-field build
        # result with a positive run_seconds. Confirms the new field
        # itself introduces no divergence from what was already scheduled.
        result = self._replay(include_run_seconds=True)
        assert result.replay_failure is None, result.replay_failure


class _FakeStdout:
    def __init__(self, lines):
        self._lines = list(lines)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._lines:
            raise StopAsyncIteration
        return self._lines.pop(0)


class _FakeProc:
    def __init__(self, lines, return_code=0):
        self.stdout = _FakeStdout(lines)
        self.returncode = return_code
        self._rc = return_code

    async def wait(self):
        self.returncode = self._rc
        return self._rc

    def terminate(self):  # pragma: no cover - cancel path only
        pass


class TestRunHostedSdkJob:
    """Runtime-exercise the restructured run_hosted_sdk_job (try/finally + DID
    lease + secret env), spawning a fake child instead of the real SDK."""

    @pytest.fixture(autouse=True)
    def _skip_db_connection_hygiene(self, monkeypatch):
        """These tests stub the ORM lookups; the resolver's Django
        connection-hygiene call must not open a real DB connection."""
        from simulate.temporal.activities import hosted_runner as hr

        monkeypatch.setattr(hr, "close_old_connections", lambda: None)

    def _run(
        self,
        monkeypatch,
        *,
        mode,
        job,
        status_lines,
        acquire=None,
        released=None,
        run_seconds=900,
    ):
        import asyncio

        from simulate.temporal.activities import hosted_runner as hr
        from simulate.temporal.types.hosted_runner import RunHostedJobInput

        async def _fake_exec(*args, **kwargs):
            return _FakeProc([ln.encode() for ln in status_lines])

        monkeypatch.setattr(hr.asyncio, "create_subprocess_exec", _fake_exec)
        # Bare-calling the activity (no worker) => no Temporal activity context.
        monkeypatch.setattr(hr.activity, "heartbeat", lambda *a, **k: None)
        if acquire is not None:
            monkeypatch.setattr(hr, "_acquire_did_slot", acquire)
        if released is not None:
            monkeypatch.setattr(hr, "_release_did_slot", released)

        inp = RunHostedJobInput(
            job_id="job-x",
            run_id="run-x",
            mode=mode,
            job_json=json.dumps(job),
            run_seconds=run_seconds,
        )
        return asyncio.run(hr.run_hosted_sdk_job(inp))

    # Both voice modes share the same non-chat guard condition (`mode !=
    # HOSTED_RUNNER_CHAT_MODE`), so the refusal is pinned for each of them
    # rather than only the one mode that happened to be exercised first.
    @pytest.mark.parametrize("mode", ["voice_sip", "voice_webrtc"])
    @pytest.mark.parametrize("run_seconds", [None, 0, -5])
    def test_missing_run_seconds_raises_before_slot_or_lease(
        self, monkeypatch, run_seconds, mode
    ):
        # The workflow no longer guards this — only this activity does,
        # since it never replays. Refuse loudly before the child slot
        # semaphore, any DID lease, or the child is ever touched.
        import asyncio

        from temporalio.exceptions import ApplicationError

        from simulate.temporal.activities import hosted_runner as hr
        from simulate.temporal.types.hosted_runner import RunHostedJobInput

        slot_calls = []
        exec_calls = []
        lease_calls = []

        async def _acquire_slot():
            slot_calls.append(True)

        async def _fake_exec(*args, **kwargs):
            exec_calls.append(args)
            return _FakeProc([])

        async def _acquire_did(job_id, run_seconds):
            lease_calls.append(job_id)
            return None

        monkeypatch.setattr(hr, "_acquire_child_slot", _acquire_slot)
        monkeypatch.setattr(hr.asyncio, "create_subprocess_exec", _fake_exec)
        monkeypatch.setattr(hr.activity, "heartbeat", lambda *a, **k: None)
        monkeypatch.setattr(hr, "_acquire_did_slot", _acquire_did)

        inp = RunHostedJobInput(
            job_id="job-x",
            run_id="run-x",
            mode=mode,
            job_json=json.dumps({"mode": mode}),
            run_seconds=run_seconds,
        )

        with pytest.raises(ApplicationError) as excinfo:
            asyncio.run(hr.run_hosted_sdk_job(inp))
        assert excinfo.value.type == "hosted_run_budget_missing"
        assert excinfo.value.non_retryable
        assert slot_calls == []
        assert exec_calls == []
        assert lease_calls == []

    def test_chat_job_runs_and_completes(self, monkeypatch):
        job = {
            "mode": "chat",
            "spec": {"run_id": "run-x", "target": {"secret_refs": {}}},
            "sink": {"api_url": "http://localhost:8000"},
            "metadata": {"run_id": "run-x"},
        }
        lines = [
            '{"phase": "running", "job_id": "job-x"}',
            '{"phase": "completed", "job_id": "job-x", "report_hash": "h1", '
            '"submission_status": "submitted"}',
        ]
        out = self._run(monkeypatch, mode="chat", job=job, status_lines=lines)
        assert out.phase == "completed"
        assert out.return_code == 0
        assert out.submission_status == "submitted"

    def test_chat_mode_with_run_seconds_none_proceeds_to_spawn(self, monkeypatch):
        # Chat never derives a budget from run_seconds, so a replayed
        # pre-field payload (run_seconds=None) must not be blocked by the
        # voice-only guard above.
        job = {
            "mode": "chat",
            "spec": {"run_id": "run-x", "target": {"secret_refs": {}}},
            "sink": {"api_url": "http://localhost:8000"},
            "metadata": {"run_id": "run-x"},
        }
        lines = [
            '{"phase": "completed", "job_id": "job-x", '
            '"submission_status": "submitted"}'
        ]
        out = self._run(
            monkeypatch,
            mode="chat",
            job=job,
            status_lines=lines,
            run_seconds=None,
        )
        assert out.phase == "completed"

    def test_voice_sip_leases_injects_and_releases(self, monkeypatch):
        released_slots = []

        async def _acquire(job_id, run_seconds):
            return {
                "did": "+15557654321",
                "dispatch_rule_name": "rule-9",
                "room_name": "sim-slot-01",
                "slot_id": "s9",
            }

        async def _release(slot):
            released_slots.append(slot["slot_id"])

        job = {
            "mode": "voice_sip",
            "voice": {
                "agent_definition": {"transport": {"kind": "sip_inbound"}},
                "params": {},
            },
            "sink": {"api_url": "http://localhost:8000"},
            "metadata": {"run_id": "run-x", "secret_env": []},
        }
        lines = [
            '{"phase": "completed", "job_id": "job-x", "submission_status": "submitted"}'
        ]
        out = self._run(
            monkeypatch,
            mode="voice_sip",
            job=job,
            status_lines=lines,
            acquire=_acquire,
            released=_release,
        )
        assert out.phase == "completed"
        # The leased slot was released in finally.
        assert released_slots == ["s9"]

    def test_web_voice_never_leases(self, monkeypatch):
        async def _acquire(  # pragma: no cover - must not be called
            job_id, run_seconds
        ):
            raise AssertionError("web voice must not lease a DID")

        job = {
            "mode": "voice_webrtc",
            "voice": {
                "agent_definition": {"transport": {"kind": "webrtc"}},
                "params": {},
            },
            "sink": {"api_url": "http://localhost:8000"},
            "metadata": {"run_id": "run-x", "secret_env": []},
        }
        lines = [
            '{"phase": "completed", "job_id": "job-x", "submission_status": "submitted"}'
        ]
        out = self._run(
            monkeypatch,
            mode="voice_webrtc",
            job=job,
            status_lines=lines,
            acquire=_acquire,
        )
        assert out.phase == "completed"

    def _originator_sip_job(self, originator):
        return {
            "mode": "voice_sip",
            "voice": {
                "agent_definition": {
                    "transport": {
                        "kind": "sip_inbound",
                        "inbound_call_originator": originator,
                    }
                },
                "params": {},
            },
            "sink": {"api_url": "http://localhost:8000"},
            "metadata": {"run_id": "run-x", "secret_env": []},
        }

    @pytest.mark.parametrize("originator", ["retell", "vapi"])
    @pytest.mark.parametrize(
        "slot",
        [
            {"slot_id": "s1", "dispatch_rule_name": "r1"},
            # A whitespace-only DID must be treated as absent, not as
            # a real leased number — both _inject_did_slot and the guard's
            # own .strip() must agree it never reaches metadata.leased_did.
            {"slot_id": "s1", "dispatch_rule_name": "r1", "did": " "},
        ],
        ids=["no_did_key", "whitespace_only_did"],
    )
    def test_guard_blocks_originator_job_when_lease_has_no_did(
        self, monkeypatch, originator, slot, tmp_path
    ):
        import asyncio

        from temporalio.exceptions import ApplicationError

        from simulate.temporal.activities import hosted_runner as hr
        from simulate.temporal.types.hosted_runner import RunHostedJobInput

        exec_calls = []
        released = []

        async def _fake_exec(*args, **kwargs):
            exec_calls.append(args)
            return _FakeProc([])

        async def _acquire(job_id, run_seconds):
            return slot

        async def _release(slot):
            released.append(slot)

        # Pin the scratch dir so we can assert it is cleaned up when
        # the guard raises before any child is ever spawned.
        scratch_dir = tmp_path / "alk-runner-scratch"
        scratch_dir.mkdir()
        # Also pin _runs_base() into tmp_path so the run_root assertion below
        # exercises the real path, not the host's system temp dir.
        runs_base = tmp_path / "alk-runner-runs"
        monkeypatch.setenv("ALK_RUNNER_RUN_ROOT", str(runs_base))

        monkeypatch.setattr(hr.asyncio, "create_subprocess_exec", _fake_exec)
        monkeypatch.setattr(hr.activity, "heartbeat", lambda *a, **k: None)
        monkeypatch.setattr(hr, "_acquire_did_slot", _acquire)
        monkeypatch.setattr(hr, "_release_did_slot", _release)
        monkeypatch.setattr(hr.tempfile, "mkdtemp", lambda *a, **k: str(scratch_dir))

        inp = RunHostedJobInput(
            job_id="job-x",
            run_id="run-x",
            mode="voice_sip",
            job_json=json.dumps(self._originator_sip_job(originator)),
            run_seconds=900,
        )

        with pytest.raises(ApplicationError) as excinfo:
            asyncio.run(hr.run_hosted_sdk_job(inp))
        assert excinfo.value.non_retryable
        assert excinfo.value.type == "inbound_originator_requires_leased_did"
        assert exec_calls == []
        # The leased slot must still be released even though the guard raised.
        assert released == [slot]
        # No child was ever started, so the scratch dir must not leak.
        assert not scratch_dir.exists()
        # Nor must the empty run_root the guard blocked before any child
        # could write artifacts into it.
        assert not (runs_base / "job-x").exists()

    @pytest.mark.parametrize("originator", ["retell", "vapi"])
    def test_guard_blocks_originator_job_when_lease_returns_none(
        self, monkeypatch, originator
    ):
        import asyncio

        from temporalio.exceptions import ApplicationError

        from simulate.temporal.activities import hosted_runner as hr
        from simulate.temporal.types.hosted_runner import RunHostedJobInput

        exec_calls = []
        release_calls = []

        async def _fake_exec(*args, **kwargs):
            exec_calls.append(args)
            return _FakeProc([])

        async def _acquire(job_id, run_seconds):
            return None

        async def _release(slot):  # pragma: no cover - must not be called
            release_calls.append(slot)

        monkeypatch.setattr(hr.asyncio, "create_subprocess_exec", _fake_exec)
        monkeypatch.setattr(hr.activity, "heartbeat", lambda *a, **k: None)
        monkeypatch.setattr(hr, "_acquire_did_slot", _acquire)
        monkeypatch.setattr(hr, "_release_did_slot", _release)

        inp = RunHostedJobInput(
            job_id="job-x",
            run_id="run-x",
            mode="voice_sip",
            job_json=json.dumps(self._originator_sip_job(originator)),
            run_seconds=900,
        )

        with pytest.raises(ApplicationError):
            asyncio.run(hr.run_hosted_sdk_job(inp))
        assert exec_calls == []
        assert release_calls == []

    @pytest.mark.parametrize("originator", ["retell", "vapi"])
    def test_guard_rejects_whitespace_leased_did_planted_directly(
        self, monkeypatch, originator
    ):
        # Pin the guard's OWN .strip() independently of
        # _inject_did_slot's — plant metadata.leased_did = " " directly
        # (bypassing _inject_did_slot entirely: the lease returns no slot, so
        # the injector never runs) and confirm the guard still treats
        # whitespace-only as no DID rather than relying on the injector to
        # have already cleaned it up.
        import asyncio

        from temporalio.exceptions import ApplicationError

        from simulate.temporal.activities import hosted_runner as hr
        from simulate.temporal.types.hosted_runner import RunHostedJobInput

        exec_calls = []

        async def _fake_exec(*args, **kwargs):
            exec_calls.append(args)
            return _FakeProc([])

        async def _acquire(job_id, run_seconds):
            return None

        async def _release(slot):  # pragma: no cover - must not be called
            pass

        monkeypatch.setattr(hr.asyncio, "create_subprocess_exec", _fake_exec)
        monkeypatch.setattr(hr.activity, "heartbeat", lambda *a, **k: None)
        monkeypatch.setattr(hr, "_acquire_did_slot", _acquire)
        monkeypatch.setattr(hr, "_release_did_slot", _release)

        job = self._originator_sip_job(originator)
        job["metadata"]["leased_did"] = " "

        inp = RunHostedJobInput(
            job_id="job-x",
            run_id="run-x",
            mode="voice_sip",
            job_json=json.dumps(job),
            run_seconds=900,
        )

        with pytest.raises(ApplicationError) as excinfo:
            asyncio.run(hr.run_hosted_sdk_job(inp))
        assert excinfo.value.non_retryable
        assert excinfo.value.type == "inbound_originator_requires_leased_did"
        assert exec_calls == []

    @pytest.mark.parametrize("originator", ["retell", "vapi"])
    def test_guard_blocks_originator_job_when_only_stale_params_inbound_did_set(
        self, monkeypatch, originator
    ):
        # Regression guard: the guard predicate must read
        # metadata.leased_did, not voice.params.inbound_did. A job that
        # (incorrectly) carries only the stale params location and no
        # metadata.leased_did must still be blocked.
        import asyncio

        from temporalio.exceptions import ApplicationError

        from simulate.temporal.activities import hosted_runner as hr
        from simulate.temporal.types.hosted_runner import RunHostedJobInput

        exec_calls = []

        async def _fake_exec(*args, **kwargs):
            exec_calls.append(args)
            return _FakeProc([])

        async def _acquire(job_id, run_seconds):
            return {"slot_id": "s1", "dispatch_rule_name": "r1"}  # no "did"

        async def _release(slot):
            pass

        monkeypatch.setattr(hr.asyncio, "create_subprocess_exec", _fake_exec)
        monkeypatch.setattr(hr.activity, "heartbeat", lambda *a, **k: None)
        monkeypatch.setattr(hr, "_acquire_did_slot", _acquire)
        monkeypatch.setattr(hr, "_release_did_slot", _release)

        job = self._originator_sip_job(originator)
        job["voice"]["params"]["inbound_did"] = "+15557654321"

        inp = RunHostedJobInput(
            job_id="job-x",
            run_id="run-x",
            mode="voice_sip",
            job_json=json.dumps(job),
            run_seconds=900,
        )

        with pytest.raises(ApplicationError) as excinfo:
            asyncio.run(hr.run_hosted_sdk_job(inp))
        assert excinfo.value.type == "inbound_originator_requires_leased_did"
        assert exec_calls == []

    def test_guard_blocks_originator_job_when_lease_has_rule_but_no_room(
        self, monkeypatch
    ):
        # D11: a leased slot naming a routing rule but no room can never route
        # a call to the simulator — refuse before spawning (mirrors
        # test_guard_blocks_originator_job_when_lease_has_no_did).
        import asyncio

        from temporalio.exceptions import ApplicationError

        from simulate.temporal.activities import hosted_runner as hr
        from simulate.temporal.types.hosted_runner import RunHostedJobInput

        exec_calls = []
        released = []

        async def _fake_exec(*args, **kwargs):
            exec_calls.append(args)
            return _FakeProc([])

        async def _acquire(job_id, run_seconds):
            return {
                "did": "+15557654321",
                "dispatch_rule_name": "rule-9",
                "slot_id": "s9",
            }

        async def _release(slot):
            released.append(slot["slot_id"])

        monkeypatch.setattr(hr.asyncio, "create_subprocess_exec", _fake_exec)
        monkeypatch.setattr(hr.activity, "heartbeat", lambda *a, **k: None)
        monkeypatch.setattr(hr, "_acquire_did_slot", _acquire)
        monkeypatch.setattr(hr, "_release_did_slot", _release)

        inp = RunHostedJobInput(
            job_id="job-x",
            run_id="run-x",
            mode="voice_sip",
            job_json=json.dumps(self._originator_sip_job("retell")),
            run_seconds=900,
        )

        with pytest.raises(ApplicationError) as excinfo:
            asyncio.run(hr.run_hosted_sdk_job(inp))
        assert excinfo.value.non_retryable
        assert excinfo.value.type == "leased_slot_requires_room"
        assert exec_calls == []
        assert released == ["s9"]

    def test_guard_room_message_falls_back_to_unknown_slot_id(self, monkeypatch):
        # D11: a malformed lease is the very case this guard exists for, so
        # slot_id/slot can both be missing too — the message must not render
        # the literal "None" for the slot identity.
        import asyncio

        from temporalio.exceptions import ApplicationError

        from simulate.temporal.activities import hosted_runner as hr
        from simulate.temporal.types.hosted_runner import RunHostedJobInput

        async def _fake_exec(*args, **kwargs):
            return _FakeProc([])

        async def _acquire(job_id, run_seconds):
            return {"did": "+15557654321", "dispatch_rule_name": "rule-9"}

        async def _release(slot):
            pass

        monkeypatch.setattr(hr.asyncio, "create_subprocess_exec", _fake_exec)
        monkeypatch.setattr(hr.activity, "heartbeat", lambda *a, **k: None)
        monkeypatch.setattr(hr, "_acquire_did_slot", _acquire)
        monkeypatch.setattr(hr, "_release_did_slot", _release)

        inp = RunHostedJobInput(
            job_id="job-x",
            run_id="run-x",
            mode="voice_sip",
            job_json=json.dumps(self._originator_sip_job("retell")),
            run_seconds=900,
        )

        with pytest.raises(ApplicationError) as excinfo:
            asyncio.run(hr.run_hosted_sdk_job(inp))
        assert excinfo.value.type == "leased_slot_requires_room"
        assert "<unknown>" in str(excinfo.value)
        assert "None" not in str(excinfo.value)

    def test_slot_with_did_and_no_routing_fields_spawns_normally(self, monkeypatch):
        # A slot naming neither a rule nor a room is not a malformed lease —
        # the kit self-provisions its own rule for it, exactly as today.
        async def _acquire(job_id, run_seconds):
            return {"did": "+15557654321", "slot_id": "s9"}

        job = self._originator_sip_job("retell")
        lines = [
            '{"phase": "completed", "job_id": "job-x", "submission_status": "submitted"}'
        ]
        out = self._run(
            monkeypatch,
            mode="voice_sip",
            job=job,
            status_lines=lines,
            acquire=_acquire,
        )
        assert out.phase == "completed"

    def test_number_guard_fires_before_room_guard(self, monkeypatch):
        # Guard order: a slot with a routing rule and no number is reported as
        # the missing-number guard, not the missing-room guard.
        import asyncio

        from temporalio.exceptions import ApplicationError

        from simulate.temporal.activities import hosted_runner as hr
        from simulate.temporal.types.hosted_runner import RunHostedJobInput

        exec_calls = []

        async def _fake_exec(*args, **kwargs):
            exec_calls.append(args)
            return _FakeProc([])

        async def _acquire(job_id, run_seconds):
            return {"dispatch_rule_name": "rule-9", "slot_id": "s9"}

        async def _release(slot):
            pass

        monkeypatch.setattr(hr.asyncio, "create_subprocess_exec", _fake_exec)
        monkeypatch.setattr(hr.activity, "heartbeat", lambda *a, **k: None)
        monkeypatch.setattr(hr, "_acquire_did_slot", _acquire)
        monkeypatch.setattr(hr, "_release_did_slot", _release)

        inp = RunHostedJobInput(
            job_id="job-x",
            run_id="run-x",
            mode="voice_sip",
            job_json=json.dumps(self._originator_sip_job("retell")),
            run_seconds=900,
        )

        with pytest.raises(ApplicationError) as excinfo:
            asyncio.run(hr.run_hosted_sdk_job(inp))
        assert excinfo.value.type == "inbound_originator_requires_leased_did"
        assert exec_calls == []

    def test_sip_outbound_job_with_rule_only_slot_not_refused_by_d11(self, monkeypatch):
        # A sip_outbound job also leases a slot today and must not trip the
        # sip_inbound-only D11 guard.
        async def _acquire(job_id, run_seconds):
            return {
                "did": "+15557654321",
                "dispatch_rule_name": "rule-9",
                "slot_id": "s9",
            }

        job = {
            "mode": "voice_sip",
            "voice": {
                "agent_definition": {
                    "transport": {"kind": "sip_outbound", "sip_call_to": "+1"}
                },
                "params": {},
            },
            "sink": {"api_url": "http://localhost:8000"},
            "metadata": {"run_id": "run-x", "secret_env": []},
        }
        lines = [
            '{"phase": "completed", "job_id": "job-x", "submission_status": "submitted"}'
        ]
        out = self._run(
            monkeypatch,
            mode="voice_sip",
            job=job,
            status_lines=lines,
            acquire=_acquire,
        )
        assert out.phase == "completed"

    def test_guard_leaves_chat_job_untouched(self, monkeypatch):
        # A chat job has no "voice" key at all — the guard's safe .get() chain
        # must not raise or block it.
        job = {
            "mode": "chat",
            "spec": {"run_id": "run-x", "target": {"secret_refs": {}}},
            "sink": {"api_url": "http://localhost:8000"},
            "metadata": {"run_id": "run-x"},
        }
        lines = [
            '{"phase": "completed", "job_id": "job-x", "submission_status": "submitted"}'
        ]
        out = self._run(monkeypatch, mode="chat", job=job, status_lines=lines)
        assert out.phase == "completed"

    def test_missing_provider_credential_blocks_child_and_releases_slot(
        self, monkeypatch, tmp_path
    ):
        # A secret_env ref to a deleted/nonexistent ProviderCredentials row must
        # never fall through to the worker's own env — it should fail loudly
        # before the child ever dials on the platform's own key.
        import asyncio

        from temporalio.exceptions import ApplicationError

        from simulate.models.agent_definition import ProviderCredentials
        from simulate.temporal.activities import hosted_runner as hr
        from simulate.temporal.types.hosted_runner import RunHostedJobInput

        exec_calls = []
        released = []

        async def _fake_exec(*args, **kwargs):
            exec_calls.append(args)
            return _FakeProc([])

        async def _acquire(job_id, run_seconds):
            return {
                "did": "+15557654321",
                "dispatch_rule_name": "r1",
                "room_name": "sim-slot-01",
                "slot_id": "s1",
            }

        async def _release(slot):
            released.append(slot["slot_id"])

        def _raise_missing(*args, **kwargs):
            raise ProviderCredentials.DoesNotExist

        # Pin the scratch dir so we can assert it is cleaned up when
        # credential resolution raises before any child is ever spawned.
        scratch_dir = tmp_path / "alk-runner-scratch"
        scratch_dir.mkdir()

        monkeypatch.setattr(hr.asyncio, "create_subprocess_exec", _fake_exec)
        monkeypatch.setattr(hr.activity, "heartbeat", lambda *a, **k: None)
        monkeypatch.setattr(hr, "_acquire_did_slot", _acquire)
        monkeypatch.setattr(hr, "_release_did_slot", _release)
        monkeypatch.setattr(ProviderCredentials.objects, "get", _raise_missing)
        monkeypatch.setattr(hr.tempfile, "mkdtemp", lambda *a, **k: str(scratch_dir))

        job = {
            "mode": "voice_sip",
            "voice": {
                "agent_definition": {
                    "transport": {
                        "kind": "sip_inbound",
                        "inbound_call_originator": "retell",
                    }
                },
                "params": {},
            },
            "sink": {"api_url": "http://localhost:8000"},
            "metadata": {
                "run_id": "run-x",
                "secret_env": [
                    {
                        "key": "RETELL_API_KEY",
                        "manager": "provider_credentials",
                        "credential_id": "does-not-exist",
                        "field": "api_key",
                    }
                ],
            },
        }
        inp = RunHostedJobInput(
            job_id="job-x",
            run_id="run-x",
            mode="voice_sip",
            job_json=json.dumps(job),
            run_seconds=900,
        )

        with pytest.raises(ApplicationError) as excinfo:
            asyncio.run(hr.run_hosted_sdk_job(inp))
        assert excinfo.value.type == "provider_credentials_missing"
        assert excinfo.value.non_retryable
        assert exec_calls == []
        # The leased slot must still be released even though resolution raised.
        assert released == ["s1"]
        # No child was ever started, so the scratch dir must not leak.
        assert not scratch_dir.exists()

    def test_empty_provider_credential_field_blocks_child_and_releases_slot(
        self, monkeypatch
    ):
        # An existing ProviderCredentials row whose resolved field
        # decrypts to an empty string must fail exactly like a missing row —
        # never let the child dial on an empty key.
        import asyncio

        from temporalio.exceptions import ApplicationError

        from simulate.models.agent_definition import ProviderCredentials
        from simulate.temporal.activities import hosted_runner as hr
        from simulate.temporal.types.hosted_runner import RunHostedJobInput

        exec_calls = []
        released = []

        async def _fake_exec(*args, **kwargs):
            exec_calls.append(args)
            return _FakeProc([])

        async def _acquire(job_id, run_seconds):
            return {
                "did": "+15557654321",
                "dispatch_rule_name": "r1",
                "room_name": "sim-slot-01",
                "slot_id": "s1",
            }

        async def _release(slot):
            released.append(slot["slot_id"])

        fake_credentials = SimpleNamespace(
            get_api_key=lambda: "", get_api_secret=lambda: ""
        )

        monkeypatch.setattr(hr.asyncio, "create_subprocess_exec", _fake_exec)
        monkeypatch.setattr(hr.activity, "heartbeat", lambda *a, **k: None)
        monkeypatch.setattr(hr, "_acquire_did_slot", _acquire)
        monkeypatch.setattr(hr, "_release_did_slot", _release)
        monkeypatch.setattr(
            ProviderCredentials.objects, "get", lambda *a, **k: fake_credentials
        )

        job = {
            "mode": "voice_sip",
            "voice": {
                "agent_definition": {
                    "transport": {
                        "kind": "sip_inbound",
                        "inbound_call_originator": "retell",
                    }
                },
                "params": {},
            },
            "sink": {"api_url": "http://localhost:8000"},
            "metadata": {
                "run_id": "run-x",
                "secret_env": [
                    {
                        "key": "RETELL_API_KEY",
                        "manager": "provider_credentials",
                        "credential_id": "cred-1",
                        "field": "api_key",
                    }
                ],
            },
        }
        inp = RunHostedJobInput(
            job_id="job-x",
            run_id="run-x",
            mode="voice_sip",
            job_json=json.dumps(job),
            run_seconds=900,
        )

        with pytest.raises(ApplicationError) as excinfo:
            asyncio.run(hr.run_hosted_sdk_job(inp))
        assert excinfo.value.type == "provider_credentials_missing"
        assert excinfo.value.non_retryable
        assert exec_calls == []
        assert released == ["s1"]

    def test_livekit_webrtc_empty_api_secret_blocks_child(self, monkeypatch):
        # Reachable for a backend-built job — a customer LiveKit
        # ProviderCredentials row saved with an api_key but a blank
        # api_secret (services/hosted_runner.py's _voice_livekit_runtime
        # webrtc branch emits a provider_credentials ref for both fields).
        # No DID lease is involved on the webrtc path.
        import asyncio

        from temporalio.exceptions import ApplicationError

        from simulate.models.agent_definition import ProviderCredentials
        from simulate.temporal.activities import hosted_runner as hr
        from simulate.temporal.types.hosted_runner import RunHostedJobInput

        exec_calls = []

        async def _fake_exec(*args, **kwargs):
            exec_calls.append(args)
            return _FakeProc([])

        fake_credentials = SimpleNamespace(
            get_api_key=lambda: "customer-lk-key", get_api_secret=lambda: ""
        )

        monkeypatch.setattr(hr.asyncio, "create_subprocess_exec", _fake_exec)
        monkeypatch.setattr(hr.activity, "heartbeat", lambda *a, **k: None)
        monkeypatch.setattr(
            ProviderCredentials.objects, "get", lambda *a, **k: fake_credentials
        )

        job = {
            "mode": "voice_webrtc",
            "voice": {
                "agent_definition": {"transport": {"kind": "webrtc"}},
                "params": {},
            },
            "sink": {"api_url": "http://localhost:8000"},
            "metadata": {
                "run_id": "run-x",
                "secret_env": [
                    {
                        "key": "LIVEKIT_API_KEY",
                        "manager": "provider_credentials",
                        "credential_id": "cred-2",
                        "field": "api_key",
                    },
                    {
                        "key": "LIVEKIT_API_SECRET",
                        "manager": "provider_credentials",
                        "credential_id": "cred-2",
                        "field": "api_secret",
                    },
                ],
            },
        }
        inp = RunHostedJobInput(
            job_id="job-x",
            run_id="run-x",
            mode="voice_webrtc",
            job_json=json.dumps(job),
            run_seconds=900,
        )

        with pytest.raises(ApplicationError) as excinfo:
            asyncio.run(hr.run_hosted_sdk_job(inp))
        assert excinfo.value.type == "provider_credentials_missing"
        assert exec_calls == []

    def test_env_passthrough_customer_key_blocks_child_even_when_worker_has_value(
        self, monkeypatch
    ):
        # A manager:"env" ref for a CUSTOMER provider key must never
        # let the child dial on the worker's own key — even when the worker
        # process happens to have that exact env var set (e.g. for the
        # platform's own, unrelated use). The ref itself is the bug signal,
        # not just an unresolved lookup.
        import asyncio

        from temporalio.exceptions import ApplicationError

        from simulate.temporal.activities import hosted_runner as hr
        from simulate.temporal.types.hosted_runner import RunHostedJobInput

        monkeypatch.setenv("RETELL_API_KEY", "platform-key")

        exec_calls = []

        async def _fake_exec(*args, **kwargs):
            exec_calls.append(args)
            return _FakeProc([])

        async def _acquire(job_id, run_seconds):
            return {
                "did": "+15557654321",
                "dispatch_rule_name": "r1",
                "room_name": "sim-slot-01",
                "slot_id": "s1",
            }

        async def _release(slot):
            pass

        monkeypatch.setattr(hr.asyncio, "create_subprocess_exec", _fake_exec)
        monkeypatch.setattr(hr.activity, "heartbeat", lambda *a, **k: None)
        monkeypatch.setattr(hr, "_acquire_did_slot", _acquire)
        monkeypatch.setattr(hr, "_release_did_slot", _release)

        job = self._originator_sip_job("retell")
        job["metadata"]["secret_env"] = [
            {"key": "RETELL_API_KEY", "manager": "env", "source": "RETELL_API_KEY"},
        ]

        inp = RunHostedJobInput(
            job_id="job-x",
            run_id="run-x",
            mode="voice_sip",
            job_json=json.dumps(job),
            run_seconds=900,
        )

        with pytest.raises(ApplicationError) as excinfo:
            asyncio.run(hr.run_hosted_sdk_job(inp))
        assert excinfo.value.type == "provider_credentials_missing"
        assert excinfo.value.non_retryable
        assert exec_calls == []

    def test_env_passthrough_system_livekit_key_still_reaches_child(self, monkeypatch):
        # Positive case: a system LiveKit runtime var
        # (platform-owned by design, see _voice_livekit_runtime) must still
        # reach the child via env passthrough.
        import asyncio

        from simulate.temporal.activities import hosted_runner as hr
        from simulate.temporal.types.hosted_runner import RunHostedJobInput

        monkeypatch.setenv("LIVEKIT_API_KEY", "system-lk-key")

        seen_env = {}

        async def _fake_exec(*args, **kwargs):
            seen_env.update(kwargs.get("env") or {})
            return _FakeProc(
                [
                    b'{"phase": "completed", "job_id": "job-x", '
                    b'"submission_status": "submitted"}'
                ]
            )

        monkeypatch.setattr(hr.asyncio, "create_subprocess_exec", _fake_exec)
        monkeypatch.setattr(hr.activity, "heartbeat", lambda *a, **k: None)

        job = {
            "mode": "voice_webrtc",
            "voice": {
                "agent_definition": {"transport": {"kind": "webrtc"}},
                "params": {},
            },
            "sink": {"api_url": "http://localhost:8000"},
            "metadata": {
                "run_id": "run-x",
                "secret_env": [
                    {
                        "key": "LIVEKIT_API_KEY",
                        "manager": "env",
                        "source": "LIVEKIT_API_KEY",
                    },
                ],
            },
        }
        inp = RunHostedJobInput(
            job_id="job-x",
            run_id="run-x",
            mode="voice_webrtc",
            job_json=json.dumps(job),
            run_seconds=900,
        )
        out = asyncio.run(hr.run_hosted_sdk_job(inp))
        assert out.phase == "completed"
        assert seen_env["LIVEKIT_API_KEY"] == "system-lk-key"

    def test_declared_ref_that_resolves_to_nothing_is_scrubbed_not_inherited(
        self, monkeypatch
    ):
        # A declared secret_env ref that resolves to
        # nothing (here, a falsy credential_id -> _resolve_provider_credential
        # returns None) must not fall back to whatever the worker process
        # happens to have under that name. Only the ref-scoped scrub in
        # _child_environment (_secret_env_ref_keys pop) protects this —
        # deleting that pop lets the platform's own LIVEKIT_API_KEY leak
        # through, since the overlay from _resolve_voice_secret_env adds
        # nothing back for an unresolved ref.
        import asyncio

        from simulate.temporal.activities import hosted_runner as hr
        from simulate.temporal.types.hosted_runner import RunHostedJobInput

        monkeypatch.setenv("LIVEKIT_API_KEY", "platform-lk")

        seen_env = {}

        async def _fake_exec(*args, **kwargs):
            seen_env.update(kwargs.get("env") or {})
            return _FakeProc(
                [
                    b'{"phase": "completed", "job_id": "job-x", '
                    b'"submission_status": "submitted"}'
                ]
            )

        monkeypatch.setattr(hr.asyncio, "create_subprocess_exec", _fake_exec)
        monkeypatch.setattr(hr.activity, "heartbeat", lambda *a, **k: None)

        job = {
            "mode": "voice_webrtc",
            "voice": {
                "agent_definition": {"transport": {"kind": "webrtc"}},
                "params": {},
            },
            "sink": {"api_url": "http://localhost:8000"},
            "metadata": {
                "run_id": "run-x",
                "secret_env": [
                    {
                        "key": "LIVEKIT_API_KEY",
                        "manager": "provider_credentials",
                        "credential_id": "",
                        "field": "api_key",
                    }
                ],
            },
        }
        inp = RunHostedJobInput(
            job_id="job-x",
            run_id="run-x",
            mode="voice_webrtc",
            job_json=json.dumps(job),
            run_seconds=900,
        )
        out = asyncio.run(hr.run_hosted_sdk_job(inp))
        assert out.phase == "completed"
        assert "LIVEKIT_API_KEY" not in seen_env

    def test_setting_manager_ref_for_customer_key_is_blocked(self):
        # A manager:"setting" ref for a customer
        # provider key must be blocked exactly like a manager:"env" ref —
        # the customer-key rule must not live only inside the env-passthrough
        # branch, where a "setting" ref (or any future manager) would
        # silently bypass it.
        import asyncio

        from temporalio.exceptions import ApplicationError

        from simulate.temporal.activities.hosted_runner import (
            _resolve_voice_secret_env,
        )

        job = {
            "metadata": {
                "secret_env": [
                    {
                        "key": "RETELL_API_KEY",
                        "manager": "setting",
                        "setting": "RETELL_API_KEY",
                    }
                ]
            }
        }
        with pytest.raises(ApplicationError) as excinfo:
            asyncio.run(_resolve_voice_secret_env(job))
        assert excinfo.value.type == "provider_credentials_missing"
        assert excinfo.value.non_retryable


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
class TestVoiceSecretResolution:
    def test_resolve_provider_credential_decrypts(self, organization, workspace):
        import asyncio

        from simulate.models.agent_definition import (
            AgentDefinition,
            ProviderCredentials,
        )
        from simulate.temporal.activities.hosted_runner import (
            _resolve_voice_secret_env,
        )

        agent = AgentDefinition.objects.create(
            agent_name="v",
            agent_type=AgentDefinition.AgentTypeChoices.VOICE,
            inbound=True,
            description="d",
            organization=organization,
            workspace=workspace,
        )
        creds = ProviderCredentials.objects.create(
            agent_definition=agent,
            provider_type="vapi",
            api_key="plain-vapi-key",
        )
        job = {
            "metadata": {
                "secret_env": [
                    {
                        "key": "VAPI_API_KEY",
                        "manager": "provider_credentials",
                        "credential_id": str(creds.id),
                        "field": "api_key",
                    }
                ]
            }
        }
        resolved = asyncio.run(_resolve_voice_secret_env(job))
        assert resolved["VAPI_API_KEY"] == "plain-vapi-key"


# ---------------------------------------------------------------------------
# CSAT task — write path + idempotency (regression: registration + guard bug)
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
class TestAlkVoiceCsatScoring:
    """The dedicated CSAT task must write conversation_metrics_data['csat_score']
    even when the eval path already set overall_score, and must be idempotent on
    its own output — not on overall_score."""

    def _completed_voice_call(self, auth_client, run_test):
        _, call_ids = _start_and_batch(auth_client, run_test)
        call = CallExecution.objects.get(id=call_ids[0])
        call.status = CallExecution.CallStatus.COMPLETED
        # A recording_url routes scoring through _score_from_recording so the
        # patched _run_agent_csat is exercised.
        call.recording_url = "https://example.com/rec.wav"
        call.save(update_fields=["status", "recording_url"])
        return call

    def test_writes_csat_when_eval_already_set_overall_score(
        self, auth_client, run_test
    ):
        from simulate.tasks import alk_sim

        call = self._completed_voice_call(auth_client, run_test)
        # Eval path won the race and wrote overall_score; csat_score still absent.
        call.overall_score = 3.0
        call.conversation_metrics_data = {"foo": "bar"}
        call.save(update_fields=["overall_score", "conversation_metrics_data"])

        with (
            patch("simulate.tasks.alk_sim.close_old_connections"),
            patch.object(alk_sim, "_run_agent_csat", return_value=8.0),
        ):
            alk_sim.calculate_alk_voice_csat_score._original_func(str(call.id))

        call.refresh_from_db()
        assert call.conversation_metrics_data["csat_score"] == 8.0
        assert call.call_metadata["csat_status"] == "completed"
        # eval-derived overall_score must not be clobbered
        assert call.overall_score == 3.0

    def test_idempotent_on_existing_csat_score(self, auth_client, run_test):
        from simulate.tasks import alk_sim

        call = self._completed_voice_call(auth_client, run_test)
        call.conversation_metrics_data = {"csat_score": 6.0}
        call.save(update_fields=["conversation_metrics_data"])

        with (
            patch("simulate.tasks.alk_sim.close_old_connections"),
            patch.object(alk_sim, "_run_agent_csat", return_value=9.0) as scorer,
        ):
            alk_sim.calculate_alk_voice_csat_score._original_func(str(call.id))

        scorer.assert_not_called()
        call.refresh_from_db()
        assert call.conversation_metrics_data["csat_score"] == 6.0
        assert call.call_metadata["csat_status"] == "completed"

    def test_seeds_overall_score_when_unset(self, auth_client, run_test):
        from simulate.tasks import alk_sim

        call = self._completed_voice_call(auth_client, run_test)
        assert call.overall_score is None

        with (
            patch("simulate.tasks.alk_sim.close_old_connections"),
            patch.object(alk_sim, "_run_agent_csat", return_value=7.0),
        ):
            alk_sim.calculate_alk_voice_csat_score._original_func(str(call.id))

        call.refresh_from_db()
        assert call.conversation_metrics_data["csat_score"] == 7.0
        assert call.overall_score == 7.0
        assert call.call_metadata["csat_status"] == "completed"

    def test_failed_scorer_is_durable_and_retryable(self, auth_client, run_test):
        from simulate.tasks import alk_sim

        call = self._completed_voice_call(auth_client, run_test)
        with (
            patch("simulate.tasks.alk_sim.close_old_connections"),
            patch.object(alk_sim, "_run_agent_csat", return_value=None),
            pytest.raises(RuntimeError, match="returned no result"),
        ):
            alk_sim.calculate_alk_voice_csat_score._original_func(str(call.id))

        call.refresh_from_db()
        assert call.call_metadata["csat_status"] == "failed"
        assert "returned no result" in call.call_metadata["csat_error"]
        assert not (call.conversation_metrics_data or {}).get("csat_score")

    def test_text_call_falls_back_to_call_transcript(self, auth_client, run_test):
        from simulate.tasks import alk_sim

        call = self._completed_voice_call(auth_client, run_test)
        call.simulation_call_type = CallExecution.SimulationCallType.TEXT
        call.recording_url = None
        call.save(update_fields=["simulation_call_type", "recording_url"])
        CallTranscript.objects.create(
            call_execution=call,
            speaker_role=CallTranscript.SpeakerRole.USER,
            content="Thanks, that resolved my issue.",
            start_time_ms=0,
            end_time_ms=1000,
        )

        with (
            patch("simulate.tasks.alk_sim.close_old_connections"),
            patch.object(alk_sim, "_run_agent_csat", return_value=9.0) as scorer,
        ):
            alk_sim.calculate_alk_voice_csat_score._original_func(str(call.id))

        scorer.assert_called_once_with("Customer: Thanks, that resolved my issue.")
        call.refresh_from_db()
        assert call.conversation_metrics_data["csat_score"] == 9.0
        assert call.overall_score == 9.0


def test_alk_sim_task_module_registered_for_worker():
    """The CSAT activity must be import-registered at worker startup, else
    apply_async dispatches to an activity no worker has registered and it never
    runs (csat_dispatched=True, csat_score=None, silent)."""
    from tfc.temporal.common.registry import TEMPORAL_ACTIVITY_MODULES

    assert "simulate.tasks.alk_sim" in TEMPORAL_ACTIVITY_MODULES


class TestHostedRunnerProviderSupport:
    """Bland targets are unsupported by the released SDK and route native."""

    def test_bland_provider_unsupported(self):
        from simulate.services.hosted_runner import hosted_runner_supports

        assert not hosted_runner_supports(
            SimpleNamespace(provider="bland", credentials_legacy=None)
        )

    def test_supported_providers(self):
        from simulate.services.hosted_runner import hosted_runner_supports

        for prov in ("vapi", "retell", "livekit"):
            assert hosted_runner_supports(
                SimpleNamespace(provider=prov, credentials_legacy=None)
            )

    def test_declared_bland_rejects_regardless_of_credentials_legacy(self):
        # Credentials are not consulted (they can never name Bland — every
        # other provider is coerced to vapi), so a stale credentials_legacy
        # must not rescue a bland column.
        from simulate.services.hosted_runner import hosted_runner_supports

        creds = SimpleNamespace(provider_type="vapi")
        assert not hosted_runner_supports(
            SimpleNamespace(provider="bland", credentials_legacy=creds)
        )

    def test_supported_column_ignores_credentials_legacy(self):
        # A supported column passes regardless of credentials_legacy, since
        # the rail no longer reads it.
        from simulate.services.hosted_runner import hosted_runner_supports

        creds = SimpleNamespace(provider_type="retell")
        assert hosted_runner_supports(
            SimpleNamespace(provider="vapi", credentials_legacy=creds)
        )

    def test_snapshot_missing_provider_key_falls_back_to_stale_bland_column(self):
        # exclude_none drops "provider" from a snapshot cut while the column
        # was null; the rail must still reject on the column, not read the
        # missing key as "unset" and let the run through.
        from simulate.services.hosted_runner import hosted_runner_supports

        agent = SimpleNamespace(provider="bland", credentials_legacy=None)
        version = SimpleNamespace(
            configuration_snapshot={},
            credentials=SimpleNamespace(provider_type="vapi"),
        )
        assert not hosted_runner_supports(agent, version)

    def test_credentials_rewrite_cannot_mask_a_stale_bland_column(self):
        # sync_provider_credentials rewrites the pinned version's own
        # credentials row (and can touch its snapshot) without touching the
        # column; the rail must still reject on the column.
        from simulate.services.hosted_runner import hosted_runner_supports

        agent = SimpleNamespace(provider="bland", credentials_legacy=None)
        version = SimpleNamespace(
            configuration_snapshot={"provider": "vapi"},
            credentials=SimpleNamespace(provider_type="vapi"),
        )
        assert not hosted_runner_supports(agent, version)

    def test_hosted_runner_supports_agrees_across_both_rails(self):
        from simulate.services.hosted_runner import hosted_runner_supports

        agent = SimpleNamespace(provider="retell", credentials_legacy=None)
        version = SimpleNamespace(configuration_snapshot={"provider": "retell"})
        assert hosted_runner_supports(agent, version)

    def test_hosted_runner_build_error_is_non_retryable(self):
        # A deterministic build failure (bad job shape) must fail once, not be
        # retried three times with backoff.
        from simulate.temporal.retry_policies import DB_RETRY_POLICY

        assert "HostedRunnerBuildError" in DB_RETRY_POLICY.non_retryable_error_types

    def test_none_agent_definition(self):
        from simulate.services.hosted_runner import hosted_runner_supports

        assert not hosted_runner_supports(None)

    def test_customer_provider_env_keys_track_profile_table(self):
        # Pin the derivation to the profile table so the two can
        # never silently drift apart even if the derivation is later inlined
        # for import reasons. A new provider profile with an api_key_env
        # automatically joins this set with no hand-maintained edit required.
        from simulate.services.hosted_runner import _PROVIDER_PROFILES
        from simulate.temporal.activities.hosted_runner import (
            _customer_provider_env_keys,
        )

        assert _customer_provider_env_keys() == {
            p["api_key_env"] for p in _PROVIDER_PROFILES.values() if p["api_key_env"]
        }


@pytest.mark.integration
@pytest.mark.django_db
class TestHostedRerunDispatch:
    """A hosted execution's call_and_eval rerun must re-dispatch through the
    simulation runner (reusing the TestExecution id), not the native
    CallExecutionWorkflow that fails with an empty provider phone number."""

    def test_dispatch_hosted_rerun_reuses_execution(self, run_test):
        from simulate.views.run_test import _dispatch_hosted_rerun

        scenario_ids = [
            str(sid) for sid in run_test.scenarios.values_list("id", flat=True)
        ]
        te = SimTestExecution.objects.create(
            run_test=run_test,
            status=SimTestExecution.ExecutionStatus.COMPLETED,
            total_scenarios=1,
            scenario_ids=scenario_ids,
            simulator_agent=run_test.simulator_agent,
        )

        with patch(
            "simulate.temporal.client.start_simulation_runner_workflow",
            return_value="wf-hosted-1",
        ) as dispatch:
            workflow_id = _dispatch_hosted_rerun(te)

        assert workflow_id == "wf-hosted-1"
        _, kwargs = dispatch.call_args
        assert kwargs["test_execution_id"] == str(te.id)
        assert kwargs["run_test_id"] == str(run_test.id)
        assert kwargs["scenario_ids"] == scenario_ids
        assert kwargs["simulator_id"] == str(run_test.simulator_agent_id)


def test_dataset_language_none_single_multi():
    """Regression: multi-language datasets must map to Deepgram 'multi', not None
    (None → English STT → non-English cases silence-fail)."""
    from simulate.models import AgentDefinition
    from simulate.services.hosted_runner import _dataset_language

    code_by_label = {
        label.lower(): code for code, label in AgentDefinition.LanguageChoices.choices
    }
    single_label = next(iter(AgentDefinition.LanguageChoices.labels))

    assert _dataset_language([]) is None
    assert _dataset_language([{"persona": {}}]) is None
    assert (
        _dataset_language([{"persona": {"language": single_label}}])
        == code_by_label[single_label.lower()]
    )
    labels = list(AgentDefinition.LanguageChoices.labels)[:2]
    mixed = [{"persona": {"language": labels[0]}}, {"persona": {"language": labels[1]}}]
    assert _dataset_language(mixed) == "multi"


# NOTE: the two tests below were previously nested inside
# test_dataset_language_none_single_multi (an indentation slip that predates
# this branch) and so were never collected by pytest — dedented to module
# level so the coverage they claim actually runs.
def test_target_speaks_first_toggle_overrides_direction():
    """The explicit target_speaks_first toggle wins over the inbound/outbound
    heuristic; None falls back to it; Retell stays pinned regardless."""
    from simulate.services.hosted_runner import _voice_params

    # True → wait for the target (agent_first) even for an inbound target
    # that the heuristic would have opened simulator_first.
    assert (
        _voice_params("webrtc", inbound=True, target_speaks_first=True)[
            "conversation_direction"
        ]
        == "agent_first"
    )
    # False → the simulator opens even for an outbound target.
    assert (
        _voice_params("webrtc", inbound=False, target_speaks_first=False)[
            "conversation_direction"
        ]
        == "simulator_first"
    )
    # None → unchanged heuristic (inbound → simulator_first).
    assert (
        _voice_params("webrtc", inbound=True, target_speaks_first=None)[
            "conversation_direction"
        ]
        == "simulator_first"
    )
    # Retell cannot greet first in the SDK → clamped even when the toggle
    # asks for agent_first.
    assert (
        _voice_params("retell_webcall", inbound=False, target_speaks_first=True)[
            "conversation_direction"
        ]
        == "simulator_first"
    )


def test_resolve_target_speaks_first_precedence():
    """Snapshot wins over the column; strings coerce. Once the pinned version
    has a snapshot dict, it alone decides — same whole-dict precedence as
    ``_agent_field`` (see ``_resolve_agent_inbound``): a key it lacks means
    "unset" (``None`` — auto), the column is NOT consulted; only a version with
    no snapshot dict at all falls back to the column."""
    from types import SimpleNamespace

    from simulate.services.hosted_runner import _resolve_target_speaks_first

    agent_true = SimpleNamespace(target_speaks_first=True)
    agent_none = SimpleNamespace(target_speaks_first=None)

    # Snapshot overrides the column.
    version = SimpleNamespace(configuration_snapshot={"target_speaks_first": False})
    assert _resolve_target_speaks_first(version, agent_true) is False

    # String "false" must not be truthy.
    version = SimpleNamespace(configuration_snapshot={"target_speaks_first": "false"})
    assert _resolve_target_speaks_first(version, agent_true) is False
    version = SimpleNamespace(configuration_snapshot={"target_speaks_first": "true"})
    assert _resolve_target_speaks_first(version, agent_none) is True

    # Missing in snapshot → None (auto); the column is NOT consulted once the
    # snapshot is a dict (a versioned run must not see a later column edit).
    version = SimpleNamespace(configuration_snapshot={})
    assert _resolve_target_speaks_first(version, agent_true) is None

    # Absent everywhere → None (auto: derive from inbound/outbound).
    assert _resolve_target_speaks_first(None, agent_none) is None
    assert _resolve_target_speaks_first(None, SimpleNamespace()) is None


class TestAgentFieldsReadFromVersionSnapshot:
    """The version's configuration_snapshot is the source of truth for the
    hosted builder; AgentDefinition columns mirror only the latest save and are
    being retired. A run pinned to an older version must see that version."""

    @staticmethod
    def _agent(contact_number="", provider="retell", assistant_id="agent_1"):
        from types import SimpleNamespace

        return SimpleNamespace(
            agent_type=AgentDefinition.AgentTypeChoices.VOICE,
            agent_name="Def Name",
            description="def prompt",
            contact_number=contact_number,
            provider=provider,
            assistant_id=assistant_id,
            credentials_legacy=None,
            latest_version=None,
        )

    @staticmethod
    def _version(**snapshot):
        from types import SimpleNamespace

        return SimpleNamespace(configuration_snapshot=snapshot, credentials=None)

    def test_snapshot_contact_number_decides_phone_mode(self):
        from simulate.services.hosted_runner import resolve_runner_mode

        # Definition says "no phone", the pinned version says phone → phone.
        agent = self._agent(contact_number="")
        assert (
            resolve_runner_mode(agent, self._version(contact_number="+15551234567"))
            == "voice_sip"
        )
        # Definition says phone, the pinned version says none → web. The stale
        # column must not turn a web-call version into a leased-DID run.
        agent = self._agent(contact_number="+15551234567")
        assert (
            resolve_runner_mode(agent, self._version(contact_number=""))
            == "voice_webrtc"
        )

    def test_snapshot_without_the_key_means_unset(self):
        from simulate.services.hosted_runner import _target_uses_phone

        agent = self._agent(contact_number="+15551234567")
        assert _target_uses_phone(agent, self._version(inbound=False)) is False

    def test_non_dict_snapshot_falls_back_to_definition_column(self):
        """A snapshot that is neither a dict nor None must not be read as
        authoritative — only a dict snapshot is; anything else falls back to
        the definition column."""
        from simulate.services.hosted_runner import _agent_field

        agent = self._agent(assistant_id="def_agent")
        version = SimpleNamespace(configuration_snapshot=["not", "a", "dict"])
        assert (
            _agent_field(agent, version, "assistant_id", agent.assistant_id)
            == "def_agent"
        )

    def test_whitespace_only_contact_number_is_not_a_phone_target(self):
        """A whitespace-only contact_number must resolve to no-phone, not a
        truthy non-empty string."""
        from simulate.services.hosted_runner import _target_uses_phone

        agent = self._agent(contact_number="   ")
        assert _target_uses_phone(agent, None) is False

    def test_versionless_agent_falls_back_to_definition(self):
        from simulate.services.hosted_runner import resolve_runner_mode

        agent = self._agent(contact_number="+15551234567")
        assert resolve_runner_mode(agent, None) == "voice_sip"
        agent = self._agent(contact_number="")
        assert resolve_runner_mode(agent, None) == "voice_webrtc"

    def test_latest_version_is_used_when_run_has_no_pinned_version(self):
        from simulate.services.hosted_runner import resolve_runner_mode

        agent = self._agent(contact_number="+15551234567")
        agent.latest_version = self._version(contact_number="")
        assert resolve_runner_mode(agent, None) == "voice_webrtc"

    def test_resolve_agent_version_prefers_active_over_latest(self):
        """_resolve_agent_version's own versionless fallback must match
        resolve_run_agent_version's last rung — one ladder for the module,
        not two that can silently disagree."""
        from simulate.services.hosted_runner import _agent_field

        agent = self._agent(assistant_id="def_agent")
        agent.active_version = self._version(assistant_id="active_agent")
        agent.latest_version = self._version(assistant_id="latest_agent")
        assert (
            _agent_field(agent, None, "assistant_id", agent.assistant_id)
            == "active_agent"
        )

    def test_hosted_runner_supports_rejects_bland_from_either_rail(self):
        # A safety rail, not a plain field read: unlike every other field, a
        # stale Bland column still blocks routing even when the pinned
        # snapshot names a supported provider, and vice versa.
        from simulate.services.hosted_runner import hosted_runner_supports

        agent = self._agent(provider="bland")
        assert hosted_runner_supports(agent, self._version(provider="retell")) is False
        agent = self._agent(provider="retell")
        assert hosted_runner_supports(agent, self._version(provider="bland")) is False

    def test_hosted_runner_supports_rejects_declared_bland_despite_vapi_credentials(
        self,
    ):
        from simulate.services.hosted_runner import hosted_runner_supports

        agent = self._agent(provider="retell")
        version = self._version(provider="bland")
        version.credentials = SimpleNamespace(provider_type="vapi")
        assert hosted_runner_supports(agent, version) is False

    def test_originator_from_number_and_prompt_come_from_snapshot(self):
        from simulate.services.hosted_runner import _voice_agent_definition

        agent = self._agent(contact_number="+15550000001", assistant_id="def_agent")
        version = self._version(
            contact_number="+15559999999",
            assistant_id="snap_agent",
            agent_name="Snap Name",
            description="snap prompt",
        )
        agent_def, _secret_env = _voice_agent_definition(
            agent, "retell", "sip_inbound", None, agent_version=version
        )
        transport = agent_def["transport"]
        assert transport["inbound_call_originator"] == "retell"
        assert transport["originator_from_number"] == "+15559999999"
        assert transport["originator_agent_id"] == "snap_agent"
        assert agent_def["name"] == "Snap Name"
        assert agent_def["system_prompt"] == "snap prompt"

    def test_web_target_id_comes_from_snapshot(self):
        from simulate.services.hosted_runner import _voice_agent_definition

        agent = self._agent(contact_number="", assistant_id="def_agent")
        version = self._version(contact_number="", assistant_id="snap_agent")
        agent_def, _ = _voice_agent_definition(
            agent, "retell", "retell_webcall", None, agent_version=version
        )
        assert agent_def["target"]["agent_id"] == "snap_agent"

    def test_agent_type_read_from_snapshot_for_mode(self):
        """resolve_runner_mode must read agent_type via _agent_field, not
        the bare AgentDefinition column, the same "pinned version wins" rule
        as every other field the builder reads."""
        from simulate.services.hosted_runner import resolve_runner_mode

        # Definition says TEXT, the pinned version says VOICE + phone -> sip.
        agent = self._agent(contact_number="+15551234567")
        agent.agent_type = AgentDefinition.AgentTypeChoices.TEXT
        version = self._version(
            agent_type=AgentDefinition.AgentTypeChoices.VOICE,
            contact_number="+15551234567",
        )
        assert resolve_runner_mode(agent, version) == "voice_sip"

        # Definition says VOICE, the pinned version says TEXT -> chat.
        agent = self._agent(contact_number="+15551234567")
        version = self._version(agent_type=AgentDefinition.AgentTypeChoices.TEXT)
        assert resolve_runner_mode(agent, version) == "chat"

    def test_agent_field_for_run_agrees_with_resolve_runner_mode(self):
        """The view-side helper (agent_field_for_run) must read agent_type
        the same way resolve_runner_mode does for the same run, or the view's
        eligibility gate could disagree with the builder's mode."""
        from types import SimpleNamespace

        from simulate.services.hosted_runner import agent_field_for_run

        agent = self._agent(contact_number="+15551234567")
        agent.agent_type = AgentDefinition.AgentTypeChoices.TEXT
        version = self._version(agent_type=AgentDefinition.AgentTypeChoices.VOICE)
        run_test = SimpleNamespace(agent_definition=agent, agent_version=version)
        assert (
            agent_field_for_run(run_test, "agent_type", agent.agent_type)
            == AgentDefinition.AgentTypeChoices.VOICE
        )

    def test_agent_field_for_run_uses_the_passed_test_execution(self):
        """agent_field_for_run must actually feed its test_execution keyword
        into the ladder, not just accept and ignore it — otherwise a caller
        passing an execution pin (a rerun, say) silently falls back to
        run_test.agent_version / latest_version instead."""
        from types import SimpleNamespace

        from simulate.services.hosted_runner import agent_field_for_run

        agent = self._agent()
        agent.agent_type = AgentDefinition.AgentTypeChoices.VOICE
        agent.latest_version = self._version(
            agent_type=AgentDefinition.AgentTypeChoices.VOICE
        )
        run_test = SimpleNamespace(agent_definition=agent, agent_version=None)
        test_execution = SimpleNamespace(
            agent_version=self._version(
                agent_type=AgentDefinition.AgentTypeChoices.TEXT
            )
        )
        assert (
            agent_field_for_run(
                run_test, "agent_type", agent.agent_type, test_execution=test_execution
            )
            == AgentDefinition.AgentTypeChoices.TEXT
        )

    def test_agent_version_override_is_read_verbatim_not_the_ladder(self):
        """The agent_version= override must win even though run_test itself
        is pinned to a different (VOICE) snapshot — a caller with an
        already-resolved version to check (e.g. a pending PATCH) must not be
        silently overridden by the ladder."""
        from types import SimpleNamespace

        from simulate.services.hosted_runner import agent_field_for_run

        agent = self._agent()
        voice_pin = self._version(agent_type=AgentDefinition.AgentTypeChoices.VOICE)
        text_override = self._version(agent_type=AgentDefinition.AgentTypeChoices.TEXT)
        run_test = SimpleNamespace(agent_definition=agent, agent_version=voice_pin)
        assert (
            agent_field_for_run(
                run_test, "agent_type", agent.agent_type, agent_version=text_override
            )
            == AgentDefinition.AgentTypeChoices.TEXT
        )

    def test_agent_version_none_still_resolves_the_definition_ladder(self):
        """An explicit agent_version=None skips the run/execution rungs, but
        _agent_field still resolves the definition's own active_version —
        not a bypass straight to the column."""
        from types import SimpleNamespace

        from simulate.services.hosted_runner import agent_field_for_run

        agent = self._agent()
        agent.agent_type = AgentDefinition.AgentTypeChoices.TEXT
        agent.active_version = self._version(
            agent_type=AgentDefinition.AgentTypeChoices.VOICE
        )
        pinned = self._version(agent_type=AgentDefinition.AgentTypeChoices.TEXT)
        run_test = SimpleNamespace(agent_definition=agent, agent_version=pinned)
        assert (
            agent_field_for_run(
                run_test, "agent_type", agent.agent_type, agent_version=None
            )
            == AgentDefinition.AgentTypeChoices.VOICE
        )

    def test_voice_provider_reads_provider_from_snapshot(self):
        """_voice_provider's own snapshot read (the credentials arm is already
        covered by test_hosted_runner_supports_reads_provider_from_snapshot and
        TestHostedRunnerProviderSupport). Reverting _voice_provider to the
        definition column must fail this."""
        from simulate.services.hosted_runner import _voice_provider

        agent = self._agent(provider="vapi")
        version = self._version(provider="retell")
        assert _voice_provider(agent, None, version) == "retell"

    def test_resolve_run_agent_version_ladder(self):
        """run_test.agent_version wins, then test_execution.agent_version
        (the native path backfills this), then the definition's
        latest_version — the one ladder shared by the view and the builder."""
        from types import SimpleNamespace

        from simulate.services.hosted_runner import resolve_run_agent_version

        latest = self._version(contact_number="+15550000000")
        agent = self._agent()
        agent.latest_version = latest
        pinned = self._version(contact_number="+15551111111")
        backfilled = self._version(contact_number="+15552222222")

        run_test = SimpleNamespace(agent_version=pinned, agent_definition=agent)
        assert resolve_run_agent_version(run_test) is pinned
        assert (
            resolve_run_agent_version(
                run_test, SimpleNamespace(agent_version=backfilled)
            )
            is pinned
        )

        run_test_no_pin = SimpleNamespace(agent_version=None, agent_definition=agent)
        assert (
            resolve_run_agent_version(
                run_test_no_pin, SimpleNamespace(agent_version=backfilled)
            )
            is backfilled
        )
        assert resolve_run_agent_version(run_test_no_pin, None) is latest

    def test_resolve_run_agent_version_prefers_active_over_latest(self):
        """The last rung must match every other version-resolving call site
        in the app (active_version or latest_version), not latest_version
        alone — otherwise an unpinned run can pick a different version (and
        credentials row) than the rest of the platform treats as current."""
        from types import SimpleNamespace

        from simulate.services.hosted_runner import resolve_run_agent_version

        active = self._version(contact_number="+15550000001")
        latest = self._version(contact_number="+15550000002")
        agent = self._agent()
        agent.active_version = active
        agent.latest_version = latest

        run_test_no_pin = SimpleNamespace(agent_version=None, agent_definition=agent)
        assert resolve_run_agent_version(run_test_no_pin, None) is active

    def test_unpinned_full_build_prefers_resolved_version_credentials_over_legacy(
        self,
    ):
        """An unpinned run now resolves active/latest before reading
        credentials, so the version's own credentials row wins over the
        definition-level legacy one — a deliberate repair (the version-scoped
        row is what the platform's credential-editing endpoints write)."""
        from types import SimpleNamespace

        from simulate.services.hosted_runner import (
            _voice_credentials,
            resolve_run_agent_version,
        )

        version_creds = SimpleNamespace(provider_type="retell")
        version = self._version()
        version.credentials = version_creds
        agent = self._agent()
        agent.latest_version = version
        agent.credentials_legacy = SimpleNamespace(provider_type="vapi")

        run_test = SimpleNamespace(agent_version=None, agent_definition=agent)
        resolved = resolve_run_agent_version(run_test, None)
        assert _voice_credentials(agent, resolved) is version_creds

    def test_view_mode_and_builder_transport_agree_when_pin_is_backfilled(self):
        """When run_test.agent_version is None but the execution's pin was
        backfilled (the native path does this before a hosted rerun), the
        shared resolve_run_agent_version ladder must give the view
        (resolve_runner_mode) and the builder (_voice_transport_kind) the same
        version — otherwise the mode/transport mismatch guard at
        _build_voice_job raises HostedRunnerBuildError."""
        from simulate.services.hosted_runner import (
            _voice_transport_kind,
            resolve_run_agent_version,
            resolve_runner_mode,
        )

        agent = self._agent(contact_number="")
        backfilled = self._version(contact_number="+15551234567")
        run_test = SimpleNamespace(agent_version=None, agent_definition=agent)
        test_execution = SimpleNamespace(agent_version=backfilled)

        resolved = resolve_run_agent_version(run_test, test_execution)
        mode = resolve_runner_mode(agent, resolved)
        transport = _voice_transport_kind(
            agent, "retell", inbound=False, agent_version=resolved
        )
        assert mode == "voice_sip"
        assert transport == "sip_inbound"


@pytest.mark.django_db
class TestViewGatesReadVersionSnapshot:
    """The DB-free suites above stand in AgentDefinition/AgentVersion with
    SimpleNamespace; these use real ORM instances (a real configuration_snapshot
    JSONField round-trip) to prove each request-time gate still reads the
    pinned version, not a column edited after the pin, the same way the
    builder does for the same run/execution. Most tests pin ``run_test``
    directly, so the ladder's active_version/latest_version querying
    properties are exercised only where a test leaves the run unpinned."""

    @staticmethod
    def _pinned_voice_agent(organization, workspace, *, provider="retell", phone=""):
        """Column says TEXT; the pinned version's snapshot, captured while the
        column still said VOICE, says VOICE — mirrors an agent_type edit that
        reached the column after a version was already pinned."""
        agent = AgentDefinition.objects.create(
            agent_name="Gate Agent",
            agent_type=AgentDefinition.AgentTypeChoices.VOICE,
            contact_number=phone,
            inbound=True,
            description="gate agent",
            provider=provider,
            assistant_id="gate_asst",
            organization=organization,
            workspace=workspace,
            languages=["en"],
        )
        version = agent.create_version(
            description="pinned", commit_message="v1", status="active"
        )
        agent.agent_type = AgentDefinition.AgentTypeChoices.TEXT
        agent.save(update_fields=["agent_type"])
        return agent, version

    def test_hosted_runner_eligible_reads_snapshot_not_column(
        self, organization, workspace
    ):
        import simulate.views.run_test as run_test_module
        from simulate.views.run_test import RunTestExecutionView

        agent, version = self._pinned_voice_agent(organization, workspace)
        run_test = RunTest.objects.create(
            name="Gate RT",
            agent_definition=agent,
            agent_version=version,
            organization=organization,
            workspace=workspace,
        )
        # Pin the flag instead of relying on its process default: a TEXT
        # classification would short-circuit True regardless of the flag, so
        # False here can only come from a genuine VOICE read of the snapshot.
        with patch.object(
            run_test_module.app_settings, "HOSTED_RUNNER_VOICE_ENABLED", False
        ):
            assert RunTestExecutionView()._hosted_runner_eligible(run_test) is False

    def test_hosted_runner_eligible_routes_voice_when_enabled(
        self, organization, workspace
    ):
        import simulate.views.run_test as run_test_module
        from simulate.views.run_test import RunTestExecutionView

        agent, version = self._pinned_voice_agent(organization, workspace)
        run_test = RunTest.objects.create(
            name="Gate RT",
            agent_definition=agent,
            agent_version=version,
            organization=organization,
            workspace=workspace,
        )
        with patch.object(
            run_test_module.app_settings, "HOSTED_RUNNER_VOICE_ENABLED", True
        ):
            assert RunTestExecutionView()._hosted_runner_eligible(run_test) is True

    def test_hosted_execution_eligible_reads_snapshot_not_column(
        self, organization, workspace
    ):
        import simulate.views.run_test as run_test_module
        from simulate.views.run_test import _hosted_execution_eligible

        agent, version = self._pinned_voice_agent(organization, workspace)
        run_test = RunTest.objects.create(
            name="Gate RT",
            agent_definition=agent,
            agent_version=version,
            organization=organization,
            workspace=workspace,
        )
        test_execution = SimTestExecution.objects.create(run_test=run_test)
        with patch.object(
            run_test_module.app_settings, "HOSTED_RUNNER_VOICE_ENABLED", False
        ):
            assert _hosted_execution_eligible(run_test, test_execution) is False

    def test_hosted_execution_eligible_routes_voice_when_enabled(
        self, organization, workspace
    ):
        import simulate.views.run_test as run_test_module
        from simulate.views.run_test import _hosted_execution_eligible

        agent, version = self._pinned_voice_agent(organization, workspace)
        run_test = RunTest.objects.create(
            name="Gate RT",
            agent_definition=agent,
            agent_version=version,
            organization=organization,
            workspace=workspace,
        )
        test_execution = SimTestExecution.objects.create(run_test=run_test)
        with patch.object(
            run_test_module.app_settings, "HOSTED_RUNNER_VOICE_ENABLED", True
        ):
            assert _hosted_execution_eligible(run_test, test_execution) is True

    def test_hosted_runner_mode_reads_snapshot_not_column(
        self, organization, workspace
    ):
        from simulate.views.run_test import RunTestExecutionView

        agent, version = self._pinned_voice_agent(
            organization, workspace, phone="+15551234567"
        )
        run_test = RunTest.objects.create(
            name="Gate RT",
            agent_definition=agent,
            agent_version=version,
            organization=organization,
            workspace=workspace,
        )
        # A TEXT read would resolve "chat"; the pinned snapshot's VOICE +
        # phone must resolve voice_sip instead.
        assert RunTestExecutionView()._hosted_runner_mode(run_test) == "voice_sip"

    def test_components_patch_gate_reads_snapshot_not_column(
        self, auth_client, organization, workspace
    ):
        # A TEXT read of the stale column would return 200 (no check run);
        # only a VOICE read of the pinned snapshot 400s on the missing key.
        agent, version = self._pinned_voice_agent(organization, workspace)
        run_test = RunTest.objects.create(
            name="Gate RT",
            agent_definition=agent,
            organization=organization,
            workspace=workspace,
        )
        response = auth_client.patch(
            f"/simulate/run-tests/{run_test.id}/components/",
            {"version": str(version.id), "enable_tool_evaluation": True},
            format="json",
        )
        assert response.status_code == 400
        assert response.json()["result"]["error_code"] == (
            "API_KEY_AND_ASSISTANT_ID_REQUIRED"
        )

    def test_create_run_test_view_gate_reads_snapshot_not_column(
        self, auth_client, organization, workspace, scenario
    ):
        # A TEXT read of the stale column would skip the entitlement gate
        # and 201; only a VOICE read of the version about to be pinned
        # reaches check_feature and 403s, before any RunTest row exists.
        from ee.usage.schemas.events import CheckResult

        agent, version = self._pinned_voice_agent(organization, workspace)
        with (
            patch("ee.usage.deployment.DeploymentMode.is_cloud", return_value=True),
            patch("tfc.ee_gates.voice_sim_oss_gate_response", return_value=None),
            patch(
                "ee.usage.services.entitlements.Entitlements.check_feature"
            ) as mock_check,
        ):
            mock_check.return_value = CheckResult(
                allowed=False,
                reason="Voice simulation requires PAYG plan",
                error_code="ENTITLEMENT_DENIED",
            )
            response = auth_client.post(
                "/simulate/run-tests/create/",
                {
                    "name": "Gate Create RT",
                    "agent_definition_id": str(agent.id),
                    "agent_version": str(version.id),
                    "scenario_ids": [str(scenario.id)],
                },
                format="json",
            )
        assert response.status_code == 403
        mock_check.assert_called_once_with(str(organization.id), "has_voice_sim")

    def test_bulk_rerun_routes_each_execution_by_its_own_pinned_version(
        self, auth_client, organization, workspace, scenario
    ):
        """Two executions of the same unpinned run_test, pinned to different
        versions whose OWN declared provider differs (one Bland/unsupported,
        one Retell/supported) — the bulk rerun must dispatch each through the
        runner its own pin resolves to, not one decision for the whole batch."""
        import simulate.views.run_test as run_test_module

        agent = AgentDefinition.objects.create(
            agent_name="Bulk Rerun Agent",
            agent_type=AgentDefinition.AgentTypeChoices.VOICE,
            contact_number="",
            inbound=True,
            description="bulk rerun agent",
            provider="bland",
            organization=organization,
            workspace=workspace,
            languages=["en"],
        )
        unsupported_version = agent.create_version(
            description="unsupported provider",
            commit_message="v-bland",
            status="active",
        )
        agent.provider = "retell"
        agent.save(update_fields=["provider"])
        supported_version = agent.create_version(
            description="supported provider", commit_message="v-retell", status="active"
        )

        run_test = RunTest.objects.create(
            name="Bulk Rerun RT",
            agent_definition=agent,
            organization=organization,
            workspace=workspace,
        )
        run_test.scenarios.add(scenario)

        native_execution = SimTestExecution.objects.create(
            run_test=run_test,
            agent_definition=agent,
            agent_version=unsupported_version,
            status=SimTestExecution.ExecutionStatus.COMPLETED,
            total_scenarios=1,
            scenario_ids=[str(scenario.id)],
        )
        hosted_execution = SimTestExecution.objects.create(
            run_test=run_test,
            agent_definition=agent,
            agent_version=supported_version,
            status=SimTestExecution.ExecutionStatus.COMPLETED,
            total_scenarios=1,
            scenario_ids=[str(scenario.id)],
        )
        CallExecution.objects.create(
            test_execution=native_execution,
            scenario=scenario,
            phone_number="+1234567890",
            status=CallExecution.CallStatus.COMPLETED,
            service_provider_call_id="vapi-test-123",
            eval_outputs={"eval1": {"score": 0.9}},
            call_metadata={
                "base_prompt": "You are a test agent",
                "voice_settings": {"provider": "elevenlabs"},
                "call_direction": "inbound",
                "eval_started": True,
                "eval_completed": True,
            },
        )
        CallExecution.objects.create(
            test_execution=hosted_execution,
            scenario=scenario,
            phone_number="+1234567890",
            status=CallExecution.CallStatus.COMPLETED,
            service_provider_call_id="vapi-test-456",
            eval_outputs={"eval1": {"score": 0.7}},
            call_metadata={
                "base_prompt": "You are a test agent",
                "voice_settings": {"provider": "elevenlabs"},
                "call_direction": "inbound",
                "eval_started": True,
                "eval_completed": True,
            },
        )

        with (
            patch.object(
                run_test_module.app_settings, "HOSTED_RUNNER_VOICE_ENABLED", True
            ),
            patch.object(
                run_test_module, "_voice_sim_gate_response", return_value=None
            ),
            patch.object(
                run_test_module,
                "_dispatch_hosted_rerun",
                return_value="hosted-wf",
            ) as dispatch_hosted,
            patch(
                "simulate.temporal.client.rerun_call_executions",
                return_value={"merged": False, "workflow_id": "native-wf"},
            ) as dispatch_native,
        ):
            response = auth_client.post(
                f"/simulate/run-tests/{run_test.id}/rerun-test-executions/",
                {
                    "rerun_type": "call_and_eval",
                    "test_execution_ids": [
                        str(native_execution.id),
                        str(hosted_execution.id),
                    ],
                },
                format="json",
            )

        assert response.status_code == 200
        dispatch_hosted.assert_called_once()
        assert dispatch_hosted.call_args[0][0].id == hosted_execution.id
        dispatch_native.assert_called_once()
        assert dispatch_native.call_args.kwargs["test_execution_id"] == str(
            native_execution.id
        )
        # A swallowed per-call error (missing agent_definition, say) would
        # still return 200 with a per-execution failure_count > 0 instead of
        # tripping dispatch_native/dispatch_hosted — assert it directly too.
        results_by_id = {r["test_execution_id"]: r for r in response.data["results"]}
        assert results_by_id[str(native_execution.id)]["failure_count"] == 0
        assert results_by_id[str(hosted_execution.id)]["failure_count"] == 0
