"""Independent, bounded audio analysis child; never changes the call outcome."""

import asyncio
from datetime import datetime, timedelta

from temporalio import workflow
from temporalio.exceptions import TimeoutError
from temporalio.workflow import ActivityCancellationType

from simulate.temporal.constants import QUEUE_AUDIO, QUEUE_S
from simulate.temporal.retry_policies import (
    AUDIO_ANALYSIS_RETRY_POLICY,
    DB_RETRY_POLICY,
)

with workflow.unsafe.imports_passed_through():
    from ee.voice.temporal.activities.audio_analysis import (
        AnalyzeCallAudioInput,
        AudioAnalysisInput,
        FinalizeFailureInput,
    )


def _is_timeout(exc):
    while exc is not None:
        if isinstance(exc, TimeoutError):
            return True
        exc = exc.__cause__
    return False


@workflow.defn
class AudioAnalysisWorkflow:
    @workflow.run
    async def run(self, input: AudioAnalysisInput) -> None:
        try:
            remaining = (
                datetime.fromisoformat(input.deadline_at.replace("Z", "+00:00"))
                - workflow.now()
            )
            if remaining <= timedelta(seconds=30):
                await self._finalize(input, "timeout")
                return
            await workflow.execute_activity(
                "analyze_call_audio",
                AnalyzeCallAudioInput(
                    input.call_id,
                    input.analysis_id,
                    input.generation,
                    input.deadline_at,
                    input.org_id,
                    input.workspace_id,
                ),
                task_queue=QUEUE_AUDIO,
                start_to_close_timeout=timedelta(minutes=15),
                schedule_to_close_timeout=remaining - timedelta(seconds=30),
                heartbeat_timeout=timedelta(minutes=2),
                retry_policy=AUDIO_ANALYSIS_RETRY_POLICY,
                cancellation_type=ActivityCancellationType.WAIT_CANCELLATION_COMPLETED,
            )
        except (Exception, asyncio.CancelledError) as exc:
            reason = (
                "timeout"
                if isinstance(exc, asyncio.CancelledError) or _is_timeout(exc)
                else "analysis_error"
            )
            await asyncio.shield(self._finalize(input, reason))
            if isinstance(exc, asyncio.CancelledError):
                raise

    async def _finalize(self, input, reason):
        await workflow.execute_activity(
            "finalize_audio_analysis_failure",
            FinalizeFailureInput(
                input.call_id, input.analysis_id, input.generation, reason
            ),
            task_queue=QUEUE_S,
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=DB_RETRY_POLICY,
        )
