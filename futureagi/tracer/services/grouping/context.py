"""Bounded, version-bound F6 candidate window for one claimed cohort."""

import json
import math
from collections import defaultdict

from django.db import transaction
from django.db.models import F, Q

from tracer.models.trace_grouping import (
    TraceGroupingAttempt,
    TraceGroupingConstraint,
    TraceGroupingFeature,
    TraceGroupingIssueState,
)
from tracer.models.trace_investigation import TraceInvestigationFinding
from tracer.queries.grouping import (
    canonical_grouping_source_digest,
    canonical_snapshot_digest,
    export_grouping_snapshot,
)
from tracer.services.grouping.control import GroupingConflict
from tracer.services.grouping.feature_store import (
    GroupingFeatureStore,
    probe_bucket_keys,
)

MAX_CANDIDATE_ISSUES = 20
MAX_MEMBERS_PER_ISSUE = 16
MAX_CANDIDATE_MEMBERS = 64
MAX_CONTEXT_BYTES = 8 * 1024 * 1024
MAX_INDEX_HITS = 512
MAX_CONSTRAINTS = 1000
NEIGHBOURS_PER_VIEW = 20
RRF_K = 60
VIEWS = ("semantics", "task")


def _live_findings(query):
    return query.filter(
        report__deleted=False,
        report__is_current=True,
        report__source="omega",
        report__execution_status="completed",
        report__job__current_report_id=F("report_id"),
    )


def _receipt(row: TraceGroupingFeature) -> dict:
    return {
        "occurrence_id": str(row.finding_id),
        "view": row.view,
        "source_digest": row.source_digest,
        "evidence_revision": row.evidence_revision,
        "text_digest": row.text_digest,
        "feature_digest": row.feature_digest,
        "bucket_keys": row.bucket_keys,
    }


def _checked_vector(row: dict) -> tuple[float, ...]:
    vector = row["vector"]
    if (
        row["dimension"] != 384
        or not isinstance(vector, (list, tuple))
        or len(vector) != 384
        or any(
            type(value) not in (int, float) or not math.isfinite(value)
            for value in vector
        )
        or math.hypot(*vector) <= 0
    ):
        raise GroupingConflict("candidate vector is invalid")
    return tuple(vector)


def rank_f6_occurrences(
    *,
    pending_features: list[dict],
    candidate_features: list[dict],
    blocked_pairs: set[tuple[str, str]],
) -> list[tuple[str, float]]:
    """F6 cosine top-20/view and RRF(60), before the issue window cap.

    The caller supplies only live, same-scope, source-bound ready features. A
    pair block is directional here because an issue exclusion may apply to one
    pending occurrence but not another; cannot-links are expanded both ways.
    """
    candidate_views = defaultdict(dict)
    for row in candidate_features:
        if row["view"] in VIEWS:
            candidate_views[row["view"]][row["occurrence_id"]] = _checked_vector(row)
    scores = defaultdict(float)
    for pending in sorted(
        pending_features, key=lambda row: (row["occurrence_id"], row["view"])
    ):
        view = pending["view"]
        if view not in VIEWS:
            continue
        source_id = pending["occurrence_id"]
        vector = _checked_vector(pending)
        norm = math.hypot(*vector)
        ranked = []
        for candidate_id, other in candidate_views[view].items():
            if candidate_id == source_id or (source_id, candidate_id) in blocked_pairs:
                continue
            similarity = math.fsum(
                a * b for a, b in zip(vector, other, strict=True)
            ) / (norm * math.hypot(*other))
            ranked.append((candidate_id, max(-1.0, min(1.0, similarity))))
        ranked.sort(key=lambda item: (-item[1], item[0]))
        for rank, (candidate_id, _) in enumerate(ranked[:NEIGHBOURS_PER_VIEW], start=1):
            scores[candidate_id] += 1 / (RRF_K + rank)
    return sorted(scores.items(), key=lambda item: (-item[1], item[0]))


