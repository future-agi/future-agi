"""Project durable Omega findings into the existing Error Feed registry.

``TraceInvestigationReport.grouping_status`` is the durable queue.  The
management command in this module's caller drains it without introducing a
Temporal dependency into the Omega worker path.

The existing scanner clustering pipeline remains the compatibility algorithm:
Omega findings are staged as unclustered ``TraceScanIssue`` rows, clustered
through the same distill/embed/centroid functions, and committed to Feed-visible
cluster membership in the same PostgreSQL transaction that retires the old
projection. A future clustering implementation can replace this adapter without
changing report publication.
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from dataclasses import dataclass

import structlog
from django.db import transaction
from django.utils import timezone

from tracer.models.trace_error_analysis import ErrorClusterTraces, TraceErrorGroup
from tracer.models.trace_investigation import (
    TraceInvestigationAttempt,
    TraceInvestigationGroupingStatus,
    TraceInvestigationJob,
    TraceInvestigationReport,
)
from tracer.models.trace_scan import (
    ScanIssueConfidence,
    TraceScanIssue,
    TraceScanResult,
)
from tracer.utils.trace_scanner import cluster_issues

logger = structlog.get_logger(__name__)

DEFAULT_REPORT_LIMIT = 25
MAX_REPORT_LIMIT = 100


class OmegaGroupingError(Exception):
    """Base error for one durable grouping attempt."""


class OmegaGroupingOperationalError(OmegaGroupingError):
    """Transient clustering failure; the report remains pending for retry."""


class _StaleReport(OmegaGroupingError):
    pass


class _InvalidReport(OmegaGroupingError):
    pass


@dataclass(frozen=True)
class _LockedCurrentReport:
    report: TraceInvestigationReport
    job: TraceInvestigationJob
    projection: TraceScanResult


def _projection_meta(projection: TraceScanResult) -> dict:
    return dict(projection.meta) if isinstance(projection.meta, dict) else {}


def _lock_current_report(report_id: uuid.UUID | str) -> _LockedCurrentReport:
    report = (
        TraceInvestigationReport.no_workspace_objects.select_for_update()
        .select_related("attempt")
        .get(id=report_id)
    )
    job = TraceInvestigationJob.no_workspace_objects.select_for_update().get(
        id=report.job_id
    )
    attempt = TraceInvestigationAttempt.no_workspace_objects.select_for_update().get(
        id=report.attempt_id
    )

    if (
        report.organization_id != job.organization_id
        or report.workspace_id != job.workspace_id
        or report.project_id != job.project_id
        or attempt.job_id != job.id
        or attempt.generation != report.attempt.generation
    ):
        raise _InvalidReport("report tenant or attempt scope is inconsistent")
    if not report.active_projection_updated or job.generation != attempt.generation:
        raise _StaleReport("report generation is no longer current")

    projection = (
        TraceScanResult.no_workspace_objects.select_for_update()
        .filter(trace_id=job.trace_id)
        .first()
    )
    if projection is None:
        raise _InvalidReport("active report has no trace projection")
    if projection.project_id != report.project_id:
        raise _InvalidReport("trace projection belongs to a different project")
    if _projection_meta(projection).get("omega_report_id") != str(report.id):
        raise _StaleReport("a newer report owns the trace projection")
    return _LockedCurrentReport(report=report, job=job, projection=projection)


def _scan_issue_ids(report: TraceInvestigationReport) -> list[uuid.UUID]:
    issue_ids: list[uuid.UUID] = []
    for occurrence in report.occurrences or []:
        value = occurrence.get("scan_issue_id")
        if value:
            try:
                issue_ids.append(uuid.UUID(str(value)))
            except (TypeError, ValueError) as error:
                raise _InvalidReport("report has an invalid scan_issue_id") from error
    return issue_ids


def _recompute_cluster_counts(cluster_ids: set[uuid.UUID]) -> None:
    for cluster in TraceErrorGroup.no_workspace_objects.select_for_update().filter(
        id__in=cluster_ids
    ):
        cluster.error_count = TraceScanIssue.no_workspace_objects.filter(
            cluster_id=cluster.id
        ).count()
        cluster.unique_traces = (
            ErrorClusterTraces.no_workspace_objects.filter(cluster_id=cluster.id)
            .exclude(trace_id__isnull=True)
            .values("trace_id")
            .distinct()
            .count()
        )
        cluster.save(update_fields=["error_count", "unique_traces", "updated_at"])


def _soft_delete_issues(issue_ids: list[uuid.UUID]) -> None:
    if not issue_ids:
        return
    now = timezone.now()
    cluster_ids = set(
        TraceScanIssue.no_workspace_objects.filter(id__in=issue_ids)
        .exclude(cluster_id__isnull=True)
        .values_list("cluster_id", flat=True)
    )
    cluster_ids.update(
        ErrorClusterTraces.no_workspace_objects.filter(
            scan_issue_id__in=issue_ids
        ).values_list("cluster_id", flat=True)
    )
    ErrorClusterTraces.no_workspace_objects.filter(scan_issue_id__in=issue_ids).update(
        deleted=True, deleted_at=now, updated_at=now
    )
    TraceScanIssue.no_workspace_objects.filter(id__in=issue_ids).update(
        deleted=True, deleted_at=now, updated_at=now
    )
    _recompute_cluster_counts(cluster_ids)


def _mark_stale(report_id: uuid.UUID | str) -> str:
    """Retire only this report's staged rows after re-checking its fence."""
    with transaction.atomic():
        report = TraceInvestigationReport.no_workspace_objects.select_for_update().get(
            id=report_id
        )
        job = TraceInvestigationJob.no_workspace_objects.select_for_update().get(
            id=report.job_id
        )
        projection = (
            TraceScanResult.no_workspace_objects.select_for_update()
            .filter(trace_id=job.trace_id)
            .first()
        )
        is_current = (
            report.active_projection_updated
            and job.generation == report.attempt.generation
            and projection is not None
            and projection.project_id == report.project_id
            and _projection_meta(projection).get("omega_report_id") == str(report.id)
        )
        if is_current:
            return "retry"
        _soft_delete_issues(_scan_issue_ids(report))
        report.grouping_status = TraceInvestigationGroupingStatus.STALE
        report.save(update_fields=["grouping_status", "updated_at"])
    return "stale"


