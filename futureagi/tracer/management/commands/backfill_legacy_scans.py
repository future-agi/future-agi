"""Batched replacement for the data step of ``tracer.0101_backfill_legacy_scans``.

0101 imported one scan per transaction with a query per child row and was faked
in production after it stalled. This command writes the same canonical rows one
batch per transaction, plus ``has_issues``: 0102 backfilled that column before
these reports existed. Scans that already have a legacy report are skipped, so
it is safe to stop and re-run at any point.
"""

import time
import uuid
from dataclasses import dataclass, field

from django.core.management.base import BaseCommand, CommandError
from django.db import IntegrityError, transaction
from django.db.models import Exists, OuterRef, Subquery

from tracer.models.trace_error_analysis import ErrorClusterTraces
from tracer.models.trace_investigation import (
    TraceInvestigationFinding,
    TraceInvestigationGroupingStatus,
    TraceInvestigationKeyMoment,
    TraceInvestigationReport,
    TraceInvestigationSource,
    TraceInvestigationTool,
)
from tracer.models.trace_scan import TraceScanIssue, TraceScanResult

AUDIT_FIELDS = ["created_at", "updated_at", "deleted", "deleted_at"]
SCAN_FIELDS = (
    "id",
    "project_id",
    "project__organization_id",
    "project__workspace_id",
    "trace_id",
    "status",
    "has_issues",
    "key_moments",
    "meta",
    "scan_version",
    "error_message",
    *AUDIT_FIELDS,
)
TOOL_ROLES = (("available", "tools_available"), ("called", "tools_called"))
MAX_BATCH_ATTEMPTS = 3

# Base managers: the default ones hide soft-deleted rows and filter by the
# ambient workspace, and 0101 migrated every row.
Scan = TraceScanResult._base_manager
Issue = TraceScanIssue._base_manager
Report = TraceInvestigationReport._base_manager
Membership = ErrorClusterTraces._base_manager


class InvalidLegacyScan(ValueError):
    pass


@dataclass
class BatchResult:
    scanned: int = 0
    created: int = 0
    already_done: int = 0
    invalid: dict[uuid.UUID, str] = field(default_factory=dict)
    last_id: uuid.UUID | None = None


def legacy_finding_id(issue_id: uuid.UUID) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"legacy-scan-issue:{issue_id}")


def _turn_count(meta: dict) -> int | None:
    value = meta.get("turn_count")
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _key_moments(report, scan) -> list[TraceInvestigationKeyMoment]:
    moments = scan["key_moments"] or []
    if not isinstance(moments, list):
        raise InvalidLegacyScan("key_moments is not a list")
    rows = []
    for ordinal, item in enumerate(moments):
        if not isinstance(item, dict):
            raise InvalidLegacyScan(f"key moment {ordinal} is not an object")
        rows.append(
            TraceInvestigationKeyMoment(
                report=report,
                ordinal=ordinal,
                kevinified=item.get("kevinified") or "",
                verbatim=item.get("verbatim") or "",
                role=item.get("role") or "",
                span_id=item.get("span") or None,
                status=item.get("status") or "",
                is_failure=bool(item.get("is_failure")),
            )
        )
    return rows


def _tools(report, meta: dict) -> list[TraceInvestigationTool]:
    rows = []
    for role, key in TOOL_ROLES:
        items = meta.get(key) or []
        if not isinstance(items, list):
            raise InvalidLegacyScan(f"{key} is not a list")
        for ordinal, item in enumerate(items):
            name = item.get("name") if isinstance(item, dict) else item
            status = item.get("status") if isinstance(item, dict) else None
            if not name:
                raise InvalidLegacyScan(f"{key}[{ordinal}] has no name")
            rows.append(
                TraceInvestigationTool(
                    report=report,
                    role=role,
                    ordinal=ordinal,
                    name=str(name),
                    status=str(status) if status is not None else None,
                )
            )
    return rows


def _findings(report, issues) -> list[TraceInvestigationFinding]:
    return [
        TraceInvestigationFinding(
            id=legacy_finding_id(issue["id"]),
            report=report,
            finding_id=f"legacy:{issue['id']}",
            source_finding_id=issue["id"],
            ordinal=ordinal,
            statement=issue["brief"],
            category=issue["category"],
            group_label=issue["group"],
            fix_layer=issue["fix_layer"],
            confidence=issue["confidence"],
            cluster_id=issue["cluster_id"],
        )
        for ordinal, issue in enumerate(issues)
    ]


