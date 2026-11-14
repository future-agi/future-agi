"""Batched legacy-scan backfill (replacement for the tracer.0101 data step)."""

import uuid
from datetime import UTC, datetime
from io import StringIO
from unittest.mock import patch

import pytest
from django.core.management import CommandError, call_command
from django.db import IntegrityError, connection, transaction
from django.test.utils import CaptureQueriesContext

from tracer.management.commands import backfill_legacy_scans
from tracer.management.commands.backfill_legacy_scans import (
    SCAN_FIELDS,
    legacy_finding_id,
)
from tracer.models.trace_error_analysis import ErrorClusterTraces, TraceErrorGroup
from tracer.models.trace_investigation import (
    TraceInvestigationFinding,
    TraceInvestigationKeyMoment,
    TraceInvestigationReport,
    TraceInvestigationSource,
    TraceInvestigationTool,
)
from tracer.models.trace_scan import TraceScanIssue, TraceScanResult
from tracer.tests.test_grouping_snapshot import _saved_report

pytestmark = pytest.mark.django_db

CREATED = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
UPDATED = datetime(2026, 5, 2, 12, 0, tzinfo=UTC)

Report = TraceInvestigationReport.all_objects


def _scan(project, **fields):
    scan = TraceScanResult.all_objects.create(
        trace_id=fields.pop("trace_id", uuid.uuid4()),
        project=project,
        status="completed",
        has_issues=fields.pop("has_issues", False),
        scan_version="v7.2",
        **fields,
    )
    TraceScanResult.all_objects.filter(id=scan.id).update(
        created_at=CREATED, updated_at=UPDATED
    )
    return scan


def _issue(scan, **fields):
    issue = TraceScanIssue.all_objects.create(
        scan_result=scan,
        category="Language-only",
        group="Tool Failures",
        fix_layer="Tools",
        confidence="H",
        brief=fields.pop("brief", "tool returned an error"),
        **fields,
    )
    TraceScanIssue.all_objects.filter(id=issue.id).update(
        created_at=CREATED, updated_at=UPDATED
    )
    return issue


def _run(*args):
    out = StringIO()
    call_command("backfill_legacy_scans", *args, stdout=out, stderr=StringIO())
    return out.getvalue()


def _legacy_report(scan):
    return Report.get(
        source=TraceInvestigationSource.LEGACY_SCAN, source_record_id=scan.id
    )


def test_imports_scan_with_children_and_relinks_memberships(observe_project):
    cluster = TraceErrorGroup.objects.create(
        project=observe_project,
        cluster_id="LEGACY-1",
        error_type="scan_issue",
        issue_group="Tool Failures",
        title="Tool failures",
        error_count=1,
        total_events=1,
        unique_traces=1,
    )
    scan = _scan(
        observe_project,
        has_issues=True,
        key_moments=[
            {"kevinified": "k0", "verbatim": "v0", "span": "a" * 80, "is_failure": 1},
            {"kevinified": "k1", "role": "tool", "status": "error"},
        ],
        meta={
            "turn_count": 3,
            "tools_available": ["search", {"name": "refund", "status": "ok"}],
            "tools_called": [{"name": "refund", "status": 500}],
        },
    )
    first, second = sorted(
        [_issue(scan, cluster=cluster), _issue(scan, cluster=cluster)],
        key=lambda issue: issue.id,
    )
    TraceScanIssue.all_objects.filter(id=second.id).update(brief="second")
    membership = ErrorClusterTraces.all_objects.create(
        cluster=cluster, trace_id=scan.trace_id, scan_issue=first
    )

    _run()

    report = _legacy_report(scan)
    assert report.organization_id == observe_project.organization_id
    assert report.workspace_id == observe_project.workspace_id
    assert report.project_id == observe_project.id
    assert report.trace_id == scan.trace_id
    assert report.source_version == "v7.2"
    assert report.execution_status == "completed"
    assert report.recorded_at == CREATED
    assert (report.created_at, report.updated_at) == (CREATED, UPDATED)
    assert report.is_current is True
    assert report.has_issues is True
    assert report.turn_count == 3
    assert report.grouping_status == "completed"

    moments = list(
        TraceInvestigationKeyMoment.all_objects.filter(report=report).order_by(
            "ordinal"
        )
    )
    assert [(m.kevinified, m.span_id, m.is_failure) for m in moments] == [
        ("k0", "a" * 80, True),
        ("k1", None, False),
    ]
    assert (moments[1].role, moments[1].status) == ("tool", "error")

    tools = TraceInvestigationTool.all_objects.filter(report=report)
    assert sorted((t.role, t.ordinal, t.name, t.status) for t in tools) == [
        ("available", 0, "search", None),
        ("available", 1, "refund", "ok"),
        ("called", 0, "refund", "500"),
    ]

    findings = list(
        TraceInvestigationFinding.all_objects.filter(report=report).order_by("ordinal")
    )
    assert [f.id for f in findings] == [
        legacy_finding_id(first.id),
        legacy_finding_id(second.id),
    ]
    assert findings[0].finding_id == f"legacy:{first.id}"
    assert findings[0].source_finding_id == first.id
    assert findings[0].cluster_id == cluster.id
    assert (findings[0].group_label, findings[0].fix_layer) == (
        "Tool Failures",
        "Tools",
    )
    assert findings[1].statement == "second"
    assert (findings[0].created_at, findings[0].updated_at) == (CREATED, UPDATED)

    membership.refresh_from_db()
    assert membership.finding_id == findings[0].id


