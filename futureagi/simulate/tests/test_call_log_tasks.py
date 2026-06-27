"""
Tests for the call-log Logs reader endpoint and core/EE dispatch boundary.

Covers TH-4335:
- `CallExecutionLogsView` returns HTTP 200 with an empty paginated page
  when the call has no logs (previously returned 400).
- Lazy backfill dispatches the EE ingestion task only when EE voice is present.
"""

import builtins
from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

import pytest
from django.utils import timezone
from rest_framework import status

from model_hub.models.choices import DatasetSourceChoices, SourceChoices, StatusType
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from simulate.models import AgentDefinition, CallLogEntry, Scenarios
from simulate.models.run_test import RunTest
from simulate.models.simulator_agent import SimulatorAgent
from simulate.models.test_execution import CallExecution, TestExecution
from simulate.serializers.test_execution import CallExecutionDetailSerializer

try:
    from ee.voice.tasks.call_log_tasks import _ingest_call_logs, ingest_call_logs_task
except ImportError:
    _ingest_call_logs = None
    ingest_call_logs_task = None

requires_ee_voice = pytest.mark.skipif(
    ingest_call_logs_task is None,
    reason="EE voice call-log ingestion task is not available in OSS",
)


# ============================================================================
# Fixtures — minimal call_execution with full ancestry
# ============================================================================


@pytest.fixture
def keep_test_db_connection_open():
    """The helper closes stale worker connections in production; keep pytest's
    transaction connection open while exercising the helper directly."""
    with (
        patch("ee.voice.tasks.call_log_tasks.close_old_connections", return_value=None),
        patch("tfc.temporal.drop_in.decorator.close_old_connections", return_value=None),
    ):
        yield


@pytest.fixture(autouse=True)
def _keep_test_db_connection_open(keep_test_db_connection_open):
    yield


@pytest.fixture
def agent_definition(db, organization, workspace):
    return AgentDefinition.objects.create(
        agent_name="Test Agent",
        agent_type=AgentDefinition.AgentTypeChoices.VOICE,
        contact_number="+1234567890",
        inbound=True,
        description="Test agent",
        organization=organization,
        workspace=workspace,
        languages=["en"],
    )


