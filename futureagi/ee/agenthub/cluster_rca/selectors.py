"""Read-layer selectors for the cluster-RCA agent (§03 layering).

The agent holds no inline ORM: every database READ goes through a named selector
here. (Its write tools are in-memory only — append a finding / set the synthesis —
so there is no write service.)

Scope classes:
  [explicit]   — takes ``project_id`` and filters on it.
  [transitive] — scoped via ``cluster_uuid`` / ``trace_uuids`` only. ErrorClusterTraces,
                 EvalLogger and the TraceScanIssue rollups have NO project FK, so they
                 are tenant-safe ONLY when the caller passes a project-validated
                 ``cluster_uuid`` (the agent validates ownership in
                 ``resolve_cluster_context`` / ``__init__``). An unvalidated id leaks
                 across tenants.

``select_related`` / ``.only(...)`` are part of each selector's CONTRACT — callers read
related attrs (``i.scan_result.trace_id``, ``er.custom_eval_config.name`` …); dropping
them regresses to N+1 / deferred-field queries. All reads filter ``deleted=False`` and
read FK *columns* (``*_id``), never PG ``Trace`` joins (collector traces have no PG row).
"""

from __future__ import annotations

import uuid

from django.db.models import Count, Q

from ee.agenthub.cluster_rca.types import CountBucket
from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.models.observation_span import EvalLogger
from tracer.models.project_version import ProjectVersion
from tracer.models.trace_error_analysis import ErrorClusterTraces, TraceErrorGroup
from tracer.models.trace_scan import TraceScanIssue

# ── TraceErrorGroup (the cluster) ────────────────────────────────────────────


def resolve_cluster_context(cluster_ref: str, project_id: str) -> dict | None:
    """[explicit] Resolve a cluster ref (UUID or CharField label) to
    ``{uuid, label, project_id}`` or ``None``. ``project_id`` is REQUIRED and
    enforced on BOTH branches (by id and by label), so a foreign-tenant cluster
    can't resolve and have the agent adopt that project."""
    ref = str(cluster_ref)
    qs = TraceErrorGroup.objects.filter(
        project_id=project_id, deleted=False
    ).only("id", "cluster_id", "project_id")
    try:
        uuid.UUID(ref)
        row = qs.filter(id=ref).first()
    except (ValueError, AttributeError):
        row = qs.filter(cluster_id=ref).first()
    if row is None:
        return None
    return {
        "uuid": str(row.id),
        "label": row.cluster_id,
        "project_id": str(row.project_id),
    }


def get_cluster_for_read(cluster_uuid: str, project_id: str) -> TraceErrorGroup | None:
    """[explicit] The cluster row for ``read(cluster)``. ``None`` if absent.
    select_related('eval_config') — caller reads ``group.eval_config.name``."""
    return (
        TraceErrorGroup.objects.select_related("eval_config")
        .only(
            "id", "cluster_id", "source", "title", "status",
            "issue_group", "issue_category", "fix_layer", "priority",
            "error_count", "unique_traces", "total_events",
            "first_seen", "last_seen", "combined_impact",
            "combined_description", "trace_impact",
            "external_issue_url", "external_issue_id",
            "eval_config__id", "eval_config__name", "eval_target_type",
            "success_trace_id",
        )
        .filter(id=cluster_uuid, project_id=project_id, deleted=False)
        .first()
    )


# ── ErrorClusterTraces (membership junction — transitive scope) ──────────────


def cluster_member_trace_ids(cluster_uuid: str) -> list[str]:
    """[transitive] Distinct trace_ids of the cluster's TRACE members. Reads the
    trace_id COLUMN (never the PG Trace FK). Session members resolve separately."""
    rows = (
        ErrorClusterTraces.objects.filter(
            cluster_id=cluster_uuid, deleted=False, trace_id__isnull=False
        )
        .values_list("trace_id", flat=True)
        .distinct()
    )
    return [str(t) for t in rows if t is not None]


def cluster_member_session_ids(cluster_uuid: str) -> list[str]:
    """[transitive] Distinct trace_session_ids of the cluster's SESSION members."""
    rows = (
        ErrorClusterTraces.objects.filter(
            cluster_id=cluster_uuid, deleted=False, trace_session__isnull=False
        )
        .values_list("trace_session_id", flat=True)
        .distinct()
    )
    return [str(r) for r in rows if r]


