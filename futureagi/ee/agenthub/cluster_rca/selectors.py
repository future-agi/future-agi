"""Read-layer selectors for the cluster-RCA agent (§03 layering).

The agent holds no inline ORM: every database READ goes through a named selector
here. (Its write tools are in-memory only — append a finding / set the synthesis —
so there is no write service.)

Scope classes:
  [explicit]   — takes ``project_id`` and filters on it.
  [transitive] — scoped via ``cluster_uuid`` / ``trace_uuids`` only. ErrorClusterTraces
                 and EvalLogger have NO direct project FK, so they
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
from dataclasses import dataclass
from types import SimpleNamespace

from django.db.models import Count, F, Q
from django.db.models.functions import Coalesce

from ee.agenthub.cluster_rca.types import CountBucket
from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.models.observation_span import EvalLogger
from tracer.models.project_version import ProjectVersion
from tracer.models.trace_error_analysis import ErrorClusterTraces, TraceErrorGroup
from tracer.models.trace_investigation import TraceInvestigationFinding


@dataclass(frozen=True)
class InvestigationIssue:
    """Expose a v2 finding through the RCA agent's existing issue tool shape."""

    id: uuid.UUID
    category: str | None
    group: str | None
    fix_layer: str | None
    confidence: str | None
    brief: str
    scan_result: SimpleNamespace
    cluster: TraceErrorGroup
    cluster_id: uuid.UUID


def _current_findings(cluster_uuid: str, trace_uuids: list[str] | None = None):
    """Only grouped findings from the active report for this cluster."""
    qs = TraceInvestigationFinding.objects.filter(
        cluster_id=cluster_uuid,
        cluster__deleted=False,
        deleted=False,
        report__deleted=False,
        report__is_current=True,
        report__project_id=F("cluster__project_id"),
    )
    if trace_uuids is not None:
        qs = qs.filter(report__trace_id__in=trace_uuids)
    return qs


def _as_issue(finding: TraceInvestigationFinding) -> InvestigationIssue:
    return InvestigationIssue(
        id=finding.id,
        category=finding.category or finding.kind,
        group=finding.group_label or finding.cluster.issue_group,
        fix_layer=finding.fix_layer or finding.cluster.fix_layer,
        confidence=finding.confidence,
        brief=finding.statement,
        scan_result=SimpleNamespace(trace_id=finding.report.trace_id),
        cluster=finding.cluster,
        cluster_id=finding.cluster_id,
    )


_FINDING_FIELDS = {
    "category": Coalesce("category", "kind"),
    "group": Coalesce("group_label", "cluster__issue_group"),
    "fix_layer": Coalesce("fix_layer", "cluster__fix_layer"),
    "confidence": F("confidence"),
}


def current_finding_ids_by_trace(
    cluster_uuid: str, trace_uuids: list[str]
) -> dict[str, uuid.UUID]:
    """One active v2 finding per member trace, for RCA provenance links."""
    rows = (
        _current_findings(cluster_uuid, trace_uuids)
        .order_by("report__trace_id", "ordinal", "id")
        .values_list("report__trace_id", "id")
    )
    result: dict[str, uuid.UUID] = {}
    for trace_id, finding_id in rows:
        result.setdefault(str(trace_id), finding_id)
    return result


# ── TraceErrorGroup (the cluster) ────────────────────────────────────────────


