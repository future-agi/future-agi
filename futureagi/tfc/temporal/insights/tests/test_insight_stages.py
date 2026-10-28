"""Insights' Temporal side: a stage retries an entitlement service that
can't answer instead of refusing the run, the report page shows only text
written for people, every long stage heartbeats under a bound above its
worst case, and a report is started with a limit on how long it may run.

No database: the run's organization lookup is stubbed, and the workflow runs
against a patched ``execute_activity``.
"""

from __future__ import annotations

import asyncio
import inspect
import math
import time
import uuid
from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest
from temporalio.exceptions import (
    ActivityError,
    ApplicationError,
    TimeoutError,
    TimeoutType,
)
from temporalio.testing import ActivityEnvironment

from tfc import ee_gates
from tfc.capabilities.errors import CapabilityDenied
from tfc.ee_gating import ENTITLEMENT_CHECK_UNAVAILABLE_MESSAGE, FeatureUnavailable
from tfc.temporal.insights import activities, workflows


@pytest.fixture
def run_org():
    """Every run belongs to one organization, without a database."""
    from tracer.models.insights import InsightRun

    runs = MagicMock()
    runs.filter.return_value.values_list.return_value.first.return_value = uuid.uuid4()
    with patch.object(InsightRun, "no_workspace_objects", runs):
        yield


def _license_error(denial) -> ApplicationError:
    with patch("tfc.ee_gating.check_ee_feature", side_effect=denial):
        with pytest.raises(ApplicationError) as raised:
            activities.require_license("run-1")
    return raised.value


# ─── An entitlement outage is retried ────────────────────────────────────────
@pytest.mark.parametrize(
    "denial",
    [
        CapabilityDenied(feature_id="insights", reason_code="RESOLVER_UNAVAILABLE"),
        FeatureUnavailable(
            "insights", detail="Feature entitlement service is unavailable."
        ),
    ],
    ids=["resolver-unavailable", "service-unavailable"],
)
def test_an_unanswerable_entitlement_check_is_retried_not_a_denial(run_org, denial):
    unavailable = _license_error(denial)
    assert unavailable.type == "CapabilityUnavailable"
    assert not unavailable.non_retryable
    assert unavailable.message == ENTITLEMENT_CHECK_UNAVAILABLE_MESSAGE


def test_an_entitlement_lookup_that_raises_retries_the_stage(run_org):
    """The real gate: the capability service raises (its plan query hit a
    database error), so check_ee_feature says the service is unavailable."""
    with (
        patch("tfc.capabilities.service.is_configured", return_value=True),
        patch("tfc.capabilities.service.check", side_effect=RuntimeError("db down")),
    ):
        with pytest.raises(ApplicationError) as raised:
            activities.require_license("run-1")
    assert raised.value.type == "CapabilityUnavailable"
    assert not raised.value.non_retryable


def test_a_plan_without_insights_still_refuses_the_run_for_good(run_org):
    refused = _license_error(
        CapabilityDenied(feature_id="insights", reason_code="PLAN_FEATURE_MISSING")
    )
    assert refused.type == "CapabilityDenied"
    assert refused.non_retryable
    assert refused.message == "Insights isn't included in your plan."


@pytest.mark.parametrize("lapsed", ["insights", "turing_models"])
def test_a_licence_that_lapses_mid_run_fails_it_as_a_licence_problem(run_org, lapsed):
    """Insights, or the Turing models it runs on, taken off the licence while
    a report runs: the run fails with the denial and the code "license", so
    the report page offers the License settings instead of a Retry that
    would be refused."""
    if lapsed == "insights":
        refused = _license_error(
            CapabilityDenied(
                feature_id="insights", reason_code="LICENSE_FEATURE_MISSING"
            )
        )
    else:
        with (
            patch("tfc.ee_gating.check_ee_feature"),
            patch.object(ee_gates, "insights_turing_denied", return_value=True),
        ):
            with pytest.raises(ApplicationError) as raised:
                activities.require_license("run-1")
        refused = raised.value
    assert (refused.type, refused.non_retryable) == ("CapabilityDenied", True)
    assert refused.message
    assert _recorded_failure(refused, codes=True) == [
        (refused.message, activities.LICENSE_CODE)
    ]
    assert activities.LICENSE_CODE == "license"


