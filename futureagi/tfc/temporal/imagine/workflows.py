"""
Temporal workflow for Imagine dynamic analysis.

One workflow per widget analysis — simple 3-activity chain:
  1. Fetch trace data from DB
  2. Run LLM analysis (with Temporal retry on rate limits)
  3. Save result to DB
"""

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

from tfc.temporal.imagine.types import (
    FetchTraceInput,
    ImagineAnalysisInput,
    ImagineAnalysisOutput,
    RunAnalysisInput,
    SaveResultInput,
)

# Retry policy for LLM calls — handles Bedrock rate limits
LLM_RETRY_POLICY = RetryPolicy(
    initial_interval=timedelta(seconds=5),
    maximum_interval=timedelta(seconds=30),
    maximum_attempts=5,
    backoff_coefficient=2.0,
)


@workflow.defn
class ImagineAnalysisWorkflow:
    """Analyze a trace for a single Imagine widget."""

    @workflow.run
    async def run(self, input: ImagineAnalysisInput) -> ImagineAnalysisOutput:
        # The whole chain is wrapped so that ANY pre-completion failure (fetch,
        # LLM, or save) durably writes a terminal "failed" status. Previously
        # only the LLM step did, so a failure in fetch_trace_data — or an
        # un-retried save — left the record stuck at "running" and the Imagine
        # UI "loading forever" (TH-4240 / TH-4388).
        try:
            # 1. Fetch trace data
            trace_ctx = await workflow.execute_activity(
                "fetch_trace_data",
                FetchTraceInput(trace_id=input.trace_id, org_id=input.org_id),
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=RetryPolicy(maximum_attempts=2),
            )

            # 2. Run LLM analysis (with retry for rate limits)
            content = await workflow.execute_activity(
                "run_llm_analysis",
                RunAnalysisInput(prompt=input.prompt, trace_context=trace_ctx),
                start_to_close_timeout=timedelta(seconds=90),
                retry_policy=LLM_RETRY_POLICY,
            )

            # 3. Save result to DB (bounded retry so a persistently-failing
            # save can no longer loop forever under Temporal's unlimited default)
            await workflow.execute_activity(
                "save_analysis_result",
                SaveResultInput(analysis_id=input.analysis_id, content=content),
                start_to_close_timeout=timedelta(seconds=10),
                retry_policy=RetryPolicy(maximum_attempts=3),
            )

            return ImagineAnalysisOutput(content=content)
        except Exception as e:
            # Best-effort terminal write so the record never stays "running".
            try:
                await workflow.execute_activity(
                    "save_analysis_result",
                    SaveResultInput(
                        analysis_id=input.analysis_id,
                        content="",
                        status="failed",
                        error=str(e)[:500] or "Imagine analysis failed",
                    ),
                    start_to_close_timeout=timedelta(seconds=10),
                    retry_policy=RetryPolicy(maximum_attempts=3),
                )
            except Exception:
                pass
            raise