def resolve_cluster_context(cluster_ref: str, project_id: str) -> dict | None:
    """[explicit] Resolve a cluster ref (UUID or CharField label) to
    ``{uuid, label, project_id}`` or ``None``. ``project_id`` is REQUIRED and
    enforced on BOTH branches (by id and by label), so a foreign-tenant cluster
    can't resolve and have the agent adopt that project."""
    ref = str(cluster_ref)
    qs = TraceErrorGroup.objects.filter(project_id=project_id, deleted=False).only(
        "id", "cluster_id", "project_id"
    )
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
            "id",
            "cluster_id",
            "source",
            "title",
            "status",
            "issue_group",
            "issue_category",
            "fix_layer",
            "priority",
            "error_count",
            "unique_traces",
            "total_events",
            "first_seen",
            "last_seen",
            "combined_impact",
            "combined_description",
            "trace_impact",
            "external_issue_url",
            "external_issue_id",
            "eval_config__id",
            "eval_config__name",
            "eval_target_type",
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
    provenance. Caller reads trace_id / eval_logger_id / created_at."""
    return list(
        ErrorClusterTraces.objects.filter(
            cluster_id=cluster_uuid, trace_id__in=trace_uuids, deleted=False
        )
        .only("trace_id", "eval_logger_id", "created_at")
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


# ── Canonical investigation findings (Omega and backfilled legacy scans) ─────


def count_scan_issue_traces_by(
    cluster_uuid: str, trace_uuids: list[str], field_name: str
) -> list[CountBucket]:
    """[transitive] DISTINCT-trace counts grouped by ``field_name`` (trace_count
    metric over scan issues)."""
    rows = (
        _current_findings(cluster_uuid, trace_uuids)
        .annotate(bucket_key=_FINDING_FIELDS[field_name])
        .values("bucket_key")
        .annotate(c=Count("report__trace_id", distinct=True))
    )
    return [CountBucket(key=r["bucket_key"], count=r["c"]) for r in rows]


def count_scan_issues_by(
    cluster_uuid: str, trace_uuids: list[str], field_name: str
) -> tuple[list[CountBucket], int]:
    """[transitive] ISSUE counts grouped by ``field_name`` + the total issue count
    (scan_issue_count metric)."""
    qs = _current_findings(cluster_uuid, trace_uuids)
    total = qs.count()
    rows = (
        qs.annotate(bucket_key=_FINDING_FIELDS[field_name])
        .values("bucket_key")
        .annotate(c=Count("id"))
    )
    return [CountBucket(key=r["bucket_key"], count=r["c"]) for r in rows], total


def list_cluster_scan_issues(
    cluster_uuid: str, trace_uuids: list[str], offset: int, limit: int
) -> tuple[list[InvestigationIssue], int]:
    """[transitive] A page of scanner findings (newest-first) + total.

    Both Omega and backfilled legacy scans read the active normalized finding.
    """
    qs = _current_findings(cluster_uuid, trace_uuids)
    total = qs.count()
    rows = qs.select_related("cluster", "report").order_by("-created_at", "-id")[
        offset : offset + limit
    ]
    return [_as_issue(f) for f in rows], total


def search_cluster_scan_issues(
    cluster_uuid: str, trace_uuids: list[str], query: str, limit: int
) -> list[InvestigationIssue]:
    """[transitive] Scan issues matching ``query`` (brief / category / group),
    capped at ``limit + 1`` for has_more."""
    rows = (
        _current_findings(cluster_uuid, trace_uuids)
        .filter(
            Q(statement__icontains=query)
            | Q(kind__icontains=query)
            | Q(category__icontains=query)
            | Q(group_label__icontains=query)
            | Q(cluster__issue_group__icontains=query)
        )
        .select_related("cluster", "report")
        .order_by("-created_at", "-id")[: limit + 1]
    )
    return [_as_issue(f) for f in rows]


def count_cluster_scan_issues(cluster_uuid: str, trace_uuids: list[str]) -> int:
    """[transitive] Total scan issues in the cluster scope (manifest count)."""
    return _current_findings(cluster_uuid, trace_uuids).count()


def get_scan_issue_for_read(
    issue_uuid: str, project_id: str
) -> InvestigationIssue | None:
    """[explicit] Read one finding by ID, with an explicit project guard."""
    finding = (
        TraceInvestigationFinding.objects.filter(
            id=issue_uuid,
            deleted=False,
            cluster__deleted=False,
            cluster__project_id=project_id,
            report__project_id=project_id,
            report__deleted=False,
            report__is_current=True,
        )
        .select_related("cluster", "report")
        .first()
    )
    return _as_issue(finding) if finding is not None else None


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
            "id",
            "trace_id",
            "output_str",
            "output_float",
            "output_bool",
            "output_str_list",
            "error",
            "eval_explanation",
            "custom_eval_config__id",
            "custom_eval_config__name",
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
            "id",
            "output_str",
            "output_float",
            "output_bool",
            "output_str_list",
            "eval_explanation",
            "error",
            "custom_eval_config__id",
            "custom_eval_config__name",
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
            "id",
            "trace_id",
            "observation_span_id",
            "target_type",
            "output_str",
            "output_float",
            "output_bool",
            "output_str_list",
            "eval_explanation",
            "error",
            "error_message",
            "results_tags",
            "eval_tags",
            "custom_eval_config__id",
            "custom_eval_config__name",
        )
        .filter(id=eval_uuid, deleted=False)
        .first()
    )


# ── Single explicit-scope reads ──────────────────────────────────────────────


def get_eval_config_for_read(cfg_uuid: str, project_id: str) -> CustomEvalConfig | None:
    """[explicit] One eval config for ``read(eval_config)``. ``None`` if absent.
    select_related('eval_template')."""
    return (
        CustomEvalConfig.objects.select_related("eval_template")
        .only(
            "id",
            "name",
            "model",
            "config",
            "mapping",
            "filters",
            "error_localizer",
            "eval_template__id",
            "eval_template__name",
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
