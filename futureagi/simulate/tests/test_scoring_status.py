"""Scoring status of simulate calls: the dispatch stamps, the clocks, the
sweeper, the run settle and the v3 calls fields that expose them.

Every hand-off to Temporal and the websocket is a spy (``dispatch``), so the
code under test runs for real against the database and nothing leaves it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.utils import timezone

from model_hub.models.choices import StatusType
from model_hub.models.evals_metric import EvalTemplate
from simulate.models import AgentDefinition, Scenarios, SimulateEvalConfig
from simulate.models.run_test import RunTest
from simulate.models.simulator_agent import SimulatorAgent
from simulate.models.test_execution import CallExecution, TestExecution
from simulate.services.harness_run_evals import (
    EVAL_QUEUE_STAMP_SKEW,
    EVAL_QUEUE_STAMP_WINDOW,
    EVAL_QUEUED_KEY,
    _queued_within_window,
    parse_stamp,
    stamp_eval_queued,
)
from simulate.services.scoring_status import (
    CSAT_STAMP_KEY,
    EVAL_PROGRESS_KEY,
    pending_eval_ids,
)

HOSTED_ATTEMPTS = "/simulate/api/harness/attempts"
ALK_BASE = "/simulate/api/alk-simulate"
CLOSED = {"eval_started": True, "eval_completed": True}


@pytest.fixture(autouse=True)
def dispatch():
    """Spies on every hand-off the scoring code can make."""
    with (
        patch(
            "simulate.services.test_executor._run_simulate_evaluations_task.apply_async"
        ) as evals,
        patch(
            "simulate.tasks.alk_sim.calculate_alk_voice_csat_score.apply_async"
        ) as csat,
        patch(
            "simulate.tasks.eval_summary_tasks.run_eval_summary_task.apply_async"
        ) as summary,
        patch(
            "simulate.tasks.chat_sim.monitor_test_execution_for_chat.apply_async"
        ) as monitor,
        patch(
            "simulate.utils.websocket_notifications.notify_simulation_update"
        ) as notify,
        patch("simulate.tasks.chat_sim.notify_simulation_update") as chat_notify,
        patch(
            "simulate.services.alk_simulate_ingestion.notify_simulation_update"
        ) as alk_notify,
    ):
        yield SimpleNamespace(
            evals=evals,
            csat=csat,
            summary=summary,
            monitor=monitor,
            notify=notify,
            chat_notify=chat_notify,
            alk_notify=alk_notify,
        )


@pytest.fixture(autouse=True)
def _capturable_test_executor_logger(monkeypatch):
    """pytest collects ``simulate/services/test_executor.py`` as a test file
    when a run includes ``simulate``; reading its ``logger`` then caches the
    proxy with the import-time processors, which ``capture_logs()`` cannot
    swap. A fresh proxy resolves the processors at first use, inside the test.
    """
    import structlog

    from simulate.services import test_executor

    monkeypatch.setattr(
        test_executor, "logger", structlog.get_logger(test_executor.__name__)
    )


@pytest.fixture
def agent_definition(db, organization, workspace):
    return AgentDefinition.objects.create(
        agent_name="Scoring Agent",
        agent_type=AgentDefinition.AgentTypeChoices.VOICE,
        contact_number="+12813716796",
        inbound=True,
        description="Agent under test for scoring status",
        organization=organization,
        workspace=workspace,
        languages=["en"],
    )


@pytest.fixture
def simulator_agent(db, organization, workspace):
    return SimulatorAgent.objects.create(
        name="Scoring Simulator",
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
        name="Scoring Scenario",
        description="Scenario for scoring status tests",
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
        name="Scoring Run Test",
        description="Run for scoring status tests",
        agent_definition=agent_definition,
        simulator_agent=simulator_agent,
        organization=organization,
        workspace=workspace,
    )
    rt.scenarios.add(scenario)
    return rt


@pytest.fixture
def eval_template(db, organization):
    return EvalTemplate.objects.create(
        name="Scoring Eval",
        config={"required_keys": ["conversation"]},
        organization=organization,
    )


def _config(run_test, eval_template, *, name=None, mapping=None, **fields):
    return SimulateEvalConfig.objects.create(
        name=name or f"Eval {SimulateEvalConfig.objects.count() + 1}",
        eval_template=eval_template,
        run_test=run_test,
        mapping=mapping if mapping is not None else {"conversation": "transcript"},
        config={},
        **fields,
    )


def _run(run_test, status, **fields):
    return TestExecution.objects.create(
        run_test=run_test,
        status=status,
        total_scenarios=1,
        agent_definition=run_test.agent_definition,
        **fields,
    )


def _call(execution, *, status="completed", metadata=None, eval_outputs=None, **fields):
    return CallExecution.objects.create(
        test_execution=execution,
        scenario=execution.run_test.scenarios.first(),
        status=status,
        call_metadata=metadata if metadata is not None else {},
        eval_outputs=eval_outputs if eval_outputs is not None else {},
        **fields,
    )


# --- the dispatch stamps and the shared helpers -----------------------------


NOW = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    "stored,expected",
    [
        pytest.param(None, None, id="absent"),
        pytest.param("not-a-date", None, id="malformed"),
        pytest.param("2026-02-30T00:00:00", None, id="impossible-date"),
        pytest.param(
            "2026-09-29T12:00:00+00:00",
            datetime(2026, 9, 29, 12, tzinfo=UTC),
            id="aware",
        ),
    ],
)
def test_extracted_stamp_helpers_unchanged(stored, expected):
    """``parse_stamp`` is the old window's parse, and the window still reads
    the same stamps the same way, on both edges."""
    assert parse_stamp(stored) == expected

    naive = parse_stamp("2026-09-29T12:00:00")
    assert naive is not None and naive.tzinfo is not None
    assert naive == timezone.make_aware(
        datetime(2026, 9, 29, 12), timezone.get_default_timezone()
    )

    def window(stamp):
        return _queued_within_window(
            {EVAL_QUEUED_KEY: {"cfg": stamp.isoformat()}}, "cfg", now=NOW
        )

    assert window(NOW) is True
    assert window(NOW - EVAL_QUEUE_STAMP_WINDOW + timedelta(seconds=1)) is True
    assert window(NOW - EVAL_QUEUE_STAMP_WINDOW) is False
    assert window(NOW + EVAL_QUEUE_STAMP_SKEW) is True
    assert window(NOW + EVAL_QUEUE_STAMP_SKEW + timedelta(seconds=1)) is False
    assert _queued_within_window(
        {EVAL_QUEUED_KEY: {"cfg": stored}}, "cfg", now=NOW
    ) is (expected is not None and expected == NOW)

    original = {"kept": "2026-09-01T00:00:00+00:00"}
    metadata = {EVAL_QUEUED_KEY: original}
    returned = stamp_eval_queued(metadata, ["a", 7], now=NOW)
    assert returned is metadata
    assert metadata[EVAL_QUEUED_KEY] == {
        "kept": "2026-09-01T00:00:00+00:00",
        "a": NOW.isoformat(),
        "7": NOW.isoformat(),
    }
    assert original == {"kept": "2026-09-01T00:00:00+00:00"}, "copied, not changed"
    untouched = {"eval_started": True}
    assert stamp_eval_queued(untouched, [], now=NOW) == {"eval_started": True}
    assert stamp_eval_queued({EVAL_QUEUED_KEY: "corrupt"}, ["a"], now=NOW) == {
        EVAL_QUEUED_KEY: {"a": NOW.isoformat()}
    }


@pytest.mark.django_db
def test_receipt_dispatch_stamps(run_test, eval_template, dispatch):
    """The latch and the stamps are one locked write: a second delivery holding
    an older copy is refused, ``None`` stamps nothing, and a failed hand-off
    keeps its stamps, while an unstamped one still expects its configs."""
    from simulate.services.alk_simulate_ingestion import _dispatch_evaluations_once

    config = _config(run_test, eval_template)
    config_id = str(config.id)
    execution = _run(run_test, TestExecution.ExecutionStatus.RUNNING)

    call = _call(execution)
    older_copy = CallExecution.objects.get(id=call.id)
    before = timezone.now()
    assert _dispatch_evaluations_once(call, eval_config_ids=[config_id]) is True
    assert _dispatch_evaluations_once(older_copy, eval_config_ids=[config_id]) is False
    assert dispatch.evals.call_count == 1
    stored = CallExecution.objects.get(id=call.id).call_metadata
    assert stored["eval_started"] is True
    assert before <= parse_stamp(stored[EVAL_QUEUED_KEY][config_id]) <= timezone.now()
    assert call.call_metadata == stored, "the caller's copy is what was written"

    unstamped = _call(execution)
    assert _dispatch_evaluations_once(unstamped, eval_config_ids=None) is True
    assert (
        EVAL_QUEUED_KEY not in CallExecution.objects.get(id=unstamped.id).call_metadata
    )

    dispatch.evals.side_effect = RuntimeError("broker down")
    failed = _call(execution)
    assert _dispatch_evaluations_once(failed, eval_config_ids=[config_id]) is False
    stored = CallExecution.objects.get(id=failed.id).call_metadata
    assert stored["eval_started"] is False
    assert "eval_dispatch_failed" in stored
    assert config_id in stored[EVAL_QUEUED_KEY], "the stamp stays, so its clock runs"
    assert pending_eval_ids(stored, {}, [config_id]) == {config_id}

    failed_unstamped = _call(execution)
    assert _dispatch_evaluations_once(failed_unstamped, eval_config_ids=None) is False
    stored = CallExecution.objects.get(id=failed_unstamped.id).call_metadata
    assert EVAL_QUEUED_KEY not in stored
    assert pending_eval_ids(stored, {}, [config_id]) == {config_id}, (
        "a failed dispatch with no stamps still expects the runnable configs, "
        "so it reads as pending and times out, never as nothing to score"
    )


def _hosted_receipt_call(organization, capture, scenario_key, *, lookup_error=None):
    """One hosted scenario that ends with a passed receipt and no evals selected."""
    from rest_framework.test import APIClient

    from simulate.services.hosted_harness import (
        canonical_digest,
        create_hosted_job,
        register_attempt,
    )
    from simulate.services.hosted_harness_ingestion import ingest_result_receipt

    from .test_hosted_harness_channels import _headers, _payload

    job, _ = create_hosted_job(
        organization, _payload(), idempotency_key=f"scoring-{scenario_key}"
    )
    capability = register_attempt(job.id, endpoint_base_url="https://platform.example")
    client = APIClient()
    provision = client.post(
        f"{HOSTED_ATTEMPTS}/{capability.attempt.id}/scenarios/",
        {
            "operation": "provision",
            "name": f"Scoring {scenario_key}",
            "modality": "text",
            "personas": [
                {
                    "scenario_key": scenario_key,
                    "name": "Caller",
                    "situation": "Needs help",
                    "outcome": "Receives help",
                }
            ],
        },
        format="json",
        **_headers(capability),
    )
    assert provision.status_code == 200, provision.content
    provisioned = provision.json()["result"]
    client.post(
        f"{HOSTED_ATTEMPTS}/{capability.attempt.id}/scenarios/",
        {
            "operation": "begin",
            "run_test_id": provisioned["run_test_id"],
            "scenario_keys": [scenario_key],
        },
        format="json",
        **_headers(capability),
    )
    receipt = {
        "schema_version": "futureagi.harness-result.v1",
        "job_id": str(job.id),
        "attempt_id": str(capability.attempt.id),
        "attempt_number": 1,
        "scenario_key": scenario_key,
        "scenario_id": provisioned["scenarios"][0]["scenario_id"],
        "scenario_attempt": 1,
        "world_index": None,
        "status": "passed",
        "sub_goals": [],
        "evaluations": [],
        "call": None,
        "failure": None,
    }
    receipt["digest"] = canonical_digest(receipt)
    lookup = (
        patch(
            "simulate.services.harness_evals._tool_evaluation_on",
            side_effect=lookup_error,
        )
        if lookup_error
        else patch(
            "simulate.services.harness_evals._tool_evaluation_on", return_value=False
        )
    )
    with lookup, capture(execute=True):
        _, created = ingest_result_receipt(capability.attempt, receipt)
    assert created is True
    return CallExecution.objects.get(hosted_registration__scenario_key=scenario_key)


@pytest.mark.django_db
def test_hosted_receipt_eval_side(
    organization, django_capture_on_commit_callbacks, dispatch
):
    """A receipt with nothing to grade closes the call's eval side, so it
    cannot hold its run as scoring; a failed selection lookup leaves it open
    with only ``eval_started``, so its evals read as pending and time out."""
    from django.db.utils import OperationalError

    closed = _hosted_receipt_call(
        organization, django_capture_on_commit_callbacks, "nothing-to-grade"
    )
    assert closed.status == CallExecution.CallStatus.COMPLETED
    assert closed.call_metadata["eval_started"] is True
    assert closed.call_metadata["eval_completed"] is True
    dispatch.evals.assert_not_called()

    held = _hosted_receipt_call(
        organization,
        django_capture_on_commit_callbacks,
        "lookup-failed",
        lookup_error=OperationalError("connection reset"),
    )
    assert held.call_metadata["eval_started"] is True
    assert "eval_completed" not in held.call_metadata
    dispatch.evals.assert_not_called()


@pytest.mark.parametrize(
    "metadata,outputs,fallback,live,expected",
    [
        pytest.param(
            {}, {"a": {"status": "pending"}}, (), None, {"a"}, id="placeholder"
        ),
        pytest.param(
            {}, {"a": {"status": " Pending "}}, (), None, {"a"}, id="placeholder-case"
        ),
        pytest.param(
            {EVAL_QUEUED_KEY: {"a": "2026-09-29T12:00:00+00:00"}},
            {},
            (),
            None,
            {"a"},
            id="stamped-absent",
        ),
        pytest.param(
            {EVAL_QUEUED_KEY: {"a": "2026-09-29T12:00:00+00:00"}},
            {"a": {"status": "Completed"}},
            (),
            None,
            set(),
            id="stamped-present",
        ),
        pytest.param(
            {"eval_started": True},
            {"a": {"status": "Completed"}},
            ("a", "b"),
            None,
            {"b"},
            id="fallback-open",
        ),
        pytest.param(CLOSED, {}, ("a", "b"), None, set(), id="fallback-closed"),
        pytest.param({}, {}, ("a", "b"), None, set(), id="fallback-never-started"),
        pytest.param(
            {"eval_started": False, "eval_dispatch_failed": ""},
            {},
            ("a", "b"),
            None,
            {"a", "b"},
            id="fallback-dispatch-failed",
        ),
        pytest.param(
            {"eval_dispatch_failed": "x", "eval_completed": True},
            {},
            ("a",),
            None,
            set(),
            id="dispatch-failed-then-closed",
        ),
        pytest.param(
            {"eval_started": True, EVAL_QUEUED_KEY: {"a": "x"}},
            {},
            ("a", "b"),
            None,
            {"a"},
            id="stamps-beat-fallback",
        ),
        pytest.param(
            {EVAL_QUEUED_KEY: {"a": "x", "gone": "x"}},
            {"gone2": {"status": "pending"}},
            (),
            ("a",),
            {"a"},
            id="live-ids-drop-removed",
        ),
        pytest.param(None, None, ("a",), None, set(), id="corrupt-columns"),
    ],
)
def test_pending_eval_ids(metadata, outputs, fallback, live, expected):
    assert pending_eval_ids(metadata, outputs, fallback, live_ids=live) == expected


@pytest.mark.django_db
def test_scoring_keys_reset_and_reserved(auth_client, run_test, dispatch):
    """A rerun reset drops the dispatch stamps and the dispatch-failure flag,
    and an SDK result can never forge a scoring clock or a non-bool
    ``eval_completed``."""
    from simulate.services.alk_simulate_ingestion import (
        _RESERVED_CALL_METADATA_KEYS,
        create_alk_sim_test_execution,
    )

    call = _call(
        _run(run_test, TestExecution.ExecutionStatus.COMPLETED),
        metadata={
            **CLOSED,
            EVAL_QUEUED_KEY: {"cfg": "2026-09-29T00:00:00+00:00"},
            "eval_dispatch_failed": "broker down",
            "row_id": "kept",
        },
    )
    call.reset_to_default(save=False)
    assert call.call_metadata == {"row_id": "kept"}

    forged = {
        EVAL_QUEUED_KEY: {"forged": "2999-01-01T00:00:00+00:00"},
        EVAL_PROGRESS_KEY: "2999-01-01T00:00:00+00:00",
        CSAT_STAMP_KEY: "2999-01-01T00:00:00+00:00",
        "eval_completed": "yes",
    }
    assert set(forged) <= _RESERVED_CALL_METADATA_KEYS
    execution = create_alk_sim_test_execution(run_test)
    batch = auth_client.post(
        f"{ALK_BASE}/test-executions/{execution.id}/batch/", {}, format="json"
    )
    assert batch.status_code == 200, batch.content
    call_id = batch.json()["result"]["call_execution_ids"][0]
    response = auth_client.patch(
        f"{ALK_BASE}/call-executions/{call_id}/result/",
        {
            "status": "completed",
            "transcript": [
                {
                    "speaker_role": "user",
                    "content": "Hi, my package is late.",
                    "start_time_ms": 0,
                    "end_time_ms": 2000,
                }
            ],
            "call_metadata": {**forged, "note": "kept"},
        },
        format="json",
    )
    assert response.status_code == 200, response.content
    stored = CallExecution.objects.get(id=call_id).call_metadata
    assert stored["note"] == "kept"
    assert "forged" not in (stored.get(EVAL_QUEUED_KEY) or {})
    assert stored.get(EVAL_PROGRESS_KEY) != forged[EVAL_PROGRESS_KEY]
    assert stored.get(CSAT_STAMP_KEY) != forged[CSAT_STAMP_KEY]
    assert stored.get("eval_completed") != "yes"


@pytest.mark.django_db
def test_second_add_eval_while_first_in_flight(run_test, eval_template):
    """While a call is still being graded -- by a first add-eval or by its own
    receipt job -- a second add-eval passes it over as ``skipped_pending`` and
    changes nothing, so two jobs never save ``eval_outputs`` over each other."""
    from simulate.services.harness_run_evals import queue_eval_for_finished_calls

    execution = _run(run_test, TestExecution.ExecutionStatus.COMPLETED)
    first = _config(run_test, eval_template)
    second = _config(run_test, eval_template)
    graded = _call(execution, metadata=dict(CLOSED))
    receipt_job = _call(
        execution,
        metadata={
            "eval_started": True,
            EVAL_QUEUED_KEY: {str(first.id): timezone.now().isoformat()},
        },
    )

    assert queue_eval_for_finished_calls(execution, first)["queued"] == 1
    graded.refresh_from_db()
    receipt_job.refresh_from_db()
    before = {
        call.id: (dict(call.call_metadata), dict(call.eval_outputs or {}))
        for call in (graded, receipt_job)
    }

    counts = queue_eval_for_finished_calls(execution, second)

    assert counts == {
        "queued": 0,
        "skipped_existing": 0,
        "skipped_in_flight": 0,
        "skipped_pending": 2,
        "completed_calls": 2,
    }
    for call in (graded, receipt_job):
        call.refresh_from_db()
        assert (dict(call.call_metadata), dict(call.eval_outputs or {})) == before[
            call.id
        ]
    assert graded.eval_outputs == {str(first.id): {"status": "pending"}}
    assert str(second.id) not in graded.call_metadata[EVAL_QUEUED_KEY]


@pytest.mark.django_db
@pytest.mark.parametrize(
    "progress,due_at",
    [
        pytest.param(
            NOW + timedelta(minutes=1), NOW + timedelta(minutes=11), id="no-progress"
        ),
        pytest.param(None, NOW + timedelta(minutes=30), id="never-started"),
    ],
)
def test_add_eval_requeues_a_lost_job_once_its_clock_is_due(
    run_test, eval_template, progress, due_at
):
    """A job lost in a run the sweeper never visits (here ``failed``) leaves
    its call open for good. Add-eval waits on it only until the scoring clock
    counts it as stuck -- 10 minutes after its last progress, or 30 after its
    dispatch when it never started -- and then queues the call again."""
    from simulate.services.harness_run_evals import queue_eval_for_finished_calls

    execution = _run(run_test, TestExecution.ExecutionStatus.FAILED)
    config = _config(run_test, eval_template)
    metadata = {
        "eval_started": True,
        "eval_completed": False,
        EVAL_QUEUED_KEY: {str(config.id): NOW.isoformat()},
    }
    if progress is not None:
        metadata[EVAL_PROGRESS_KEY] = progress.isoformat()
    placeholder = {str(config.id): {"status": "pending"}}
    call = _call(
        execution,
        metadata=metadata,
        eval_outputs=placeholder,
        completed_at=NOW - timedelta(hours=1),
    )
    clock = "simulate.services.harness_run_evals.timezone.now"

    with patch(clock, return_value=due_at - timedelta(seconds=1)):
        early = queue_eval_for_finished_calls(execution, config)

    assert (early["queued"], early["skipped_pending"]) == (0, 1)
    call.refresh_from_db()
    assert call.call_metadata == metadata

    with patch(clock, return_value=due_at):
        due = queue_eval_for_finished_calls(execution, config)

    assert (due["queued"], due["skipped_pending"]) == (1, 0)
    call.refresh_from_db()
    assert call.call_metadata[EVAL_QUEUED_KEY] == {str(config.id): due_at.isoformat()}
    assert call.call_metadata["eval_completed"] is False
    assert call.eval_outputs == placeholder
    execution.refresh_from_db()
    assert execution.status == TestExecution.ExecutionStatus.FAILED


# --- the eval task and the native settle ------------------------------------


_NO_SKIP = SimpleNamespace(processing_skipped=False, processing_skip_reason=None)


@pytest.mark.django_db
@pytest.mark.parametrize("branch", ["configs", "tool_only"])
def test_eval_progress_stamped_per_step(run_test, eval_template, branch):
    """The job clock is refreshed at the start, before each config and before
    the tool judge on both branches, so a job that is still working is never
    read as silent; a stamp that cannot be written is logged and grading goes on.

    With the clock table's ``eval_progress_at + 10 min`` edge, this order is
    what keeps a call whose tool step started 9 minutes in open at minute 10.
    """
    from structlog.testing import capture_logs

    from simulate.services import scoring_status
    from simulate.services.test_executor import TestExecutor

    run_test.enable_tool_evaluation = True
    run_test.save(update_fields=["enable_tool_evaluation"])
    if branch == "configs":
        _config(run_test, eval_template)
        _config(run_test, eval_template)
    selection = None if branch == "configs" else []
    call = _call(
        _run(run_test, TestExecution.ExecutionStatus.RUNNING), duration_seconds=120
    )
    events = []
    real_mark = scoring_status.mark_eval_progress

    def _mark(call_execution):
        real_mark(call_execution)
        stored = CallExecution.objects.get(id=call_execution.id).call_metadata
        events.append(("progress", stored.get(EVAL_PROGRESS_KEY)))

    def _run_task():
        with (
            patch("simulate.services.test_executor.close_old_connections"),
            patch(
                "simulate.services.test_executor.decide_processing_skip",
                lambda **_: _NO_SKIP,
            ),
            patch.object(
                TestExecutor,
                "_get_call_transcript_data",
                return_value={"transcript": "Customer: hi"},
            ),
            patch.object(
                TestExecutor,
                "_run_single_simulate_evaluation",
                side_effect=lambda config, *_: events.append(("config", config.id)),
            ),
            patch.object(
                TestExecutor,
                "_run_tool_evaluation",
                side_effect=lambda *_: events.append(("tool", None)),
            ),
            patch.object(TestExecutor, "_check_and_update_eval_completion"),
        ):
            TestExecutor(initialize_voice_service=False)._run_simulate_evaluations(
                call, eval_config_ids=selection
            )

    with patch(
        "simulate.services.scoring_status.mark_eval_progress", side_effect=_mark
    ):
        _run_task()
    kinds = [kind for kind, _ in events]
    if branch == "configs":
        assert kinds == [
            "progress",
            "progress",
            "config",
            "progress",
            "config",
            "progress",
            "tool",
        ]
    else:
        assert kinds == ["progress", "progress", "tool"]
    assert all(stamp for kind, stamp in events if kind == "progress")

    stamps_expected = kinds.count("progress")
    events.clear()
    with (
        patch(
            "simulate.services.scoring_status.locked_call_metadata_update",
            side_effect=RuntimeError("database gone"),
        ),
        capture_logs() as logs,
    ):
        _run_task()
    assert [kind for kind, _ in events] == [
        kind for kind in kinds if kind != "progress"
    ], "a failed stamp never stops a step"
    failures = [
        line for line in logs if line["event"] == "simulate_eval_progress_stamp_failed"
    ]
    assert len(failures) == stamps_expected
    assert all(line["call_execution_id"] == str(call.id) for line in failures)


@pytest.mark.django_db
@pytest.mark.parametrize("result", [None, {}], ids=["none", "empty"])
def test_eval_no_result_writes_failed_entry(run_test, eval_template, result):
    """An evaluator that returns nothing leaves a failed entry with a reason
    a person can read, instead of a missing one that holds the call open."""
    from structlog.testing import capture_logs

    from simulate.services.scoring_status import REASON_EVAL_NO_RESULT
    from simulate.services.test_executor import TestExecutor
    from simulate.utils.eval_summary import derive_kpi_output_type

    config = _config(run_test, eval_template)
    call = _call(_run(run_test, TestExecution.ExecutionStatus.RUNNING))
    transcript_data = {
        "transcript": "Customer: hi",
        "voice_recording": None,
        "assistant_recording": None,
        "customer_recording": None,
        "stereo_recording": None,
        "user_chat_transcript": "",
        "assistant_chat_transcript": "",
    }
    with (
        patch("simulate.services.test_executor.close_old_connections"),
        patch(
            "simulate.services.test_executor.build_simulation_context_map",
            return_value=({}, {}),
        ),
        patch("simulate.services.test_executor.run_eval_func", return_value=result),
        capture_logs() as logs,
    ):
        TestExecutor(initialize_voice_service=False)._run_single_simulate_evaluation(
            config, call, transcript_data
        )

    call.refresh_from_db()
    entry = dict(call.eval_outputs[str(config.id)])
    assert entry.pop("timestamp")
    assert entry == {
        "reason": REASON_EVAL_NO_RESULT,
        "error": "error",
        "name": config.name,
        "output": None,
        "output_type": derive_kpi_output_type(eval_template),
        "status": "Failed",
    }
    assert [line for line in logs if line["event"] == "simulate_eval_no_result"] == [
        {
            "event": "simulate_eval_no_result",
            "log_level": "warning",
            "call_execution_id": str(call.id),
            "eval_config_id": str(config.id),
        }
    ]


@pytest.mark.django_db
def test_native_settle_arms_and_hooks(
    run_test, django_capture_on_commit_callbacks, dispatch
):
    """The settle holds a run while CSAT is open, never moves a failed,
    cancelled or completed run nor re-stamps it, finishes a cancelling run as
    cancelled, and fires the summary and live update once per completion."""
    from simulate.models.test_execution import EvalExplanationSummaryStatus
    from simulate.services.test_executor import TestExecutor

    settle = TestExecutor(
        initialize_voice_service=False
    )._check_and_update_test_execution_completion
    Status = TestExecution.ExecutionStatus
    earlier = timezone.now() - timedelta(hours=2)

    held = _run(run_test, Status.EVALUATING)
    _call(held, metadata={**CLOSED, "csat_status": "pending"})
    settle(held.id)
    held.refresh_from_db()
    assert held.status == Status.EVALUATING
    assert held.completed_at is None

    for status in (Status.FAILED, Status.CANCELLED, Status.COMPLETED):
        finished = _run(run_test, status, completed_at=earlier)
        _call(finished, metadata=dict(CLOSED))
        settle(finished.id)
        finished.refresh_from_db()
        assert (finished.status, finished.completed_at) == (status, earlier)

    still_scoring = _run(run_test, Status.CANCELLING)
    _call(still_scoring, metadata={"eval_started": True})
    settle(still_scoring.id)
    still_scoring.refresh_from_db()
    assert still_scoring.status == Status.CANCELLING

    for completed_at in (earlier, None):
        stopping = _run(run_test, Status.CANCELLING, completed_at=completed_at)
        _call(stopping, metadata=dict(CLOSED))
        _call(stopping, status=CallExecution.CallStatus.CANCELLED)
        settle(stopping.id)
        stopping.refresh_from_db()
        assert stopping.status == Status.CANCELLED
        if completed_at is None:
            assert stopping.completed_at is not None
        else:
            assert stopping.completed_at == earlier

    # A regraded run still holds its previous summary; completing it again
    # must mark that summary stale.
    done = _run(
        run_test,
        Status.EVALUATING,
        eval_explanation_summary_status=EvalExplanationSummaryStatus.COMPLETED,
    )
    _call(done, metadata=dict(CLOSED))
    _call(done, status=CallExecution.CallStatus.FAILED)
    # The summary task reads the run on its own connection, so it must not be
    # queued before the completed run is committed.
    with django_capture_on_commit_callbacks() as callbacks:
        settle(done.id)
        settle(done.id)
        dispatch.summary.assert_not_called()
        dispatch.notify.assert_not_called()
    for callback in callbacks:
        callback()
    done.refresh_from_db()
    assert done.status == Status.COMPLETED
    assert done.completed_at is not None
    assert (done.total_calls, done.completed_calls, done.failed_calls) == (2, 1, 1)
    assert done.eval_explanation_summary_status == EvalExplanationSummaryStatus.PENDING
    dispatch.summary.assert_called_once_with(args=(str(done.id),))
    dispatch.notify.assert_called_once_with(
        organization_id=run_test.organization_id,
        run_test_id=str(run_test.id),
        test_execution_id=str(done.id),
    )


@pytest.mark.django_db
def test_settle_hook_failure_is_logged_and_the_other_hook_runs(
    run_test, django_capture_on_commit_callbacks, dispatch
):
    """A summary that cannot be queued still completes the run, still sends
    the live update, and leaves a log line naming the hook."""
    from structlog.testing import capture_logs

    from simulate.services.test_executor import TestExecutor

    run = _run(run_test, TestExecution.ExecutionStatus.EVALUATING)
    _call(run, metadata=dict(CLOSED))
    dispatch.summary.side_effect = RuntimeError("broker down")

    with capture_logs() as logs, django_capture_on_commit_callbacks(execute=True):
        TestExecutor(
            initialize_voice_service=False
        )._check_and_update_test_execution_completion(run.id)

    run.refresh_from_db()
    assert run.status == TestExecution.ExecutionStatus.COMPLETED
    assert [
        (entry["test_execution_id"], entry["hook"])
        for entry in logs
        if entry["event"] == "simulate_run_completed_hooks_failed"
    ] == [(str(run.id), "summary")]
    dispatch.notify.assert_called_once()


@pytest.mark.django_db
@pytest.mark.parametrize("other", ["removed", "live_placeholder"])
def test_removed_eval_does_not_hold_call(run_test, eval_template, other):
    """A job closes its call once its own configs are written, unless another
    live config is still expected; a config deleted since it was stamped no
    longer counts."""
    from simulate.services.test_executor import TestExecutor

    graded = _config(run_test, eval_template)
    second = _config(run_test, eval_template)
    stamped_at = timezone.now().isoformat()
    outputs = {str(graded.id): {"status": "Completed", "output": "Passed"}}
    if other == "removed":
        SimulateEvalConfig.objects.filter(id=second.id).update(deleted=True)
    else:
        outputs[str(second.id)] = {"status": "pending"}
    run = _run(run_test, TestExecution.ExecutionStatus.EVALUATING)
    call = _call(
        run,
        metadata={
            "eval_started": True,
            "eval_completed": False,
            EVAL_QUEUED_KEY: {str(graded.id): stamped_at, str(second.id): stamped_at},
        },
        eval_outputs=outputs,
    )

    TestExecutor(initialize_voice_service=False)._check_and_update_eval_completion(
        call, eval_config_ids=[str(graded.id)]
    )

    call.refresh_from_db()
    run.refresh_from_db()
    if other == "removed":
        assert call.call_metadata["eval_completed"] is True
        assert run.status == TestExecution.ExecutionStatus.COMPLETED
    else:
        assert call.call_metadata["eval_completed"] is False
        assert run.status == TestExecution.ExecutionStatus.EVALUATING


# --- CSAT -------------------------------------------------------------------


@pytest.mark.django_db
def test_csat_stamps_and_late_result(run_test, dispatch):
    """The CSAT dispatch is one locked write that stamps and rebinds, so a
    re-ingest holding an older copy does not dispatch twice; each attempt's
    ``running`` restarts the clock; a late result beats a timeout."""
    from simulate.services.alk_simulate_ingestion import _dispatch_csat_once
    from simulate.services.scoring_status import REASON_CSAT_TIMED_OUT_RUNNING
    from simulate.tasks.alk_sim import _set_csat_state

    call = _call(_run(run_test, TestExecution.ExecutionStatus.RUNNING))
    older_copy = CallExecution.objects.get(id=call.id)

    _dispatch_csat_once(call)
    _dispatch_csat_once(older_copy)

    assert dispatch.csat.call_count == 1
    stored = CallExecution.objects.get(id=call.id).call_metadata
    assert stored["csat_dispatched"] is True
    assert stored["csat_status"] == "pending"
    assert parse_stamp(stored[CSAT_STAMP_KEY]) is not None
    assert call.call_metadata == stored, "the caller's later save keeps the dispatch"

    old_stamp = "2026-01-01T00:00:00+00:00"
    CallExecution.objects.filter(id=call.id).update(
        call_metadata={**stored, CSAT_STAMP_KEY: old_stamp}
    )
    _set_csat_state(call, "running")
    running = CallExecution.objects.get(id=call.id).call_metadata
    assert running["csat_status"] == "running"
    assert parse_stamp(running[CSAT_STAMP_KEY]) > parse_stamp(old_stamp)

    CallExecution.objects.filter(id=call.id).update(
        call_metadata={
            **running,
            "csat_status": "timed_out",
            "csat_error": REASON_CSAT_TIMED_OUT_RUNNING,
        }
    )
    _set_csat_state(call, "completed")
    final = CallExecution.objects.get(id=call.id).call_metadata
    assert final["csat_status"] == "completed"
    assert "csat_error" not in final


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("arm", "csat_status"),
    [("scored", "completed"), ("scorer_failed", "failed"), ("already", "completed")],
)
def test_csat_task_settles_the_run_on_every_arm(run_test, arm, csat_status):
    """CSAT is the last thing a run waits on once its evals are closed, so
    every way the task ends must hand the run to the settle, after CSAT's own
    terminal state is written."""
    from simulate.tasks import alk_sim

    run = _run(run_test, TestExecution.ExecutionStatus.EVALUATING)
    call = _call(
        run,
        metadata={**CLOSED, "csat_status": "pending"},
        recording_url="https://example.com/rec.wav",
        conversation_metrics_data={"csat_score": 6.0} if arm == "already" else {},
    )
    score = None if arm == "scorer_failed" else 8.0

    with (
        patch("simulate.tasks.alk_sim.close_old_connections"),
        patch.object(alk_sim, "_run_agent_csat", return_value=score),
    ):
        if arm == "scorer_failed":
            with pytest.raises(RuntimeError, match="returned no result"):
                alk_sim.calculate_alk_voice_csat_score._original_func(str(call.id))
        else:
            alk_sim.calculate_alk_voice_csat_score._original_func(str(call.id))

    call.refresh_from_db()
    run.refresh_from_db()
    assert call.call_metadata["csat_status"] == csat_status
    assert run.status == TestExecution.ExecutionStatus.COMPLETED


# --- the transport writers hand the run to the settle -----------------------


def _cleanup_attempt(organization, execution, key):
    """A hosted attempt that finished its scenarios, ready for sandbox cleanup."""
    from simulate.models import HostedHarnessAttempt, HostedHarnessJob
    from simulate.services.hosted_harness import create_hosted_job, register_attempt

    from .test_hosted_harness_channels import _payload

    environment, _ = create_hosted_job(
        organization, _payload(), idempotency_key=f"{key}-environment"
    )
    job, _ = create_hosted_job(organization, _payload(), idempotency_key=key)
    attempt = register_attempt(
        job.id, endpoint_base_url="https://platform.example.com"
    ).attempt
    HostedHarnessJob.no_workspace_objects.filter(id=job.id).update(
        environment=environment, test_execution=execution
    )
    attempt.provider_ref = f"sandbox-{key}"
    attempt.terminal_stage = "completed"
    attempt.terminal_event_received = True
    attempt.manifest_acked = True
    attempt.state = HostedHarnessAttempt.State.COMPLETED
    attempt.save()
    return attempt


def _run_settle_hook(callbacks):
    hooks = [
        callback
        for callback in callbacks
        if callback.__qualname__.startswith("settle_run_after_commit")
    ]
    assert len(hooks) == 1, "the writer hands the run to the settle once"
    hooks[0]()


@pytest.mark.django_db
def test_record_cleanup_holds_run_evaluating(
    organization, run_test, django_capture_on_commit_callbacks
):
    """Sandbox cleanup ends transport, not scoring: the run waits in
    ``evaluating`` until CSAT is done, and a run being stopped is left to
    the settle."""
    from simulate.services.hosted_harness import record_cleanup
    from simulate.services.test_executor import TestExecutor
    from simulate.tasks.alk_sim import _set_csat_state

    Status = TestExecution.ExecutionStatus
    scoring = _run(run_test, Status.RUNNING)
    call = _call(scoring, metadata={**CLOSED, "csat_status": "pending"})
    attempt = _cleanup_attempt(organization, scoring, "cleanup-holds")
    with django_capture_on_commit_callbacks(execute=False) as callbacks:
        record_cleanup(
            attempt.id, provider_ref=attempt.provider_ref, verified_absent=True
        )
    _run_settle_hook(callbacks)
    scoring.refresh_from_db()
    assert scoring.status == Status.EVALUATING
    assert scoring.completed_at is None

    _set_csat_state(call, "completed")
    TestExecutor(
        initialize_voice_service=False
    )._check_and_update_test_execution_completion(scoring.id)
    scoring.refresh_from_db()
    assert scoring.status == Status.COMPLETED
    assert scoring.completed_at is not None

    stopping = _run(run_test, Status.CANCELLING)
    _call(stopping, metadata={**CLOSED, "csat_status": "pending"})
    attempt = _cleanup_attempt(organization, stopping, "cleanup-cancelling")
    with django_capture_on_commit_callbacks(execute=False) as callbacks:
        record_cleanup(
            attempt.id, provider_ref=attempt.provider_ref, verified_absent=True
        )
    _run_settle_hook(callbacks)
    stopping.refresh_from_db()
    assert stopping.status == Status.CANCELLING
    assert stopping.completed_at is None


@pytest.mark.django_db
def test_record_cleanup_settle_hook_closes_race(
    organization, run_test, django_capture_on_commit_callbacks
):
    """Scoring that closes between the cleanup's write and its commit is still
    seen: the settle runs after the commit and completes the run."""
    from simulate.services.hosted_harness import record_cleanup
    from simulate.tasks.alk_sim import _set_csat_state

    run = _run(run_test, TestExecution.ExecutionStatus.RUNNING)
    call = _call(run, metadata={**CLOSED, "csat_status": "running"})
    attempt = _cleanup_attempt(organization, run, "cleanup-race")
    with django_capture_on_commit_callbacks(execute=False) as callbacks:
        record_cleanup(
            attempt.id, provider_ref=attempt.provider_ref, verified_absent=True
        )
    run.refresh_from_db()
    assert run.status == TestExecution.ExecutionStatus.EVALUATING

    _set_csat_state(call, "completed")
    _run_settle_hook(callbacks)

    run.refresh_from_db()
    assert run.status == TestExecution.ExecutionStatus.COMPLETED


@pytest.mark.django_db
def test_alk_roll_up_scoring_aware(run_test):
    """The SDK roll-up moves only a run in transport: it never overrides a
    cancel, holds a scoring run in ``evaluating``, and a replayed ingest on a
    completed run changes neither its status nor its ``completed_at``."""
    from simulate.services.alk_simulate_ingestion import _roll_up_external_execution

    Status = TestExecution.ExecutionStatus
    earlier = timezone.now() - timedelta(hours=2)

    cancelled = _run(run_test, Status.CANCELLED, completed_at=earlier)
    _call(cancelled, metadata=dict(CLOSED))
    _call(cancelled, status=CallExecution.CallStatus.FAILED)
    _roll_up_external_execution(cancelled.id)
    cancelled.refresh_from_db()
    assert (cancelled.status, cancelled.completed_at) == (Status.CANCELLED, earlier)

    scoring = _run(run_test, Status.PENDING)
    _call(scoring, metadata={**CLOSED, "csat_status": "pending"})
    _call(scoring, status=CallExecution.CallStatus.FAILED)
    _roll_up_external_execution(scoring.id)
    scoring.refresh_from_db()
    assert scoring.status == Status.EVALUATING
    assert scoring.completed_at is None
    assert (scoring.total_calls, scoring.completed_calls, scoring.failed_calls) == (
        2,
        1,
        1,
    )

    done = _run(run_test, Status.COMPLETED, completed_at=earlier)
    _call(done, metadata=dict(CLOSED))
    _roll_up_external_execution(done.id)
    done.refresh_from_db()
    assert (done.status, done.completed_at) == (Status.COMPLETED, earlier)

    lost = _run(run_test, Status.RUNNING)
    _call(lost, status=CallExecution.CallStatus.FAILED)
    _roll_up_external_execution(lost.id)
    lost.refresh_from_db()
    assert lost.status == Status.FAILED
    assert lost.completed_at is not None


@pytest.mark.django_db
def test_chat_monitor_delegates_to_settle(
    run_test, django_capture_on_commit_callbacks, dispatch
):
    """The chat monitor leaves a run being stopped alone, moves a
    finished-transport run to ``evaluating``, and lets only the settle
    complete it."""
    from simulate.tasks.alk_sim import _set_csat_state
    from simulate.tasks.chat_sim import monitor_test_execution_for_chat

    Status = TestExecution.ExecutionStatus
    stopping = _run(run_test, Status.CANCELLING)
    _call(stopping, metadata=dict(CLOSED))
    monitor_test_execution_for_chat._original_func(str(stopping.id))
    stopping.refresh_from_db()
    assert stopping.status == Status.CANCELLING

    run = _run(run_test, Status.PENDING)
    call = _call(run, metadata={**CLOSED, "csat_status": "pending"})
    monitor_test_execution_for_chat._original_func(str(run.id))
    run.refresh_from_db()
    assert run.status == Status.EVALUATING
    dispatch.summary.assert_not_called()

    _set_csat_state(call, "completed")
    with django_capture_on_commit_callbacks(execute=True):
        monitor_test_execution_for_chat._original_func(str(run.id))
    run.refresh_from_db()
    assert run.status == Status.COMPLETED
    dispatch.summary.assert_called_once_with(args=(str(run.id),))


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("run_status", "expected"),
    [
        ("pending", "failed"),
        ("running", "failed"),
        ("evaluating", "evaluating"),
        ("completed", "completed"),
    ],
)
def test_chat_monitor_fails_only_a_run_in_transport(
    run_test, dispatch, run_status, expected
):
    """Every call failing is a transport verdict: the monitor fails a run
    still in transport, never one that has left it since."""
    from simulate.tasks.chat_sim import monitor_test_execution_for_chat

    run = _run(run_test, run_status)
    _call(run, status=CallExecution.CallStatus.FAILED)
    monitor_test_execution_for_chat._original_func(str(run.id))
    run.refresh_from_db()
    assert run.status == expected
    assert dispatch.chat_notify.called == (run_status in ("pending", "running"))


# Queries under asyncio.run() use their own connection, which sees only
# committed rows.
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    "run_status",
    [TestExecution.ExecutionStatus.EVALUATING, TestExecution.ExecutionStatus.COMPLETED],
)
def test_mark_terminal_keeps_evaluating_run(run_test, monkeypatch, run_status):
    """A failed runner child does not turn a run that is already scoring (or
    already completed) into a failed one, nor fail its remaining calls."""
    import asyncio

    from simulate.temporal.activities import hosted_runner
    from simulate.temporal.types.hosted_runner import FinalizeRunnerInput

    async def _inline(fn, /, *args, **kwargs):
        return fn(*args, **kwargs)

    monkeypatch.setattr(hosted_runner, "_run_db", _inline)
    monkeypatch.setenv("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
    run = _run(run_test, run_status)
    _call(run, metadata={**CLOSED, "csat_status": "pending"})
    ongoing = _call(run, status=CallExecution.CallStatus.ONGOING)

    result = asyncio.run(
        hosted_runner.finalize_hosted_execution(
            FinalizeRunnerInput(test_execution_id=str(run.id), job_phase="failed")
        )
    )

    assert result == "failed"
    run.refresh_from_db()
    ongoing.refresh_from_db()
    assert run.status == run_status
    assert ongoing.status == CallExecution.CallStatus.ONGOING


# --- the one clock and the sweeper ------------------------------------------


def _ago(delta):
    return (NOW - delta).isoformat()


def _inp(
    *,
    call_status="completed",
    meta=None,
    entries=None,
    csat_scored=False,
    has_csat_value=False,
    anchor=None,
):
    from simulate.services.scoring_status import ScoringInput

    return ScoringInput(
        call_status=call_status,
        metadata=meta or {},
        eval_entries=entries or {},
        csat_scored=csat_scored,
        has_csat_value=has_csat_value or csat_scored,
        anchor=anchor or NOW - timedelta(days=1),
    )


_JOB = timedelta(minutes=10)
_START = timedelta(minutes=30)
_SECOND = timedelta(seconds=1)
_STARTED = {"eval_started": True}


@pytest.mark.parametrize(
    "meta,anchor,component,code",
    [
        pytest.param(
            {
                **_STARTED,
                EVAL_QUEUED_KEY: {"a": _ago(2 * _JOB)},
                EVAL_PROGRESS_KEY: _ago(_JOB),
            },
            None,
            "evals",
            "job_timeout",
            id="eval-job-due",
        ),
        pytest.param(
            {
                **_STARTED,
                EVAL_QUEUED_KEY: {"a": _ago(2 * _JOB)},
                EVAL_PROGRESS_KEY: _ago(_JOB - _SECOND),
            },
            None,
            "evals",
            None,
            id="eval-job-1s-early",
        ),
        pytest.param(
            {**_STARTED, EVAL_QUEUED_KEY: {"a": _ago(_START)}},
            None,
            "evals",
            "start_timeout",
            id="eval-start-due",
        ),
        pytest.param(
            {**_STARTED, EVAL_QUEUED_KEY: {"a": _ago(_START - _SECOND)}},
            None,
            "evals",
            None,
            id="eval-start-1s-early",
        ),
        pytest.param(
            {
                **_STARTED,
                EVAL_QUEUED_KEY: {"a": _ago(_START)},
                EVAL_PROGRESS_KEY: _ago(_START + _JOB),
            },
            None,
            "evals",
            "start_timeout",
            id="progress-older-than-dispatch",
        ),
        pytest.param(
            {
                **_STARTED,
                EVAL_QUEUED_KEY: {"a": _ago(_JOB)},
                EVAL_PROGRESS_KEY: _ago(_JOB - timedelta(minutes=9)),
            },
            None,
            "evals",
            None,
            id="tool-step-stamped-at-plus-9",
        ),
        pytest.param(
            {
                **_STARTED,
                EVAL_QUEUED_KEY: {"a": _ago(_JOB)},
                EVAL_PROGRESS_KEY: _ago(_JOB),
            },
            None,
            "evals",
            "job_timeout",
            id="tool-step-not-stamped",
        ),
        pytest.param(_STARTED, NOW - _START, "evals", "start_timeout", id="no-stamps"),
        pytest.param(
            _STARTED, NOW - _START + _SECOND, "evals", None, id="no-stamps-early"
        ),
        pytest.param(
            {
                "eval_started": False,
                "eval_dispatch_failed": True,
                EVAL_QUEUED_KEY: {"a": _ago(_START)},
            },
            None,
            "evals",
            "start_timeout",
            id="stamped-dispatch-failure",
        ),
        pytest.param(
            {"eval_dispatch_failed": True},
            NOW - _START,
            "evals",
            "start_timeout",
            id="unstamped-dispatch-failure",
        ),
        pytest.param(
            {
                **_STARTED,
                EVAL_QUEUED_KEY: {
                    "a": (NOW + EVAL_QUEUE_STAMP_SKEW + _SECOND).isoformat()
                },
            },
            NOW - _START,
            "evals",
            "start_timeout",
            id="far-future-stamp-reads-absent",
        ),
        pytest.param(
            {
                **_STARTED,
                EVAL_QUEUED_KEY: {"a": (NOW + EVAL_QUEUE_STAMP_SKEW).isoformat()},
            },
            NOW - _START,
            "evals",
            None,
            id="skewed-stamp-still-counts",
        ),
        pytest.param(
            {**CLOSED, "csat_status": "running", CSAT_STAMP_KEY: _ago(_JOB)},
            None,
            "csat",
            "job_timeout",
            id="csat-running-due",
        ),
        pytest.param(
            {**CLOSED, "csat_status": "running", CSAT_STAMP_KEY: _ago(_JOB - _SECOND)},
            None,
            "csat",
            None,
            id="csat-running-early",
        ),
        pytest.param(
            {**CLOSED, "csat_status": "pending", CSAT_STAMP_KEY: _ago(_START)},
            None,
            "csat",
            "start_timeout",
            id="csat-pending-due",
        ),
        pytest.param(
            {
                **CLOSED,
                "csat_status": "pending",
                CSAT_STAMP_KEY: _ago(_START - _SECOND),
            },
            None,
            "csat",
            None,
            id="csat-pending-early",
        ),
        pytest.param(
            {**CLOSED, "csat_status": "pending"},
            NOW - _START,
            "csat",
            "start_timeout",
            id="csat-no-stamp",
        ),
    ],
)
def test_scoring_clocks_exactly_at_threshold(meta, anchor, component, code):
    """Each clock is due exactly at its threshold and not one second before;
    a live tool step's stamp keeps its call open (a missing one would not)."""
    from simulate.services.scoring_status import (
        REASON_CSAT_TIMED_OUT_NOT_STARTED,
        REASON_CSAT_TIMED_OUT_RUNNING,
        REASON_EVAL_TIMED_OUT_NOT_STARTED,
        REASON_EVAL_TIMED_OUT_RUNNING,
        scoring_due,
    )

    due = scoring_due(
        _inp(meta=meta, anchor=anchor), runnable_ids={"a"}, cancelled=False, now=NOW
    )

    if component == "evals":
        written = due.evals.get("a")
        reasons = {
            "job_timeout": REASON_EVAL_TIMED_OUT_RUNNING,
            "start_timeout": REASON_EVAL_TIMED_OUT_NOT_STARTED,
        }
    else:
        written = due.csat
        reasons = {
            "job_timeout": REASON_CSAT_TIMED_OUT_RUNNING,
            "start_timeout": REASON_CSAT_TIMED_OUT_NOT_STARTED,
        }
    if code is None:
        assert written is None
    else:
        assert written == ("timed_out", reasons[code], code)