def _restore_audit_fields(model, objs, sources, using):
    """bulk_create stamps auto_now/auto_now_add; keep the legacy row's values."""
    for obj, source in zip(objs, sources, strict=True):
        for name in AUDIT_FIELDS:
            setattr(obj, name, source[name])
    model._base_manager.using(using).bulk_update(objs, AUDIT_FIELDS)


def backfill_batch(scans: list[dict], using: str) -> BatchResult:
    """Import one page of legacy scans (ordered by id) in a single transaction."""
    result = BatchResult(scanned=len(scans), last_id=scans[-1]["id"])
    done = set(
        Report.using(using)
        .filter(
            source=TraceInvestigationSource.LEGACY_SCAN,
            source_record_id__in=[scan["id"] for scan in scans],
        )
        .values_list("source_record_id", flat=True)
    )
    pending = [scan for scan in scans if scan["id"] not in done]
    result.already_done = len(scans) - len(pending)
    if not pending:
        return result

    omega_current = set(
        Report.using(using)
        .filter(
            source=TraceInvestigationSource.OMEGA,
            project_id__in={scan["project_id"] for scan in pending},
            trace_id__in={scan["trace_id"] for scan in pending},
            is_current=True,
            deleted=False,
        )
        .values_list("project_id", "trace_id")
    )
    issues_by_scan: dict[uuid.UUID, list[dict]] = {}
    for issue in (
        Issue.using(using)
        .filter(scan_result_id__in=[scan["id"] for scan in pending])
        .order_by("scan_result_id", "id")
        .values()
    ):
        issues_by_scan.setdefault(issue["scan_result_id"], []).append(issue)

    reports, report_sources = [], []
    key_moments, tools, findings, finding_sources = [], [], [], []
    for scan in pending:
        meta = scan["meta"] or {}
        pair = (scan["project_id"], scan["trace_id"])
        report = TraceInvestigationReport(
            id=uuid.uuid4(),
            organization_id=scan["project__organization_id"],
            workspace_id=scan["project__workspace_id"],
            project_id=scan["project_id"],
            trace_id=scan["trace_id"],
            source=TraceInvestigationSource.LEGACY_SCAN,
            source_record_id=scan["id"],
            source_version=scan["scan_version"],
            recorded_at=scan["created_at"],
            is_current=not scan["deleted"] and pair not in omega_current,
            execution_status=scan["status"],
            outcome=None,
            error_message=scan["error_message"],
            has_issues=scan["has_issues"],
            grouping_status=TraceInvestigationGroupingStatus.COMPLETED,
        )
        try:
            if not isinstance(meta, dict):
                raise InvalidLegacyScan("meta is not an object")
            report.turn_count = _turn_count(meta)
            scan_moments = _key_moments(report, scan)
            scan_tools = _tools(report, meta)
        except InvalidLegacyScan as exc:
            result.invalid[scan["id"]] = str(exc)
            continue
        scan_issues = issues_by_scan.get(scan["id"], [])
        reports.append(report)
        report_sources.append(scan)
        key_moments += scan_moments
        tools += scan_tools
        findings += _findings(report, scan_issues)
        finding_sources += scan_issues

    current = {(r.project_id, r.trace_id) for r in reports if r.is_current}
    if current:
        # A live Omega report is never demoted: none existed for these traces at
        # read time, so one appearing now must win via the unique-current retry.
        stale = [
            report_id
            for report_id, project_id, trace_id in Report.using(using)
            .filter(
                project_id__in={project for project, _ in current},
                trace_id__in={trace for _, trace in current},
                is_current=True,
            )
            .exclude(source=TraceInvestigationSource.OMEGA, deleted=False)
            .values_list("id", "project_id", "trace_id")
            if (project_id, trace_id) in current
        ]
        Report.using(using).filter(id__in=stale).update(is_current=False)

    TraceInvestigationReport.objects.using(using).bulk_create(reports)
    _restore_audit_fields(TraceInvestigationReport, reports, report_sources, using)
    TraceInvestigationKeyMoment.objects.using(using).bulk_create(key_moments)
    TraceInvestigationTool.objects.using(using).bulk_create(tools)
    TraceInvestigationFinding.objects.using(using).bulk_create(findings)
    _restore_audit_fields(TraceInvestigationFinding, findings, finding_sources, using)
    Membership.using(using).filter(
        scan_issue_id__in=[issue["id"] for issue in finding_sources]
    ).update(
        finding_id=Subquery(
            TraceInvestigationFinding._base_manager.filter(
                source_finding_id=OuterRef("scan_issue_id")
            ).values("id")[:1]
        )
    )
    result.created = len(reports)
    return result


