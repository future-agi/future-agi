"""Authoritative F6 proposal validation and atomic Feed projection."""

import hashlib
import json
import uuid

from django.db import transaction
from django.db.models import Count, F, Max, Min, Q
from django.utils import timezone

from tracer.models.trace_error_analysis import ErrorClusterTraces, TraceErrorGroup
from tracer.models.trace_grouping import (
    GroupingAttemptState,
    GroupingWorkState,
    TraceGroupingCall,
    TraceGroupingConstraint,
    TraceGroupingDecision,
    TraceGroupingFindingState,
    TraceGroupingIssueState,
    TraceGroupingOutbox,
    TraceGroupingScope,
    TraceGroupingWork,
)
from tracer.models.trace_investigation import (
    InvestigationWorkload,
    TraceInvestigationFinding,
    TraceInvestigationGroupingStatus,
    TraceInvestigationReport,
)
from tracer.queries.grouping import (
    canonical_grouping_source_digest,
    canonical_snapshot_digest,
    export_grouping_snapshot,
)
from tracer.services.grouping.control import (
    GroupingConflict,
    GroupingControlError,
    lock_attempt_scope,
    require_attempt,
)

MAX_COMMANDS = 150
MAX_PROPOSAL_BYTES = 2 * 1024 * 1024
MAX_RECONCILIATION_MEMBERS = 16
MECHANISM_KEYS = {"mechanism", "fix_hypothesis", "falsifier"}
TITLE_KEYS = MECHANISM_KEYS | {"title"}
COMMAND_FIELDS = {
    "create": {
        "type",
        "temporary_id",
        "occurrence_ids",
        "mechanism",
        "prototype_occurrence_ids",
        "citations",
        "admission",
    },
    "attach": {
        "type",
        "issue_id",
        "expected_issue_revision",
        "occurrence_ids",
        "citations",
        "admission",
    },
    "defer": {"type", "occurrence_ids", "reason"},
    "refresh": {
        "type",
        "issue_id",
        "expected_issue_revision",
        "prototype_occurrence_ids",
        "mechanism",
    },
    "merge": {
        "type",
        "source_issue_ids",
        "expected_revisions",
        "temporary_id",
        "mechanism",
        "prototype_occurrence_ids",
        "citations",
        "admission",
    },
    "split": {
        "type",
        "issue_id",
        "expected_issue_revision",
        "parts",
        "citations",
        "admissions",
    },
    "remove": {
        "type",
        "issue_id",
        "expected_issue_revision",
        "occurrence_ids",
        "hard_constraints",
        "reason",
        "admission",
    },
}


def _digest(value: object) -> str:
    return (
        "sha256:"
        + hashlib.sha256(
            json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        ).hexdigest()
    )


def _text_digest(value: str) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


def _ids(value: object, *, limit: int, label: str) -> list[str]:
    if not isinstance(value, list) or not 1 <= len(value) <= limit:
        raise GroupingControlError(f"{label} must be a bounded nonempty list")
    if any(not isinstance(item, str) or not item for item in value) or len(
        set(value)
    ) != len(value):
        raise GroupingControlError(f"{label} contains duplicate or invalid IDs")
    return value


def _mechanism(value: object) -> dict:
    if not isinstance(value, dict) or set(value) not in (MECHANISM_KEYS, TITLE_KEYS):
        raise GroupingControlError("mechanism must have exact F6 fields")
    if any(
        not isinstance(item, str) or not item.strip() or len(item) > 3000
        for item in value.values()
    ):
        raise GroupingControlError("mechanism fields must be bounded nonempty text")
    if "title" in value and (
        len(value["title"]) > 120 or len(value["title"].split()) > 12
    ):
        raise GroupingControlError("issue title must be a concise headline")
    return value


def _command_shape(command: object) -> dict:
    if not isinstance(command, dict) or command.get("type") not in COMMAND_FIELDS:
        raise GroupingControlError("unsupported grouping command")
    if set(command) != COMMAND_FIELDS[command["type"]]:
        raise GroupingControlError("grouping command has unknown or missing fields")
    return command


def _uuid(value: str, label: str) -> uuid.UUID:
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(value)
    except (ValueError, TypeError, AttributeError) as exc:
        raise GroupingControlError(f"invalid {label}") from exc


def _report_snapshots(reports: list[TraceInvestigationReport]) -> dict[str, dict]:
    return {str(item.id): export_grouping_snapshot(report=item) for item in reports}


def _finding_evidence(snapshot: dict, occurrence_id: str) -> dict[str, tuple[str, str]]:
    occurrence = next(
        (
            item
            for item in snapshot["occurrences"]
            if item["occurrence_id"] == occurrence_id
        ),
        None,
    )
    if occurrence is None:
        raise GroupingConflict("citation occurrence absent from current report")
    finding = next(
        item
        for item in snapshot["report"]["findings"]
        if item["finding_id"] == occurrence["finding_id"]
    )
    report = snapshot["report"]
    evidence = {"report": (finding["statement"], _text_digest(finding["statement"]))}
    receipts = {item["evidence_id"]: item for item in report["evidence_receipts"]}
    requirement = next(
        (
            item
            for item in report["requirement_checks"]
            if item["requirement_id"] == finding["requirement_id"]
        ),
        None,
    )
    references = set(finding["evidence_ids"])
    for role in ("origin", "decisive", "symptom"):
        if finding["attribution"][role]:
            references.update(finding["attribution"][role]["evidence_ids"])
    if requirement:
        references.update(requirement["evidence_ids"])
    for evidence_id in references:
        if evidence_id in receipts:
            text = receipts[evidence_id]["excerpt"]
            evidence[f"receipt:{evidence_id}"] = (text, _text_digest(text))
    for index, check in enumerate(report["requirement_checks"]):
        text = check["requirement"]
        evidence[f"requirement-{index}"] = (text, _text_digest(text))
    # F6 companion evidence only connects two accepted findings in the same
    # investigation when their cited event IDs overlap. It is never a raw
    # trace assertion and never replaces each member's own report citation.
    peers = []
    own_events = set(finding["evidence_ids"])
    for other in snapshot["occurrences"]:
        if other["occurrence_id"] == occurrence_id:
            continue
        other_finding = next(
            item
            for item in report["findings"]
            if item["finding_id"] == other["finding_id"]
        )
        overlap = own_events.intersection(other_finding["evidence_ids"])
        if overlap:
            peers.append(
                (-len(overlap), other["occurrence_id"], other_finding["statement"])
            )
    for _, peer_id, text in sorted(peers)[:2]:
        evidence[f"companion:{peer_id}"] = (text, _text_digest(text))
    return evidence