@pytest.fixture
def simulator_agent(db, organization, workspace):
    return SimulatorAgent.objects.create(
        name="Test Simulator",
        prompt="You are a simulator.",
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
    col = Column.objects.create(
        dataset=dataset,
        name="situation",
        data_type="text",
        source=SourceChoices.OTHERS.value,
    )
    dataset.column_order = [str(col.id)]
    dataset.save()

    row = Row.objects.create(dataset=dataset, order=0)
    Cell.objects.create(dataset=dataset, column=col, row=row, value="Test situation")
    return dataset


@pytest.fixture
def scenario(db, organization, workspace, dataset_for_scenario, agent_definition):
    return Scenarios.objects.create(
        name="Test Scenario",
        description="desc",
        source="src",
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
        description="desc",
        agent_definition=agent_definition,
        simulator_agent=simulator_agent,
        organization=organization,
        workspace=workspace,
    )
    rt.scenarios.add(scenario)
    return rt


@pytest.fixture
def test_execution(db, run_test, simulator_agent, agent_definition):
    return TestExecution.objects.create(
        run_test=run_test,
        status=TestExecution.ExecutionStatus.COMPLETED,
        total_scenarios=1,
        total_calls=1,
        simulator_agent=simulator_agent,
        agent_definition=agent_definition,
    )


@pytest.fixture
def call_execution(db, test_execution, scenario):
    return CallExecution.objects.create(
        test_execution=test_execution,
        scenario=scenario,
        phone_number="+1234567890",
        status=CallExecution.CallStatus.COMPLETED,
        service_provider_call_id="vapi-test-123",
        call_metadata={"call_direction": "outbound"},
    )


def test_call_execution_serializer_ignores_missing_scenario_row(call_execution):
    call_execution.call_metadata = {"row_id": str(uuid4())}

    serializer = CallExecutionDetailSerializer(
        call_execution,
        context={
            "rows_map": {},
            "columns_by_dataset": {},
            "cells_by_row": {},
            "snapshots_by_call": {},
        },
    )

    assert serializer.data["scenario_columns"] == {}


def test_call_execution_serializer_exposes_raw_simulation_metrics(call_execution):
    call_execution.duration_seconds = 42
    call_execution.response_time_ms = 1234
    call_execution.avg_agent_latency_ms = 567
    call_execution.cost_cents = 89
    call_execution.customer_cost_cents = 123
    call_execution.save(
        update_fields=[
            "duration_seconds",
            "response_time_ms",
            "avg_agent_latency_ms",
            "cost_cents",
            "customer_cost_cents",
        ]
    )

    data = CallExecutionDetailSerializer(call_execution).data

    assert data["duration"] == 42
    assert data["duration_seconds"] == 42
    assert data["response_time"] == 1.234
    assert data["response_time_ms"] == 1234
    assert data["avg_agent_latency"] == 567
    assert data["avg_agent_latency_ms"] == 567
    assert data["cost_cents"] == 89
    assert data["customer_cost_cents"] == 123


def _outputs(eval_outputs):
    from types import SimpleNamespace

    return CallExecutionDetailSerializer().get_eval_outputs(
        SimpleNamespace(eval_outputs=eval_outputs)
    )


def _metrics(eval_outputs):
    from types import SimpleNamespace

    return CallExecutionDetailSerializer().get_eval_metrics(
        SimpleNamespace(eval_outputs=eval_outputs, id=None)
    )


def test_outputs_carries_all_three_axes_for_choice_scores_dict():
    out = _outputs(
        {
            "e1": {
                "output_bool": None,
                "output_float": 1.0,
                "output_str_list": ["Good"],
                "output": {"score": 1.0, "choice": "Good"},
                "name": "cs-template",
            }
        }
    )
    assert out["e1"]["output_pass"] is None
    assert out["e1"]["output_score"] == pytest.approx(1.0)
    assert out["e1"]["output_choices"] == ["Good"]


def test_outputs_pending_status_emits_canonical_none_payload():
    out = _outputs({"e1": {"status": "pending"}})
    assert out["e1"] == {
        "output_pass": None,
        "output_score": None,
        "output_choices": None,
        "status": "pending",
    }


def test_outputs_grouped_obj_with_count_returns_empty():
    serializer = CallExecutionDetailSerializer()
    assert serializer.get_eval_outputs({"eval_outputs": {"e1": {}}, "count": 3}) == {}


def test_outputs_preserves_value_reason_name_type_status_skipped():
    out = _outputs(
        {
            "e1": {
                "output_float": 0.5,
                "output": 0.5,
                "reason": "ok",
                "name": "score-template",
                "output_type": "score",
                "status": "completed",
            }
        }
    )
    entry = out["e1"]
    assert entry["reason"] == "ok"
    assert entry["name"] == "score-template"
    assert entry["type"] == "score"
    assert entry["status"] == "completed"
    assert entry["skipped"] is False
    assert entry["error"] is False


def test_metrics_renames_axes():
    metrics = _metrics(
        {
            "e1": {
                "output_bool": False,
                "output_float": 0.0,
                "output_str_list": ["x"],
                "output": "x",
                "name": "n",
            }
        }
    )
    assert metrics["e1"]["output_pass"] is False
    assert metrics["e1"]["output_score"] == 0.0
    assert metrics["e1"]["output_choices"] == ["x"]
    for storage_key in ("output_bool", "output_float", "output_str_list"):
        assert storage_key not in metrics["e1"]


@pytest.mark.parametrize(
    "storage_key,api_key,value",
    [
        ("output_bool", "output_pass", True),
        ("output_bool", "output_pass", False),
        ("output_float", "output_score", 0.0),
        ("output_float", "output_score", 1.0),
        ("output_str_list", "output_choices", ["one"]),
        ("output_str_list", "output_choices", ["a", "b"]),
    ],
)
def test_outputs_rename_round_trip(storage_key, api_key, value):
    eval_data = {storage_key: value, "output": value, "name": "n"}
    out = _outputs({"e1": eval_data})
    assert out["e1"][api_key] == value
    assert storage_key not in out["e1"]


def _fake_log_payload(body, severity="INFO", category="llm", ts_ms=1_700_000_000_000):
    """Build a VAPI-shaped log payload dict."""
    return {
        "time": ts_ms,
        "level": 30,
        "severityText": severity,
        "body": body,
        "attributes": {"category": category},
    }


@pytest.mark.django_db
@requires_ee_voice
class TestIngestCallLogsHelper:
    """_ingest_call_logs — plain-Python helper extracted from the Temporal
    task so activities can run ingestion inline without chaining a second
    Temporal activity."""

    def test_persists_rows_and_summary_for_customer_source(self, call_execution):
        payloads = [
            _fake_log_payload("first line"),
            _fake_log_payload("second line", severity="WARN", category="model"),
        ]
        with patch("ee.voice.tasks.call_log_tasks.VoiceServiceManager") as MockVSM:
            MockVSM.return_value.iter_call_logs.return_value = iter(payloads)

            ok = _ingest_call_logs(
                str(call_execution.id),
                "https://example.com/log",
                source=CallLogEntry.LogSource.CUSTOMER,
            )

        assert ok is True
        rows = CallLogEntry.objects.filter(call_execution=call_execution)
        assert rows.count() == 2
        assert set(rows.values_list("source", flat=True)) == {
            CallLogEntry.LogSource.CUSTOMER
        }

        call_execution.refresh_from_db()
        assert call_execution.customer_logs_summary["total_entries"] == 2

    def test_customer_vs_agent_summary_field(self, call_execution):
        with patch("ee.voice.tasks.call_log_tasks.VoiceServiceManager") as MockVSM:
            MockVSM.return_value.iter_call_logs.return_value = iter(
                [_fake_log_payload("x")]
            )
            _ingest_call_logs(
                str(call_execution.id),
                "https://example.com/log",
                source=CallLogEntry.LogSource.AGENT,
            )

        call_execution.refresh_from_db()
        # AGENT source populates logs_summary (not customer_logs_summary)
        assert call_execution.logs_summary == {
            "total_entries": 1,
            "level_counts": {"30": 1},
            "category_counts": {"llm": 1},
            "last_logged_at": call_execution.logs_summary["last_logged_at"],
        }
        assert not call_execution.customer_logs_summary

    def test_missing_call_execution_returns_false_without_raising(self, db):
        # Non-existent call id should be handled gracefully (False return)
        # so the caller's own persistence doesn't get rolled back.
        ok = _ingest_call_logs(
            "00000000-0000-0000-0000-000000000000",
            "https://example.com/log",
            source=CallLogEntry.LogSource.CUSTOMER,
        )
        assert ok is False


# ============================================================================
# ingest_call_logs_task — legacy wrapper forwards to helper
# ============================================================================


@pytest.mark.django_db
@requires_ee_voice
def test_ingest_call_logs_task_delegates_to_helper(call_execution):
    """The Temporal-decorated wrapper must remain a thin pass-through so the
    legacy TestExecutor code path continues working. Run it on an empty
    iterator — the wrapper's only job is to forward args to the helper."""
    with patch("ee.voice.tasks.call_log_tasks.VoiceServiceManager") as MockVSM:
        MockVSM.return_value.iter_call_logs.return_value = iter([])
        ok = ingest_call_logs_task(
            str(call_execution.id),
            "https://example.com/log",
            verify_ssl=False,
            source=CallLogEntry.LogSource.CUSTOMER,
        )

    assert ok is True
    MockVSM.return_value.iter_call_logs.assert_called_once_with(
        url="https://example.com/log",
        verify_ssl=False,
    )


@requires_ee_voice
def test_temporal_worker_imports_call_log_activity_module():
    from tfc.temporal.common.registry import (
        TEMPORAL_ACTIVITY_MODULES,
        _import_temporal_activity_modules,
    )
    from tfc.temporal.drop_in.decorator import _ACTIVITY_REGISTRY

    assert "ee.voice.tasks.call_log_tasks" in TEMPORAL_ACTIVITY_MODULES

    _import_temporal_activity_modules()

    assert _ACTIVITY_REGISTRY["ingest_call_logs_task"]["queue"] == "tasks_l"


# ============================================================================
# CallExecutionLogsView — empty result returns 200, not 400
# ============================================================================


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
class TestCallExecutionLogsViewEmpty:
    """Regression guard for TH-4335: the reader used to return HTTP 400
    ("No logs found") on an empty queryset, which the frontend can't tell
    apart from a real error. Empty now returns 200 with an empty page."""

    URL_TEMPLATE = "/simulate/call-executions/{}/logs/"

    def test_returns_200_with_empty_results_when_no_logs(
        self, auth_client, call_execution
    ):
        url = self.URL_TEMPLATE.format(call_execution.id)
        response = auth_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        payload = response.json()
        # Paginated response shape: {count, next, previous, results: {...}}
        assert payload.get("count", 0) == 0
        # The inner `results` dict carries the logs array + source marker
        inner = payload.get("results") or {}
        assert inner.get("results") == []
        assert inner.get("ingestion_pending") is False

    def test_returns_persisted_customer_logs(self, auth_client, call_execution):
        CallLogEntry.objects.create(
            call_execution=call_execution,
            source=CallLogEntry.LogSource.CUSTOMER,
            provider=CallLogEntry.Provider.VAPI,
            logged_at=timezone.now(),
            level=30,
            severity_text="INFO",
            category="model",
            body="first line",
            attributes={"category": "model"},
            payload={"body": "first line", "attributes": {"category": "model"}},
        )

        response = auth_client.get(self.URL_TEMPLATE.format(call_execution.id))

        assert response.status_code == status.HTTP_200_OK
        payload = response.json()
        inner = payload.get("results") or {}
        assert payload.get("count") == 1
        assert inner.get("source") == CallLogEntry.LogSource.CUSTOMER
        assert inner.get("ingestion_pending") is False
        assert inner.get("results")[0]["body"] == "first line"

    def test_lazy_backfill_without_ee_returns_empty_completed(
        self, auth_client, call_execution, monkeypatch
    ):
        call_execution.provider_call_data = {
            "vapi": {"artifact": {"logUrl": "https://example.com/call.log.gz"}}
        }
        call_execution.save(update_fields=["provider_call_data"])

        original_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "ee.voice.tasks.call_log_tasks":
                raise ImportError("No module named 'ee'")
            return original_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)

        response = auth_client.get(self.URL_TEMPLATE.format(call_execution.id))

        assert response.status_code == status.HTTP_200_OK
        payload = response.json()
        inner = payload.get("results") or {}
        assert payload.get("count", 0) == 0
        assert inner.get("results") == []
        assert inner.get("ingestion_pending") is False

        call_execution.refresh_from_db()
        assert call_execution.customer_log_url == "https://example.com/call.log.gz"
        assert call_execution.customer_logs_summary["total_entries"] == 0
        assert (
            call_execution.customer_logs_summary["skipped_reason"]
            == "ee_voice_not_available"
        )

    @requires_ee_voice
    def test_lazy_backfill_dispatches_when_provider_log_url_exists(
        self, auth_client, call_execution
    ):
        call_execution.provider_call_data = {
            "vapi": {"artifact": {"logUrl": "https://example.com/call.log.gz"}}
        }
        call_execution.save(update_fields=["provider_call_data"])

        with patch(
            "ee.voice.tasks.call_log_tasks.ingest_call_logs_task.apply_async"
        ) as apply_async:
            response = auth_client.get(self.URL_TEMPLATE.format(call_execution.id))

        assert response.status_code == status.HTTP_200_OK
        apply_async.assert_called_once_with(
            args=(str(call_execution.id), "https://example.com/call.log.gz"),
            kwargs={
                "verify_ssl": False,
                "source": CallLogEntry.LogSource.CUSTOMER,
            },
        )
        call_execution.refresh_from_db()
        assert call_execution.customer_log_url == "https://example.com/call.log.gz"
        assert call_execution.logs_ingested_at is not None

        payload = response.json()
        assert payload.get("count", 0) == 0
        inner = payload.get("results") or {}
        assert inner.get("results") == []
        assert inner.get("ingestion_pending") is True

    @requires_ee_voice
    def test_stale_ingestion_attempt_is_not_pending_forever(
        self, auth_client, call_execution
    ):
        call_execution.customer_log_url = "https://example.com/call.log.gz"
        call_execution.logs_ingested_at = timezone.now() - timedelta(minutes=10)
        call_execution.save(update_fields=["customer_log_url", "logs_ingested_at"])

        with patch(
            "ee.voice.tasks.call_log_tasks.ingest_call_logs_task.apply_async"
        ) as apply_async:
            response = auth_client.get(self.URL_TEMPLATE.format(call_execution.id))

        assert response.status_code == status.HTTP_200_OK
        apply_async.assert_not_called()
        payload = response.json()
        assert payload.get("count", 0) == 0
        inner = payload.get("results") or {}
        assert inner.get("results") == []
        assert inner.get("ingestion_pending") is False

    @pytest.mark.e2e
    @requires_ee_voice
    def test_e2e_first_open_pending_then_ingested_rows_are_returned(
        self, auth_client, call_execution
    ):
        """E2E-style API flow: first drawer open starts ingestion, second open
        returns persisted logs after the ingestion task has completed."""

        call_execution.provider_call_data = {
            "vapi": {"artifact": {"logUrl": "https://example.com/call.log.gz"}}
        }
        call_execution.save(update_fields=["provider_call_data"])

        with patch(
            "ee.voice.tasks.call_log_tasks.ingest_call_logs_task.apply_async"
        ) as apply_async:
            first_response = auth_client.get(
                self.URL_TEMPLATE.format(call_execution.id)
            )

        assert first_response.status_code == status.HTTP_200_OK
        apply_async.assert_called_once()
        assert first_response.json()["results"]["ingestion_pending"] is True

        CallLogEntry.objects.create(
            call_execution=call_execution,
            source=CallLogEntry.LogSource.CUSTOMER,
            provider=CallLogEntry.Provider.VAPI,
            logged_at=timezone.now(),
            level=40,
            severity_text="ERROR",
            category="webhook",
            body="provider webhook failed",
            attributes={"category": "webhook"},
            payload={"body": "provider webhook failed"},
        )
        call_execution.refresh_from_db()
        call_execution.customer_logs_summary = {
            "total_entries": 1,
            "level_counts": {"40": 1},
            "category_counts": {"webhook": 1},
        }
        call_execution.save(update_fields=["customer_logs_summary"])

        second_response = auth_client.get(self.URL_TEMPLATE.format(call_execution.id))

        assert second_response.status_code == status.HTTP_200_OK
        payload = second_response.json()
        assert payload["count"] == 1
        assert payload["results"]["ingestion_pending"] is False
        assert payload["results"]["results"][0]["body"] == "provider webhook failed"


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
class TestCallExecutionDetailView:
    URL_TEMPLATE = "/simulate/call-executions/{}/"

    def test_returns_actual_provider_from_stored_provider_payload(
        self, auth_client, call_execution
    ):
        call_execution.provider_call_data = {
            "livekit": {
                "room_sid": "RM_test",
                "recording": {"stereo": "https://example.com/stereo.wav"},
            }
        }
        call_execution.save(update_fields=["provider_call_data"])

        response = auth_client.get(self.URL_TEMPLATE.format(call_execution.id))

        assert response.status_code == status.HTTP_200_OK
        payload = response.json()
        assert payload["provider"] == "livekit"
        assert payload["attributes"]["raw_log"]["room_sid"] == "RM_test"


