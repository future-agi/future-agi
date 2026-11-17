"""A voice call that fails on an error shows a fixed sentence as its reason.

CallExecutionWorkflow runs under Temporal's time-skipping test server, with
stand-in activities registered under the real activity names on the real
queues. Whatever error stops a call (a refused balance and a provider setup
that cannot be simulated aside), the call's ended reason (the end_reason that
fetch_and_persist_call_result receives), its processing skip reason and the
workflow's returned error carry one of the two sentences test_executor shows:
"could not start" before the call reaches the provider, "stopped" after. Where
the transcript check has its own reason, that is the skip reason. The
provider's error text, a timeout's seconds and the agent's close reason appear
in none of them; they go to the workflow log only. A provider setup that
cannot be simulated shows prepare_call's sentence saying why, which a re-run
cannot fix, so it has no "Re-run it.".
"""

import dataclasses
import uuid
from datetime import timedelta

import pytest
from temporalio import activity, workflow
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, UnsandboxedWorkflowRunner, Worker

from simulate.models.test_execution import CallExecution
from simulate.temporal.constants import QUEUE_L, QUEUE_S
from simulate.temporal.types.activities import (
    AcquirePhoneInput,
    AcquirePhoneOutput,
    CheckBalanceInput,
    CheckBalanceOutput,
    FetchAndPersistCallResultInput,
    FetchAndPersistCallResultOutput,
    InitiateCallInput,
    InitiateCallOutput,
    MonitorCallInput,
    MonitorCallOutput,
    PersistProcessingSkipStateInput,
    PrepareCallInput,
    PrepareCallOutput,
    ReleaseSlotInput,
    ReportErrorInput,
    RequestSlotInput,
    SignalCallCompleteInput,
    UpdateCallStatusInput,
)
from simulate.temporal.types.call_execution import CallExecutionInput

cew = pytest.importorskip("ee.voice.temporal.workflows.call_execution_workflow")
CallExecutionWorkflow = cew.CallExecutionWorkflow

MADE_UP = "made-up provider error 7f3a"

COULD_NOT_START = "The call could not start because of an error. Re-run it."
STOPPED = "The call stopped because of an error. Re-run it."
SKIP_COULD_NOT_START = (
    "Call failed before evaluations. "
    "The call could not start because of an error. Re-run it."
)
SKIP_STOPPED = (
    "Call failed before evaluations. The call stopped because of an error. Re-run it."
)

BALANCE = "Insufficient balance to make calls."
SKIP_BALANCE = "Call failed before evaluations: Insufficient balance to make calls."

# prepare_call's sentences for a provider setup that cannot be simulated.
SIP_NOT_SUPPORTED = (
    "Outbound simulation is not supported for the provider 'eleven_labs' "
    "over a phone or SIP connection."
)
NO_WEB_CONNECTOR = (
    "The provider 'bland' has no web connector. Add a phone number to simulate it."
)
SKIP_SIP_NOT_SUPPORTED = f"Call failed before evaluations. {SIP_NOT_SUPPORTED}"
SKIP_NO_WEB_CONNECTOR = f"Call failed before evaluations. {NO_WEB_CONNECTOR}"

# The transcript check's own reason for a failed call with no transcript.
SKIP_NO_TRANSCRIPT = (
    "Call transcript is unavailable and we could not determine call duration, "
    "so processing was skipped."
)


