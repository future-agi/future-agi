"""
Temporal activities for Imagine dynamic analysis.

These run on the tasks_xl worker (LLM-heavy).
"""

import asyncio

import structlog
from temporalio import activity

from tfc.temporal.imagine.types import (
    FetchTraceInput,
    RunAnalysisInput,
    SaveResultInput,
)

logger = structlog.get_logger(__name__)


@activity.defn
async def fetch_trace_data(input: FetchTraceInput) -> str:
    """Fetch trace + spans from DB and format as context string for LLM."""
    from channels.db import database_sync_to_async

    from tracer.models.trace import Trace
    from tracer.services.clickhouse.v2 import get_reader

    try:
        trace = await database_sync_to_async(
            lambda: Trace.objects.select_related("project").get(
                id=input.trace_id,
                project__organization_id=input.org_id,
            )
        )()
    except Trace.DoesNotExist:
        return f"Trace {input.trace_id} not found."

    # Spans now come from ClickHouse via CHSpanReader. The legacy ORM
    # call returned `.values("id", "name", ..., "input", "output", ...)`
    # in start_time order; CHSpan exposes these as dataclass fields and
    # list_by_trace already orders by start_time. input/output are
    # JSON-string columns on CH — keep them as strings for the truncated
    # display string below (matches the prior `str(...)[:300]`).
    reader = await database_sync_to_async(get_reader)()

    def _fetch_spans():
        try:
            return reader.list_by_trace(str(input.trace_id))[:20]
        finally:
            reader.close()

    chspans = await database_sync_to_async(_fetch_spans)()

    project_name = trace.project.name if trace.project else "?"

    lines = [
        f"Trace ID: {trace.id}",
        f"Project: {project_name}",
        f"Spans: {len(chspans)}",
        "",
    ]

    total_latency = 0
    total_tokens = 0
    for i, s in enumerate(chspans):
        lat = s.latency_ms or 0
        tok = s.total_tokens or 0
        total_latency += lat
        total_tokens += tok
        model_str = f" model={s.model}" if s.model else ""
        lines.append(
            f"  {i + 1}. {s.name} [{s.observation_type or '?'}] "
            f"{lat}ms {tok}tok status={s.status or '?'}{model_str}"
        )

    lines.insert(3, f"Total: {total_latency}ms latency, {total_tokens} tokens")

    # Root span input/output
    if chspans:
        root = chspans[0]
        inp = str(root.input or "")[:300]
        out = str(root.output or "")[:300]
        if inp:
            lines.append(f"\nInput: {inp}")
        if out:
            lines.append(f"Output: {out}")

    return "\n".join(lines)


@activity.defn
async def run_llm_analysis(input: RunAnalysisInput) -> str:
    """Run LLM analysis using Falcon's LLM client. Returns markdown."""
    # Falcon is gated on deployment mode (EE / Cloud) AND code presence.
    from tfc.ee_gating import is_oss as _is_oss_fn
    if _is_oss_fn():
        raise RuntimeError(
            "Imagine requires Falcon AI (EE). Not available on OSS."
        )

    try:
        from ee.falcon_ai.llm_client import FalconLLMClient
    except ImportError:
        raise RuntimeError(
            "Imagine requires Falcon AI (EE). Not available on OSS."
        )

    client = FalconLLMClient()

    full_prompt = (
        f"{input.prompt}\n\n"
        f"Trace context:\n{input.trace_context}\n\n"
        f"Respond in markdown. Be specific with numbers from the trace data. "
        f"Keep it concise (3-5 paragraphs max)."
    )

    messages = [{"role": "user", "content": full_prompt}]

    content = ""
    async for chunk in client.stream_completion(messages, tools=None):
        choices = chunk.get("choices", [])
        if not choices:
            continue
        delta = choices[0].get("delta", {})
        text = delta.get("content", "")
        if text:
            content += text
        if choices[0].get("finish_reason") == "stop":
            break

    if not content.strip():
        raise Exception("LLM returned empty response")

    return content


@activity.defn
async def save_analysis_result(input: SaveResultInput) -> None:
    """Save analysis result to DB."""
    from channels.db import database_sync_to_async

    def _save():
        from tracer.models.imagine_analysis import ImagineAnalysis

        try:
            analysis = ImagineAnalysis.objects.get(id=input.analysis_id)
            analysis.content = input.content
            analysis.status = input.status
            analysis.error = input.error
            analysis.save(update_fields=["content", "status", "error", "updated_at"])
        except ImagineAnalysis.DoesNotExist:
            logger.warning("imagine_analysis_not_found", id=input.analysis_id)

    await database_sync_to_async(_save)()