def cluster_memberships(
    cluster_uuid: str, trace_uuids: list[str]
) -> list[ErrorClusterTraces]:
    """[transitive] Junction rows for the given traces, newest-first, for
    provenance. Caller reads trace_id / scan_issue_id / eval_logger_id / created_at."""
    return list(
        ErrorClusterTraces.objects.filter(
            cluster_id=cluster_uuid, trace_id__in=trace_uuids, deleted=False
        )
        .only("trace_id", "scan_issue_id", "eval_logger_id", "created_at")
        .order_by("-created_at")
    )


def count_cluster_eval_members(cluster_uuid: str) -> int:
    """[transitive] Distinct eval-logger members of the cluster (manifest count)."""
    return (
        ErrorClusterTraces.objects.filter(
            cluster_id=cluster_uuid, deleted=False, eval_logger__isnull=False
        )
        .values("eval_logger")
        .distinct()
        .count()
    )


# ── TraceScanIssue (scanner findings — transitive scope) ─────────────────────


def count_scan_issue_traces_by(
    cluster_uuid: str, trace_uuids: list[str], field_name: str
) -> list[CountBucket]:
    """[transitive] DISTINCT-trace counts grouped by ``field_name`` (trace_count
    metric over scan issues)."""
    rows = (
        TraceScanIssue.objects.filter(
            cluster_id=cluster_uuid, deleted=False, scan_result__trace_id__in=trace_uuids
        )
        .values(field_name)
        .annotate(c=Count("scan_result__trace_id", distinct=True))
    )
    return [CountBucket(key=r[field_name], count=r["c"]) for r in rows]


def count_scan_issues_by(
    cluster_uuid: str, trace_uuids: list[str], field_name: str
) -> tuple[list[CountBucket], int]:
    """[transitive] ISSUE counts grouped by ``field_name`` + the total issue count
    (scan_issue_count metric)."""
    qs = TraceScanIssue.objects.filter(
        cluster_id=cluster_uuid, deleted=False, scan_result__trace_id__in=trace_uuids
    )
    total = qs.count()
    rows = qs.values(field_name).annotate(c=Count("id"))
    return [CountBucket(key=r[field_name], count=r["c"]) for r in rows], total


def list_cluster_scan_issues(
    cluster_uuid: str, trace_uuids: list[str], offset: int, limit: int
) -> tuple[list[TraceScanIssue], int]:
    """[transitive] A page of the cluster's scan issues (newest-first) + total.
    select_related('scan_result') — caller reads ``i.scan_result.trace_id``."""
    qs = TraceScanIssue.objects.filter(
        cluster_id=cluster_uuid, scan_result__trace_id__in=trace_uuids, deleted=False
    )
    total = qs.count()
    rows = list(
        qs.select_related("scan_result")
        .only(
            "id", "category", "group", "fix_layer", "confidence", "brief",
            "scan_result__id", "scan_result__trace_id",
        )
        .order_by("-created_at")[offset : offset + limit]
    )
    return rows, total


def search_cluster_scan_issues(
    cluster_uuid: str, trace_uuids: list[str], query: str, limit: int
) -> list[TraceScanIssue]:
    """[transitive] Scan issues matching ``query`` (brief / category / group),
    capped at ``limit + 1`` for has_more. select_related('scan_result')."""
    q = (
        Q(brief__icontains=query)
        | Q(category__icontains=query)
        | Q(group__icontains=query)
    )
    return list(
        TraceScanIssue.objects.filter(
            cluster_id=cluster_uuid,
            scan_result__trace_id__in=trace_uuids,
            deleted=False,
        )
        .filter(q)
        .select_related("scan_result")
        .only(
            "id", "category", "group", "fix_layer", "brief",
            "scan_result__id", "scan_result__trace_id",
        )[: limit + 1]
    )


def count_cluster_scan_issues(cluster_uuid: str, trace_uuids: list[str]) -> int:
    """[transitive] Total scan issues in the cluster scope (manifest count)."""
    return TraceScanIssue.objects.filter(
        cluster_id=cluster_uuid, deleted=False, scan_result__trace_id__in=trace_uuids
    ).count()


