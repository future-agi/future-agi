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
        # 1. Fetch trace data
        trace_ctx = await workflow.execute_activity(
            "fetch_trace_data",
            FetchTraceInput(trace_id=input.trace_id, org_id=input.org_id),
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=RetryPolicy(maximum_attempts=2),
        )

        # 2. Run LLM analysis (with retry for rate limits)
        try:
            content = await workflow.execute_activity(
                "run_llm_analysis",
                RunAnalysisInput(
                    prompt=input.prompt,
                    trace_context=trace_ctx,
                    org_id=input.org_id,
                ),
                start_to_close_timeout=timedelta(seconds=90),
                retry_policy=LLM_RETRY_POLICY,
            )
        except Exception:
            # Save failure to DB
            await workflow.execute_activity(
                "save_analysis_result",
                SaveResultInput(
                    analysis_id=input.analysis_id,
                    content="",
                    status="failed",
                    error="LLM analysis failed after retries",
                ),
                start_to_close_timeout=timedelta(seconds=10),
            )
            raise

        # 3. Save result to DB
        await workflow.execute_activity(
            "save_analysis_result",
            SaveResultInput(analysis_id=input.analysis_id, content=content),
            start_to_close_timeout=timedelta(seconds=10),
        )

        return ImagineAnalysisOutput(content=content)