@pytest.mark.django_db
def test_sweep_times_out_dead_job(run_test, eval_template):
    """A job that died mid-grading is written ``timed_out`` in the stored
    shape, its call closed and its run completed, with one event."""
    from structlog.testing import capture_logs

    from simulate.services.scoring_status import REASON_EVAL_TIMED_OUT_RUNNING
    from simulate.tasks.chat_sim import _sweep_stuck_scoring_pass
    from simulate.utils.verdicts import has_stored_verdict

    config_id = str(_config(run_test, eval_template, name="Resolution").id)
    now = timezone.now()
    run = _run(run_test, TestExecution.ExecutionStatus.EVALUATING)
    call = _call(
        run,
        metadata={
            "eval_started": True,
            EVAL_QUEUED_KEY: {config_id: (now - 2 * _JOB).isoformat()},
            EVAL_PROGRESS_KEY: (now - _JOB).isoformat(),
            "csat_status": "completed",
        },
        completed_at=now - timedelta(hours=1),
    )

    with capture_logs() as logs:
        counts = _sweep_stuck_scoring_pass(now)

    call.refresh_from_db()
    run.refresh_from_db()
    assert call.eval_outputs[config_id] == {
        "output": None,
        "reason": REASON_EVAL_TIMED_OUT_RUNNING,
        "output_type": None,
        "name": "Resolution",
        "status": "timed_out",
        "timestamp": now.isoformat(),
    }
    assert call.call_metadata["eval_completed"] is True
    assert call.status == CallExecution.CallStatus.COMPLETED
    assert has_stored_verdict(call, config_id) is False, "a later grading replaces it"
    assert run.status == TestExecution.ExecutionStatus.COMPLETED
    assert counts == {
        "scanned": 1,
        "due": 1,
        "timed_out": 1,
        "skipped_cancelled": 0,
        "closed": 0,
        "settled": 1,
        "errors": 0,
    }
    assert [line for line in logs if line["event"] == "simulate_scoring_timed_out"] == [
        {
            "event": "simulate_scoring_timed_out",
            "log_level": "warning",
            "component": "evals",
            "reason_code": "job_timeout",
            "eval_count": 1,
            "call_execution_id": str(call.id),
            "test_execution_id": str(run.id),
        }
    ]