def test_rerun_and_partial_prior_import_are_skipped(observe_project):
    done, pending = sorted(
        [_scan(observe_project), _scan(observe_project)], key=lambda s: s.id
    )
    _run("--limit", "1")
    assert Report.filter(source_record_id=done.id).exists()
    assert not Report.filter(source_record_id=pending.id).exists()

    output = _run()
    assert "created=1 already_done=1" in output
    assert "created=0 already_done=2" in _run()
    assert Report.filter(source=TraceInvestigationSource.LEGACY_SCAN).count() == 2


def test_current_omega_report_keeps_the_current_slot(observe_project):
    omega = _saved_report(observe_project)
    scan = _scan(observe_project, trace_id=omega.trace_id)

    _run()

    omega.refresh_from_db()
    assert omega.is_current is True
    assert _legacy_report(scan).is_current is False


def test_deleted_current_report_is_demoted(observe_project):
    omega = _saved_report(observe_project)
    Report.filter(id=omega.id).update(deleted=True)
    scan = _scan(observe_project, trace_id=omega.trace_id)

    _run()

    omega.refresh_from_db()
    assert omega.is_current is False
    assert _legacy_report(scan).is_current is True


def test_deleted_scan_is_imported_as_deleted_and_not_current(observe_project):
    scan = _scan(observe_project)
    issue = _issue(scan)
    TraceScanResult.all_objects.filter(id=scan.id).update(
        deleted=True, deleted_at=UPDATED
    )
    TraceScanIssue.all_objects.filter(id=issue.id).update(deleted=True)
    membership = ErrorClusterTraces.all_objects.create(
        cluster=TraceErrorGroup.objects.create(
            project=observe_project, cluster_id="LEGACY-3", error_type="scan_issue"
        ),
        trace_id=scan.trace_id,
        scan_issue=issue,
        deleted=True,
    )

    _run()

    report = _legacy_report(scan)
    assert (report.deleted, report.deleted_at, report.is_current) == (
        True,
        UPDATED,
        False,
    )
    finding = TraceInvestigationFinding.all_objects.get(report=report)
    assert finding.deleted is True
    membership.refresh_from_db()
    assert membership.finding_id == finding.id


