"""
Temporal activities for Imagine dynamic analysis.

These run on the tasks_xl worker (LLM-heavy).
"""

import structlog
from temporalio import activity
from temporalio.exceptions import ApplicationError

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
    from django.core.exceptions import ValidationError

    from tracer.models.project import Project
    from tracer.models.trace import Trace
    from tracer.services.clickhouse.v2 import get_reader

    # Older workflow payloads decode without a project binding. Never fall
    # back to an organization-only lookup for those in-flight executions.
    if not input.project_id:
        return f"Trace {input.trace_id} not found."

    def _fetch_metadata():
        project = Project.no_workspace_objects.get(
            id=input.project_id,
            organization_id=input.org_id,
            deleted=False,
        )
        trace = (
            Trace.no_workspace_objects.only("id", "project_id")
            .filter(id=input.trace_id, deleted=False)
            .first()
        )
        if trace is not None and trace.project_id != project.id:
            raise Trace.DoesNotExist
        return project, trace

    try:
        project, trace = await database_sync_to_async(_fetch_metadata)()
    except (Project.DoesNotExist, Trace.DoesNotExist, ValidationError):
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
            return reader.list_by_trace(
                str(input.trace_id), project_id=str(input.project_id)
            )[:20]
        finally:
            reader.close()

    chspans = await database_sync_to_async(_fetch_spans)()

    if trace is None and not chspans:
        return f"Trace {input.trace_id} not found."

    lines = [
        f"Trace ID: {trace.id if trace else input.trace_id}",
        f"Project: {project.name}",
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
    # Fetch may have completed before deployment, so guard the persisted LLM
    # activity payload independently of the fetch activity's validation.
    if not input.project_id:
        raise ApplicationError(
            "Imagine project binding missing.",
            type="ImagineProjectBindingMissing",
            non_retryable=True,
        )

    from tfc.ee_gating import EEFeature, check_ee_feature

    check_ee_feature(EEFeature.FALCON_AI, org_id=input.org_id, activity=True)

    try:
        from ee.falcon_ai.llm_client import FalconLLMClient
    except ImportError as exc:
        raise ApplicationError(
            "Falcon AI module not available.",
            type="FeatureUnavailable",
            non_retryable=True,
        ) from exc

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
    """Persist only bound results; quarantine unbound historical saves."""
    from channels.db import database_sync_to_async
    from django.core.exceptions import ValidationError
    from django.utils import timezone

    unbound = not input.project_id or not input.org_id
    workflow_id = activity.info().workflow_id

    def _save():
        from tracer.models.imagine_analysis import ImagineAnalysis

        try:
            if unbound:
                # The payload cannot authorize a content write. Only the row
                # linked to this executing workflow may receive fixed failure
                # fields; never fall back to an id-only cleanup or persist any
                # content/error/status from the legacy payload.
                if not workflow_id:
                    return
                updated = ImagineAnalysis.objects.filter(
                    id=input.analysis_id,
                    workflow_id=workflow_id,
                ).update(
                    content="",
                    status="failed",
                    error="Imagine project binding missing.",
                    updated_at=timezone.now(),
                )
            else:
                # A current project match cannot certify historical context.
                # Only the versioned executing workflow can write its result;
                # an old run must not overwrite a freshly dispatched analysis.
                if not (workflow_id or "").startswith("imagine-pb1-"):
                    return
                # Keep authorization and mutation in the same database write.
                updated = ImagineAnalysis.objects.filter(
                    id=input.analysis_id,
                    workflow_id=workflow_id,
                    project_id=input.project_id,
                    organization_id=input.org_id,
                    project__organization_id=input.org_id,
                    project__deleted=False,
                ).update(
                    content=input.content,
                    status=input.status,
                    error=input.error,
                    updated_at=timezone.now(),
                )
        except ValidationError:
            updated = 0
        if not updated:
            logger.warning("imagine_analysis_not_found")

    await database_sync_to_async(_save)()