# ─── The report page shows only text written for people ──────────────────────
def _recorded_failure(cause, *, codes=False) -> list:
    """What a run whose first stage failed with ``cause`` records: its
    message, or (message, code) with ``codes``."""
    recorded = []

    async def execute_activity(name, arg, **_):
        if name == "insight_fail":
            recorded.append((arg.message, arg.code) if codes else arg.message)
            return None
        raise ActivityError(
            "activity failed",
            scheduled_event_id=1,
            started_event_id=2,
            identity="w",
            activity_type=name,
            activity_id="1",
            retry_state=None,
        ) from cause

    with patch.object(workflows.workflow, "execute_activity", execute_activity):
        with pytest.raises(ActivityError):
            asyncio.run(
                workflows.InsightReportWorkflow().run(
                    workflows.InsightRunInput(run_id="run-1")
                )
            )
    return recorded


@pytest.mark.parametrize(
    "cause",
    [
        # What a worker without the activity fails it with, during a rollout.
        ApplicationError(
            "Activity function insight_prepare for workflow insight-run-1 is "
            "not registered on this worker, available activities: "
            "eval_task_reconcile, run_eval_batch",
            type="NotFoundError",
        ),
        # Any other exception escaping an activity, converted by the SDK.
        ApplicationError("Apps aren't loaded yet.", type="AppRegistryNotReady"),
        ApplicationError("Failed decoding arguments"),
        TimeoutError(
            "activity StartToClose timeout",
            type=TimeoutType.START_TO_CLOSE,
            last_heartbeat_details=[],
        ),
    ],
    ids=["not-registered", "sdk-converted", "untyped", "timeout"],
)
def test_internal_failure_text_never_reaches_the_report(cause):
    assert _recorded_failure(cause) == [workflows.FAILED_MESSAGE]


@pytest.mark.parametrize(
    "cause",
    [
        ApplicationError(
            ee_gates.INSIGHTS_DENIED_MESSAGE,
            type="CapabilityDenied",
            non_retryable=True,
        ),
        ApplicationError(
            ENTITLEMENT_CHECK_UNAVAILABLE_MESSAGE, type="CapabilityUnavailable"
        ),
        ApplicationError(
            "The analysis model is unavailable right now. Try again shortly.",
            type="InsightModelError",
        ),
    ],
    ids=["refused", "entitlements-down", "model"],
)
def test_the_stages_own_failures_reach_the_report(cause):
    assert _recorded_failure(cause) == [cause.message]


@pytest.mark.requires_ee
def test_a_model_error_from_a_stage_reaches_the_report():
    from ee.agenthub.insights.llm import InsightModelError

    def organise(run_id):
        raise InsightModelError("Nothing could be read from these items.")

    with pytest.raises(ApplicationError) as raised:
        asyncio.run(activities._run("organise", organise, "run-1", licensed=False))
    assert raised.value.non_retryable
    assert _recorded_failure(raised.value) == [
        "Nothing could be read from these items."
    ]


@pytest.mark.requires_ee
def test_a_setup_error_fails_the_run_for_good_with_its_code():
    """A licence the managed service won't activate is not retried by
    Temporal either, and the run records why, so the report page offers the
    fix instead of Retry."""
    from ee.agenthub.insights.llm import LICENSE, InsightModelSetupError

    message = "Insights couldn't use Future AGI's managed models."

    def extract(run_id, unit_pks):
        raise InsightModelSetupError(message, LICENSE)

    with pytest.raises(ApplicationError) as raised:
        asyncio.run(activities._run("extract", extract, "run-1", [1], licensed=False))
    assert raised.value.non_retryable
    assert _recorded_failure(raised.value, codes=True) == [(message, LICENSE)]
    # Any other error records no code.
    assert _recorded_failure(
        ApplicationError("Try again shortly.", type="InsightModelError"), codes=True
    ) == [("Try again shortly.", "")]