def _mark_failed_preserving_projection(report_id: uuid.UUID | str, reason: str) -> str:
    """Mark a structurally unusable current report without declaring health."""
    with transaction.atomic():
        report = (
            TraceInvestigationReport.no_workspace_objects.select_for_update()
            .select_related("attempt")
            .get(id=report_id)
        )
        job = TraceInvestigationJob.no_workspace_objects.select_for_update().get(
            id=report.job_id
        )
        attempt = (
            TraceInvestigationAttempt.no_workspace_objects.select_for_update().get(
                id=report.attempt_id
            )
        )
        projection = (
            TraceScanResult.no_workspace_objects.select_for_update()
            .filter(trace_id=job.trace_id)
            .first()
        )
        is_current = (
            report.active_projection_updated
            and report.organization_id == job.organization_id
            and report.workspace_id == job.workspace_id
            and report.project_id == job.project_id
            and attempt.job_id == job.id
            and job.generation == attempt.generation
            and projection is not None
            and projection.project_id == report.project_id
            and _projection_meta(projection).get("omega_report_id") == str(report.id)
        )
        if not is_current:
            _soft_delete_issues(_scan_issue_ids(report))
            report.grouping_status = TraceInvestigationGroupingStatus.STALE
            report.save(update_fields=["grouping_status", "updated_at"])
            return "stale"

        report.grouping_status = TraceInvestigationGroupingStatus.FAILED
        report.save(update_fields=["grouping_status", "updated_at"])
        meta = _projection_meta(projection)
        meta.update(
            {
                "grouping_status": TraceInvestigationGroupingStatus.FAILED,
                "grouping_error": reason,
            }
        )
        projection.meta = meta
        projection.has_issues = TraceScanIssue.no_workspace_objects.filter(
            scan_result=projection
        ).exists()
        projection.save(update_fields=["meta", "has_issues", "updated_at"])
    return "failed"