def _validate_citations(
    command: dict,
    required_ids: set[str],
    snapshots: dict[str, dict],
    findings: dict[str, TraceInvestigationFinding],
) -> None:
    citations = command["citations"]
    if not isinstance(citations, list) or len(citations) > 400:
        raise GroupingControlError("citations exceed F6 bound")
    own = set()
    for item in citations:
        if not isinstance(item, dict) or set(item) != {
            "occurrence_id",
            "evidence_id",
            "evidence_digest",
            "quote",
        }:
            raise GroupingControlError("citation has unknown or missing fields")
        occurrence_id = item["occurrence_id"]
        if occurrence_id not in required_ids or occurrence_id not in findings:
            raise GroupingConflict(
                "citation is outside proposed current members/prototypes"
            )
        finding = findings[occurrence_id]
        evidence = _finding_evidence(snapshots[str(finding.report_id)], occurrence_id)
        source = evidence.get(item["evidence_id"])
        if source is None or source[1] != item["evidence_digest"]:
            raise GroupingConflict("citation evidence digest or provenance changed")
        quote = item["quote"]
        if not isinstance(quote, str) or len(quote) < 8 or quote not in source[0]:
            raise GroupingConflict(
                "citation quote does not resolve to accepted evidence"
            )
        if item["evidence_id"] == "report":
            own.add(occurrence_id)
    if not required_ids.issubset(own):
        raise GroupingConflict(
            "every proposed member and prototype needs its own report citation"
        )


def _canonical_citations(citations: object) -> list[str]:
    if not isinstance(citations, list) or len(citations) > 400:
        raise GroupingControlError("invalid bounded admission citations")
    output = []
    for citation in citations:
        if not isinstance(citation, dict) or set(citation) != {
            "occurrence_id",
            "evidence_id",
            "evidence_digest",
            "quote",
        }:
            raise GroupingControlError("admission citation shape changed")
        output.append(
            json.dumps(
                citation, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            )
        )
    return sorted(output)


def _model_citations(citations: object) -> list[dict]:
    if not isinstance(citations, list) or len(citations) > 400:
        raise GroupingConflict("stored model citations exceed bound")
    output = []
    for citation in citations:
        if not isinstance(citation, dict) or set(citation) != {
            "finding_id",
            "evidence_id",
            "evidence_digest",
            "quote",
        }:
            raise GroupingConflict("stored model citation shape is invalid")
        output.append(
            {
                "occurrence_id": citation["finding_id"],
                "evidence_id": citation["evidence_id"],
                "evidence_digest": citation["evidence_digest"],
                "quote": citation["quote"],
            }
        )
    return output


def _admitted_group(
    *,
    admission: object,
    receipt_map: dict[uuid.UUID, TraceGroupingCall],
    member_ids: list[str],
    target_issue_id: str | None,
    mechanism: dict | None,
    expected_action: str | None,
    required_own_ids: set[str],
) -> list[dict]:
    if not isinstance(admission, dict) or set(admission) != {
        "primary_receipt_id",
        "group_index",
        "repair_receipt_id",
    }:
        raise GroupingControlError("group admission provenance is missing")
    primary = receipt_map.get(_uuid(admission["primary_receipt_id"], "primary receipt"))
    index = admission["group_index"]
    if primary is None or type(index) is not int or not 0 <= index < 100:
        raise GroupingConflict("primary admission receipt is absent")
    result = primary.result
    if (
        not isinstance(result, dict)
        or not isinstance(result.get("groups"), list)
        or index >= len(result["groups"])
    ):
        raise GroupingConflict("primary admission result has no selected group")
    if result.get("action") != expected_action:
        raise GroupingConflict("stored model action does not authorize command type")
    group = result["groups"][index]
    if not isinstance(group, dict) or set(group) not in (
        {
            "target_issue_id",
            "member_ids",
            "mechanism",
            "fix_hypothesis",
            "falsifier",
            "predicted_observations",
            "citations",
            "contradictions",
            "alternatives",
            "missing_evidence",
        },
        {
            "target_issue_id",
            "member_ids",
            "title",
            "mechanism",
            "fix_hypothesis",
            "falsifier",
            "predicted_observations",
            "citations",
            "contradictions",
            "alternatives",
            "missing_evidence",
        },
    ):
        raise GroupingConflict("stored admission group has invalid schema")
    if (
        (
            "title" in group
            and (
                not isinstance(group["title"], str)
                or not group["title"].strip()
                or len(group["title"]) > 120
                or len(group["title"].split()) > 12
            )
        )
        or any(
            not isinstance(group[key], str)
            or not group[key].strip()
            or len(group[key]) > 3000
            for key in MECHANISM_KEYS
        )
        or any(
            not isinstance(group[key], list)
            or not group[key]
            or any(not isinstance(item, str) or not item.strip() for item in group[key])
            for key in ("predicted_observations", "alternatives")
        )
        or not isinstance(group["missing_evidence"], list)
        or any(not isinstance(item, str) for item in group["missing_evidence"])
        or group["contradictions"] != []
    ):
        raise GroupingConflict("stored admission is not an Emerging F6 group")
    if (
        not isinstance(group["member_ids"], list)
        or any(not isinstance(item, str) or not item for item in group["member_ids"])
        or len(set(group["member_ids"])) != len(member_ids)
        or set(group["member_ids"]) != set(member_ids)
        or group["target_issue_id"] != target_issue_id
    ):
        raise GroupingConflict(
            "stored admission does not match command membership/target"
        )
    if (
        mechanism is not None
        and {key: group[key] for key in set(mechanism)} != mechanism
    ):
        raise GroupingConflict("stored mechanism does not match worker command")
    combined = _model_citations(group["citations"])
    own = {
        item["occurrence_id"] for item in combined if item["evidence_id"] == "report"
    }
    missing = required_own_ids - own
    repair_id = admission["repair_receipt_id"]
    if repair_id is not None:
        repair = receipt_map.get(_uuid(repair_id, "repair receipt"))
        if (
            repair is None
            or not isinstance(repair.result, dict)
            or repair.result.get("action") != "add_citations"
        ):
            raise GroupingConflict(
                "repair receipt does not contain accepted citation repair"
            )
        intent = repair.repair_intent
        if (
            not isinstance(intent, dict)
            or intent.get("primary_receipt_id") != str(primary.id)
            or intent.get("group_index") != index
            or sorted(intent.get("missing_own_report_ids", [])) != sorted(missing)
        ):
            raise GroupingConflict(
                "repair receipt is not bound to primary missing citations"
            )
        repaired = _model_citations(repair.result.get("citations"))
        if (
            {item["occurrence_id"] for item in repaired} != missing
            or any(item["evidence_id"] != "report" for item in repaired)
            or len(repaired) != len(missing)
        ):
            raise GroupingConflict(
                "repair changed more than missing own-report citations"
            )
        combined.extend(repaired)
    elif missing:
        raise GroupingConflict("primary admission lacks required own-report citations")
    return combined