class CallActivities:
    """The call workflow's activities, as stand-ins under their real names.

    The settings say how the call goes; the stand-ins record what the workflow
    passed to fetch_and_persist_call_result and persist_processing_skip_state.
    """

    def __init__(
        self,
        *,
        balance_ok: bool = True,
        prepare_error: str | None = None,
        prepare_reason: str | None = "",
        provider: str = "vapi",
        is_outbound: bool = False,
        phone_error: str | None = None,
        initiate_error: str | None = None,
        initiate_raises: bool = False,
        monitor_error: str | None = None,
        ongoing_update_raises: bool = False,
        message_count: int = 6,
    ) -> None:
        self.balance_ok = balance_ok
        self.prepare_error = prepare_error
        self.prepare_reason = prepare_reason
        self.provider = provider
        self.is_outbound = is_outbound
        self.phone_error = phone_error
        self.initiate_error = initiate_error
        self.initiate_raises = initiate_raises
        self.monitor_error = monitor_error
        self.ongoing_update_raises = ongoing_update_raises
        self.message_count = message_count
        self.persisted: list[FetchAndPersistCallResultInput] = []
        self.skip_states: list[PersistProcessingSkipStateInput] = []

    @activity.defn(name="check_call_balance")
    async def check_call_balance(self, _input: CheckBalanceInput) -> CheckBalanceOutput:
        return CheckBalanceOutput(sufficient=self.balance_ok)

    @activity.defn(name="prepare_call")
    async def prepare_call(self, _input: PrepareCallInput) -> dict:
        # The result as it travels to the workflow. A prepare_reason of None
        # leaves out the "reason" key, as a result written before
        # PrepareCallOutput had that field does.
        result = dataclasses.asdict(
            PrepareCallOutput(
                is_outbound=self.is_outbound,
                provider=self.provider,
                error=self.prepare_error,
            )
        )
        if self.prepare_reason is None:
            result.pop("reason", None)
        else:
            result["reason"] = self.prepare_reason
        return result

    @activity.defn(name="request_call_slot")
    async def request_call_slot(self, _input: RequestSlotInput) -> None:
        return None

    @activity.defn(name="acquire_phone_number")
    async def acquire_phone_number(
        self, _input: AcquirePhoneInput
    ) -> AcquirePhoneOutput:
        return AcquirePhoneOutput(success=False, error=self.phone_error)

    @activity.defn(name="initiate_call")
    async def initiate_call(self, _input: InitiateCallInput) -> InitiateCallOutput:
        if self.initiate_raises:
            raise ApplicationError(MADE_UP, non_retryable=True)
        if self.initiate_error is not None:
            return InitiateCallOutput(success=False, error=self.initiate_error)
        return InitiateCallOutput(success=True, provider_call_id="provider-call-1")

    @activity.defn(name="update_call_status")
    async def update_call_status(self, input: UpdateCallStatusInput) -> None:
        ongoing = input.status == CallExecution.CallStatus.ONGOING
        if self.ongoing_update_raises and ongoing:
            raise ApplicationError(MADE_UP, non_retryable=True)

    @activity.defn(name="monitor_call_until_complete")
    async def monitor_call(self, _input: MonitorCallInput) -> MonitorCallOutput:
        return MonitorCallOutput(success=False, error=self.monitor_error)

    @activity.defn(name="release_call_slot")
    async def release_call_slot(self, _input: ReleaseSlotInput) -> None:
        return None

    @activity.defn(name="fetch_and_persist_call_result")
    async def fetch_and_persist_call_result(
        self, input: FetchAndPersistCallResultInput
    ) -> FetchAndPersistCallResultOutput:
        self.persisted.append(input)
        # With a conversation from both sides the transcript check has no
        # reason of its own, so the skip reason the workflow builds is the one
        # persisted.
        has_conversation = self.message_count > 0
        return FetchAndPersistCallResultOutput(
            success=True,
            message_count=self.message_count,
            has_agent_message=has_conversation,
            has_customer_message=has_conversation,
        )

    @activity.defn(name="persist_processing_skip_state")
    async def persist_processing_skip_state(
        self, input: PersistProcessingSkipStateInput
    ) -> None:
        self.skip_states.append(input)

    @activity.defn(name="signal_call_completed")
    async def signal_call_completed(self, _input: SignalCallCompleteInput) -> None:
        return None

    @activity.defn(name="report_workflow_error")
    async def report_workflow_error(self, _input: ReportErrorInput) -> None:
        return None

    def on_queue_s(self) -> list:
        return [
            self.check_call_balance,
            self.prepare_call,
            self.request_call_slot,
            self.acquire_phone_number,
            self.update_call_status,
            self.persist_processing_skip_state,
            self.report_workflow_error,
        ]

    def on_queue_l(self) -> list:
        return [
            self.initiate_call,
            self.monitor_call,
            self.release_call_slot,
            self.fetch_and_persist_call_result,
            self.signal_call_completed,
        ]


async def run_call(
    activities: CallActivities,
    server_options: dict,
    *,
    grant_slot: bool = True,
    call_ended: list | None = None,
):
    """Run one call to its end; return the workflow's result and history.

    ``grant_slot`` sends the dispatcher's slot grant (only for calls that get
    as far as waiting for it). ``call_ended`` is the agent worker's call_ended
    signal arguments, for a LiveKit call.
    """
    async with await WorkflowEnvironment.start_time_skipping(**server_options) as env:
        async with (
            Worker(
                env.client,
                task_queue=QUEUE_L,
                workflows=[CallExecutionWorkflow],
                activities=activities.on_queue_l(),
                workflow_runner=UnsandboxedWorkflowRunner(),
            ),
            Worker(
                env.client,
                task_queue=QUEUE_S,
                activities=activities.on_queue_s(),
            ),
        ):
            handle = await env.client.start_workflow(
                CallExecutionWorkflow.run,
                CallExecutionInput(
                    call_id="call-1",
                    org_id="org-1",
                    workspace_id="workspace-1",
                    test_workflow_id="test-execution-workflow-1",
                    test_execution_id="test-execution-1",
                ),
                id=f"call-exec-{uuid.uuid4()}",
                task_queue=QUEUE_L,
                # Longer than the LiveKit wait (2100 s); a stand-in that never
                # answers ends the test instead of hanging it.
                execution_timeout=timedelta(hours=2),
            )
            if grant_slot:
                await handle.signal(CallExecutionWorkflow.slot_granted)
            if call_ended is not None:
                await handle.signal(CallExecutionWorkflow.call_ended, args=call_ended)
            result = await handle.result()
            history = await handle.fetch_history()
    return result, history


