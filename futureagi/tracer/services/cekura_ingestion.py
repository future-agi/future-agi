"""Persist completed Cekura runs as eval scores on the run's trace.

Scores land in ``EvalLogger``, the table the trace/eval read paths query
(``tracer_eval_logger``, mirrored into ClickHouse by CDC). The eval half of
the Langfuse ingestion path does the same thing for the same reason, see
``tracer/utils/langfuse_upsert.py``.
"""

from dataclasses import dataclass
from typing import Any

import structlog
from django.db import transaction
from django.utils import timezone

from integrations.transformers.cekura_transformer import CekuraTransformer
from tracer.models.cekura_integration import CekuraIntegration
from tracer.models.observation_span import EvalLogger, ObservationSpan
from tracer.models.trace import Trace

logger = structlog.get_logger(__name__)

_transformer = CekuraTransformer()

# Same deterministic root-span id the Langfuse importer builds, capped to the
# 255-char id column. Sharing the formula is what lets a transcript import of
# the same run reuse this row instead of adding a second root.
_ROOT_SPAN_PREFIX = "root-"
_ROOT_SPAN_ID_LIMIT = 245


@dataclass(frozen=True)
class IngestionResult:
    """How many metrics were written, and how many were left out."""

    ingested: int
    skipped: int


def ingest_cekura_run(
    integration: CekuraIntegration, payload: dict[str, Any]
) -> IngestionResult:
    """Write one Cekura run's per-metric scores onto its trace.

    Idempotent per ``(run, metric)``: a redelivered webhook updates the same
    ``EvalLogger`` rows rather than adding duplicates.
    """
    run_id = str(payload.get("run_id") or "").strip()
    metrics = payload.get("metrics") or []

    if not _transformer.is_ingestible(payload):
        logger.info(
            "cekura_run_not_final",
            run_id=run_id,
            status=payload.get("status"),
            project_id=str(integration.project_id),
        )
        return IngestionResult(ingested=0, skipped=len(metrics))

    scores = _dedupe_by_eval_id(_transformer.to_eval_logger_fields(payload), run_id)
    if not scores:
        return IngestionResult(ingested=0, skipped=len(metrics))

    with transaction.atomic():
        trace = _resolve_trace(integration, run_id, payload)
        anchor_span = _resolve_anchor_span(integration, trace, run_id, payload)

        for score in scores:
            EvalLogger.no_workspace_objects.update_or_create(
                eval_id=score.pop("eval_id"),
                defaults={
                    "trace": trace,
                    "observation_span": anchor_span,
                    **score,
                },
            )

    logger.info(
        "cekura_run_ingested",
        run_id=run_id,
        project_id=str(integration.project_id),
        ingested=len(scores),
        skipped=len(metrics) - len(scores),
    )
    return IngestionResult(ingested=len(scores), skipped=len(metrics) - len(scores))


def _dedupe_by_eval_id(
    scores: list[dict[str, Any]], run_id: str
) -> list[dict[str, Any]]:
    """Collapse repeated metric names, keeping the last reported value.

    Two metrics sharing a name resolve to one ``eval_id``, so writing both
    would leave whichever ran last in the row anyway. Collapsing up front
    keeps the reported counts honest and keeps a redelivery of the same
    payload from depending on iteration order.
    """
    by_eval_id: dict[str, dict[str, Any]] = {}
    for score in scores:
        eval_id = score["eval_id"]
        if eval_id in by_eval_id:
            logger.warning(
                "cekura_duplicate_metric_name",
                run_id=run_id,
                eval_type_id=score["eval_type_id"],
            )
        by_eval_id[eval_id] = score
    return list(by_eval_id.values())


def _resolve_trace(
    integration: CekuraIntegration, run_id: str, payload: dict[str, Any]
) -> Trace:
    """Find the trace this run was imported as, or stand one up for it.

    Correlation is ``project`` + ``external_id`` — the same pair the Langfuse
    importer matches on — so a transcript imported for this run, before or
    after the webhook, is the trace the scores hang off rather than a second
    copy. ``filter().first()`` instead of ``get_or_create`` because there is no
    unique constraint on the pair and concurrent deliveries can race.
    """
    trace = (
        Trace.no_workspace_objects.filter(
            project_id=integration.project_id,
            external_id=run_id,
        )
        .order_by("created_at")
        .first()
    )
    if trace is not None:
        return trace

    return Trace.no_workspace_objects.create(
        project_id=integration.project_id,
        external_id=run_id,
        name=_run_name(payload, run_id),
        metadata={"source": "cekura", "run_id": run_id},
    )


def _resolve_anchor_span(
    integration: CekuraIntegration,
    trace: Trace,
    run_id: str,
    payload: dict[str, Any],
) -> ObservationSpan:
    """Return the span the scores attach to.

    ``EvalLogger`` rows targeting a span or a trace must carry both FKs (see
    the ``eval_logger_target_type_fks`` constraint), so a score cannot exist
    without one. Prefer the trace's earliest span, which is the imported
    transcript's root once one exists; otherwise create the placeholder root
    under the shared deterministic id, which a later transcript import
    upserts in place.
    """
    existing = (
        ObservationSpan.no_workspace_objects.filter(trace=trace)
        .order_by("start_time")
        .first()
    )
    if existing is not None:
        return existing

    started_at, completed_at = _transformer.run_window(payload)
    started_at = started_at or timezone.now()
    latency_ms = None
    if completed_at is not None:
        latency_ms = max(int((completed_at - started_at).total_seconds() * 1000), 0)

    span, _ = ObservationSpan.no_workspace_objects.update_or_create(
        id=f"{_ROOT_SPAN_PREFIX}{run_id[:_ROOT_SPAN_ID_LIMIT]}",
        defaults={
            "trace": trace,
            "project_id": integration.project_id,
            "org_id": integration.organization_id,
            "parent_span_id": None,
            "observation_type": "chain",
            "name": _run_name(payload, run_id),
            "start_time": started_at,
            "end_time": completed_at,
            "latency_ms": latency_ms,
            "status": "OK",
            "metadata": {"source": "cekura", "run_id": run_id},
            "span_attributes": {"fi.span.kind": "CHAIN"},
        },
    )
    return span


def _run_name(payload: dict[str, Any], run_id: str) -> str:
    """Human-facing label for the run, falling back to its id."""
    name: str | None = payload.get("name") or payload.get("test_name")
    return str(name).strip() if name else f"Cekura run {run_id}"
