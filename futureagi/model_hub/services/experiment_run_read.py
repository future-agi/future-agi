"""Tenant-scoped reads of one experiment run and its recorded items (SRV-3).

The callers (the SDK's cross-machine resume, ``rows="failed"`` without a local
journal, ``runs show`` and test verification) need the pinned plan, per-status
counts and each item's receipt, trace link and score evidence. Reads never
change a run. Output and error values are bounded per item so one page stays
small; a bounded value is replaced by a UTF-8-safe prefix of its canonical JSON
and flagged, never silently dropped.
"""

from __future__ import annotations

import json
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from django.db.models import Count, QuerySet

from model_hub.models.experiments import ExperimentRun, ExperimentRunItem
from model_hub.services.experiment_run_writes import _RUN_KEY, RunWriteError, _canonical

ITEM_STATUSES = ("pending", "running", "completed", "failed", "skipped")
DEFAULT_VALUE_BYTES = 4096
MAX_VALUE_BYTES = 262144
# limit * max_value_bytes may not exceed this; two bounded fields per item.
MAX_PAGE_VALUE_BYTES = 4194304


@dataclass(frozen=True)
class RunItemPage:
    items: list[dict[str, Any]]
    total: int
    limit: int
    offset: int
    has_more: bool


def get_run(*, run_id: UUID | str, datasets: QuerySet) -> ExperimentRun | None:
    """Return the run when its dataset is in ``datasets`` (the caller's scope)."""
    return (
        ExperimentRun.no_workspace_objects.select_related("dataset_version__dataset")
        .filter(
            id=run_id,
            deleted=False,
            dataset_version__deleted=False,
            dataset_version__dataset__in=datasets,
        )
        .first()
    )


def plan_identities(run: ExperimentRun) -> list[dict[str, Any]]:
    metadata = run.metadata if isinstance(run.metadata, Mapping) else {}
    envelope = metadata.get(_RUN_KEY)
    plan = envelope.get("plan") if isinstance(envelope, Mapping) else None
    identities = plan.get("identities") if isinstance(plan, Mapping) else None
    return list(identities) if isinstance(identities, list) else []


def item_counts(run: ExperimentRun) -> dict[str, int]:
    """Counts per item status, plus planned identities with no item yet."""
    counts = dict.fromkeys(ITEM_STATUSES, 0)
    rows = (
        ExperimentRunItem.no_workspace_objects.filter(run_id=run.id, deleted=False)
        .order_by()
        .values("status")
        .annotate(n=Count("id"))
    )
    for row in rows:
        counts[row["status"]] = counts.get(row["status"], 0) + row["n"]
    recorded = sum(counts.values())
    counts["not_started"] = max(len(plan_identities(run)) - recorded, 0)
    return counts


def _receipt_key(item: ExperimentRunItem) -> str | None:
    metadata = item.metadata if isinstance(item.metadata, Mapping) else {}
    envelope = metadata.get(_RUN_KEY)
    if not isinstance(envelope, Mapping):
        return None
    for key in ("result_receipt", "claim_receipt"):
        receipt = envelope.get(key)
        if isinstance(receipt, Mapping) and isinstance(receipt.get("receipt_key"), str):
            return receipt["receipt_key"]
    return None


def _bounded(value: Any, limit: int) -> tuple[Any, bool, int]:
    """Return (value or UTF-8-safe JSON prefix, truncated flag, canonical byte size)."""
    if value is None:
        return None, False, 0
    try:
        encoded = _canonical(value)
    except RunWriteError:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    raw = encoded.encode("utf-8")
    if len(raw) <= limit:
        return value, False, len(raw)
    return raw[:limit].decode("utf-8", errors="ignore"), True, len(raw)


def _score_ref(ref: Any) -> dict[str, Any]:
    """Normalise stored evidence (external and internal runner aliases)."""
    ref = ref if isinstance(ref, Mapping) else {}
    value = ref.get("value", ref.get("score"))
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        value = None
    return {
        "metric_id": ref.get("metric_id"),
        "evaluator_version_id": ref.get("evaluator_version_id", ref.get("version_id")),
        "status": ref.get(
            "status", "assessed" if value is not None else "not_assessed"
        ),
        "value": value,
        "explanation": ref.get("explanation"),
        "trace_id": ref.get("trace_id"),
        "span_id": ref.get("span_id"),
    }


def item_view(item: ExperimentRunItem, max_value_bytes: int) -> dict[str, Any]:
    output, output_truncated, output_bytes = _bounded(item.output, max_value_bytes)
    error, error_truncated, error_bytes = _bounded(item.error, max_value_bytes)
    refs = item.score_refs if isinstance(item.score_refs, list) else []
    return {
        "id": item.id,
        "example_id": item.example_id,
        "trial_number": item.trial_number,
        "status": item.status,
        "receipt_key": _receipt_key(item),
        "trace_id": item.trace_id,
        "root_span_id": item.root_span_id,
        "latency_ms": item.latency_ms,
        "cost": item.cost,
        "token_usage": item.token_usage,
        "score_refs": [_score_ref(ref) for ref in refs],
        "output": output,
        "output_truncated": output_truncated,
        "output_bytes": output_bytes,
        "error": error,
        "error_truncated": error_truncated,
        "error_bytes": error_bytes,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
    }


def list_items(
    run: ExperimentRun,
    *,
    statuses: Collection[str] | None = None,
    limit: int = 100,
    offset: int = 0,
    max_value_bytes: int = DEFAULT_VALUE_BYTES,
) -> RunItemPage:
    """One page of recorded items in plan order (example_id, trial_number)."""
    queryset = ExperimentRunItem.no_workspace_objects.filter(
        run_id=run.id, deleted=False
    )
    if statuses:
        queryset = queryset.filter(status__in=sorted(set(statuses)))
    total = queryset.count()
    page = list(
        queryset.order_by("example_id", "trial_number", "id")[
            offset : offset + limit + 1
        ]
    )
    has_more = len(page) > limit
    return RunItemPage(
        items=[item_view(item, max_value_bytes) for item in page[:limit]],
        total=total,
        limit=limit,
        offset=offset,
        has_more=has_more,
    )