def get_scan_issue_for_read(
    issue_uuid: str, project_id: str
) -> TraceScanIssue | None:
    """[explicit] One scan issue for ``read(scan_issue)``, project-scoped via
    scan_result.project (TraceScanIssue has no direct project FK). ``None`` if absent.
    select_related('scan_result','cluster')."""
    return (
        TraceScanIssue.objects.select_related("scan_result", "cluster")
        .only(
            "id", "category", "group", "fix_layer", "confidence", "brief",
            "scan_result__id", "scan_result__trace_id",
            "cluster__id", "cluster__cluster_id",
        )
        .filter(id=issue_uuid, scan_result__project_id=project_id, deleted=False)
        .first()
    )


# ── EvalLogger (eval results — no project FK; transitive via scope_q) ─────────


def count_cluster_eval_metrics(scope_q: Q) -> list[CountBucket]:
    """[transitive] DISTINCT eval-row counts grouped by eval-config name, scoped by
    ``scope_q`` (trace OR session — see the agent's _eval_scope_q)."""
    rows = (
        EvalLogger.objects.filter(scope_q, deleted=False)
        .values("custom_eval_config__name")
        .annotate(c=Count("id", distinct=True))
    )
    return [CountBucket(key=r["custom_eval_config__name"], count=r["c"]) for r in rows]


def list_cluster_eval_results(
    scope_q: Q, offset: int, limit: int
) -> tuple[list[EvalLogger], int]:
    """[transitive] A page of the cluster's eval rows (newest-first) + total.
    select_related('custom_eval_config') — caller reads ``er.custom_eval_config.name``."""
    qs = EvalLogger.objects.filter(scope_q, deleted=False)
    total = qs.count()
    rows = list(
        qs.select_related("custom_eval_config")
        .only(
            "id", "trace_id", "output_str", "output_float", "output_bool",
            "output_str_list", "error", "eval_explanation",
            "custom_eval_config__id", "custom_eval_config__name",
        )
        .order_by("-created_at")[offset : offset + limit]
    )
    return rows, total


def trace_eval_results(trace_uuid: str) -> list[EvalLogger]:
    """[per-trace, UNSCOPED] Eval rows for ONE trace. Filters ``EvalLogger`` by
    ``trace_id`` ALONE — no project/cluster guard of its own. Tenant-safe ONLY
    because its sole caller, ``ClusterAnalysisAgent._read_trace``, gates on a
    project-scoped CH spans read first (``_spans_for_trace`` → NOT_FOUND for a
    trace outside ``self.project_id``), so it never reaches this for a foreign
    trace. select_related('custom_eval_config')."""
    return list(
        EvalLogger.objects.filter(trace_id=trace_uuid, deleted=False)
        .select_related("custom_eval_config")
        .only(
            "id", "output_str", "output_float", "output_bool", "output_str_list",
            "eval_explanation", "error",
            "custom_eval_config__id", "custom_eval_config__name",
        )
    )


def get_eval_result_for_read(scope_q: Q, eval_uuid: str) -> EvalLogger | None:
    """[transitive] One eval row for ``read(eval_result)`` — the ``scope_q`` is the
    project guard (membership in this cluster's evidence). ``None`` if absent.
    select_related('custom_eval_config')."""
    return (
        EvalLogger.objects.filter(scope_q)
        .select_related("custom_eval_config")
        .only(
            "id", "trace_id", "observation_span_id", "target_type",
            "output_str", "output_float", "output_bool", "output_str_list",
            "eval_explanation", "error", "error_message",
            "results_tags", "eval_tags",
            "custom_eval_config__id", "custom_eval_config__name",
        )
        .filter(id=eval_uuid, deleted=False)
        .first()
    )


# ── Single explicit-scope reads ──────────────────────────────────────────────


def get_eval_config_for_read(
    cfg_uuid: str, project_id: str
) -> CustomEvalConfig | None:
    """[explicit] One eval config for ``read(eval_config)``. ``None`` if absent.
    select_related('eval_template')."""
    return (
        CustomEvalConfig.objects.select_related("eval_template")
        .only(
            "id", "name", "model", "config", "mapping", "filters",
            "error_localizer", "eval_template__id", "eval_template__name",
        )
        .filter(id=cfg_uuid, project_id=project_id, deleted=False)
        .first()
    )


def get_version_for_read(ver_uuid: str, project_id: str) -> ProjectVersion | None:
    """[explicit] One project version for ``read(version)``. ``None`` if absent."""
    return (
        ProjectVersion.objects.only("id", "name", "created_at")
        .filter(id=ver_uuid, project_id=project_id, deleted=False)
        .first()
    )
