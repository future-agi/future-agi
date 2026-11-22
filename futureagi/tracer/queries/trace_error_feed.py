"""Read-only Error Feed lookups for Tracing list rows and trace detail drawers."""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from typing import Any

from django.db.models import Q

from tracer.models.trace_error_analysis import ErrorClusterTraces
from tracer.models.trace_investigation import (
    TraceInvestigationAttribution,
    TraceInvestigationFinding,
    TraceInvestigationFindingEvidence,
)
from tracer.queries.feed import priority_to_severity

_SEVERITY_ORDER = {"critical": 4, "high": 3, "medium": 2, "low": 1}


def _parse_uuids(values: Iterable[str | uuid.UUID]) -> list[uuid.UUID]:
    parsed: list[uuid.UUID] = []
    seen: set[uuid.UUID] = set()
    for raw in values:
        if not raw:
            continue
        try:
            val = raw if isinstance(raw, uuid.UUID) else uuid.UUID(str(raw))
        except (TypeError, ValueError):
            continue
        if val not in seen:
            seen.add(val)
            parsed.append(val)
    return parsed


def _visible_cluster_filter() -> Q:
    return Q(cluster__issue_state__isnull=True) | Q(
        cluster__issue_state__dirty=False,
        cluster__issue_state__retired=False,
    )


def list_issues_for_traces(
    *,
    project_ids: list[str] | set[str],
    trace_ids: list[str],
) -> dict[str, list[dict[str, Any]]]:
    """Return visible Error Feed issues linked to the given traces.

    Uses two bounded queries (membership rows + finding attributions) so trace
    listing and detail endpoints stay free of N+1 queries.
    """
    valid_projects = _parse_uuids(project_ids)
    valid_traces = _parse_uuids(trace_ids)
    if not valid_projects or not valid_traces:
        return {}

    rows = list(
        ErrorClusterTraces.no_workspace_objects.filter(
            _visible_cluster_filter(),
            trace_id__in=valid_traces,
            cluster__project_id__in=valid_projects,
            deleted=False,
            cluster__deleted=False,
        )
        .values(
            "trace_id",
            "span_id",
            "finding_id",
            "cluster__cluster_id",
            "cluster__title",
            "cluster__error_type",
            "cluster__priority",
            "cluster__status",
        )
        .order_by("-created_at")[:500]
    )
    if not rows:
        return {}

    finding_ids = [r["finding_id"] for r in rows if r["finding_id"]]
    attributions_by_finding: dict[uuid.UUID, dict[str, str]] = {}
    if finding_ids:
        for attr in TraceInvestigationAttribution.no_workspace_objects.filter(
            finding_id__in=finding_ids,
            role__in=("origin", "symptom"),
            status="supported",
            span_id__isnull=False,
            deleted=False,
        ).values("finding_id", "role", "span_id"):
            attributions_by_finding.setdefault(attr["finding_id"], {})[attr["role"]] = (
                str(attr["span_id"])
            )

    grouped: dict[str, dict[str, dict[str, Any]]] = {}
    for row in rows:
        tid = str(row["trace_id"])
        cid = str(row["cluster__cluster_id"] or "")
        if not cid:
            continue
        trace_bucket = grouped.setdefault(tid, {})
        finding_attrs = (
            attributions_by_finding.get(row["finding_id"], {})
            if row["finding_id"]
            else {}
        )
        origin_span_id = finding_attrs.get("origin") or (
            str(row["span_id"]) if row["span_id"] else None
        )
        symptom_span_id = finding_attrs.get("symptom")
        if cid in trace_bucket:
            existing = trace_bucket[cid]
            if not existing["origin_span_id"] and origin_span_id:
                existing["origin_span_id"] = origin_span_id
            if not existing["symptom_span_id"] and symptom_span_id:
                existing["symptom_span_id"] = symptom_span_id
            continue
        trace_bucket[cid] = {
            "cluster_id": cid,
            "title": (
                row["cluster__title"]
                or row["cluster__error_type"]
                or "Error Feed issue"
            )[:240],
            "severity": priority_to_severity(row["cluster__priority"]),
            "status": row["cluster__status"] or "for_review",
            "origin_span_id": origin_span_id,
            "symptom_span_id": symptom_span_id,
        }

    out: dict[str, list[dict[str, Any]]] = {}
    for tid, by_cluster in grouped.items():
        items = list(by_cluster.values())
        items.sort(
            key=lambda item: (
                -_SEVERITY_ORDER.get(item["severity"], 0),
                item["cluster_id"],
            )
        )
        out[tid] = items
    return out


