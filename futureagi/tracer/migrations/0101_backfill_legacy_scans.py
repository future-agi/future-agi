"""Move legacy scanner history into the canonical Error Feed tables.

This migration is intentionally restartable. Each scan is committed separately;
the source identity prevents duplicate imports after an interrupted deployment.
"""

import uuid

from django.db import migrations, models, transaction


def backfill_legacy_scans(apps, schema_editor):
    alias = schema_editor.connection.alias
    Scan = apps.get_model("tracer", "TraceScanResult")
    Issue = apps.get_model("tracer", "TraceScanIssue")
    Report = apps.get_model("tracer", "TraceInvestigationReport")
    Finding = apps.get_model("tracer", "TraceInvestigationFinding")
    KeyMoment = apps.get_model("tracer", "TraceInvestigationKeyMoment")
    Tool = apps.get_model("tracer", "TraceInvestigationTool")
    Membership = apps.get_model("tracer", "ErrorClusterTraces")

    cursor_id = None
    while True:
        page = Scan.objects.using(alias).select_related("project").order_by("id")
        if cursor_id is not None:
            page = page.filter(id__gt=cursor_id)
        scans = list(page[:250])
        if not scans:
            break
        for scan in scans:
            cursor_id = scan.id
            with transaction.atomic(using=alias):
                if Report.objects.using(alias).filter(
                    source="legacy_scan", source_record_id=scan.id
                ).exists():
                    continue
                current = (
                    not scan.deleted
                    and not Report.objects.using(alias).filter(
                        project_id=scan.project_id,
                        trace_id=scan.trace_id,
                        source="omega",
                        is_current=True,
                        deleted=False,
                    ).exists()
                )
                if current:
                    Report.objects.using(alias).filter(
                        project_id=scan.project_id,
                        trace_id=scan.trace_id,
                        is_current=True,
                    ).update(is_current=False)
                meta = scan.meta or {}
                if not isinstance(meta, dict):
                    raise ValueError(f"Invalid legacy scan metadata: {scan.id}")
                turn_count = meta.get("turn_count")
                if isinstance(turn_count, bool) or not isinstance(turn_count, int) or turn_count < 0:
                    turn_count = None
                report = Report.objects.using(alias).create(
                    organization_id=scan.project.organization_id,
                    workspace_id=scan.project.workspace_id,
                    project_id=scan.project_id,
                    trace_id=scan.trace_id,
                    source="legacy_scan",
                    source_record_id=scan.id,
                    source_version=scan.scan_version,
                    recorded_at=scan.created_at,
                    is_current=current,
                    execution_status=scan.status,
                    outcome=None,
                    error_message=scan.error_message,
                    turn_count=turn_count,
                    grouping_status="completed",
                )
                moments = scan.key_moments or []
                if not isinstance(moments, list):
                    raise ValueError(f"Invalid legacy key moments: {scan.id}")
                for ordinal, item in enumerate(moments):
                    if not isinstance(item, dict):
                        raise ValueError(f"Invalid legacy key moment {ordinal}: {scan.id}")
                    KeyMoment.objects.using(alias).create(
                        report=report,
                        ordinal=ordinal,
                        kevinified=item.get("kevinified") or "",
                        verbatim=item.get("verbatim") or "",
                        role=item.get("role") or "",
                        span_id=item.get("span") or None,
                        status=item.get("status") or "",
                        is_failure=bool(item.get("is_failure")),
                    )
                for role, key in (("available", "tools_available"), ("called", "tools_called")):
                    items = meta.get(key) or []
                    if not isinstance(items, list):
                        raise ValueError(f"Invalid legacy {key}: {scan.id}")
                    for ordinal, item in enumerate(items):
                        name = item.get("name") if isinstance(item, dict) else item
                        status = item.get("status") if isinstance(item, dict) else None
                        if not name:
                            raise ValueError(f"Missing legacy tool name {ordinal}: {scan.id}")
                        Tool.objects.using(alias).create(
                            report=report,
                            role=role,
                            ordinal=ordinal,
                            name=str(name),
                            status=str(status) if status is not None else None,
                        )
                issues = Issue.objects.using(alias).filter(scan_result_id=scan.id).order_by("id")
                for ordinal, issue in enumerate(issues):
                    finding_id = uuid.uuid5(uuid.NAMESPACE_URL, f"legacy-scan-issue:{issue.id}")
                    Finding.objects.using(alias).create(
                        id=finding_id,
                        report=report,
                        finding_id=f"legacy:{issue.id}",
                        source_finding_id=issue.id,
                        ordinal=ordinal,
                        statement=issue.brief,
                        category=issue.category,
                        group_label=issue.group,
                        fix_layer=issue.fix_layer,
                        confidence=issue.confidence,
                        cluster_id=issue.cluster_id,
                    )
                    Membership.objects.using(alias).filter(scan_issue_id=issue.id).update(
                        finding_id=finding_id
                    )
                    Finding.objects.using(alias).filter(id=finding_id).update(
                        created_at=issue.created_at,
                        updated_at=issue.updated_at,
                        deleted=issue.deleted,
                        deleted_at=issue.deleted_at,
                    )
                Report.objects.using(alias).filter(id=report.id).update(
                    created_at=scan.created_at,
                    updated_at=scan.updated_at,
                    deleted=scan.deleted,
                    deleted_at=scan.deleted_at,
                )


class Migration(migrations.Migration):
    atomic = False
    dependencies = [("tracer", "0100_reconcile_pre_release_schema")]
    operations = [
        migrations.AlterField(
            model_name="traceinvestigationkeymoment",
            name="span_id",
            field=models.TextField(null=True, blank=True),
        ),
        migrations.RunPython(backfill_legacy_scans, migrations.RunPython.noop),
    ]
