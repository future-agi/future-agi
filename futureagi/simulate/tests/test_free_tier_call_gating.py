"""TH-5610: free-tier gating across the Temporal simulation path."""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from temporalio import activity
from temporalio.testing import ActivityEnvironment, WorkflowEnvironment
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

from simulate.temporal.activities.small import check_call_balance
from simulate.temporal.constants import QUEUE_L, QUEUE_S
from simulate.temporal.types.activities import (
    CheckBalanceInput,
    CheckBalanceOutput,
    FinalizeInput,
    SetupTestInput,
    SetupTestOutput,
)
from simulate.temporal.types.test_execution import TestExecutionInput
from simulate.temporal.workflows.test_execution_workflow import TestExecutionWorkflow

pytest.importorskip("ee.usage")

# ============================================================================
# check_call_balance: event-type threading + OSS guard
# ============================================================================


@pytest.mark.asyncio
async def test_check_call_balance_uses_threaded_event_type():
    with patch("ee.usage.services.metering.check_usage") as mock_check:
        mock_check.return_value = MagicMock(allowed=True)
        env = ActivityEnvironment()
        out = await env.run(
            check_call_balance,
            CheckBalanceInput(
                org_id="org-1",
                estimated_duration_minutes=30,
                event_type="text_call",
            ),
        )
    assert out.sufficient is True
    mock_check.assert_called_once_with("org-1", "text_call")


@pytest.mark.asyncio
async def test_check_call_balance_defaults_to_voice_call():
    with patch("ee.usage.services.metering.check_usage") as mock_check:
        mock_check.return_value = MagicMock(allowed=True)
        env = ActivityEnvironment()
        await env.run(
            check_call_balance,
            CheckBalanceInput(org_id="org-1", estimated_duration_minutes=30),
        )
    mock_check.assert_called_once_with("org-1", "voice_call")


@pytest.mark.asyncio
async def test_check_call_balance_allows_when_ee_unavailable():
    with patch.dict("sys.modules", {"ee.usage.services.metering": None}):
        env = ActivityEnvironment()
        out = await env.run(
            check_call_balance,
            CheckBalanceInput(org_id="org-1", estimated_duration_minutes=30),
        )
    assert out.sufficient is True


# ============================================================================
# Parent workflow gate
# ============================================================================


@pytest.mark.asyncio
async def test_parent_workflow_fails_run_when_free_tier_exhausted():
    invoked: list[str] = []

    @activity.defn(name="check_call_balance")
    async def check_call_balance_denied(inp: CheckBalanceInput) -> CheckBalanceOutput:
        invoked.append(f"check_call_balance:{inp.event_type}")
        return CheckBalanceOutput(
            sufficient=False,
            error="Free tier Voice Simulation Minutes limit reached",
        )

    @activity.defn(name="setup_test_execution")
    async def setup_stub(inp: SetupTestInput) -> SetupTestOutput:
        invoked.append("setup_test_execution")
        return SetupTestOutput(success=False, error="gate did not fire")

    @activity.defn(name="finalize_test_execution")
    async def finalize_stub(inp: FinalizeInput) -> None:
        invoked.append("finalize_test_execution")

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with (
            Worker(
                env.client,
                task_queue="test-exec-gate",
                workflows=[TestExecutionWorkflow],
                workflow_runner=UnsandboxedWorkflowRunner(),
            ),
            Worker(
                env.client, task_queue=QUEUE_S, activities=[check_call_balance_denied]
            ),
            Worker(
                env.client, task_queue=QUEUE_L, activities=[setup_stub, finalize_stub]
            ),
        ):
            result = await env.client.execute_workflow(
                TestExecutionWorkflow.run,
                TestExecutionInput(
                    test_execution_id=str(uuid.uuid4()),
                    run_test_id=str(uuid.uuid4()),
                    org_id="org-1",
                    scenario_ids=[str(uuid.uuid4())],
                    event_type="voice_call",
                ),
                id=f"test-exec-gate-{uuid.uuid4()}",
                task_queue="test-exec-gate",
            )

    assert result.status == "failed"
    assert "Free tier" in result.error
    assert "check_call_balance:voice_call" in invoked
    assert "setup_test_execution" not in invoked


# ============================================================================
# Mid-flight VAPI watchdog
# ============================================================================

pytest.importorskip("ee.voice")

from ee.voice.temporal.activities.voice_large import (  # noqa: E402
    monitor_call_until_complete,
)
from ee.voice.temporal.types.voice_activities import MonitorCallInput  # noqa: E402
from simulate.semantics import CallExecutionStatus  # noqa: E402

CONTROL_URL = "https://phone-call-websocket.vapi.ai/c1/control"