# ─── Stage bounds allow the model's nested request retries ──────────────────
@pytest.mark.requires_ee
def test_an_extract_batch_heartbeats_under_a_bound_above_its_worst_case():
    from ee.agenthub.insights import llm, pipeline

    scheduled = {}

    async def execute_activity(name, arg, **options):
        scheduled.setdefault(name, options)
        return [1, 2] if name == "insight_prepare" else []

    with patch.object(workflows.workflow, "execute_activity", execute_activity):
        asyncio.run(
            workflows.InsightReportWorkflow().run(
                workflows.InsightRunInput(run_id="run-1")
            )
        )
    extract = scheduled["insight_extract"]
    # A unit's model attempts, each up to the gateway client's 3 tries
    # (max_retries=2), for each unit a thread reads in a row.
    attempts = inspect.signature(llm.chat_json).parameters["attempts"].default
    in_a_row = math.ceil(workflows.BATCH / pipeline.EXTRACT_WORKERS)
    worst = timedelta(seconds=in_a_row * attempts * 3 * llm._TIMEOUT)
    assert extract["start_to_close_timeout"] > worst
    assert (
        timedelta(seconds=activities.HEARTBEAT_EVERY)
        < extract["heartbeat_timeout"]
        < timedelta(minutes=5)
    )


def test_grouping_and_writing_heartbeat_under_the_extract_bound():
    scheduled = {}

    async def execute_activity(name, arg, **options):
        scheduled.setdefault(name, options)
        return [1, 2] if name == "insight_prepare" else []

    with patch.object(workflows.workflow, "execute_activity", execute_activity):
        asyncio.run(
            workflows.InsightReportWorkflow().run(
                workflows.InsightRunInput(run_id="run-1")
            )
        )
    for stage in ("insight_organise", "insight_finish"):
        options = scheduled[stage]
        # Several thinking-model calls in a row, each up to about 27 minutes
        # when the provider hangs: allow the same time as an extract batch.
        assert options["start_to_close_timeout"] >= workflows.EXTRACT_TIMEOUT
        assert options["heartbeat_timeout"] == workflows.HEARTBEAT_TIMEOUT


def test_a_report_starts_with_a_limit_no_longer_than_its_in_flight_window():
    from tfc.temporal.insights import client
    from tracer.views.insights import ACTIVE_RUN_WINDOW

    started = {}

    def start_workflow_sync(workflow_class, workflow_input, **options):
        started.update(options)

    with patch.object(client, "start_workflow_sync", start_workflow_sync):
        assert client.start_insight_run("run-1") == "insight-run-run-1"
    assert started["execution_timeout"] == client.RUN_TIMEOUT
    # A run older than the window is no longer joined by "Run again", so the
    # workflow must be closed by then.
    assert client.RUN_TIMEOUT <= ACTIVE_RUN_WINDOW


def test_the_start_helper_hands_the_execution_timeout_to_temporal():
    from tfc.temporal.common import client as common_client

    calls = []

    class Client:
        async def start_workflow(self, run, workflow_input, **options):
            calls.append(options)

    async def get_client():
        return Client()

    with patch.object(common_client, "get_client", get_client):
        asyncio.run(
            common_client.start_workflow_async(
                workflows.InsightReportWorkflow,
                workflows.InsightRunInput(run_id="run-1"),
                "insight-run-run-1",
                "tasks_xl",
                cancel_existing=False,
                execution_timeout=timedelta(hours=2),
            )
        )
        asyncio.run(
            common_client.start_workflow_async(
                workflows.InsightReportWorkflow,
                workflows.InsightRunInput(run_id="run-2"),
                "insight-run-run-2",
                "tasks_xl",
                cancel_existing=False,
            )
        )
    assert [call["execution_timeout"] for call in calls] == [timedelta(hours=2), None]


def test_a_stage_heartbeats_while_it_runs(monkeypatch):
    monkeypatch.setattr(activities, "HEARTBEAT_EVERY", 0.01, raising=False)
    beats = []
    env = ActivityEnvironment()
    env.on_heartbeat = lambda *details: beats.append(details)

    def extract(run_id, unit_pks):
        time.sleep(0.3)
        return {"done": len(unit_pks)}

    result = asyncio.run(
        env.run(activities._run, "extract", extract, "run-1", [1, 2], licensed=False)
    )
    assert result == {"done": 2}
    assert beats
