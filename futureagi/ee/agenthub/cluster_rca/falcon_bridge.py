"""Bridge that runs the cluster-RCA agent inside Falcon's async chat loop.

Falcon's ``AgentLoop.run`` delegates here when the active skill is ``cluster-rca``
on the first turn of a conversation. We run the (synchronous) ``ClusterAnalysisAgent``
in a thread, marshal its ``on_event`` callbacks back onto the event loop, and emit
them as WebSocket frames the Analyze tab understands. When the run finishes we
persist the synthesis onto the cluster (the cached headline) and return an
assistant-message dict whose ``content`` is the synthesis — that becomes the seed
context Falcon answers follow-ups against.

This is the ONLY new code in Falcon's agent path: a thin adapter, not a second
agent loop. The investigation logic, tools, and prompt all stay in
``ee.agenthub.cluster_rca``.
"""

from __future__ import annotations

import asyncio
import threading
import uuid as _uuid
from typing import Any, Awaitable, Callable, Optional

import structlog
from asgiref.sync import sync_to_async
from django.db import close_old_connections

from ee.agenthub.cluster_rca.types import (
    RcaErrorEvent,
    RcaReasoningEvent,
    RcaStatusEvent,
    RcaStepResultEvent,
    RcaStepStartEvent,
    RcaSynthesisEvent,
)

logger = structlog.get_logger(__name__)

SendCallback = Callable[[dict], Awaitable[None]]

# Sentinel pushed onto the event queue when the worker thread is done.
_DONE = object()


def _resolve_cluster(organization: Any, workspace: Any, label: str, project_id: Optional[str]):
    """Resolve a cluster CharField label (e.g. "C01") to (uuid, project_uuid,
    error_count) within the caller's org/workspace scope. Returns None if the
    label can't be resolved — caller falls through to normal Falcon."""
    from tracer.models.project import Project
    from tracer.models.trace_error_analysis import TraceErrorGroup

    if not organization:
        return None
    proj_qs = Project.objects.filter(organization_id=organization.id)
    if workspace is not None:
        proj_qs = proj_qs.filter(workspace_id=workspace.id)
    accessible = list(proj_qs.values_list("id", flat=True))
    if not accessible:
        return None

    qs = TraceErrorGroup.objects.filter(
        cluster_id=label, project_id__in=accessible, deleted=False
    )
    if project_id:
        qs = qs.filter(project_id=project_id)
    cluster = qs.only("id", "project_id", "error_count").first()
    if cluster is None:
        return None
    return str(cluster.id), str(cluster.project_id), int(cluster.error_count or 0)


def _persist_synthesis(cluster_uuid: str, synthesis: Any, error_count_at_run: int, trace: list[dict] | None) -> None:
    """Cache the synthesis + investigation trail onto the cluster row (the
    headline card reads the synthesis; the Analyze tab replays the trail)."""
    from django.utils import timezone

    from tracer.models.trace_error_analysis import TraceErrorGroup

    TraceErrorGroup.objects.filter(id=cluster_uuid).update(
        rca_synthesis=synthesis.synthesis,
        rca_fix=synthesis.fix,
        rca_confidence=getattr(synthesis.confidence, "value", synthesis.confidence),
        rca_evidence_trace_ids=list(synthesis.evidence_trace_ids or []),
        rca_at=timezone.now(),
        rca_failures_at_run=error_count_at_run,
        rca_trace=trace or None,
    )

    # A Linear ticket filed before this run would stay bare forever — post
    # the fresh RCA as a comment on it (no-op when nothing is linked).
    from tracer.utils.feed import post_rca_comment_to_linked_issue

    post_rca_comment_to_linked_issue(cluster_uuid)