def _fagi(status, ended_reason=None):
    data = MagicMock()
    data.status = status
    data.duration_seconds = 42.0
    data.ended_reason = ended_reason
    data.raw_log = {"vapi": {"id": "p1", "monitor": {"controlUrl": CONTROL_URL}}}
    return data


def _monitor_input(**overrides):
    defaults = dict(
        call_id="c1",
        provider_call_id="p1",
        call_type="inbound",
        provider="vapi",
        org_id="org-1",
        event_type="voice_call",
        poll_interval_seconds=0,
        max_duration_seconds=60,
    )
    defaults.update(overrides)
    return MonitorCallInput(**defaults)


@pytest.mark.asyncio
async def test_free_plan_over_limit_ends_vapi_call_mid_flight():
    polls = [
        _fagi(CallExecutionStatus.ONGOING),
        _fagi(CallExecutionStatus.ANALYZING, ended_reason="assistant-ended-call"),
    ]
    with (
        patch("ee.voice.services.voice_service_manager.VoiceServiceManager") as vsm_cls,
        patch("ee.usage.services.metering.check_usage") as mock_check,
        patch("ee.usage.services.metering._get_cached_plan", return_value="free"),
        patch("ee.usage.services.config.BillingConfig.get") as mock_cfg,
    ):
        mock_cfg.return_value.get_plan.return_value.usage_caps = "hard"
        mock_check.return_value = MagicMock(
            allowed=False, reason="Free tier Voice Simulation Minutes limit reached"
        )
        vsm = vsm_cls.return_value
        vsm.get_call_async = AsyncMock(side_effect=polls)
        vsm.end_call = MagicMock(return_value=True)

        env = ActivityEnvironment()
        out = await env.run(monitor_call_until_complete, _monitor_input())

    vsm.end_call.assert_called_once()
    payload = vsm.end_call.call_args.args[0].provider_call_payload
    assert payload["monitor"]["controlUrl"] == CONTROL_URL
    assert out.success is True
    assert "free-tier-limit-reached" in out.end_reason


@pytest.mark.asyncio
async def test_soft_cap_plan_never_checks_usage_per_poll():
    polls = [_fagi(CallExecutionStatus.ANALYZING, ended_reason="customer-ended-call")]
    with (
        patch("ee.voice.services.voice_service_manager.VoiceServiceManager") as vsm_cls,
        patch("ee.usage.services.metering.check_usage") as mock_check,
        patch("ee.usage.services.metering._get_cached_plan", return_value="payg"),
        patch("ee.usage.services.config.BillingConfig.get") as mock_cfg,
    ):
        mock_cfg.return_value.get_plan.return_value.usage_caps = "soft"
        vsm = vsm_cls.return_value
        vsm.get_call_async = AsyncMock(side_effect=polls)
        vsm.end_call = MagicMock()

        env = ActivityEnvironment()
        out = await env.run(monitor_call_until_complete, _monitor_input())

    mock_check.assert_not_called()
    vsm.end_call.assert_not_called()
    assert out.end_reason == "customer-ended-call"


@pytest.mark.asyncio
async def test_free_plan_within_limit_does_not_end_call():
    polls = [_fagi(CallExecutionStatus.ANALYZING, ended_reason="customer-ended-call")]
    with (
        patch("ee.voice.services.voice_service_manager.VoiceServiceManager") as vsm_cls,
        patch("ee.usage.services.metering.check_usage") as mock_check,
        patch("ee.usage.services.metering._get_cached_plan", return_value="free"),
        patch("ee.usage.services.config.BillingConfig.get") as mock_cfg,
    ):
        mock_cfg.return_value.get_plan.return_value.usage_caps = "hard"
        mock_check.return_value = MagicMock(allowed=True, reason=None)
        vsm = vsm_cls.return_value
        vsm.get_call_async = AsyncMock(side_effect=polls)
        vsm.end_call = MagicMock()

        env = ActivityEnvironment()
        out = await env.run(monitor_call_until_complete, _monitor_input())

    mock_check.assert_called()
    vsm.end_call.assert_not_called()
    assert out.end_reason == "customer-ended-call"


@pytest.mark.asyncio
async def test_missing_org_id_skips_watchdog():
    polls = [_fagi(CallExecutionStatus.ANALYZING, ended_reason="customer-ended-call")]
    with (
        patch("ee.voice.services.voice_service_manager.VoiceServiceManager") as vsm_cls,
        patch("ee.usage.services.metering.check_usage") as mock_check,
    ):
        vsm = vsm_cls.return_value
        vsm.get_call_async = AsyncMock(side_effect=polls)
        vsm.end_call = MagicMock()

        env = ActivityEnvironment()
        out = await env.run(monitor_call_until_complete, _monitor_input(org_id=""))

    mock_check.assert_not_called()
    vsm.end_call.assert_not_called()
    assert out.success is True