@pytest.mark.parametrize(
    "invalid",
    [
        {"meta": ["not", "an", "object"]},
        {"key_moments": {"not": "a list"}},
        {"key_moments": ["not an object"]},
        {"meta": {"tools_available": "search"}},
        {"meta": {"tools_called": [{"status": "ok"}]}},
    ],
)
def test_invalid_scan_is_skipped_and_reported_after_others_import(
    observe_project, invalid
):
    bad = _scan(observe_project, **invalid)
    good = _scan(observe_project, meta={"turn_count": True})

    with pytest.raises(CommandError, match="1 legacy scans were skipped"):
        _run("--batch-size", "1")

    assert not Report.filter(source_record_id=bad.id).exists()
    assert _legacy_report(good).turn_count is None


def test_resumes_after_cursor(observe_project):
    first, second, third = sorted(
        (_scan(observe_project) for _ in range(3)), key=lambda s: s.id
    )

    _run("--after", str(first.id), "--batch-size", "1")

    imported = set(
        Report.filter(source=TraceInvestigationSource.LEGACY_SCAN).values_list(
            "source_record_id", flat=True
        )
    )
    assert imported == {second.id, third.id}


def test_dry_run_counts_without_writing(observe_project):
    scan = _scan(observe_project)
    _issue(scan)
    ErrorClusterTraces.all_objects.create(
        cluster=TraceErrorGroup.objects.create(
            project=observe_project, cluster_id="LEGACY-2", error_type="scan_issue"
        ),
        trace_id=scan.trace_id,
        scan_issue=TraceScanIssue.all_objects.get(scan_result=scan),
    )

    output = _run("--dry-run")

    assert "total=1 remaining=1" in output
    assert "without a finding=1" in output
    assert not Report.filter(source=TraceInvestigationSource.LEGACY_SCAN).exists()


def test_batch_query_count_does_not_grow_with_batch_size(observe_project):
    def queries_for(count):
        for _ in range(count):
            scan = _scan(
                observe_project,
                key_moments=[{"kevinified": "k"}] * 3,
                meta={"tools_called": ["search"]},
            )
            _issue(scan)
            _issue(scan)
        with CaptureQueriesContext(connection) as ctx:
            _run("--batch-size", "100")
        return len(ctx.captured_queries)

    assert queries_for(2) == queries_for(20)


def test_integrity_error_retries_the_batch(observe_project):
    scan = _scan(observe_project)
    real = backfill_legacy_scans.backfill_batch
    calls = []

    def flaky(scans, using):
        calls.append(len(scans))
        if len(calls) == 1:
            raise IntegrityError("unique_current_trace_investigation")
        return real(scans, using)

    with patch.object(backfill_legacy_scans, "backfill_batch", flaky):
        _run()

    assert calls == [1, 1]
    assert _legacy_report(scan).is_current is True


def test_omega_report_published_mid_batch_is_not_demoted(observe_project):
    scan = _scan(observe_project)
    build_key_moments = backfill_legacy_scans._key_moments

    def publish_omega_then_build(report, source):
        # Runs after the batch read Omega's current reports, as a concurrent
        # publish committing mid-batch would.
        omega = _saved_report(observe_project)
        Report.filter(id=omega.id).update(trace_id=scan.trace_id)
        return build_key_moments(report, source)

    scans = list(TraceScanResult.all_objects.values(*SCAN_FIELDS))
    with (
        patch.object(backfill_legacy_scans, "_key_moments", publish_omega_then_build),
        pytest.raises(IntegrityError, match="unique_current_trace_investigation"),
        transaction.atomic(),
    ):
        backfill_legacy_scans.backfill_batch(scans, "default")


def test_persistent_conflict_rolls_back_the_batch_and_raises(observe_project):
    scan = _scan(observe_project, key_moments=[{"kevinified": "k"}])
    issue = _issue(scan)
    taken = TraceInvestigationFinding.all_objects.filter(
        report=_saved_report(observe_project)
    ).first()
    TraceInvestigationFinding.all_objects.filter(id=taken.id).update(
        source_finding_id=issue.id
    )

    with pytest.raises(IntegrityError):
        _run()

    assert not Report.filter(source=TraceInvestigationSource.LEGACY_SCAN).exists()
    assert not TraceInvestigationKeyMoment.all_objects.filter(
        report__source=TraceInvestigationSource.LEGACY_SCAN
    ).exists()