def _read_vectors_bounded(store, *, scope, receipts: list[dict]) -> list[dict]:
    # The store bounds each exact ClickHouse read to 800 receipts. Rank input
    # may contain 512 index hits with two views, so preserve that bound in
    # chunks rather than truncate candidates before cosine ranking.
    output = []
    for offset in range(0, len(receipts), 800):
        output.extend(
            store.read_vectors(
                organization_id=scope.organization_id,
                project_id=scope.project_id,
                receipts=receipts[offset : offset + 800],
            )
        )
    return output


def build_claim_context(
    *, attempt: TraceGroupingAttempt, pending_snapshots: list[dict]
) -> dict:
    scope = attempt.work.scope
    pending_ids = {
        item["occurrence_id"]
        for snap in pending_snapshots
        for item in snap["occurrences"]
    }
    pending_reports = {snap["report"]["id"]: snap for snap in pending_snapshots}
    receipts = list(
        TraceGroupingFeature.no_workspace_objects.filter(
            finding_id__in=pending_ids,
            job__state="ready",
            finding__report_id__in=pending_reports,
        ).order_by("finding_id", "view", "id")
    )
    by_occurrence = defaultdict(dict)
    for row in receipts:
        if row.view in by_occurrence[str(row.finding_id)]:
            raise GroupingConflict("claimed feature identity is ambiguous")
        by_occurrence[str(row.finding_id)][row.view] = row
        report = pending_reports[str(row.finding.report_id)]
        if (
            row.source_digest != canonical_grouping_source_digest(report)
            or row.evidence_revision != report["report"]["evidence_digest"]
        ):
            raise GroupingConflict("claimed feature source is stale")
    if any("semantics" not in by_occurrence[item] for item in pending_ids):
        raise GroupingConflict("claimed cohort lacks semantic F6 views")
    store = GroupingFeatureStore()
    neighbor_ids = set()
    for finding_id in sorted(pending_ids):
        probes = probe_bucket_keys(
            [_receipt(item) for item in by_occurrence[finding_id].values()]
        )
        neighbor_ids.update(
            store.candidate_occurrences(
                organization_id=scope.organization_id,
                project_id=scope.project_id,
                bucket_keys=probes,
            )
        )
        if len(neighbor_ids) > MAX_INDEX_HITS:
            raise GroupingConflict("candidate index exceeds bounded window")
    hit_findings = list(
        _live_findings(
            TraceInvestigationFinding.no_workspace_objects.filter(
                id__in=neighbor_ids,
                report__project_id=scope.project_id,
                cluster_id__isnull=False,
            )
        ).select_related("report")
    )
    hit_by_id = {str(item.id): item for item in hit_findings}
    candidate_states = list(
        TraceGroupingIssueState.no_workspace_objects.select_related("cluster")
        .filter(
            scope=scope,
            retired=False,
            dirty=False,
            cluster__deleted=False,
            cluster_id__in={item.cluster_id for item in hit_findings},
        )
        .order_by("cluster_id")
    )
    state_by_issue = {str(item.cluster_id): item for item in candidate_states}
    hit_by_issue = defaultdict(list)
    for finding in hit_findings:
        issue_id = str(finding.cluster_id)
        if issue_id in state_by_issue:
            hit_by_issue[issue_id].append(finding)
    hit_ids = {str(item.id) for members in hit_by_issue.values() for item in members}
    hard_constraints = list(
        TraceGroupingConstraint.no_workspace_objects.filter(scope=scope)
        .filter(
            Q(
                kind="exclude_issue",
                first_finding_id__in=pending_ids,
                issue_id__in=state_by_issue,
            )
            | Q(
                kind="cannot_link",
                first_finding_id__in=pending_ids,
                second_finding__cluster_id__in=state_by_issue,
            )
            | Q(
                kind="cannot_link",
                second_finding_id__in=pending_ids,
                first_finding__cluster_id__in=state_by_issue,
            )
        )
        .select_related("first_finding", "second_finding")
        .order_by("id")[: MAX_CONSTRAINTS + 1]
    )
    if len(hard_constraints) > MAX_CONSTRAINTS:
        raise GroupingConflict("candidate hard-constraint window exceeds bound")
    blocked_issue_pairs = set()
    for rule in hard_constraints:
        if rule.kind == "exclude_issue":
            blocked_issue_pairs.add((str(rule.first_finding_id), str(rule.issue_id)))
        elif str(rule.first_finding_id) in pending_ids and rule.second_finding_id:
            blocked_issue_pairs.add(
                (str(rule.first_finding_id), str(rule.second_finding.cluster_id))
            )
        elif rule.second_finding_id and str(rule.second_finding_id) in pending_ids:
            blocked_issue_pairs.add(
                (str(rule.second_finding_id), str(rule.first_finding.cluster_id))
            )
    blocked_pairs = {
        (pending_id, str(finding.id))
        for issue_id, members in hit_by_issue.items()
        for finding in members
        for pending_id in pending_ids
        if (pending_id, issue_id) in blocked_issue_pairs
    }
    hit_reports = {}
    for members in hit_by_issue.values():
        for finding in members:
            report_id = str(finding.report_id)
            if report_id not in hit_reports:
                hit_reports[report_id] = pending_reports.get(
                    report_id
                ) or export_grouping_snapshot(report=finding.report)
    hit_receipts = list(
        TraceGroupingFeature.no_workspace_objects.filter(
            finding_id__in=hit_ids, job__state="ready"
        )
        .select_related("finding")
        .order_by("finding_id", "view", "id")
    )
    pending_identity = {
        (row.model, row.model_revision, row.serving_release, row.dimension)
        for row in receipts
    }
    if len(pending_identity) != 1:
        raise GroupingConflict("claimed feature model identity is ambiguous")
    identity = next(iter(pending_identity))
    hit_feature_map = defaultdict(dict)
    ineligible_issues = set()
    for row in hit_receipts:
        issue_id = str(hit_by_id[str(row.finding_id)].cluster_id)
        report = hit_reports[str(row.finding.report_id)]
        if (
            row.view not in VIEWS
            or row.view in hit_feature_map[str(row.finding_id)]
            or row.source_digest != canonical_grouping_source_digest(report)
            or row.evidence_revision != report["report"]["evidence_digest"]
            or (row.model, row.model_revision, row.serving_release, row.dimension)
            != identity
        ):
            ineligible_issues.add(issue_id)
        else:
            hit_feature_map[str(row.finding_id)][row.view] = row
    for issue_id, members in hit_by_issue.items():
        if any("semantics" not in hit_feature_map[str(item.id)] for item in members):
            ineligible_issues.add(issue_id)
    eligible_hit_receipts = [
        _receipt(row)
        for row in hit_receipts
        if str(hit_by_id[str(row.finding_id)].cluster_id) not in ineligible_issues
    ]
    pending_vectors = _read_vectors_bounded(
        store, scope=scope, receipts=[_receipt(row) for row in receipts]
    )
    hit_vectors = _read_vectors_bounded(
        store, scope=scope, receipts=eligible_hit_receipts
    )
    if any(
        (row["model"], row["model_revision"], row["serving_release"], row["dimension"])
        != identity
        for row in [*pending_vectors, *hit_vectors]
    ):
        raise GroupingConflict(
            "ClickHouse vector model identity differs from ready receipt"
        )
    ranked_hits = rank_f6_occurrences(
        pending_features=pending_vectors,
        candidate_features=hit_vectors,
        blocked_pairs=blocked_pairs,
    )
    issue_scores = {}
    for finding_id, score in ranked_hits:
        issue_id = str(hit_by_id[finding_id].cluster_id)
        issue_scores[issue_id] = max(issue_scores.get(issue_id, 0), score)
    ranked_states = sorted(
        (state for state in candidate_states if str(state.cluster_id) in issue_scores),
        key=lambda state: (-issue_scores[str(state.cluster_id)], str(state.cluster_id)),
    )
    issues = []
    omitted = [
        {"issue_id": issue_id, "reason": "candidate_feature_not_current"}
        for issue_id in sorted(ineligible_issues)
    ]
    omitted.extend(
        {"issue_id": issue_id, "reason": "hard_constraint_or_no_top20_view_hit"}
        for issue_id in sorted(state_by_issue)
        if issue_id not in ineligible_issues and issue_id not in issue_scores
    )
    candidate_member_ids = set()
    for state in ranked_states:
        if len(issues) >= MAX_CANDIDATE_ISSUES:
            omitted.append(
                {
                    "issue_id": str(state.cluster_id),
                    "reason": "ranked_issue_window_bound",
                }
            )
            continue
        from tracer.services.grouping.publish import _protected

        members = list(
            _live_findings(
                TraceInvestigationFinding.no_workspace_objects.filter(
                    cluster_id=state.cluster_id
                )
            )
            .select_related("report")
            .order_by("id")[: MAX_MEMBERS_PER_ISSUE + 1]
        )
        if (
            len(members) > MAX_MEMBERS_PER_ISSUE
            or len(candidate_member_ids) + len(members) > MAX_CANDIDATE_MEMBERS
        ):
            omitted.append(
                {"issue_id": str(state.cluster_id), "reason": "full_membership_bound"}
            )
            continue
        if not members:
            raise GroupingConflict("candidate issue has no current members")
        from tracer.services.grouping.publish import _issue_members

        if _issue_members(state) != [str(item.id) for item in members]:
            raise GroupingConflict("candidate issue full membership changed")
        issue_members = []
        for member in members:
            candidate_member_ids.add(str(member.id))
            issue_members.append(
                {
                    "occurrence_id": str(member.id),
                    "report_id": str(member.report_id),
                    "trace_id": str(member.report.trace_id),
                }
            )
        issues.append(
            {
                "issue_id": str(state.cluster_id),
                "revision": state.revision,
                "protected": _protected(state),
                "mechanism": state.mechanism,
                "prototype_occurrence_ids": state.prototype_occurrence_ids,
                "members": issue_members,
                "membership_complete": True,
            }
        )
    candidate_reports = {}
    if candidate_member_ids:
        report_ids = set(
            _live_findings(
                TraceInvestigationFinding.no_workspace_objects.filter(
                    id__in=candidate_member_ids
                )
            ).values_list("report_id", flat=True)
        )
        for report_id in sorted(report_ids):
            if str(report_id) in pending_reports:
                candidate_reports[str(report_id)] = pending_reports[str(report_id)]
            else:
                from tracer.models.trace_investigation import TraceInvestigationReport

                report = TraceInvestigationReport.no_workspace_objects.get(pk=report_id)
                candidate_reports[str(report_id)] = export_grouping_snapshot(
                    report=report
                )
    else:
        candidate_reports = {}
    report_digests = {
        key: canonical_grouping_source_digest(snap)
        for key, snap in {**pending_reports, **candidate_reports}.items()
    }
    receipt_rows = list(receipts)
    if candidate_member_ids:
        candidate_receipts = list(
            TraceGroupingFeature.no_workspace_objects.filter(
                finding_id__in=candidate_member_ids, job__state="ready"
            ).order_by("finding_id", "view", "id")
        )
        if {
            str(item.finding_id)
            for item in candidate_receipts
            if item.view == "semantics"
        } != candidate_member_ids:
            raise GroupingConflict("candidate full members lack semantic features")
        for row in candidate_receipts:
            report_id = str(row.finding.report_id)
            if (
                row.source_digest != report_digests[report_id]
                or row.evidence_revision
                != candidate_reports[report_id]["report"]["evidence_digest"]
            ):
                raise GroupingConflict("candidate full member feature is stale")
        receipt_rows.extend(candidate_receipts)
    receipt_map = {
        (str(row.finding_id), row.view): _receipt(row) for row in receipt_rows
    }
    if len(receipt_map) != len(receipt_rows):
        raise GroupingConflict("candidate feature identity is ambiguous")
    features = store.read_vectors(
        organization_id=scope.organization_id,
        project_id=scope.project_id,
        receipts=list(receipt_map.values()),
    )
    feature_by_id = {row["occurrence_id"]: row for row in features}
    for issue in issues:
        for member in issue["members"]:
            feature = feature_by_id[member["occurrence_id"]]
            member["source_digest"] = feature["source_digest"]
            member["evidence_revision"] = feature["evidence_revision"]
    relevant_ids = pending_ids | candidate_member_ids
    constraints = list(
        TraceGroupingConstraint.no_workspace_objects.filter(
            scope=scope, kind="cannot_link"
        )
        .filter(
            Q(first_finding_id__in=relevant_ids) | Q(second_finding_id__in=relevant_ids)
        )
        .order_by("id")[: MAX_CONSTRAINTS + 1]
    )
    if len(constraints) > MAX_CONSTRAINTS:
        raise GroupingConflict("candidate constraint window exceeds bound")
    constraint_rows = [
        sorted([str(item.first_finding_id), str(item.second_finding_id)])
        for item in constraints
        if item.kind == "cannot_link"
        and item.second_finding_id
        and str(item.first_finding_id) in relevant_ids
        and str(item.second_finding_id) in relevant_ids
    ]
    window = {
        "registry_revision": attempt.registry_revision,
        "issues": issues,
        "omitted_candidates": omitted,
    }
    candidate_digest = canonical_snapshot_digest(
        {"candidate_window": window, "report_digests": report_digests}
    )
    public_features = [
        {
            **{
                key: row[key]
                for key in (
                    "occurrence_id",
                    "view",
                    "source_digest",
                    "evidence_revision",
                    "text_digest",
                    "model",
                    "model_revision",
                    "serving_release",
                    "dimension",
                    "vector",
                    "feature_digest",
                )
            },
            "index_buckets": row["bucket_keys"],
        }
        for row in features
    ]
    response = {
        "candidate_snapshots": list(candidate_reports.values()),
        "features": public_features,
        "candidate_window": window,
        "constraints": constraint_rows,
    }
    if (
        len(json.dumps(response, default=str, ensure_ascii=False).encode("utf-8"))
        > MAX_CONTEXT_BYTES
    ):
        raise GroupingConflict("candidate context exceeds response bound")
    with transaction.atomic():
        from tracer.models.trace_grouping import TraceGroupingScope

        TraceGroupingScope.no_workspace_objects.select_for_update().get(pk=scope.pk)
        current = (
            TraceGroupingAttempt.no_workspace_objects.select_for_update(of=("self",))
            .select_related("work__scope")
            .get(pk=attempt.pk)
        )
        if (
            current.registry_revision != current.work.scope.registry_revision
            or current.state != "claimed"
        ):
            raise GroupingConflict("candidate registry changed while building claim")
        current.offered_issue_ids = [issue["issue_id"] for issue in issues]
        current.omitted_candidate_ids = [issue["issue_id"] for issue in omitted]
        current.omitted_candidates = omitted
        current.candidate_digest = candidate_digest
        previous = current.work.attempts.filter(
            attempt_number=current.attempt_number - 1
        ).first()
        if previous and previous.candidate_digest != candidate_digest:
            current.checkpoint = {}
            current.checkpoint_revision = 0
        current.save(
            update_fields=[
                "offered_issue_ids",
                "omitted_candidate_ids",
                "omitted_candidates",
                "candidate_digest",
                "checkpoint",
                "checkpoint_revision",
                "updated_at",
            ]
        )
    return response