def _bill_cost_from_thread(organization: Any, cost_usd: float) -> None:
    """Emit the AI-credit usage event for ``cost_usd`` from the worker thread.

    Normally billing happens in the consumer after ``await fut`` returns. On an
    explicit Stop the consumer coroutine is cancelled before that await resolves,
    so the consumer's billing path never runs — yet the LLM spend was real. We
    mirror the thread-side persistence above and bill here so the spend is never
    dropped. Only called on the Stop path; the normal path still bills in the
    consumer (billing here unconditionally would double-charge)."""
    org_id = getattr(organization, "id", None)
    if not org_id or cost_usd <= 0:
        return
    from ee.usage.schemas.event_types import BillingEventType
    from ee.usage.schemas.events import UsageEvent
    from ee.usage.services.config import BillingConfig
    from ee.usage.services.emitter import emit

    try:
        credits = BillingConfig.get().calculate_ai_credits(cost_usd)
        emit(
            UsageEvent(
                org_id=str(org_id),
                event_type=BillingEventType.FALCON_AI_CHAT,
                amount=credits,
                properties={
                    "source": "falcon_ai",
                    "model_used": "cluster-rca",
                    "raw_cost_usd": str(cost_usd),
                    "pricing_source": "gateway_header",
                    "stopped": "true",
                },
            )
        )
    except Exception:
        logger.exception("cluster_rca_bridge_stop_bill_failed", org_id=str(org_id))


def _format_synthesis_message(synthesis: Any) -> str:
    """Render the synthesis as the assistant message body — this is the seed
    context Falcon answers follow-ups against, so it must read on its own."""
    conf = getattr(synthesis.confidence, "value", synthesis.confidence)
    conf_label = {"H": "High", "M": "Medium", "L": "Low"}.get(conf, conf)
    parts = [synthesis.synthesis.strip()]
    if synthesis.fix:
        parts.append(f"\n\n**Fix:** {synthesis.fix.strip()}")
    parts.append(f"\n\n_Confidence: {conf_label}_")
    return "".join(parts)