def _admitted_removal(
    *,
    admission: object,
    receipt_map: dict[uuid.UUID, TraceGroupingCall],
    removed_ids: list[str],
) -> None:
    if not isinstance(admission, dict) or set(admission) != {"primary_receipt_id"}:
        raise GroupingControlError("removal admission provenance is missing")
    primary = receipt_map.get(_uuid(admission["primary_receipt_id"], "removal receipt"))
    result = primary.result if primary else None
    if (
        not isinstance(result, dict)
        or result.get("action") != "remove"
        or not isinstance(result.get("removed_ids"), list)
        or set(result["removed_ids"]) != set(removed_ids)
        or len(result["removed_ids"]) != len(removed_ids)
    ):
        raise GroupingConflict("stored reconciliation did not authorize exact removal")


def _issue_members(state: TraceGroupingIssueState) -> list[str]:
    rows = list(
        TraceInvestigationFinding.no_workspace_objects.filter(
            cluster_id=state.cluster_id
        )
        .select_related("report__job")
        .order_by("id")[: MAX_RECONCILIATION_MEMBERS + 1]
    )
    if not rows or len(rows) > MAX_RECONCILIATION_MEMBERS:
        raise GroupingConflict(
            "issue full membership is empty or exceeds reconciliation bound"
        )
    junctions = list(
        ErrorClusterTraces.no_workspace_objects.filter(
            cluster=state.cluster,
            finding__isnull=False,
        )
        .order_by("finding_id")
        .values_list("finding_id", "trace_id", "span_id", "trace_session_id")[
            : MAX_RECONCILIATION_MEMBERS + 1
        ]
    )
    if len(junctions) != len(rows) or {item[0] for item in junctions} != {
        item.id for item in rows
    }:
        raise GroupingConflict("issue finding and junction membership disagree")
    traces = {item.id: item.report.trace_id for item in rows}
    if any(
        item[1] != traces[item[0]] or item[2] is not None or item[3] is not None
        for item in junctions
    ):
        raise GroupingConflict("issue junction provenance is not canonical Omega")
    if any(
        finding.report.test_execution_id != state.cluster.test_execution_id
        for finding in rows
    ):
        raise GroupingConflict("issue contains cross-execution membership")
    for finding in rows:
        report = finding.report
        if (
            report.deleted
            or not report.is_current
            or report.source != "omega"
            or report.execution_status != "completed"
            or report.job.current_report_id != report.id
            or report.project_id != state.scope.project_id
        ):
            raise GroupingConflict("issue has stale or cross-scope membership")
    return [str(item.id) for item in rows]


def _protected(state: TraceGroupingIssueState) -> bool:
    cluster = state.cluster
    return bool(
        state.protected
        or cluster.assignee_id
        or cluster.external_issue_url
        or cluster.external_issue_id
        or cluster.status not in {"escalating", "for_review"}
    )


def _hard_safe(
    scope: TraceGroupingScope, member_ids: list[str], issue_id: str | None = None
) -> None:
    if len(member_ids) != len(set(member_ids)):
        raise GroupingConflict("issue membership contains duplicate finding")
    constraints = TraceGroupingConstraint.no_workspace_objects.filter(scope=scope)
    if constraints.filter(
        kind="cannot_link",
        first_finding_id__in=member_ids,
        second_finding_id__in=member_ids,
    ).exists():
        raise GroupingConflict("hard cannot-link forbids issue membership")
    if (
        issue_id
        and constraints.filter(
            kind="exclude_issue", issue_id=issue_id, first_finding_id__in=member_ids
        ).exists()
    ):
        raise GroupingConflict("hard issue exclusion forbids membership")


def _clear_rca(cluster: TraceErrorGroup) -> None:
    cluster.rca_synthesis = None
    cluster.rca_fix = None
    cluster.rca_confidence = None
    cluster.rca_evidence_trace_ids = []
    cluster.rca_at = None
    cluster.rca_failures_at_run = None
    cluster.rca_trace = None