def _stage_findings(report_id: uuid.UUID | str) -> tuple[str, list[uuid.UUID]]:
    with transaction.atomic():
        locked = _lock_current_report(report_id)
        report, projection = locked.report, locked.projection
        if report.grouping_status == TraceInvestigationGroupingStatus.COMPLETED:
            return str(report.project_id), _scan_issue_ids(report)
        if report.grouping_status != TraceInvestigationGroupingStatus.PENDING:
            raise _InvalidReport("report is not pending grouping")

        findings = report.result.get("findings")
        if not isinstance(findings, list) or not findings:
            raise _InvalidReport("pending report has no findings")
        finding_by_id = {
            str(finding.get("finding_id")): finding
            for finding in findings
            if isinstance(finding, dict) and finding.get("finding_id")
        }
        if len(finding_by_id) != len(findings):
            raise _InvalidReport("finding IDs must be nonempty and unique")
        occurrences = report.occurrences or []
        if len(occurrences) != len(findings):
            raise _InvalidReport("finding occurrences do not match the report")

        issue_ids: list[uuid.UUID] = []
        staged_occurrences: list[dict] = []
        for occurrence in occurrences:
            finding = finding_by_id.get(str(occurrence.get("finding_id")))
            if finding is None:
                raise _InvalidReport("finding occurrence does not match the report")
            try:
                occurrence_id = uuid.UUID(str(occurrence["occurrence_id"]))
            except (KeyError, TypeError, ValueError) as error:
                raise _InvalidReport("report has an invalid occurrence ID") from error
            issue_id = uuid.uuid5(report.id, str(occurrence_id))
            kind = finding.get("kind")
            statement = finding.get("statement")
            if (
                not isinstance(kind, str)
                or not kind
                or len(kind) > 100
                or not isinstance(statement, str)
                or not statement
            ):
                raise _InvalidReport("finding cannot be represented as a scan issue")

            existing = TraceScanIssue.all_objects.filter(id=issue_id).first()
            expected = {
                "scan_result_id": projection.id,
                "category": kind,
                "group": kind,
                "fix_layer": "",
                "confidence": ScanIssueConfidence.MEDIUM,
                "brief": statement,
            }
            if existing is None:
                TraceScanIssue.no_workspace_objects.create(id=issue_id, **expected)
            elif existing.deleted or any(
                getattr(existing, field) != value for field, value in expected.items()
            ):
                raise _InvalidReport(
                    "deterministic scan issue conflicts with stored data"
                )

            issue_ids.append(issue_id)
            staged_occurrences.append({**occurrence, "scan_issue_id": str(issue_id)})

        if report.occurrences != staged_occurrences:
            report.occurrences = staged_occurrences
            report.save(update_fields=["occurrences", "updated_at"])
        return str(report.project_id), issue_ids


@contextmanager
def _current_issue_write_fence(
    report_id: uuid.UUID | str, expected_issue_ids: frozenset[uuid.UUID], issue
):
    """Fence one scanner assignment against report/generation replacement.

    The existing assign/create functions mix PostgreSQL and ClickHouse writes.
    Keeping this transaction open across that one operation makes a CH failure
    roll back its PG issue/membership mutation, leaving the issue visibly
    unclustered for retry.  It deliberately does not encompass batch distilling,
    embedding, or centroid lookup.
    """
    issue_id = uuid.UUID(str(issue.issue_id))
    if issue_id not in expected_issue_ids:
        raise _InvalidReport("clustering attempted an issue outside the report")
    with transaction.atomic():
        locked = _lock_current_report(report_id)
        if locked.report.grouping_status != TraceInvestigationGroupingStatus.PENDING:
            raise _StaleReport("report is no longer pending grouping")
        staged = TraceScanIssue.no_workspace_objects.select_for_update().filter(
            id=issue_id,
            scan_result=locked.projection,
            cluster__isnull=True,
        )
        if not staged.exists():
            raise _StaleReport("staged issue is no longer assignable")
        yield


def _finalize_replacement(
    report_id: uuid.UUID | str, issue_ids: list[uuid.UUID]
) -> str:
    with transaction.atomic():
        locked = _lock_current_report(report_id)
        report, projection = locked.report, locked.projection
        if report.grouping_status == TraceInvestigationGroupingStatus.COMPLETED:
            return "completed"
        if report.grouping_status not in {
            TraceInvestigationGroupingStatus.PENDING,
            TraceInvestigationGroupingStatus.NOT_REQUIRED,
        }:
            raise _StaleReport("report is no longer publishable")

        current = list(
            TraceScanIssue.no_workspace_objects.select_for_update().filter(
                id__in=issue_ids,
                scan_result=projection,
            )
        )
        if len(current) != len(issue_ids) or any(
            issue.cluster_id is None for issue in current
        ):
            raise OmegaGroupingOperationalError(
                "not every staged finding has a durable cluster assignment"
            )

        old_issue_ids = list(
            TraceScanIssue.no_workspace_objects.filter(scan_result=projection)
            .exclude(id__in=issue_ids)
            .values_list("id", flat=True)
        )
        _soft_delete_issues(old_issue_ids)
        _recompute_cluster_counts(
            {issue.cluster_id for issue in current if issue.cluster_id is not None}
        )

        report.grouping_status = TraceInvestigationGroupingStatus.COMPLETED
        report.save(update_fields=["grouping_status", "updated_at"])
        meta = _projection_meta(projection)
        meta.pop("grouping_error", None)
        meta.update(
            {
                "grouping_status": TraceInvestigationGroupingStatus.COMPLETED,
                "grouping_adapter": "scanner_compatibility_v1",
            }
        )
        projection.meta = meta
        projection.has_issues = bool(issue_ids)
        projection.save(update_fields=["meta", "has_issues", "updated_at"])
    return "completed"