async def run_cluster_rca(
    *,
    tool_context,
    context_info: Optional[dict],
    user_message: str,
    send_callback: SendCallback,
) -> Optional[dict]:
    """Run the cluster-RCA agent and stream it over Falcon's socket.

    Returns an assistant-message result dict (same shape as ``AgentLoop.run``)
    on success, or ``None`` if no cluster could be resolved from context — in
    which case the caller should fall through to the normal Falcon loop.
    """
    ctx = context_info or {}
    label = ctx.get("entity_id") or ctx.get("cluster_id")
    project_id = ctx.get("project_id")
    is_cluster_request = ctx.get("entity_type") == "error_cluster"

    async def _fail(reason: str) -> dict:
        """End the turn instead of handing it to the general Falcon loop.

        Falling through looks harmless but is not: the general loop answers
        "Help me with: Cluster RCA" into a hidden conversation nobody reads, so
        the user is billed for a chat they never see, and it streams frames the
        Fix tab's handler ignores. The run then ends with an empty thread, which
        renders as the pre-run empty state — the cluster looks like it was never
        analysed, and the failure is reported to nobody.
        """
        message_id = str(_uuid.uuid4())
        await send_callback(
            RcaErrorEvent(message_id=message_id, error=reason).to_frame()
        )
        return {
            "id": message_id,
            "content": "",
            "tool_calls": [],
            "completion_card": None,
            "model_used": "cluster-rca",
            "mode": "cluster_rca",
            "input_tokens": 0,
            "output_tokens": 0,
            "cost_usd": 0.0,
            "title": None,
        }

    if not label:
        if is_cluster_request:
            logger.warning("cluster_rca_bridge_no_entity_id", context=ctx)
            return await _fail("No cluster was supplied to analyse.")
        return None

    resolved = await sync_to_async(_resolve_cluster, thread_sensitive=False)(
        tool_context.organization, tool_context.workspace, str(label), project_id
    )
    if resolved is None:
        # Resolution is workspace-scoped, so a cluster whose project sits
        # outside this socket's workspace fails here every time — reproducibly,
        # for that cluster only, which reads as "this one can't be analysed".
        logger.warning(
            "cluster_rca_bridge_unresolved", label=label, project_id=project_id
        )
        if is_cluster_request:
            return await _fail(
                "That cluster isn't available in this workspace, so it can't be analysed."
            )
        return None

    cluster_uuid, project_uuid, error_count = resolved

    assistant_message_id = str(_uuid.uuid4())
    loop = asyncio.get_running_loop()
    events_q: asyncio.Queue = asyncio.Queue()

    # Cooperative-cancel hook. The agent loop polls this Event at the top of each
    # turn and returns its partial result (with accumulated cost) when it is set.
    # We stash it on the running asyncio Task — the same handle the consumer's
    # Stop handler already tracks (``self._agent_task``) — so the handler can set
    # it without any new signatures threaded through AgentLoop.run. Only an
    # explicit Stop sets it; a client disconnect leaves the run untouched.
    stop_event = threading.Event()
    _task = asyncio.current_task()
    if _task is not None:
        _task._rca_stop_event = stop_event

    # Ordered investigation trail, persisted to rca_trace so the Analyze tab can
    # replay the full run (reasoning + steps + synthesis) on reload. Reasoning is
    # capped to bound storage; tool results are already token-budgeted upstream.
    #
    # Built and persisted in the WORKER THREAD, not the consumer coroutine: a
    # client disconnect cancels the coroutine, but the thread always finishes
    # the run — without thread-side persistence a mid-run reload would burn the
    # full LLM spend and then discard the result.
    trace: list[dict] = []
    call_counter = 0
    open_call_id: Optional[str] = None

    def on_event(event_type: str, payload: dict) -> None:
        # Called from the worker thread. Record the trail here, assign call
        # ids, then hop onto the loop thread-safely for live streaming.
        nonlocal call_counter, open_call_id
        if event_type == "reasoning":
            _rsn = payload.get("reasoning") or payload.get("content")
            if _rsn:
                trace.append({"type": "reasoning", "text": str(_rsn)[:4000]})
        elif event_type == "tool_call":
            call_counter += 1
            open_call_id = f"rca-{call_counter}"
            payload = {**payload, "_call_id": open_call_id}
            trace.append(
                {
                    "type": "step_start",
                    "call_id": open_call_id,
                    "tool": payload.get("tool"),
                    "args": payload.get("args"),
                }
            )
        elif event_type == "tool_result":
            payload = {**payload, "_call_id": open_call_id}
            trace.append(
                {
                    "type": "step_result",
                    "call_id": open_call_id,
                    "tool": payload.get("tool"),
                    "result": payload.get("result"),
                }
            )
            open_call_id = None
        elif event_type == "synthesis":
            trace.append(
                {
                    "type": "synthesis",
                    "synthesis": payload.get("synthesis"),
                    "fix": payload.get("fix"),
                    "confidence": payload.get("confidence"),
                    "suggested_questions": payload.get("suggested_questions", []),
                }
            )
        loop.call_soon_threadsafe(events_q.put_nowait, (event_type, payload))

    def run_agent() -> Any:
        from ee.agenthub.cluster_rca.agent import ClusterAnalysisAgent

        try:
            agent = ClusterAnalysisAgent(
                cluster_id=cluster_uuid,
                project_id=project_uuid,
                question=user_message or None,
                on_event=on_event,
                stop_event=stop_event,
            )
            result = agent.run()
            # Persist from the thread so the run survives the consumer
            # coroutine being cancelled by a client disconnect mid-run.
            if getattr(result, "synthesis", None) is not None:
                try:
                    close_old_connections()
                    _persist_synthesis(
                        cluster_uuid, result.synthesis, error_count, trace
                    )
                except Exception:
                    logger.exception(
                        "cluster_rca_bridge_persist_failed", cluster_id=cluster_uuid
                    )
            # On an explicit Stop the consumer coroutine is cancelled before it
            # can bill, so the partial spend would go un-billed. Bill it here
            # from the thread (same reasoning as thread-side persistence above).
            # Guarded on the Event so the normal path still bills in the consumer.
            if stop_event.is_set():
                _bill_cost_from_thread(
                    tool_context.organization,
                    getattr(result, "cost_usd", 0.0) or 0.0,
                )
            return result
        finally:
            loop.call_soon_threadsafe(events_q.put_nowait, _DONE)

    fut = loop.run_in_executor(None, run_agent)

    synthesis_obj = None
    tool_calls_log: list[dict] = []

    while True:
        item = await events_q.get()
        if item is _DONE:
            break
        event_type, payload = item

        if event_type == "reasoning":
            await send_callback(
                RcaReasoningEvent(
                    message_id=assistant_message_id,
                    turn=payload.get("turn"),
                    reasoning=payload.get("reasoning"),
                    content=payload.get("content"),
                ).to_frame()
            )
            # Trail append happens in on_event (worker thread) — not here.
        elif event_type == "status":
            # Lightweight progress pings emitted during setup (before the first
            # LLM round-trip) so the Analyze loader shows real activity instead
            # of dead-air. Not persisted — purely a live UX signal.
            await send_callback(
                RcaStatusEvent(
                    message_id=assistant_message_id,
                    phase=payload.get("phase"),
                    detail=payload.get("detail"),
                ).to_frame()
            )
        elif event_type == "tool_call":
            tool_calls_log.append(
                {"tool_name": payload.get("tool"), "params": payload.get("args")}
            )
            await send_callback(
                RcaStepStartEvent(
                    message_id=assistant_message_id,
                    call_id=payload.get("_call_id"),
                    tool=payload.get("tool"),
                    args=payload.get("args"),
                    turn=payload.get("turn"),
                ).to_frame()
            )
        elif event_type == "tool_result":
            await send_callback(
                RcaStepResultEvent(
                    message_id=assistant_message_id,
                    call_id=payload.get("_call_id"),
                    tool=payload.get("tool"),
                    result=payload.get("result"),
                    turn=payload.get("turn"),
                ).to_frame()
            )
        elif event_type == "synthesis":
            # payload is asdict(ClusterSynthesis)
            await send_callback(
                RcaSynthesisEvent(
                    message_id=assistant_message_id,
                    synthesis=payload.get("synthesis"),
                    fix=payload.get("fix"),
                    confidence=payload.get("confidence"),
                    evidence_trace_ids=payload.get("evidence_trace_ids", []),
                    suggested_questions=payload.get("suggested_questions", []),
                ).to_frame()
            )
        elif event_type == "error":
            await send_callback(
                RcaErrorEvent(
                    message_id=assistant_message_id,
                    error=payload.get("message", "Cluster analysis failed"),
                ).to_frame()
            )

    # Worker finished — collect the typed result.
    try:
        result = await fut
    except Exception:
        logger.exception("cluster_rca_bridge_run_failed", cluster_id=cluster_uuid)
        await send_callback(
            RcaErrorEvent(message_id=assistant_message_id).to_frame()
        )
        return {
            "id": assistant_message_id,
            "content": "",
            "tool_calls": tool_calls_log,
            "completion_card": None,
            "model_used": "cluster-rca",
            "mode": "cluster_rca",
            "input_tokens": 0,
            "output_tokens": 0,
            "cost_usd": 0.0,
            "title": None,
        }

    # Persistence already happened in the worker thread (run_agent) — the
    # coroutine only formats the chat message and closes the stream.
    synthesis_obj = result.synthesis
    content = ""
    if synthesis_obj is not None:
        content = _format_synthesis_message(synthesis_obj)

    cost_usd = getattr(result, "cost_usd", 0.0) or 0.0

    # Don't send our own 'done' — the consumer sends the canonical one with
    # mode/model/token fields that the FE expects.

    return {
        "id": assistant_message_id,
        "content": content,
        "tool_calls": tool_calls_log,
        "completion_card": None,
        "model_used": "cluster-rca",
        "mode": "cluster_rca",
        "input_tokens": 0,
        "output_tokens": 0,
        "cost_usd": cost_usd,
        "title": None,
    }