@pytest.mark.django_db
def test_sweep_legacy_anchor_and_close(run_test, eval_template):
    """A call with no stamps is timed from its ``completed_at``; a completed
    call whose scoring never started is closed quietly once that clock is due,
    and not a second before. An unstamped dispatch failure reads the same
    through the reduced projection as through the row, as a flag only."""
    from structlog.testing import capture_logs

    from simulate.services.scoring_status import (
        REASON_EVAL_TIMED_OUT_NOT_STARTED,
        scoring_input_from_call,
        scoring_input_from_values,
        scoring_values,
    )
    from simulate.tasks.chat_sim import _sweep_stuck_scoring_pass

    config_id = str(_config(run_test, eval_template).id)
    now = timezone.now()
    run = _run(run_test, TestExecution.ExecutionStatus.EVALUATING)
    legacy = _call(run, metadata=dict(_STARTED), completed_at=now - _START)
    never = _call(run, metadata={}, completed_at=now - _START)
    fresh = _call(run, metadata={}, completed_at=now - _START + _SECOND)
    failed = _call(
        run, metadata={"eval_dispatch_failed": "boom"}, completed_at=now - _START
    )

    row = scoring_values(CallExecution.objects.filter(id=failed.id)).get()
    assert row["scoring_meta"] == {"eval_dispatch_failed": True}
    assert scoring_input_from_values(row) == scoring_input_from_call(
        CallExecution.objects.get(id=failed.id)
    )

    with capture_logs() as logs:
        counts = _sweep_stuck_scoring_pass(now)

    for call in (legacy, never, fresh, failed):
        call.refresh_from_db()
    for call in (legacy, failed):
        assert call.eval_outputs[config_id]["status"] == "timed_out"
        assert call.eval_outputs[config_id]["reason"] == (
            REASON_EVAL_TIMED_OUT_NOT_STARTED
        )
        assert call.call_metadata["eval_completed"] is True
    assert never.eval_outputs == {}
    assert never.call_metadata["eval_completed"] is True
    assert "eval_completed" not in fresh.call_metadata
    assert (counts["due"], counts["timed_out"], counts["closed"]) == (3, 2, 1)
    events = [line for line in logs if line["event"] == "simulate_scoring_timed_out"]
    assert [(line["call_execution_id"], line["reason_code"]) for line in events] == [
        (str(legacy.id), "start_timeout"),
        (str(failed.id), "start_timeout"),
    ]