def assert_call_shows(activities, result, reason, skip_reason, *hidden):
    """The call's ended reason, skip reason and returned error, and what none
    of them may contain."""
    (persisted,) = activities.persisted
    (skip_state,) = activities.skip_states
    assert persisted.end_reason == reason
    assert skip_state.processing_skip_reason == skip_reason
    assert result.error == reason
    assert result.status == CallExecution.CallStatus.FAILED
    for text in hidden:
        assert text not in persisted.end_reason
        assert text not in skip_state.processing_skip_reason
        assert text not in result.error


# a. One failure of each kind ------------------------------------------------


@pytest.mark.asyncio
async def test_call_whose_preparation_fails_shows_could_not_start_sentence(
    temporal_test_server_options,
):
    activities = CallActivities(prepare_error=MADE_UP)

    result, _ = await run_call(
        activities, temporal_test_server_options, grant_slot=False
    )

    assert_call_shows(
        activities, result, COULD_NOT_START, SKIP_COULD_NOT_START, MADE_UP
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("reason", "skip_reason"),
    [
        pytest.param(SIP_NOT_SUPPORTED, SKIP_SIP_NOT_SUPPORTED, id="no-sip-path"),
        pytest.param(NO_WEB_CONNECTOR, SKIP_NO_WEB_CONNECTOR, id="no-web-connector"),
    ],
)
async def test_call_whose_provider_setup_cannot_be_simulated_shows_why(
    temporal_test_server_options, reason, skip_reason
):
    activities = CallActivities(prepare_error=reason, prepare_reason=reason)

    result, _ = await run_call(
        activities, temporal_test_server_options, grant_slot=False
    )

    # A re-run cannot fix the setup, so the sentence offers none.
    assert_call_shows(activities, result, reason, skip_reason, "Re-run it.")


@pytest.mark.asyncio
async def test_preparation_result_without_a_reason_shows_could_not_start_sentence(
    temporal_test_server_options,
):
    # A preparation result written before it had a reason field.
    activities = CallActivities(prepare_error=MADE_UP, prepare_reason=None)

    result, _ = await run_call(
        activities, temporal_test_server_options, grant_slot=False
    )

    assert_call_shows(
        activities, result, COULD_NOT_START, SKIP_COULD_NOT_START, MADE_UP
    )


@pytest.mark.asyncio
async def test_call_that_gets_no_phone_number_shows_could_not_start_sentence(
    temporal_test_server_options, monkeypatch
):
    # A new call waits for the phone number dispatcher, which has no failure
    # answer. The direct acquisition, which can fail, runs for calls started
    # before the "phone-dispatcher" patch; answer as such a call does.
    real_patched = workflow.patched

    def patched(patch_id: str) -> bool:
        if patch_id == "phone-dispatcher":
            return False
        return real_patched(patch_id)

    monkeypatch.setattr(workflow, "patched", patched)
    activities = CallActivities(is_outbound=True, phone_error=MADE_UP)

    result, _ = await run_call(activities, temporal_test_server_options)

    assert_call_shows(
        activities, result, COULD_NOT_START, SKIP_COULD_NOT_START, MADE_UP
    )


@pytest.mark.asyncio
async def test_call_the_provider_does_not_start_shows_could_not_start_sentence(
    temporal_test_server_options,
):
    activities = CallActivities(initiate_error=MADE_UP)

    result, _ = await run_call(activities, temporal_test_server_options)

    assert_call_shows(
        activities, result, COULD_NOT_START, SKIP_COULD_NOT_START, MADE_UP
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "monitor_error",
    [MADE_UP, "Monitoring timeout after 14400s"],
    ids=["provider-error", "monitoring-timeout"],
)
async def test_call_whose_monitoring_fails_shows_stopped_sentence(
    temporal_test_server_options, monitor_error
):
    activities = CallActivities(monitor_error=monitor_error)

    result, _ = await run_call(activities, temporal_test_server_options)

    assert_call_shows(activities, result, STOPPED, SKIP_STOPPED, monitor_error)