def _index_span_tree(
    observation_spans: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    by_id: dict[str, dict[str, Any]] = {}
    order: list[str] = []

    def _walk(entries: list[dict[str, Any]]) -> None:
        for entry in entries or []:
            obs = entry.get("observation_span")
            if isinstance(obs, dict):
                sid = str(obs.get("id") or "")
                if sid and sid not in by_id:
                    by_id[sid] = obs
                    order.append(sid)
            children = entry.get("children")
            if children:
                _walk(children)

    _walk(observation_spans)
    return by_id, order


def annotate_trace_detail_with_error_feed(
    *,
    project_id: str,
    trace_id: str,
    observation_spans: list[dict[str, Any]],
    cluster_id: str | None = None,
) -> tuple[list[dict[str, Any]], str | None]:
    """Attach Error Feed issue metadata and causal step annotations onto spans.

    Mutates ``span_attributes`` in-place on matching spans inside
    ``observation_spans`` so the trace drawer's Key Path view highlights the
    selected issue's grounded origin -> symptom chain.
    """
    issues = list_issues_for_traces(
        project_ids=[project_id],
        trace_ids=[trace_id],
    ).get(str(trace_id), [])
    if not issues:
        return [], None

    active_issue = next(
        (item for item in issues if cluster_id and item["cluster_id"] == cluster_id),
        issues[0],
    )
    active_cluster_id = active_issue["cluster_id"]

    span_by_id, span_order = _index_span_tree(observation_spans)
    if not span_by_id:
        return issues, active_cluster_id

    try:
        trace_uuid = uuid.UUID(str(trace_id))
        project_uuid = uuid.UUID(str(project_id))
    except (TypeError, ValueError):
        return issues, active_cluster_id

    finding = (
        TraceInvestigationFinding.no_workspace_objects.filter(
            report__project_id=project_uuid,
            report__trace_id=trace_uuid,
            report__is_current=True,
            report__deleted=False,
            cluster__cluster_id=active_cluster_id,
            deleted=False,
        )
        .select_related("cluster")
        .prefetch_related("attributions")
        .order_by("ordinal", "id")
        .first()
    )

    step_specs: dict[str, dict[str, str]] = {}
    if finding is not None:
        role_map = {
            attr.role: attr
            for attr in finding.attributions.all()
            if attr.status == "supported" and attr.span_id
        }
        origin_attr = role_map.get("origin")
        symptom_attr = role_map.get("symptom")
        if origin_attr and str(origin_attr.span_id) in span_by_id:
            sid = str(origin_attr.span_id)
            step_specs[sid] = {
                "role": "origin_span",
                "headline": (
                    origin_attr.explanation
                    or finding.statement
                    or span_by_id[sid].get("name")
                    or "Origin span"
                )[:240],
                "error_title": active_issue["title"],
                "error_message": (origin_attr.explanation or finding.statement or "")[
                    :400
                ],
            }
        if symptom_attr and str(symptom_attr.span_id) in span_by_id:
            sid = str(symptom_attr.span_id)
            if sid not in step_specs:
                step_specs[sid] = {
                    "role": "symptom_span",
                    "headline": (
                        symptom_attr.explanation
                        or finding.statement
                        or span_by_id[sid].get("name")
                        or "Symptom span"
                    )[:240],
                    "error_title": active_issue["title"],
                    "error_message": (
                        symptom_attr.explanation or finding.statement or ""
                    )[:400],
                }
        cited_span_ids = (
            TraceInvestigationFindingEvidence.no_workspace_objects.filter(
                finding=finding,
                evidence__span_id__isnull=False,
            )
            .order_by("evidence__ordinal")
            .values_list("evidence__span_id", flat=True)[:6]
        )
        for raw_sid in cited_span_ids:
            sid = str(raw_sid)
            if sid in span_by_id and sid not in step_specs:
                step_specs[sid] = {
                    "role": "cited_span",
                    "headline": (span_by_id[sid].get("name") or "Cited span")[:240],
                }
    else:
        origin_sid = active_issue.get("origin_span_id")
        symptom_sid = active_issue.get("symptom_span_id")
        if origin_sid and origin_sid in span_by_id:
            step_specs[origin_sid] = {
                "role": "origin_span",
                "headline": active_issue["title"],
                "error_title": active_issue["title"],
                "error_message": active_issue["title"],
            }
        if symptom_sid and symptom_sid in span_by_id and symptom_sid not in step_specs:
            step_specs[symptom_sid] = {
                "role": "symptom_span",
                "headline": active_issue["title"],
            }

    if len(step_specs) == 1 and span_order:
        # Ensure Key Path always shows the trace entry context alongside a single cited span.
        root_sid = span_order[0]
        if root_sid not in step_specs:
            step_specs[root_sid] = {
                "role": "cited_span",
                "headline": (span_by_id[root_sid].get("name") or "Trace root")[:240],
            }

    order_index = {sid: idx for idx, sid in enumerate(span_order)}
    ordered_sids = sorted(step_specs, key=lambda sid: order_index.get(sid, 0))
    for step_num, sid in enumerate(ordered_sids, start=1):
        obs = span_by_id[sid]
        attrs = obs.get("span_attributes")
        if not isinstance(attrs, dict):
            attrs = {}
            obs["span_attributes"] = attrs
        spec = step_specs[sid]
        attrs["error_feed.step"] = step_num
        attrs["error_feed.role"] = spec["role"]
        attrs["error_feed.alias"] = f"S{order_index.get(sid, step_num - 1)}"
        attrs["error_feed.headline"] = spec["headline"]
        if spec.get("error_title"):
            attrs["error_feed.clean_error_title"] = spec["error_title"]
        if spec.get("error_message"):
            attrs["error_feed.clean_error_message"] = spec["error_message"]

    return issues, active_cluster_id