@pytest.mark.django_db
def test_hosted_cancel_mid_scoring_ends_cancelled(
    auth_client, organization, run_test, eval_template
):
    """Stopping a hosted run while it is still scoring ends it ``cancelled``
    within one sweep: open evals and CSAT are marked not scored, and the
    settle finishes the run."""
    from structlog.testing import capture_logs

    from simulate.models import HostedHarnessJob
    from simulate.services.hosted_harness import create_hosted_job
    from simulate.services.scoring_status import (
        REASON_CSAT_RUN_CANCELLED,
        REASON_EVAL_RUN_CANCELLED,
    )
    from simulate.tasks.chat_sim import _sweep_stuck_scoring_pass
    from simulate.views import run_test as run_test_views

    from .test_hosted_harness_channels import _payload

    config = _config(run_test, eval_template, name="Resolution")
    now = timezone.now()
    run = _run(run_test, TestExecution.ExecutionStatus.EVALUATING)
    call = _call(
        run,
        metadata={
            "eval_started": True,
            EVAL_QUEUED_KEY: {str(config.id): now.isoformat()},
            "csat_status": "pending",
            CSAT_STAMP_KEY: now.isoformat(),
        },
        completed_at=now,
    )
    job, _ = create_hosted_job(organization, _payload(), idempotency_key="scoring-stop")
    HostedHarnessJob.no_workspace_objects.filter(id=job.id).update(
        test_execution=run, state=HostedHarnessJob.State.COMPLETED
    )

    with (
        patch.object(
            run_test_views.app_settings,
            "TEMPORAL_TEST_EXECUTION_ENABLED",
            True,
            create=True,
        ),
        patch("simulate.temporal.client.cancel_hosted_harness_gateway_workflow"),
    ):
        response = auth_client.post(
            f"/simulate/test-executions/{run.id}/cancel/", {}, format="json"
        )
    assert response.status_code == 200, response.content
    run.refresh_from_db()
    assert run.status == TestExecution.ExecutionStatus.CANCELLING

    with capture_logs() as logs:
        counts = _sweep_stuck_scoring_pass(timezone.now())

    call.refresh_from_db()
    run.refresh_from_db()
    assert call.eval_outputs[str(config.id)]["status"] == "skipped"
    assert call.eval_outputs[str(config.id)]["reason"] == REASON_EVAL_RUN_CANCELLED
    assert call.call_metadata["csat_status"] == "skipped"
    assert call.call_metadata["csat_error"] == REASON_CSAT_RUN_CANCELLED
    assert run.status == TestExecution.ExecutionStatus.CANCELLED
    assert run.completed_at is not None
    assert (counts["skipped_cancelled"], counts["settled"]) == (1, 1)
    events = [line["event"] for line in logs]
    assert events.count("simulate_scoring_skipped_cancelled") == 2
    assert events.count("simulate_run_cancel_settled") == 1