@pytest.mark.asyncio
async def test_livekit_call_with_no_end_signal_shows_stopped_sentence(
    temporal_test_server_options,
):
    # No call_ended signal: the workflow's own 2100 s wait runs out.
    activities = CallActivities(provider="livekit")

    result, _ = await run_call(activities, temporal_test_server_options)

    assert_call_shows(
        activities,
        result,
        STOPPED,
        SKIP_STOPPED,
        "Call timeout after 2100s",
        "no call_ended signal",
    )


@pytest.mark.asyncio
async def test_livekit_call_the_agent_reports_failed_shows_stopped_sentence(
    temporal_test_server_options,
):
    activities = CallActivities(provider="livekit")

    result, _ = await run_call(
        activities,
        temporal_test_server_options,
        call_ended=["failed", 12, MADE_UP],
    )

    assert_call_shows(
        activities,
        result,
        STOPPED,
        SKIP_STOPPED,
        MADE_UP,
        "Agent reported failure",
    )


@pytest.mark.asyncio
async def test_call_that_errors_before_reaching_the_provider_shows_could_not_start(
    temporal_test_server_options,
):
    # initiate_call raises, so the workflow has no provider call id.
    activities = CallActivities(initiate_raises=True)

    result, _ = await run_call(activities, temporal_test_server_options)

    assert_call_shows(
        activities, result, COULD_NOT_START, SKIP_COULD_NOT_START, MADE_UP
    )


@pytest.mark.asyncio
async def test_call_that_errors_after_reaching_the_provider_shows_stopped_sentence(
    temporal_test_server_options,
):
    # The provider started the call; marking it ongoing then raises.
    activities = CallActivities(ongoing_update_raises=True)

    result, _ = await run_call(activities, temporal_test_server_options)

    assert_call_shows(activities, result, STOPPED, SKIP_STOPPED, MADE_UP)


@pytest.mark.asyncio
async def test_failed_call_without_a_transcript_shows_the_transcript_reason(
    temporal_test_server_options,
):
    # The transcript check's own reason is the skip reason when it has one.
    activities = CallActivities(initiate_error=MADE_UP, message_count=0)

    await run_call(activities, temporal_test_server_options)

    (skip_state,) = activities.skip_states
    assert skip_state.processing_skip_reason == SKIP_NO_TRANSCRIPT


# b. A history with other reason values replays -------------------------------


@pytest.mark.asyncio
async def test_history_with_other_reason_values_replays_under_this_workflow(
    temporal_test_server_options, monkeypatch
):
    # A call in flight across a deploy has a history whose activity arguments
    # carry other reason texts; these are the ones such a history has for a
    # call the provider did not start.
    other_reason = f"Call initiation failed: {MADE_UP}"
    other_skip_reason = f"Call failed before evaluations: {other_reason}"
    activities = CallActivities(initiate_error=MADE_UP)
    with monkeypatch.context() as other:
        other.setattr(cew, "CALL_COULD_NOT_START", other_reason, raising=False)
        other.setattr(
            cew,
            "FAILED_BEFORE_EVALUATIONS",
            "Call failed before evaluations: ",
            raising=False,
        )
        _, history = await run_call(activities, temporal_test_server_options)

    (persisted,) = activities.persisted
    (skip_state,) = activities.skip_states
    assert persisted.end_reason == other_reason
    assert skip_state.processing_skip_reason == other_skip_reason

    replayer = Replayer(
        workflows=[CallExecutionWorkflow],
        workflow_runner=UnsandboxedWorkflowRunner(),
    )
    replay = await replayer.replay_workflow(history, raise_on_replay_failure=False)
    assert replay.replay_failure is None, replay.replay_failure


# c. The sentences are test_executor's -----------------------------------------


def test_call_workflow_sentences_are_test_executors_word_for_word():
    from simulate.services import test_executor

    assert cew.CALL_COULD_NOT_START == test_executor.CALL_COULD_NOT_START
    assert cew.CALL_STOPPED == test_executor.CALL_STOPPED
    assert test_executor.CALL_COULD_NOT_START == COULD_NOT_START
    assert test_executor.CALL_STOPPED == STOPPED
    could_not_start = cew.FAILED_BEFORE_EVALUATIONS + cew.CALL_COULD_NOT_START
    assert could_not_start == SKIP_COULD_NOT_START
    assert cew.FAILED_BEFORE_EVALUATIONS + cew.CALL_STOPPED == SKIP_STOPPED
    assert cew.INSUFFICIENT_BALANCE == BALANCE


# d. The balance sentence ------------------------------------------------------


@pytest.mark.asyncio
async def test_call_without_balance_shows_the_balance_sentence(
    temporal_test_server_options,
):
    activities = CallActivities(balance_ok=False)

    result, _ = await run_call(
        activities, temporal_test_server_options, grant_slot=False
    )

    assert_call_shows(activities, result, BALANCE, SKIP_BALANCE)