def _recount(state: TraceGroupingIssueState) -> None:
    cluster = state.cluster
    memberships = ErrorClusterTraces.no_workspace_objects.filter(
        Q(
            finding__report__workload_type=InvestigationWorkload.SIMULATION_TEST_EXECUTION,
            trace_id__isnull=True,
        )
        | Q(
            finding__report__workload_type=InvestigationWorkload.TRACE,
            trace_id=F("finding__report__trace_id"),
        ),
        cluster=cluster,
        finding__isnull=False,
        finding__deleted=False,
        finding__cluster=cluster,
        finding__report__deleted=False,
        finding__report__is_current=True,
        finding__report__source="omega",
        finding__report__execution_status="completed",
        span_id__isnull=True,
        trace_session_id__isnull=True,
    )
    totals = memberships.aggregate(
        count=Count("finding_id", distinct=True),
        traces=Count("trace_id", distinct=True),
        first=Min("finding__report__recorded_at"),
        last=Max("finding__report__recorded_at"),
    )
    cluster.error_count = totals["count"] or 0
    cluster.total_events = totals["count"] or 0
    cluster.unique_traces = totals["traces"] or 0
    # Canonical individual members live in the junction; do not maintain an
    # unbounded copied array of finding labels on the aggregate issue row.
    cluster.error_ids = []
    cluster.first_seen = totals["first"]
    cluster.last_seen = totals["last"]
    _clear_rca(cluster)
    cluster.save(
        update_fields=[
            "error_count",
            "total_events",
            "unique_traces",
            "error_ids",
            "first_seen",
            "last_seen",
            "rca_synthesis",
            "rca_fix",
            "rca_confidence",
            "rca_evidence_trace_ids",
            "rca_at",
            "rca_failures_at_run",
            "rca_trace",
            "updated_at",
        ]
    )


def _new_issue(
    scope: TraceGroupingScope, mechanism: dict, prototype_ids: list[str]
) -> TraceGroupingIssueState:
    cluster_id = uuid.uuid4()
    for width in (8, 12, 16):
        display_id = f"S-{cluster_id.hex[:width].upper()}"
        if not TraceErrorGroup.no_workspace_objects.filter(
            project_id=scope.project_id, cluster_id=display_id, deleted=False
        ).exists():
            break
    else:
        raise GroupingConflict("could not allocate a unique issue ID")
    prototype_scopes = set(
        TraceInvestigationFinding.no_workspace_objects.filter(
            id__in=prototype_ids
        ).values_list("report__test_execution_id", flat=True)
    )
    if len(prototype_scopes) != 1:
        raise GroupingConflict("grouping prototypes cross execution scopes")
    test_execution_id = next(iter(prototype_scopes))
    cluster = TraceErrorGroup.no_workspace_objects.create(
        id=cluster_id,
        project_id=scope.project_id,
        cluster_id=display_id,
        source="scanner",
        issue_group="Investigation findings",
        eval_target_type=None,
        error_type=mechanism["mechanism"][:200],
        title=mechanism.get("title", mechanism["mechanism"])[:1000],
        combined_description=mechanism["mechanism"],
        error_count=0,
        severity_source="default",
        target_type=("simulation" if test_execution_id is not None else "error_feed"),
        test_execution_id=test_execution_id,
    )
    return TraceGroupingIssueState.no_workspace_objects.create(
        scope=scope,
        cluster=cluster,
        mechanism=mechanism,
        prototype_occurrence_ids=prototype_ids,
    )


def _assign(
    finding: TraceInvestigationFinding,
    state: TraceGroupingIssueState,
    scope: TraceGroupingScope,
) -> None:
    if (
        finding.report.project_id != scope.project_id
        or finding.report.organization_id != scope.organization_id
        or finding.report.workspace_id != scope.workspace_id
    ):
        raise GroupingConflict("finding scope changed")
    if finding.report.test_execution_id != state.cluster.test_execution_id:
        raise GroupingConflict("finding belongs to another simulation execution")
    existing = ErrorClusterTraces.no_workspace_objects.filter(finding=finding)
    if existing.exclude(cluster=state.cluster).exists():
        # The caller must first retire a source issue; never override manual
        # or eval memberships by accepting a worker proposal.
        raise GroupingConflict("finding has another active membership")
    if finding.cluster_id not in {None, state.cluster_id}:
        raise GroupingConflict("finding is owned by another issue")
    junction, _ = ErrorClusterTraces.no_workspace_objects.get_or_create(
        cluster=state.cluster,
        finding=finding,
        defaults={
            "trace_id": finding.report.trace_id,
            "span_id": None,
            "trace_session_id": None,
        },
    )
    if (
        junction.trace_id != finding.report.trace_id
        or junction.span_id is not None
        or junction.trace_session_id is not None
    ):
        raise GroupingConflict(
            "existing same-issue junction has noncanonical provenance"
        )
    finding.cluster = state.cluster
    finding.save(update_fields=["cluster", "updated_at"])
    TraceGroupingFindingState.no_workspace_objects.update_or_create(
        finding=finding,
        defaults={
            "scope": scope,
            "disposition": "assigned",
            "reason": "",
            "source_digest": canonical_grouping_source_digest(
                export_grouping_snapshot(report=finding.report)
            ),
        },
    )


def _unassign(
    finding: TraceInvestigationFinding, scope: TraceGroupingScope, reason: str
) -> None:
    if finding.cluster_id:
        ErrorClusterTraces.no_workspace_objects.filter(
            finding=finding, cluster_id=finding.cluster_id
        ).update(
            deleted=True,
            deleted_at=timezone.now(),
            updated_at=timezone.now(),
        )
        finding.cluster = None
        finding.save(update_fields=["cluster", "updated_at"])
    TraceGroupingFindingState.no_workspace_objects.update_or_create(
        finding=finding,
        defaults={
            "scope": scope,
            "disposition": "deferred",
            "reason": reason[:255],
            "source_digest": canonical_grouping_source_digest(
                export_grouping_snapshot(report=finding.report)
            ),
        },
    )