@pytest.mark.django_db(transaction=True)
def test_sweep_selection_and_isolation(run_test, eval_template):
    """The sweep reaches ``evaluating`` and ``cancelling`` runs of any age and
    nothing else, never an ``analyzing`` call; it skips a row another worker
    holds, keeps going past a row that raises, and never lets 250 open calls
    that are not due delay one that is."""
    import threading

    from django.db import connection
    from django.db import transaction as db_transaction
    from structlog.testing import capture_logs

    from simulate.services.scoring_status import SWEEP_COUNT_KEYS, close_due_scoring
    from simulate.tasks.chat_sim import _sweep_stuck_scoring_pass, sweep_stuck_scoring

    config_id = str(_config(run_test, eval_template).id)
    Status = TestExecution.ExecutionStatus
    now = timezone.now()
    long_ago = now - timedelta(days=365)
    fresh_stamps = {**_STARTED, EVAL_QUEUED_KEY: {config_id: now.isoformat()}}

    year_old = _run(run_test, Status.EVALUATING, completed_at=long_ago)
    TestExecution.objects.filter(id=year_old.id).update(created_at=long_ago)
    swept_old = _call(year_old, metadata={}, completed_at=long_ago)
    analyzing = _call(
        year_old, status=CallExecution.CallStatus.ANALYZING, completed_at=long_ago
    )
    stopping = _run(run_test, Status.CANCELLING)
    stopped = _call(stopping, metadata=dict(fresh_stamps), completed_at=now)
    unvisited = [
        _call(_run(run_test, status), metadata={}, completed_at=long_ago)
        for status in (Status.RUNNING, Status.COMPLETED, Status.FAILED)
    ]

    busy = _run(run_test, Status.EVALUATING)
    CallExecution.objects.bulk_create(
        [
            CallExecution(
                test_execution=busy,
                scenario=busy.run_test.scenarios.first(),
                status=CallExecution.CallStatus.COMPLETED,
                call_metadata=dict(fresh_stamps),
                eval_outputs={},
                completed_at=now,
            )
            for _ in range(250)
        ]
    )
    CallExecution.objects.filter(test_execution=busy).update(
        created_at=now - timedelta(hours=2)
    )
    held = _call(busy, metadata={}, completed_at=long_ago)
    raising = _call(busy, metadata={}, completed_at=long_ago)
    late_due = _call(busy, metadata={}, completed_at=long_ago)

    def _close_or_raise(call, names, at):
        if call.id == raising.id:
            # Fails inside Postgres, which aborts the open transaction: only
            # the per-row savepoint keeps the rows after this one writable.
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1/0")
        return close_due_scoring(call, names, at)

    locked, release = threading.Event(), threading.Event()

    def _hold_row():
        try:
            with db_transaction.atomic():
                list(CallExecution.objects.select_for_update().filter(id=held.id))
                locked.set()
                release.wait(timeout=30)
        finally:
            connection.close()

    holder = threading.Thread(target=_hold_row)
    holder.start()
    try:
        assert locked.wait(timeout=30)
        with patch(
            "simulate.tasks.chat_sim.close_due_scoring", side_effect=_close_or_raise
        ):
            counts = _sweep_stuck_scoring_pass(timezone.now())
    finally:
        release.set()
        holder.join(timeout=30)

    def _closed(call):
        call.refresh_from_db()
        return call.call_metadata.get("eval_completed") is True

    assert _closed(swept_old), "a run stuck a year ago is still reached"
    assert _closed(late_due), "250 open calls that are not due take no slot"
    stopped.refresh_from_db()
    assert stopped.eval_outputs[config_id]["status"] == "skipped"
    assert not any(_closed(call) for call in unvisited), "other run statuses wait"
    assert not _closed(held), "a row another worker holds is left for next pass"
    assert not _closed(raising)
    analyzing.refresh_from_db()
    assert analyzing.status == CallExecution.CallStatus.ANALYZING
    assert analyzing.call_metadata == {}
    assert counts["scanned"] == 255
    assert counts["due"] == 5
    # Only the stopped run: a run still scoring would take a backstop slot
    # from a run whose wake-up was lost.
    assert counts["settled"] == 1
    assert (counts["closed"], counts["skipped_cancelled"], counts["errors"]) == (
        2,
        1,
        1,
    )

    with (
        patch(
            "simulate.tasks.chat_sim._sweep_stuck_scoring_pass",
            side_effect=RuntimeError("pass died"),
        ),
        capture_logs() as logs,
    ):
        assert sweep_stuck_scoring._original_func() == dict.fromkeys(
            SWEEP_COUNT_KEYS, 0
        ), "a pass that raises is logged, never raised"
    assert [line["event"] for line in logs] == ["simulate_scoring_sweep_failed"]