class TestSimulateWriterPattern:
    @staticmethod
    def _mk_eval_config(*, output: str, multi_choice: bool = False):
        from types import SimpleNamespace

        template = SimpleNamespace(
            config={"output": output},
            multi_choice=multi_choice,
        )
        return SimpleNamespace(
            id="cfg-1",
            name="dummy-eval",
            eval_template=template,
        )

    @pytest.mark.parametrize(
        "output,value,populated_axis,expected",
        [
            ("score", 0.7, "output_float", 0.7),
            ("numeric", 0.42, "output_float", 0.42),
            ("Pass/Fail", "Passed", "output_bool", True),
            ("Pass/Fail", "Failed", "output_bool", False),
            ("choices", "always", "output_str_list", ["always"]),
        ],
    )
    def test_value_routes_to_correct_axis(
        self, output, value, populated_axis, expected
    ):
        from evaluations.engine.normalize import eval_config_output
        from simulate.utils.processing_outcomes import build_simulate_eval_payload

        cfg = self._mk_eval_config(output=output)
        payload = build_simulate_eval_payload(
            value=value,
            config_output=eval_config_output(cfg),
            name=cfg.name,
        )
        assert payload[populated_axis] == expected

    def test_choice_scores_dict_populates_both_axes(self):
        from evaluations.engine.normalize import eval_config_output
        from simulate.utils.processing_outcomes import build_simulate_eval_payload

        cfg = self._mk_eval_config(output="score")
        payload = build_simulate_eval_payload(
            value={"score": 0.8, "choice": "good"},
            config_output=eval_config_output(cfg),
            name=cfg.name,
        )
        assert payload["output_float"] == pytest.approx(0.8)
        assert payload["output_str_list"] == ["good"]

    def test_choice_scores_list_of_dicts_populates_both_axes(self):
        from evaluations.engine.normalize import eval_config_output
        from simulate.utils.processing_outcomes import build_simulate_eval_payload

        cfg = self._mk_eval_config(output="choices")
        payload = build_simulate_eval_payload(
            value=[{"score": 0.6, "choice": "a"}, {"score": 0.9, "choice": "b"}],
            config_output=eval_config_output(cfg),
            name=cfg.name,
        )
        assert payload["output_str_list"] == ["a", "b"]
        assert payload["output_float"] == pytest.approx(0.75)

    def test_none_value_yields_all_none_axes_and_preserves_error_metadata(self):
        from evaluations.engine.normalize import eval_config_output
        from simulate.utils.processing_outcomes import build_simulate_eval_payload

        cfg = self._mk_eval_config(output="score")
        payload = build_simulate_eval_payload(
            value=None,
            config_output=eval_config_output(cfg),
            reason="missing transcript",
            name=cfg.name,
            error="error",
            status="failed",
        )
        assert payload["output_bool"] is None
        assert payload["output_float"] is None
        assert payload["output_str_list"] is None
        assert payload["error"] == "error"
        assert payload["status"] == "failed"

    def test_eval_config_output_defaults_to_score_on_missing_template(self):
        from types import SimpleNamespace

        from evaluations.engine.normalize import eval_config_output
        from simulate.utils.processing_outcomes import build_simulate_eval_payload

        cfg = SimpleNamespace(id="cfg-2", name="no-template", eval_template=None)
        payload = build_simulate_eval_payload(
            value=0.5,
            config_output=eval_config_output(cfg),
            name=cfg.name,
        )
        assert payload["output_float"] == 0.5