class Command(BaseCommand):
    help = (
        "Backfill legacy trace scans into the Error Feed investigation tables in "
        "batches (the data step of tracer.0101). Idempotent and resumable."
    )

    def add_arguments(self, parser):
        parser.add_argument("--batch-size", type=int, default=500)
        parser.add_argument(
            "--after",
            type=uuid.UUID,
            help="Resume after this scan id (printed with every batch).",
        )
        parser.add_argument(
            "--limit", type=int, help="Stop after examining this many scans."
        )
        parser.add_argument(
            "--sleep",
            type=float,
            default=0.0,
            help="Seconds to pause between batches to shed database load.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report how many scans still need a legacy report and exit.",
        )
        parser.add_argument("--database", default="default")

    def handle(self, *args, **opts):
        using = opts["database"]
        if opts["batch_size"] < 1:
            raise CommandError("--batch-size must be positive")
        if opts["dry_run"]:
            self._report_remaining(using)
            return

        cursor, limit = opts["after"], opts["limit"]
        totals = BatchResult()
        started = time.monotonic()
        batch_number = 0
        while limit is None or totals.scanned < limit:
            size = opts["batch_size"]
            if limit is not None:
                size = min(size, limit - totals.scanned)
            batch = self._run_batch(cursor, size, using)
            if batch is None:
                break
            batch_number += 1
            cursor = batch.last_id
            totals.scanned += batch.scanned
            totals.created += batch.created
            totals.already_done += batch.already_done
            totals.invalid.update(batch.invalid)
            elapsed = time.monotonic() - started
            self.stdout.write(
                f"batch {batch_number}: scanned={totals.scanned} "
                f"created={totals.created} already_done={totals.already_done} "
                f"invalid={len(totals.invalid)} last_id={cursor} "
                f"({totals.scanned / elapsed:.0f} scans/s)"
            )
            if opts["sleep"]:
                time.sleep(opts["sleep"])

        self.stdout.write(
            f"done: scanned={totals.scanned} created={totals.created} "
            f"already_done={totals.already_done} invalid={len(totals.invalid)} "
            f"last_id={cursor}"
        )
        if totals.invalid:
            for scan_id, reason in totals.invalid.items():
                self.stderr.write(f"skipped invalid legacy scan {scan_id}: {reason}")
            raise CommandError(
                f"{len(totals.invalid)} legacy scans were skipped as invalid; "
                "every other scan was imported."
            )
        self.stdout.write(self.style.SUCCESS("Legacy scan backfill complete."))

    def _run_batch(self, cursor, size, using) -> BatchResult | None:
        for attempt in range(1, MAX_BATCH_ATTEMPTS + 1):
            try:
                with transaction.atomic(using=using):
                    page = Scan.using(using).order_by("id")
                    if cursor is not None:
                        page = page.filter(id__gt=cursor)
                    scans = list(page.values(*SCAN_FIELDS)[:size])
                    if not scans:
                        return None
                    return backfill_batch(scans, using)
            except IntegrityError as exc:
                # A live Omega publish can take the current slot for a trace
                # after our read; the insert then conflicts and a re-read
                # marks the legacy report as not current.
                if attempt == MAX_BATCH_ATTEMPTS:
                    raise
                self.stderr.write(
                    f"batch after {cursor} hit {exc!r}; retrying ({attempt})"
                )
        return None

    def _report_remaining(self, using):
        imported = Report.using(using).filter(
            source=TraceInvestigationSource.LEGACY_SCAN,
            source_record_id=OuterRef("id"),
        )
        total = Scan.using(using).count()
        remaining = Scan.using(using).filter(~Exists(imported)).count()
        unlinked = (
            Membership.using(using)
            .filter(scan_issue__isnull=False, finding__isnull=True)
            .count()
        )
        self.stdout.write(
            f"legacy scans: total={total} remaining={remaining}; "
            f"cluster memberships without a finding={unlinked}"
        )