@pytest.mark.django_db
def test_backstop_settles_lost_wakeup(run_test):
    """Two calls closed in parallel whose settles each saw the other still
    open leave the run ``evaluating``; the next pass settles it, a settle that
    changed nothing is retried, and a settled run leaves the set."""
    from simulate.services.test_executor import TestExecutor
    from simulate.tasks.chat_sim import _sweep_stuck_scoring_pass

    run = _run(run_test, TestExecution.ExecutionStatus.EVALUATING)
    _call(run, metadata=dict(CLOSED))
    _call(run, metadata=dict(CLOSED))

    with patch.object(
        TestExecutor, "_check_and_update_test_execution_completion"
    ) as swallowed:
        first = _sweep_stuck_scoring_pass(timezone.now())
    run.refresh_from_db()
    assert swallowed.call_count == 1
    assert first["settled"] == 1
    assert run.status == TestExecution.ExecutionStatus.EVALUATING

    second = _sweep_stuck_scoring_pass(timezone.now())
    run.refresh_from_db()
    assert second["settled"] == 1
    assert run.status == TestExecution.ExecutionStatus.COMPLETED
    assert _sweep_stuck_scoring_pass(timezone.now())["settled"] == 0


@pytest.mark.django_db
def test_backstop_keeps_going_past_a_raising_settle(run_test):
    """A settle that raises for one run is counted and logged, the runs after
    it are still settled, the pass still finishes, and the next pass retries
    the run that raised."""
    from structlog.testing import capture_logs

    from simulate.services.test_executor import TestExecutor
    from simulate.tasks.chat_sim import _sweep_stuck_scoring_pass

    Status = TestExecution.ExecutionStatus
    runs = [_run(run_test, Status.EVALUATING) for _ in range(2)]
    for run in runs:
        _call(run, metadata=dict(CLOSED))
    real = TestExecutor._check_and_update_test_execution_completion
    raised = []

    def settle(self, test_execution_id):
        if not raised:
            raised.append(str(test_execution_id))
            raise RuntimeError("settle raised")
        return real(self, test_execution_id)

    with (
        capture_logs() as logs,
        patch.object(
            TestExecutor, "_check_and_update_test_execution_completion", settle
        ),
    ):
        first = _sweep_stuck_scoring_pass(timezone.now())

    assert (first["settled"], first["errors"]) == (1, 1)
    assert [
        line["test_execution_id"]
        for line in logs
        if line["event"] == "simulate_scoring_sweep_settle_failed"
    ] == raised
    assert logs[-1]["event"] == "simulate_scoring_sweep_finished"
    statuses = {
        str(run_id): status
        for run_id, status in TestExecution.objects.filter(
            id__in=[run.id for run in runs]
        ).values_list("id", "status")
    }
    assert statuses.pop(raised[0]) == Status.EVALUATING
    assert list(statuses.values()) == [Status.COMPLETED]

    second = _sweep_stuck_scoring_pass(timezone.now())
    assert (second["settled"], second["errors"]) == (1, 0)
    assert TestExecution.objects.get(id=raised[0]).status == Status.COMPLETED


def _drive_update_eval_config(ctx):
    with patch(
        "simulate.services.test_executor.run_new_evals_on_call_executions_task.apply_async"
    ):
        response = ctx.client.post(
            f"/simulate/run-tests/{ctx.run_test.id}/eval-configs/{ctx.config.id}/update/",
            {"run": True, "test_execution_id": str(ctx.execution.id)},
            format="json",
        )
    assert response.status_code == 200, response.content


def _drive_call_rerun_view(ctx):
    with patch(
        "simulate.temporal.client.rerun_call_executions",
        return_value={"workflow_id": "scoring-rerun", "merged": False},
    ):
        response = ctx.client.post(
            f"/simulate/test-executions/{ctx.execution.id}/rerun-calls/",
            {"rerun_type": "eval_only", "select_all": True},
            format="json",
        )
    assert response.status_code == 200, response.content


def _drive_bulk_rerun_view(ctx):
    with patch(
        "simulate.temporal.client.rerun_call_executions",
        return_value={"workflow_id": "scoring-rerun", "merged": False},
    ):
        response = ctx.client.post(
            f"/simulate/run-tests/{ctx.run_test.id}/rerun-test-executions/",
            {"rerun_type": "eval_only", "test_execution_ids": [str(ctx.execution.id)]},
            format="json",
        )
    assert response.status_code == 200, response.content


def _drive_run_new_evals_view(ctx):
    with patch(
        "simulate.services.test_executor.run_new_evals_on_call_executions_task.apply_async"
    ):
        response = ctx.client.post(
            f"/simulate/run-tests/{ctx.run_test.id}/run-new-evals/",
            {
                "test_execution_ids": [str(ctx.execution.id)],
                "eval_config_ids": [str(ctx.config.id)],
            },
            format="json",
        )
    assert response.status_code == 200, response.content


def _tool_context(ctx):
    from ai_tools.base import ToolContext

    return ToolContext(
        user=ctx.user, organization=ctx.organization, workspace=ctx.workspace
    )


def _drive_ai_run_new_evals(ctx):
    from ai_tools.tools.simulation.run_new_evals import (
        RunNewEvalsOnSimulationInput,
        RunNewEvalsOnSimulationTool,
    )

    with patch(
        "simulate.services.test_executor.run_new_evals_on_call_executions_task.apply_async"
    ):
        result = RunNewEvalsOnSimulationTool().execute(
            RunNewEvalsOnSimulationInput(
                run_test_id=ctx.run_test.id,
                eval_config_ids=[ctx.config.id],
                test_execution_ids=[ctx.execution.id],
            ),
            _tool_context(ctx),
        )
    assert not result.is_error, result.content


def _drive_ai_rerun_call(ctx):
    from ai_tools.tools.simulation.rerun_call_execution import (
        RerunCallExecutionInput,
        RerunCallExecutionTool,
    )

    with patch(
        "simulate.temporal.client.rerun_call_executions",
        return_value={"workflow_id": "scoring-rerun", "merged": False},
    ):
        result = RerunCallExecutionTool().execute(
            RerunCallExecutionInput(
                call_execution_id=ctx.call.id, rerun_type="eval_only"
            ),
            _tool_context(ctx),
        )
    assert not result.is_error, result.content


def _drive_ai_rerun_test(ctx):
    from ai_tools.tools.simulation.rerun_test_execution import RerunTestExecutionTool

    with patch(
        "simulate.temporal.client.rerun_call_executions",
        return_value={"workflow_id": "scoring-rerun", "merged": False},
    ):
        result = RerunTestExecutionTool()._rerun_bulk(
            run_test=ctx.run_test,
            test_executions=[ctx.execution],
            rerun_type="eval_only",
            context=_tool_context(ctx),
        )
    assert not result.is_error, result.content


@pytest.mark.django_db
@pytest.mark.parametrize(
    "drive,grades",
    [
        pytest.param(_drive_update_eval_config, "edited", id="update-eval-config"),
        pytest.param(_drive_call_rerun_view, "runnable", id="call-rerun-view"),
        pytest.param(_drive_bulk_rerun_view, "runnable", id="bulk-rerun-view"),
        pytest.param(_drive_run_new_evals_view, "edited", id="run-new-evals-view"),
        pytest.param(
            _drive_ai_run_new_evals,
            "edited",
            id="ai-run-new-evals",
            marks=pytest.mark.xfail(
                raises=ModuleNotFoundError,
                strict=True,
                reason="the tool imports simulate.models.call_execution, "
                "which does not exist, so it cannot run today",
            ),
        ),
        pytest.param(_drive_ai_rerun_call, "runnable", id="ai-rerun-call"),
        pytest.param(_drive_ai_rerun_test, "runnable", id="ai-rerun-test"),
    ],
)
def test_eval_rerun_sites_stamp_eval_queued(
    auth_client, user, organization, workspace, run_test, eval_template, drive, grades
):
    """Every eval-only rerun site with a caller stamps what its job grades,
    so the clock starts at the rerun: a call whose own completion is days old
    still reads pending after a sweep instead of being closed before its job
    starts."""
    from simulate.services.scoring_status import scoring_due, scoring_input_from_call
    from simulate.tasks.chat_sim import _sweep_stuck_scoring_pass

    config = _config(run_test, eval_template)
    other = _config(run_test, eval_template)
    execution = _run(run_test, TestExecution.ExecutionStatus.COMPLETED)
    call = _call(
        execution,
        metadata=dict(CLOSED),
        completed_at=timezone.now() - timedelta(days=3),
    )
    runnable = {str(config.id), str(other.id)}
    ctx = SimpleNamespace(
        client=auth_client,
        user=user,
        organization=organization,
        workspace=workspace,
        run_test=run_test,
        execution=execution,
        call=call,
        config=config,
    )

    drive(ctx)

    call.refresh_from_db()
    expected = {str(config.id)} if grades == "edited" else runnable
    assert set(call.call_metadata.get(EVAL_QUEUED_KEY) or {}) == expected
    TestExecution.objects.filter(id=execution.id).update(
        status=TestExecution.ExecutionStatus.EVALUATING
    )
    _sweep_stuck_scoring_pass(timezone.now())
    call.refresh_from_db()
    assert call.call_metadata.get("eval_completed") is not True
    due = scoring_due(
        scoring_input_from_call(call),
        runnable_ids=runnable,
        cancelled=False,
        now=timezone.now(),
    )
    assert expected <= due.pending
    assert not due.evals
    assert due.eval_side is False


