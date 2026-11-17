"""Generation-fenced lifecycle receipts; findings and cache are never receipts.

One row survives retries/deletion of findings. Every update compares both the
observed generation and expected status in SQL; a read is not a claim. There is
no automatic expiry: loss of a worker must not promote partial findings to done.
"""

from uuid import uuid4

from django.utils import timezone

from tracer.models.background_trace_analysis_state import BackgroundTraceAnalysisState


def get_state(trace_id, *, project_ids=None):
    query = BackgroundTraceAnalysisState.objects.filter(trace_id=trace_id)
    if project_ids is not None:
        query = query.filter(project_id__in=project_ids)
    return query.first()


def begin(project_id, trace_id, *, only_if_absent=False):
    """Claim a fresh pending generation, or return None for an active competitor.

    The unconditional unique constraint serializes concurrent first creation.
    Terminal retries use a compare-and-swap, including the terminal status, so
    two racing callers cannot both acquire the same replacement generation.
    """
    generation = uuid4()
    row, created = BackgroundTraceAnalysisState.objects.get_or_create(
        project_id=project_id,
        trace_id=trace_id,
        defaults={"generation": generation, "status": "retry_pending"},
    )
    if created:
        return str(generation)
    if only_if_absent or row.status not in ("aborted", "completed"):
        return None
    changed = BackgroundTraceAnalysisState.objects.filter(
        project_id=project_id,
        trace_id=trace_id,
        generation=row.generation,
        status=row.status,
    ).update(
        generation=generation,
        status="retry_pending",
        reason_code="",
        result_analysis_id=None,
        updated_at=timezone.now(),
    )
    return str(generation) if changed else None


def transition(
    project_id, trace_id, generation, *, expected_status, status, reason_code=""
):
    return bool(
        BackgroundTraceAnalysisState.objects.filter(
            project_id=project_id,
            trace_id=trace_id,
            generation=generation,
            status=expected_status,
        ).update(status=status, reason_code=reason_code, updated_at=timezone.now())
    )


def admit(project_id, trace_id, generation):
    return transition(
        project_id,
        trace_id,
        generation,
        expected_status="retry_pending",
        status="running",
    )


def complete(project_id, trace_id, generation):
    """Freeze the candidate first, then publish with one bounded lifecycle CAS."""
    from tracer.models.trace_error_analysis import TraceErrorAnalysis
    from tracer.services.analysis_result_lock import result_lock

    with result_lock(generation):
        row = BackgroundTraceAnalysisState.objects.filter(
            project_id=project_id,
            trace_id=trace_id,
            generation=generation,
            status="running",
        ).first()
        if row is None:
            return False
        # Raw read is intentionally local to publication; readers never use it.
        candidate = TraceErrorAnalysis._base_manager.filter(
            pk=generation,
            project_id=project_id,
            trace_id=trace_id,
            deleted=False,
        ).first()
        if candidate is None:
            abort(project_id, trace_id, generation, "analysis_failed")
            return False
        published = bool(
            BackgroundTraceAnalysisState.objects.filter(
                project_id=project_id,
                trace_id=trace_id,
                generation=generation,
                status="running",
            ).update(
                status="completed",
                result_analysis_id=generation,
                reason_code="",
                updated_at=timezone.now(),
            )
        )
        if published and candidate.agent_version == "1.0":
            from ee.agenthub.traceerroragent.analysis_service import (
                TraceErrorAnalysisService,
            )

            # Judge learning is part of the publication transaction, after its
            # CAS. Duplicate/stale owners cannot teach; failures roll back both
            # the receipt and patterns rather than losing or duplicating them.
            TraceErrorAnalysisService(project_id)._learn_published_result(candidate)
        return published


def abort(project_id, trace_id, generation, code, *, expected_status="running"):
    # Never persist arbitrary exception text or provider messages as a reason.
    if code not in (
        "analysis_access_revoked",
        "analysis_deadline_exceeded",
        "analysis_failed",
        "analysis_dispatch_failed",
    ):
        code = "analysis_failed"
    return transition(
        project_id,
        trace_id,
        generation,
        expected_status=expected_status,
        status="aborted",
        reason_code=code,
    )