def publish_omega_investigation_grouping(report_id: uuid.UUID | str) -> str:
    """Publish one report into Error Feed, preserving the prior UI until ready."""
    try:
        with transaction.atomic():
            locked = _lock_current_report(report_id)
            report = locked.report
            if report.grouping_status == TraceInvestigationGroupingStatus.COMPLETED:
                return "completed"
            if report.grouping_status in {
                TraceInvestigationGroupingStatus.FAILED,
                TraceInvestigationGroupingStatus.STALE,
            }:
                return report.grouping_status
            findings = report.result.get("findings")
            execution_status = report.result.get("execution_status")
            outcome = report.result.get("outcome")
            if not isinstance(findings, list):
                raise _InvalidReport("report findings are not a list")
    except _StaleReport:
        return _mark_stale(report_id)
    except _InvalidReport as error:
        return _mark_failed_preserving_projection(report_id, str(error))

    # Any completed report with findings is useful input to grouping, including
    # a successful overall outcome with a recoverable/local finding. Only the
    # exact completed + successful + zero-findings shape can retire old issues
    # as a healthy replacement.
    if execution_status != "completed":
        return _mark_failed_preserving_projection(
            report_id, "investigation execution did not complete"
        )
    if not findings:
        if outcome != "success":
            return _mark_failed_preserving_projection(
                report_id, "zero-finding report did not establish success"
            )
        try:
            return _finalize_replacement(report_id, [])
        except _StaleReport:
            return _mark_stale(report_id)

    try:
        # TraceScanIssue has no unpublished state. Keep the scanner's PG writes
        # and the old->new replacement in one bounded transaction so a partial
        # report never appears in Feed. This does hold a DB transaction across
        # the compatibility algorithm's embedding and ClickHouse calls; the
        # command bounds reports and findings, and Atharva's replacement can
        # provide a first-class prepare/commit boundary later.
        with transaction.atomic():
            project_id, issue_ids = _stage_findings(report_id)
            summary = cluster_issues(
                project_id,
                issue_ids=[str(issue_id) for issue_id in issue_ids],
                write_fence=lambda issue: _current_issue_write_fence(
                    report_id, frozenset(issue_ids), issue
                ),
            )
            if summary.failed:
                raise OmegaGroupingOperationalError(
                    f"{summary.failed} staged finding(s) failed clustering"
                )
            return _finalize_replacement(report_id, issue_ids)
    except _StaleReport:
        return _mark_stale(report_id)
    except _InvalidReport as error:
        return _mark_failed_preserving_projection(report_id, str(error))
    except OmegaGroupingOperationalError:
        TraceInvestigationReport.no_workspace_objects.filter(
            id=report_id,
            grouping_status=TraceInvestigationGroupingStatus.PENDING,
        ).update(updated_at=timezone.now())
        raise
    except Exception as error:
        TraceInvestigationReport.no_workspace_objects.filter(
            id=report_id,
            grouping_status=TraceInvestigationGroupingStatus.PENDING,
        ).update(updated_at=timezone.now())
        raise OmegaGroupingOperationalError(
            "scanner compatibility clustering failed"
        ) from error


def publish_pending_omega_investigation_groups(
    *, report_limit: int = DEFAULT_REPORT_LIMIT
) -> dict[str, int]:
    """Drain a bounded page of the durable report queue."""
    if isinstance(report_limit, bool) or not 1 <= report_limit <= MAX_REPORT_LIMIT:
        raise ValueError(f"report_limit must be between 1 and {MAX_REPORT_LIMIT}")
    report_ids = list(
        TraceInvestigationReport.no_workspace_objects.filter(
            active_projection_updated=True,
            grouping_status__in=(
                TraceInvestigationGroupingStatus.PENDING,
                TraceInvestigationGroupingStatus.NOT_REQUIRED,
            ),
        )
        # A transiently failing oldest page is touched after each attempt and
        # rotates behind newer pending reports instead of starving them.
        .order_by("updated_at", "id")
        .values_list("id", flat=True)[:report_limit]
    )
    summary = {
        "selected": len(report_ids),
        "completed": 0,
        "stale": 0,
        "failed": 0,
        "deferred": 0,
    }
    for report_id in report_ids:
        try:
            status = publish_omega_investigation_grouping(report_id)
        except OmegaGroupingOperationalError:
            summary["deferred"] += 1
            logger.exception("omega_grouping_deferred", report_id=str(report_id))
            continue
        if status in summary:
            summary[status] += 1
    return summary