# --- The one derivation and the v3 calls API --------------------------------


def _derivation_cases():
    from simulate.services import scoring_status as ss

    pending = {"status": "pending"}
    placeholder_meta = {"eval_started": True, "eval_completed": False}
    due_placeholder = {**placeholder_meta, EVAL_QUEUED_KEY: {"b": _ago(_START)}}
    early_placeholder = {
        **placeholder_meta,
        EVAL_QUEUED_KEY: {"b": _ago(_START - _SECOND)},
    }
    na = ("not_applicable", None)
    return [
        # id, inp kwargs, run status, row status, evals, missing, csat
        (
            "succeeded",
            {
                "meta": {**CLOSED, "csat_status": "completed"},
                "entries": {"a": {"status": "Completed"}},
            },
            "completed",
            "succeeded",
            {"a": ("succeeded", None)},
            (),
            ("succeeded", None),
        ),
        (
            "harness-check",
            {
                "meta": CLOSED,
                "entries": {"h": {"status": "completed", "source": "harness"}},
            },
            "completed",
            "succeeded",
            {"h": ("succeeded", None)},
            (),
            na,
        ),
        (
            "xl-row-without-status",
            {"meta": CLOSED, "entries": {"a": {}}},
            "completed",
            "succeeded",
            {"a": ("succeeded", None)},
            (),
            na,
        ),
        (
            "failed-entry",
            {"meta": CLOSED, "entries": {"a": {"status": "Failed", "error": "error"}}},
            "completed",
            "failed",
            {"a": ("failed", None)},
            (),
            na,
        ),
        (
            "error-status",
            {"meta": CLOSED, "entries": {"a": {"status": "ERROR"}}},
            "completed",
            "failed",
            {"a": ("failed", None)},
            (),
            na,
        ),
        (
            "skipped-only",
            {"meta": CLOSED, "entries": {"a": {"status": "skipped", "skipped": True}}},
            "completed",
            "not_applicable",
            {"a": ("skipped", None)},
            (),
            na,
        ),
        (
            "pending-beats-timed-out",
            {
                "meta": {
                    **_STARTED,
                    EVAL_QUEUED_KEY: {"b": _ago(timedelta(minutes=1))},
                },
                "entries": {"a": {"status": "timed_out"}},
            },
            "completed",
            "pending",
            {"a": ("timed_out", None), "b": ("pending", None)},
            ("b",),
            na,
        ),
        (
            "timed-out-beats-failed",
            {
                "meta": CLOSED,
                "entries": {"a": {"status": "timed_out"}, "b": {"status": "Failed"}},
            },
            "completed",
            "timed_out",
            {"a": ("timed_out", None), "b": ("failed", None)},
            (),
            na,
        ),
        (
            "closed-call-placeholder",
            {"meta": CLOSED, "entries": {"b": pending}},
            "completed",
            "timed_out",
            {"b": ("timed_out", ss.REASON_EVAL_EXPIRED)},
            (),
            na,
        ),
        (
            "due-placeholder-completed-run",
            {"meta": due_placeholder, "entries": {"b": pending}},
            "completed",
            "timed_out",
            {"b": ("timed_out", ss.REASON_EVAL_TIMED_OUT_NOT_STARTED)},
            (),
            na,
        ),
        (
            "due-placeholder-failed-run",
            {"meta": due_placeholder, "entries": {"b": pending}},
            "failed",
            "timed_out",
            {"b": ("timed_out", ss.REASON_EVAL_TIMED_OUT_NOT_STARTED)},
            (),
            na,
        ),
        (
            "due-placeholder-cancelled-run",
            {"meta": due_placeholder, "entries": {"b": pending}},
            "cancelled",
            "not_applicable",
            {"b": ("skipped", ss.REASON_EVAL_RUN_CANCELLED)},
            (),
            na,
        ),
        (
            "placeholder-1s-before-due",
            {"meta": early_placeholder, "entries": {"b": pending}},
            "completed",
            "pending",
            {"b": ("pending", None)},
            (),
            na,
        ),
        (
            "stale-transport",
            {"call_status": "ongoing"},
            "completed",
            "timed_out",
            {
                "a": ("timed_out", ss.REASON_EVAL_EXPIRED),
                "b": ("timed_out", ss.REASON_EVAL_EXPIRED),
            },
            ("a", "b"),
            ("timed_out", ss.REASON_CSAT_EXPIRED),
        ),
        (
            "transport-in-active-run",
            {"call_status": "ongoing"},
            "running",
            "pending",
            {"a": ("pending", None), "b": ("pending", None)},
            ("a", "b"),
            ("pending", None),
        ),
        (
            "analyzing-in-cancelled-run",
            {"call_status": "analyzing"},
            "cancelled",
            "not_applicable",
            {
                "a": ("skipped", ss.REASON_EVAL_RUN_CANCELLED),
                "b": ("skipped", ss.REASON_EVAL_RUN_CANCELLED),
            },
            ("a", "b"),
            ("skipped", ss.REASON_CSAT_RUN_CANCELLED),
        ),
        (
            "stored-csat-beats-reverted-status",
            {
                "meta": {
                    **CLOSED,
                    "csat_status": "running",
                    CSAT_STAMP_KEY: _ago(_JOB),
                },
                "csat_scored": True,
            },
            "completed",
            "succeeded",
            {},
            (),
            ("succeeded", None),
        ),
        (
            "untracked-native-csat",
            {"meta": CLOSED, "has_csat_value": True},
            "completed",
            "succeeded",
            {},
            (),
            ("succeeded", None),
        ),
        (
            "csat-failed-hides-error-text",
            {"meta": {**CLOSED, "csat_status": "failed", "csat_error": "Traceback: x"}},
            "completed",
            "failed",
            {},
            (),
            ("failed", ss.REASON_CSAT_FAILED),
        ),
        (
            "csat-skipped-known-reason",
            {
                "meta": {
                    **CLOSED,
                    "csat_status": "skipped",
                    "csat_error": ss.REASON_CSAT_NO_EVIDENCE,
                }
            },
            "completed",
            "not_applicable",
            {},
            (),
            ("skipped", ss.REASON_CSAT_NO_EVIDENCE),
        ),
        (
            "csat-skipped-unknown-reason",
            {"meta": {**CLOSED, "csat_status": "skipped", "csat_error": "boom"}},
            "completed",
            "not_applicable",
            {},
            (),
            ("skipped", ss.REASON_CSAT_SKIPPED),
        ),
        (
            "csat-timed-out-unknown-reason",
            {"meta": {**CLOSED, "csat_status": "timed_out", "csat_error": "boom"}},
            "completed",
            "timed_out",
            {},
            (),
            ("timed_out", ss.REASON_CSAT_EXPIRED),
        ),
        (
            "csat-skipped-non-string-reason",
            {"meta": {**CLOSED, "csat_status": "skipped", "csat_error": ["boom"]}},
            "completed",
            "not_applicable",
            {},
            (),
            ("skipped", ss.REASON_CSAT_SKIPPED),
        ),
        (
            "csat-timed-out-non-string-reason",
            {"meta": {**CLOSED, "csat_status": "timed_out", "csat_error": {"e": 1}}},
            "completed",
            "timed_out",
            {},
            (),
            ("timed_out", ss.REASON_CSAT_EXPIRED),
        ),
        (
            "csat-pending-due",
            {
                "meta": {
                    **CLOSED,
                    "csat_status": "pending",
                    CSAT_STAMP_KEY: _ago(_START),
                }
            },
            "completed",
            "timed_out",
            {},
            (),
            ("timed_out", ss.REASON_CSAT_TIMED_OUT_NOT_STARTED),
        ),
        (
            "csat-running-due",
            {"meta": {**CLOSED, "csat_status": "running", CSAT_STAMP_KEY: _ago(_JOB)}},
            "completed",
            "timed_out",
            {},
            (),
            ("timed_out", ss.REASON_CSAT_TIMED_OUT_RUNNING),
        ),
        (
            "unstamped-dispatch-failure-pending",
            {
                "meta": {"eval_started": False, "eval_dispatch_failed": True},
                "anchor": NOW - timedelta(minutes=1),
            },
            "completed",
            "pending",
            {"a": ("pending", None), "b": ("pending", None)},
            ("a", "b"),
            na,
        ),
        (
            "unstamped-dispatch-failure-due",
            {
                "meta": {"eval_started": False, "eval_dispatch_failed": True},
                "anchor": NOW - _START,
            },
            "completed",
            "timed_out",
            {
                "a": ("timed_out", ss.REASON_EVAL_TIMED_OUT_NOT_STARTED),
                "b": ("timed_out", ss.REASON_EVAL_TIMED_OUT_NOT_STARTED),
            },
            ("a", "b"),
            na,
        ),
        (
            "nothing-to-score",
            {"meta": CLOSED},
            "completed",
            "not_applicable",
            {},
            (),
            na,
        ),
        (
            "removed-config-hidden",
            {"meta": CLOSED, "entries": {"gone": {"status": "Completed"}}},
            "completed",
            "not_applicable",
            {},
            (),
            na,
        ),
        (
            "failed-call-placeholder-due",
            {
                "call_status": "failed",
                "entries": {"a": pending},
                "anchor": NOW - _START,
            },
            "failed",
            "timed_out",
            {"a": ("timed_out", ss.REASON_EVAL_TIMED_OUT_NOT_STARTED)},
            (),
            na,
        ),
        (
            "failed-call-placeholder-early",
            {
                "call_status": "failed",
                "entries": {"a": pending},
                "anchor": NOW - _START + _SECOND,
            },
            "failed",
            "pending",
            {"a": ("pending", None)},
            (),
            na,
        ),
    ]


@pytest.mark.parametrize(
    "case",
    _derivation_cases(),
    ids=lambda case: case[0] if isinstance(case, tuple) else "",
)
def test_derivation_table(case):
    """Every stored shape maps to exactly one row status, eval statuses and
    CSAT status, with the precedence, the closed-call rule, the clock in every
    run status, stale transport and the CSAT reason whitelist."""
    from simulate.services.scoring_status import derive_call_scoring

    _, kwargs, run_status, row, evals, missing, csat = case
    scoring = derive_call_scoring(
        _inp(**kwargs),
        visible_ids={"a", "b"},
        runnable_ids={"a", "b"},
        run_status=run_status,
        now=NOW,
    )
    assert scoring.status == row
    assert {
        eval_id: (entry.status, entry.reason)
        for eval_id, entry in scoring.evals.items()
    } == evals
    assert scoring.missing == missing
    assert (scoring.csat_status, scoring.csat_reason) == csat