def _move(
    finding: TraceInvestigationFinding,
    source_ids: set[uuid.UUID],
    target: TraceGroupingIssueState,
    scope: TraceGroupingScope,
) -> None:
    if finding.cluster_id not in source_ids:
        raise GroupingConflict("move source membership changed")
    ErrorClusterTraces.no_workspace_objects.filter(
        finding=finding, cluster_id__in=source_ids
    ).update(
        deleted=True,
        deleted_at=timezone.now(),
        updated_at=timezone.now(),
    )
    finding.cluster = None
    finding.save(update_fields=["cluster", "updated_at"])
    _assign(finding, target, scope)


def publish_grouping(
    *,
    attempt_id: uuid.UUID,
    lease_token: str,
    idempotency_key: str,
    snapshot_digest: str,
    registry_revision: int,
    commands: list[dict],
    receipt_ids: list[str],
) -> dict:
    if not isinstance(idempotency_key, str) or not 1 <= len(idempotency_key) <= 255:
        raise GroupingControlError("invalid publication idempotency key")
    if (
        not isinstance(commands, list)
        or not 1 <= len(commands) <= MAX_COMMANDS
        or not isinstance(receipt_ids, list)
        or len(receipt_ids) > 100
    ):
        raise GroupingControlError("invalid bounded grouping proposal")
    try:
        if (
            len(json.dumps(commands, ensure_ascii=False, allow_nan=False).encode())
            > MAX_PROPOSAL_BYTES
        ):
            raise GroupingControlError("grouping proposal exceeds bound")
    except (TypeError, ValueError) as exc:
        raise GroupingControlError("grouping proposal is not JSON") from exc
    # DRF UUIDField supplies UUID objects; direct callers may supply strings.
    # Canonicalize validated receipt IDs so both paths share one JSON digest.
    receipt_ids = [str(_uuid(value, "receipt ID")) for value in receipt_ids]
    proposal_digest = _digest(
        {
            "snapshot_digest": snapshot_digest,
            "registry_revision": registry_revision,
            "commands": commands,
            "receipt_ids": receipt_ids,
        }
    )
    with transaction.atomic():
        scope, attempt = lock_attempt_scope(
            attempt_id=attempt_id,
            lease_token=lease_token,
            allow_expired_for_settlement=True,
        )
        prior = TraceGroupingDecision.no_workspace_objects.filter(
            scope=scope, idempotency_key=idempotency_key
        ).first()
        if prior:
            if prior.proposal_digest != proposal_digest:
                raise GroupingConflict(
                    "publication key was reused for a different proposal"
                )
            return prior.result
        attempt = require_attempt(attempt_id=attempt_id, lease_token=lease_token)
        if (
            attempt.snapshot_digest != snapshot_digest
            or attempt.registry_revision != registry_revision
            or scope.registry_revision != registry_revision
            or not attempt.candidate_digest
        ):
            raise GroupingConflict("grouping publication read set is stale")
        receipt_uuids = [_uuid(value, "receipt ID") for value in receipt_ids]
        if len(set(receipt_uuids)) != len(receipt_uuids):
            raise GroupingControlError("duplicate call receipt")
        receipts = list(
            TraceGroupingCall.no_workspace_objects.filter(
                id__in=receipt_uuids, scope=scope
            )
        )
        if len(receipts) != len(receipt_uuids) or any(
            item.result is None
            or item.status not in {"settled", "unknown"}
            or item.attempt.snapshot_digest != snapshot_digest
            or item.attempt.registry_revision != registry_revision
            or item.attempt.candidate_digest != attempt.candidate_digest
            for item in receipts
        ):
            raise GroupingConflict(
                "model call receipt is absent or not bound to this current cohort"
            )
        receipt_map = {item.id: item for item in receipts}
        work_ids = [_uuid(item, "claimed work") for item in attempt.claimed_work_ids]
        works = list(
            TraceGroupingWork.no_workspace_objects.select_for_update()
            .filter(id__in=work_ids, scope=scope)
            .order_by("id")
        )
        if len(works) != len(work_ids) or any(
            item.state != GroupingWorkState.RUNNING for item in works
        ):
            raise GroupingConflict("claimed cohort work changed")
        pending_snapshots = _report_snapshots([item.report for item in works])
        # A claim's order is report-cohort order, not UUID sort order.
        ordered_snapshots = [
            pending_snapshots[str(item.report_id)]
            for item in (
                TraceGroupingWork.no_workspace_objects.get(
                    pk=_uuid(key, "claimed work")
                )
                for key in attempt.claimed_work_ids
            )
        ]
        if (
            canonical_snapshot_digest({"pending_snapshots": ordered_snapshots})
            != snapshot_digest
        ):
            raise GroupingConflict("claimed report snapshot changed")
        pending_ids = {
            item["occurrence_id"]
            for snap in ordered_snapshots
            for item in snap["occurrences"]
        }
        states = {
            str(item.cluster_id): item
            for item in TraceGroupingIssueState.no_workspace_objects.select_for_update()
            .select_related("cluster")
            .filter(
                scope=scope,
                cluster_id__in=attempt.offered_issue_ids,
                retired=False,
                dirty=False,
            )
        }
        if set(states) != set(attempt.offered_issue_ids):
            raise GroupingConflict("offered issue changed")
        membership = {key: _issue_members(state) for key, state in states.items()}
        relevant_ids = pending_ids | {
            item for values in membership.values() for item in values
        }
        finding_rows = list(
            TraceInvestigationFinding.no_workspace_objects.select_for_update()
            .filter(id__in=relevant_ids)
            .order_by("id")
        )
        findings = {str(item.id): item for item in finding_rows}
        if len(findings) != len(relevant_ids):
            raise GroupingConflict("member finding disappeared")
        all_reports = {str(item.report_id): item.report for item in finding_rows}
        snapshots = {
            **pending_snapshots,
            **_report_snapshots(list(all_reports.values())),
        }
        candidate_window = {
            "registry_revision": attempt.registry_revision,
            "issues": [],
            "omitted_candidates": attempt.omitted_candidates,
        }
        for key in attempt.offered_issue_ids:
            state = states[key]
            candidate_window["issues"].append(
                {
                    "issue_id": key,
                    "revision": state.revision,
                    "protected": _protected(state),
                    "mechanism": state.mechanism,
                    "prototype_occurrence_ids": state.prototype_occurrence_ids,
                    "members": [
                        {
                            "occurrence_id": item,
                            "report_id": str(findings[item].report_id),
                            "trace_id": (
                                str(findings[item].report.trace_id)
                                if findings[item].report.trace_id is not None
                                else None
                            ),
                            **(
                                {
                                    "test_execution_id": str(
                                        findings[item].report.test_execution_id
                                    )
                                }
                                if findings[item].report.test_execution_id
                                else {}
                            ),
                            "source_digest": canonical_grouping_source_digest(
                                snapshots[str(findings[item].report_id)]
                            ),
                            "evidence_revision": snapshots[
                                str(findings[item].report_id)
                            ]["report"]["evidence_digest"],
                        }
                        for item in membership[key]
                    ],
                    "membership_complete": True,
                }
            )
        report_digests = {
            key: canonical_grouping_source_digest(item)
            for key, item in snapshots.items()
        }
        if (
            canonical_snapshot_digest(
                {"candidate_window": candidate_window, "report_digests": report_digests}
            )
            != attempt.candidate_digest
        ):
            raise GroupingConflict("candidate read set changed since claim")
        if any(
            item.report.project_id != scope.project_id
            or item.report.organization_id != scope.organization_id
            or item.report.workspace_id != scope.workspace_id
            for item in finding_rows
        ):
            raise GroupingConflict("candidate member scope changed")
        if any(findings[item].cluster_id is not None for item in pending_ids):
            raise GroupingConflict("pending finding is already owned")
        assigned_pending = set()
        deferred_pending = set()
        touched = set()
        new_ids = {}
        if (
            any(
                item.get("type") in {"create", "attach", "merge", "split", "remove"}
                for item in commands
            )
            and not receipt_ids
        ):
            raise GroupingConflict(
                "model-backed issue mutation requires durable call receipts"
            )
        for original in commands:
            command = _command_shape(original)
            kind = command["type"]
            if kind == "defer":
                ids = _ids(
                    command["occurrence_ids"],
                    limit=100,
                    label="deferred occurrence IDs",
                )
                if (
                    not isinstance(command["reason"], str)
                    or not command["reason"].strip()
                ):
                    raise GroupingControlError("deferral needs reason")
                if not set(ids).issubset(pending_ids) or set(ids) & (
                    assigned_pending | deferred_pending
                ):
                    raise GroupingConflict(
                        "deferred disposition is duplicated or not pending"
                    )
                for item in ids:
                    _unassign(findings[item], scope, command["reason"])
                deferred_pending.update(ids)
                continue
            if kind in {"create", "merge", "split"}:
                if kind != "split":
                    temporary_id = command["temporary_id"]
                    if (
                        not isinstance(temporary_id, str)
                        or not temporary_id
                        or temporary_id in states
                    ):
                        raise GroupingControlError("duplicate temporary issue ID")
            if kind == "create":
                ids = _ids(
                    command["occurrence_ids"], limit=100, label="new issue members"
                )
                if not set(ids).issubset(pending_ids) or set(ids) & (
                    assigned_pending | deferred_pending
                ):
                    raise GroupingConflict(
                        "new issue contains nonpending or duplicate member"
                    )
                prototypes = _ids(
                    command["prototype_occurrence_ids"], limit=5, label="prototypes"
                )
                if not set(prototypes).issubset(ids):
                    raise GroupingConflict("prototype is not an issue member")
                _hard_safe(scope, ids)
                admitted = _admitted_group(
                    admission=command["admission"],
                    receipt_map=receipt_map,
                    member_ids=ids,
                    target_issue_id=None,
                    mechanism=_mechanism(command["mechanism"]),
                    expected_action=None,
                    required_own_ids=set(ids),
                )
                if _canonical_citations(command["citations"]) != _canonical_citations(
                    admitted
                ):
                    raise GroupingConflict(
                        "create citations differ from stored admission"
                    )
                _validate_citations(command, set(ids), snapshots, findings)
                state = _new_issue(scope, _mechanism(command["mechanism"]), prototypes)
                new_ids[command["temporary_id"]] = str(state.cluster_id)
                states[command["temporary_id"]] = state
                membership[command["temporary_id"]] = list(ids)
                for item in ids:
                    _assign(findings[item], state, scope)
                assigned_pending.update(ids)
                touched.add(str(state.cluster_id))
            elif kind == "attach":
                state = states.get(command["issue_id"])
                if (
                    state is None
                    or state.retired
                    or _protected(state)
                    or state.revision != command["expected_issue_revision"]
                ):
                    raise GroupingConflict("attach target is stale or not offered")
                ids = _ids(command["occurrence_ids"], limit=100, label="attach members")
                if not set(ids).issubset(pending_ids) or set(ids) & (
                    assigned_pending | deferred_pending
                ):
                    raise GroupingConflict(
                        "attach contains nonpending or duplicate member"
                    )
                combined = membership[command["issue_id"]] + ids
                _hard_safe(scope, combined, str(state.cluster_id))
                required = set(ids) | set(state.prototype_occurrence_ids)
                admitted = _admitted_group(
                    admission=command["admission"],
                    receipt_map=receipt_map,
                    member_ids=ids,
                    target_issue_id=command["issue_id"],
                    mechanism=None,
                    expected_action=None,
                    required_own_ids=required,
                )
                if _canonical_citations(command["citations"]) != _canonical_citations(
                    admitted
                ):
                    raise GroupingConflict(
                        "attach citations differ from stored admission"
                    )
                _validate_citations(command, required, snapshots, findings)
                for item in ids:
                    _assign(findings[item], state, scope)
                membership[command["issue_id"]] = combined
                state.membership_revision += 1
                state.save(update_fields=["membership_revision", "updated_at"])
                assigned_pending.update(ids)
                touched.add(str(state.cluster_id))
            elif kind == "refresh":
                state = states.get(command["issue_id"])
                if (
                    state is None
                    or state.retired
                    or _protected(state)
                    or state.revision != command["expected_issue_revision"]
                ):
                    raise GroupingConflict("refresh target is stale or not offered")
                if _mechanism(command["mechanism"]) != state.mechanism:
                    raise GroupingConflict("refresh cannot silently relabel the issue")
                prototypes = _ids(
                    command["prototype_occurrence_ids"],
                    limit=5,
                    label="refreshed prototypes",
                )
                if not set(prototypes).issubset(membership[command["issue_id"]]):
                    raise GroupingConflict(
                        "refreshed prototype is not a current member"
                    )
                if prototypes != state.prototype_occurrence_ids:
                    state.prototype_occurrence_ids = prototypes
                    state.revision += 1
                    state.save(
                        update_fields=[
                            "prototype_occurrence_ids",
                            "revision",
                            "updated_at",
                        ]
                    )
                touched.add(str(state.cluster_id))
            elif kind == "merge":
                source_ids = _ids(
                    command["source_issue_ids"], limit=2, label="merge sources"
                )
                if len(source_ids) != 2 or any(
                    key not in states or states[key].retired for key in source_ids
                ):
                    raise GroupingConflict("merge sources were not both offered")
                if not isinstance(command["expected_revisions"], dict) or set(
                    command["expected_revisions"]
                ) != set(source_ids):
                    raise GroupingControlError(
                        "merge expected revisions are incomplete"
                    )
                sources = [states[key] for key in source_ids]
                if any(
                    _protected(states[key])
                    or states[key].revision != command["expected_revisions"][key]
                    for key in source_ids
                ):
                    raise GroupingConflict("merge source is protected or stale")
                ids = [item for key in source_ids for item in membership[key]]
                if len(set(ids)) != len(ids):
                    raise GroupingConflict("merge sources overlap")
                _hard_safe(scope, ids)
                prototypes = _ids(
                    command["prototype_occurrence_ids"],
                    limit=5,
                    label="merge prototypes",
                )
                if not set(prototypes).issubset(ids):
                    raise GroupingConflict("merge prototype is not a source member")
                admitted = _admitted_group(
                    admission=command["admission"],
                    receipt_map=receipt_map,
                    member_ids=ids,
                    target_issue_id=None,
                    mechanism=_mechanism(command["mechanism"]),
                    expected_action="merge",
                    required_own_ids=set(ids),
                )
                if _canonical_citations(command["citations"]) != _canonical_citations(
                    admitted
                ):
                    raise GroupingConflict(
                        "merge citations differ from stored reconciliation"
                    )
                _validate_citations(command, set(ids), snapshots, findings)
                target = _new_issue(scope, _mechanism(command["mechanism"]), prototypes)
                new_ids[command["temporary_id"]] = str(target.cluster_id)
                states[command["temporary_id"]] = target
                membership[command["temporary_id"]] = list(ids)
                old_uuid = {state.cluster_id for state in sources}
                for item in ids:
                    _move(findings[item], old_uuid, target, scope)
                for state in sources:
                    state.retired = True
                    state.revision += 1
                    state.membership_revision += 1
                    state.save(
                        update_fields=[
                            "retired",
                            "revision",
                            "membership_revision",
                            "updated_at",
                        ]
                    )
                    touched.add(str(state.cluster_id))
                touched.add(str(target.cluster_id))
            elif kind == "split":
                state = states.get(command["issue_id"])
                if (
                    state is None
                    or state.retired
                    or state.revision != command["expected_issue_revision"]
                    or _protected(state)
                ):
                    raise GroupingConflict("split source is protected or stale")
                parts = command["parts"]
                if not isinstance(parts, list) or not 2 <= len(parts) <= 16:
                    raise GroupingControlError("split requires bounded parts")
                all_ids = []
                part_specs = []
                for part in parts:
                    if not isinstance(part, dict) or set(part) != {
                        "temporary_id",
                        "occurrence_ids",
                        "mechanism",
                        "prototype_occurrence_ids",
                    }:
                        raise GroupingControlError("split part has invalid shape")
                    temp = part["temporary_id"]
                    if (
                        not isinstance(temp, str)
                        or not temp
                        or temp in states
                        or any(temp == item[0] for item in part_specs)
                    ):
                        raise GroupingControlError("duplicate split issue identity")
                    ids = _ids(part["occurrence_ids"], limit=100, label="split members")
                    prototypes = _ids(
                        part["prototype_occurrence_ids"],
                        limit=5,
                        label="split prototypes",
                    )
                    if not set(prototypes).issubset(ids):
                        raise GroupingConflict("split prototype is not a part member")
                    _hard_safe(scope, ids)
                    part_specs.append(
                        (temp, ids, _mechanism(part["mechanism"]), prototypes)
                    )
                    all_ids.extend(ids)
                if len(all_ids) != len(set(all_ids)) or set(all_ids) != set(
                    membership[command["issue_id"]]
                ):
                    raise GroupingConflict(
                        "split did not partition every source member exactly once"
                    )
                admissions = command["admissions"]
                if not isinstance(admissions, list) or len(admissions) != len(
                    part_specs
                ):
                    raise GroupingControlError(
                        "split admission count differs from parts"
                    )
                admitted = []
                for (_, ids, mechanism, _), admission in zip(
                    part_specs, admissions, strict=True
                ):
                    admitted.extend(
                        _admitted_group(
                            admission=admission,
                            receipt_map=receipt_map,
                            member_ids=ids,
                            target_issue_id=None,
                            mechanism=mechanism,
                            expected_action="split",
                            required_own_ids=set(ids),
                        )
                    )
                if _canonical_citations(command["citations"]) != _canonical_citations(
                    admitted
                ):
                    raise GroupingConflict(
                        "split citations differ from stored reconciliation"
                    )
                _validate_citations(command, set(all_ids), snapshots, findings)
                for temp, ids, mechanism, prototypes in part_specs:
                    target = _new_issue(scope, mechanism, prototypes)
                    new_ids[temp] = str(target.cluster_id)
                    states[temp] = target
                    membership[temp] = list(ids)
                    for item in ids:
                        _move(findings[item], {state.cluster_id}, target, scope)
                    touched.add(str(target.cluster_id))
                state.retired = True
                state.revision += 1
                state.membership_revision += 1
                state.save(
                    update_fields=[
                        "retired",
                        "revision",
                        "membership_revision",
                        "updated_at",
                    ]
                )
                touched.add(str(state.cluster_id))
            elif kind == "remove":
                state = states.get(command["issue_id"])
                if (
                    state is None
                    or state.retired
                    or state.revision != command["expected_issue_revision"]
                    or _protected(state)
                ):
                    raise GroupingConflict("remove source is protected or stale")
                ids = _ids(
                    command["occurrence_ids"], limit=100, label="removed members"
                )
                remaining = set(membership[command["issue_id"]]) - set(ids)
                if not remaining or not set(ids).issubset(
                    membership[command["issue_id"]]
                ):
                    raise GroupingConflict(
                        "remove would empty issue or target absent member"
                    )
                _admitted_removal(
                    admission=command["admission"],
                    receipt_map=receipt_map,
                    removed_ids=ids,
                )
                hard = command["hard_constraints"]
                if not isinstance(hard, list) or len(hard) > 128:
                    raise GroupingControlError("invalid removal hard-rule proof")
                for item in ids:
                    if not any(
                        isinstance(pair, list)
                        and len(pair) == 2
                        and item in pair
                        and (set(pair) - {item}).issubset(remaining)
                        and (
                            TraceGroupingConstraint.no_workspace_objects.filter(
                                scope=scope,
                                kind="cannot_link",
                                first_finding_id=pair[0],
                                second_finding_id=pair[1],
                            ).exists()
                            or TraceGroupingConstraint.no_workspace_objects.filter(
                                scope=scope,
                                kind="cannot_link",
                                first_finding_id=pair[1],
                                second_finding_id=pair[0],
                            ).exists()
                        )
                        for pair in hard
                    ):
                        raise GroupingConflict(
                            "removal lacks current hard contradiction"
                        )
                for item in ids:
                    _unassign(findings[item], scope, command["reason"])
                assigned_pending.difference_update(ids)
                membership[command["issue_id"]] = sorted(remaining)
                state.prototype_occurrence_ids = [
                    item for item in state.prototype_occurrence_ids if item in remaining
                ]
                if not state.prototype_occurrence_ids:
                    state.prototype_occurrence_ids = [sorted(remaining)[0]]
                state.revision += 1
                state.membership_revision += 1
                state.save(
                    update_fields=[
                        "prototype_occurrence_ids",
                        "revision",
                        "membership_revision",
                        "updated_at",
                    ]
                )
                touched.add(command["issue_id"])
        if assigned_pending | deferred_pending != pending_ids:
            raise GroupingConflict(
                "proposal silently dropped or duplicated pending findings"
            )
        for key in touched:
            state = TraceGroupingIssueState.no_workspace_objects.select_related(
                "cluster"
            ).get(cluster_id=_uuid(key, "issue ID"))
            _recount(state)
            from tracer.services.grouping.severity import enqueue_severity

            enqueue_severity(issue=state, attempt=attempt)
        for work in works:
            work.state = GroupingWorkState.COMPLETED
            work.save(update_fields=["state", "updated_at"])
            work.report.grouping_status = TraceInvestigationGroupingStatus.COMPLETED
            work.report.save(update_fields=["grouping_status", "updated_at"])
        attempt.state = GroupingAttemptState.COMPLETED
        attempt.save(update_fields=["state", "updated_at"])
        scope.registry_revision = F("registry_revision") + 1
        scope.save(update_fields=["registry_revision", "updated_at"])
        scope.refresh_from_db(fields=["registry_revision"])
        result = {
            "status": "completed",
            "registry_revision": scope.registry_revision,
            "created_issue_ids": new_ids,
            "assigned": len(assigned_pending),
            "deferred": len(deferred_pending),
        }
        TraceGroupingDecision.no_workspace_objects.create(
            scope=scope,
            attempt=attempt,
            idempotency_key=idempotency_key,
            proposal_digest=proposal_digest,
            snapshot_digest=snapshot_digest,
            commands=commands,
            receipt_ids=receipt_ids,
            registry_revision=scope.registry_revision,
            result=result,
        )
        TraceGroupingOutbox.no_workspace_objects.get_or_create(
            scope=scope,
            event_kind="error-feed.grouping-published.v1",
            source_id=attempt.id,
            revision=scope.registry_revision,
        )
        return result