@pytest.mark.django_db
def test_stale_save_after_sweep_converges(run_test, eval_template):
    """A job's stale copy saved after a sweep never leaves a row pending for
    good: an open call is closed again on the next pass; a closed call shows a
    stamped or placeholder config as timed out and a fallback config as absent.
    """
    from simulate.services.scoring_status import (
        REASON_EVAL_EXPIRED,
        derive_call_scoring,
        scoring_input_from_call,
    )
    from simulate.tasks.chat_sim import _sweep_stuck_scoring_pass

    stamped = str(_config(run_test, eval_template).id)
    fallback = str(_config(run_test, eval_template).id)
    ids = {stamped, fallback}
    now = timezone.now()
    run = _run(run_test, TestExecution.ExecutionStatus.EVALUATING)
    # A call still in transport keeps the run open, so the settle does not
    # finish it between the two passes.
    _call(run, status=CallExecution.CallStatus.ONGOING)
    open_call = _call(
        run,
        metadata={**_STARTED, EVAL_QUEUED_KEY: {stamped: (now - _START).isoformat()}},
        completed_at=now - _START,
    )
    stale_copy = CallExecution.objects.get(id=open_call.id)
    _sweep_stuck_scoring_pass(now)
    stale_copy.save(update_fields=["call_metadata", "eval_outputs"])

    counts = _sweep_stuck_scoring_pass(now)

    open_call.refresh_from_db()
    assert counts["timed_out"] == 1
    assert open_call.call_metadata["eval_completed"] is True
    assert open_call.eval_outputs[stamped]["status"] == "timed_out"

    stamped_meta = {**CLOSED, EVAL_QUEUED_KEY: {stamped: now.isoformat()}}
    shapes = {
        "placeholder": _call(
            run, metadata=stamped_meta, eval_outputs={stamped: {"status": "pending"}}
        ),
        "stamped-absent": _call(run, metadata=stamped_meta, eval_outputs={}),
        "fallback-absent": _call(run, metadata=dict(CLOSED), eval_outputs={}),
    }
    for shape, call in shapes.items():
        scoring = derive_call_scoring(
            scoring_input_from_call(call),
            visible_ids=ids,
            runnable_ids=ids,
            run_status="evaluating",
            now=now,
        )
        if shape == "fallback-absent":
            assert scoring.evals == {}
            assert scoring.status == "not_applicable"
        else:
            assert (scoring.evals[stamped].status, scoring.evals[stamped].reason) == (
                "timed_out",
                REASON_EVAL_EXPIRED,
            )
            assert scoring.status == "timed_out"


@pytest.mark.django_db
def test_scored_csat_beats_reverted_status(run_test):
    """CSAT reverted to ``running`` by a stale save beside a stored score reads
    succeeded, and the sweeper writes it back ``completed``, never timed out."""
    from structlog.testing import capture_logs

    from simulate.services.scoring_status import (
        derive_call_scoring,
        scoring_input_from_call,
    )
    from simulate.tasks.chat_sim import _sweep_stuck_scoring_pass

    now = timezone.now()
    run = _run(run_test, TestExecution.ExecutionStatus.EVALUATING)
    call = _call(
        run,
        metadata={
            **CLOSED,
            "csat_status": "running",
            CSAT_STAMP_KEY: (now - 2 * _JOB).isoformat(),
        },
        conversation_metrics_data={"csat_score": 8.0},
        overall_score=8.0,
    )
    scoring = derive_call_scoring(
        scoring_input_from_call(call),
        visible_ids=set(),
        runnable_ids=set(),
        run_status="evaluating",
        now=now,
    )
    assert (scoring.csat_status, scoring.csat_reason) == ("succeeded", None)

    with capture_logs() as logs:
        counts = _sweep_stuck_scoring_pass(now)

    call.refresh_from_db()
    assert call.call_metadata["csat_status"] == "completed"
    assert counts["closed"] == 1
    assert counts["timed_out"] == 0
    assert not [line for line in logs if line["event"] == "simulate_scoring_timed_out"]


def _calls_url(execution):
    return f"/simulate/v3/test-executions/{execution.id}/calls/"


def _mixed_run(run_test, eval_template):
    """A completed run whose calls sit in every row status."""
    config = _config(run_test, eval_template, name="Tone")
    config_id = str(config.id)
    now = timezone.now()
    run = _run(run_test, TestExecution.ExecutionStatus.COMPLETED, completed_at=now)
    _call(
        run,
        metadata={"eval_started": True, EVAL_QUEUED_KEY: {config_id: now.isoformat()}},
        completed_at=now,
    )
    _call(
        run,
        metadata=dict(CLOSED),
        eval_outputs={config_id: {"status": "timed_out", "name": "Tone"}},
        completed_at=now,
    )
    _call(
        run,
        metadata=dict(CLOSED),
        eval_outputs={config_id: {"status": "Failed", "error": "error"}},
        completed_at=now,
    )
    _call(
        run,
        metadata={**CLOSED, "csat_status": "completed"},
        eval_outputs={config_id: {"status": "Completed", "output": "Passed"}},
        completed_at=now,
    )
    _call(run, metadata=dict(CLOSED), completed_at=now)
    _call(run, status=CallExecution.CallStatus.FAILED, completed_at=now)
    _call(run, status=CallExecution.CallStatus.ANALYZING)
    return run


@pytest.mark.django_db
def test_summary_scoring_matches_rows(auth_client, run_test, eval_template):
    """``summary.scoring`` counts the same derivation the rows show, over the
    whole filtered set rather than one page, and sums to ``summary.total``."""
    from collections import Counter

    from simulate.services.scoring_status import SCORING_STATUSES

    run = _mixed_run(run_test, eval_template)
    # A code check the harness ran has no eval config; off its page it is
    # counted from the reduced projection, which must keep its source.
    _call(
        run,
        metadata=dict(CLOSED),
        eval_outputs={
            "harness-check": {
                "name": "No PII",
                "output": "Failed",
                "output_type": "Pass/Fail",
                "status": "completed",
                "source": "harness",
            }
        },
        completed_at=timezone.now(),
    )
    seen = Counter()
    page, summaries = 1, []
    while True:
        response = auth_client.get(_calls_url(run), {"page": page, "page_size": 2})
        assert response.status_code == 200, response.content
        body = response.json()
        summaries.append(body["summary"]["scoring"])
        seen.update(row["scoring_status"] for row in body["results"])
        if page >= body["total_pages"]:
            break
        page += 1

    expected = {status: seen.get(status, 0) for status in SCORING_STATUSES}
    assert all(summary == expected for summary in summaries)
    assert sum(expected.values()) == body["summary"]["total"] == 8
    assert expected["pending"] == 1
    assert expected["timed_out"] == 2, "a stored timeout and the stale analyzing call"


@pytest.mark.django_db
def test_summary_scoring_counts_the_rows_it_returns(
    auth_client, run_test, eval_template
):
    """A finished run's calls page is cached for a few minutes, and a result
    that lands meanwhile moves nothing in its key. The row shows the result at
    once, and the counts of the same response count that call as its row does.
    """
    from collections import Counter

    from simulate.services.scoring_status import SCORING_STATUSES

    run = _mixed_run(run_test, eval_template)
    query = {"page_size": 50}
    before = auth_client.get(_calls_url(run), query)
    assert before.status_code == 200, before.content
    assert before.json()["summary"]["scoring"]["timed_out"] == 2

    late = next(
        call
        for call in CallExecution.objects.filter(test_execution=run)
        if any(
            isinstance(entry, dict) and entry.get("status") == "timed_out"
            for entry in (call.eval_outputs or {}).values()
        )
    )
    late.eval_outputs = {
        eval_id: {"status": "Completed", "output": "Passed", "name": "Tone"}
        for eval_id in late.eval_outputs
    }
    late.save(update_fields=["eval_outputs"])

    after = auth_client.get(_calls_url(run), query)
    assert after.status_code == 200, after.content
    body = after.json()
    statuses = {row["id"]: row["scoring_status"] for row in body["results"]}
    assert statuses[str(late.id)] == "succeeded"
    counts = Counter(statuses.values())
    assert body["summary"]["scoring"] == {
        status: counts.get(status, 0) for status in SCORING_STATUSES
    }
    assert body["summary"]["scoring"]["timed_out"] == 1


@pytest.mark.django_db
def test_calls_scoring_adds_no_per_row_queries(auth_client, run_test, eval_template):
    """The scoring fields cost the same queries for 5 rows as for 50."""
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    config_id = str(_config(run_test, eval_template).id)
    now = timezone.now()

    def _run_with(count):
        run = _run(run_test, TestExecution.ExecutionStatus.EVALUATING)
        for _ in range(count):
            _call(
                run,
                metadata={
                    "eval_started": True,
                    EVAL_QUEUED_KEY: {config_id: now.isoformat()},
                    "csat_status": "pending",
                    CSAT_STAMP_KEY: now.isoformat(),
                },
                completed_at=now,
            )
        return run

    def _queries(run):
        with CaptureQueriesContext(connection) as captured:
            response = auth_client.get(_calls_url(run), {"page_size": 50})
        assert response.status_code == 200, response.content
        assert all(
            row["scoring_status"] == "pending" for row in response.json()["results"]
        )
        return len(captured)

    # One throwaway request first, so nothing a first request caches is
    # counted against the 5-row run only.
    _queries(_run_with(1))
    assert _queries(_run_with(5)) == _queries(_run_with(50))


@pytest.mark.django_db
def test_detail_carries_scoring_fields(auth_client, run_test, eval_template):
    """The run's own summary never carries the call counts, filtered or not,
    and the call detail route has the three scoring fields."""
    run = _mixed_run(run_test, eval_template)

    for query in ({}, {"search": "Scoring"}):
        response = auth_client.get(_calls_url(run), query)
        assert response.status_code == 200, response.content
        body = response.json()
        assert "scoring" in body["summary"]
        assert "scoring" not in body["execution"]["summary"]

    pending_call = CallExecution.objects.get(
        test_execution=run,
        call_metadata__eval_completed__isnull=True,
        status="completed",
    )
    detail = auth_client.get(f"/simulate/v3/call-executions/{pending_call.id}/")
    assert detail.status_code == 200, detail.content
    fields = detail.json()
    assert fields["scoring_status"] == "pending"
    assert fields["csat_status"] == "not_applicable"
    assert fields["csat_reason"] is None
    assert [entry["status"] for entry in fields["evaluations"]] == ["pending"]


@pytest.mark.django_db
@pytest.mark.parametrize(
    "case",
    [
        pytest.param("closed", id="closed-call-placeholder"),
        pytest.param("not_started", id="stamped-past-start-clock"),
    ],
)
def test_stored_placeholder_cell_shows_derived_status(
    auth_client, run_test, eval_template, case
):
    """A stored placeholder that will never be scored reads as timed out in
    its own eval cell, with the reason the derivation gave. Echoing the stored
    ``pending`` would leave that cell loading forever while the row says it
    timed out."""
    from simulate.services.scoring_status import (
        REASON_EVAL_EXPIRED,
        REASON_EVAL_TIMED_OUT_NOT_STARTED,
    )

    config_id = str(_config(run_test, eval_template, name="Tone").id)
    now = timezone.now()
    placeholder = {config_id: {"status": "pending", "name": "Tone"}}
    if case == "closed":
        run = _run(run_test, TestExecution.ExecutionStatus.COMPLETED, completed_at=now)
        metadata, reason = dict(CLOSED), REASON_EVAL_EXPIRED
    else:
        run = _run(run_test, TestExecution.ExecutionStatus.EVALUATING)
        dispatched = now - timedelta(minutes=31)
        metadata = {
            "eval_started": True,
            EVAL_QUEUED_KEY: {config_id: dispatched.isoformat()},
        }
        reason = REASON_EVAL_TIMED_OUT_NOT_STARTED
    call = _call(run, metadata=metadata, eval_outputs=placeholder, completed_at=now)

    response = auth_client.get(_calls_url(run))
    assert response.status_code == 200, response.content
    (row,) = response.json()["results"]
    assert row["id"] == str(call.id)
    assert row["scoring_status"] == "timed_out"
    assert [
        (entry["id"], entry["status"], entry["reason"]) for entry in row["evaluations"]
    ] == [(config_id, "timed_out", reason)]
