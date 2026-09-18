"""Preserve legacy pass/fail flags and align prototype report constraints."""

from django.db import migrations, models


def backfill_has_issues(apps, schema_editor):
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(
            """UPDATE tracer_trace_investigation_report AS report
               SET has_issues = scan.has_issues
               FROM tracer_trace_scan_result AS scan
               WHERE report.source = 'legacy_scan'
                 AND report.source_record_id = scan.id
                 AND report.has_issues IS NULL"""
        )
        cursor.execute(
            """UPDATE tracer_trace_investigation_report AS report
               SET has_issues = EXISTS (
                   SELECT 1 FROM tracer_trace_investigation_finding AS finding
                   WHERE finding.report_id = report.id AND NOT finding.deleted
               )
               WHERE report.source = 'omega' AND report.has_issues IS NULL"""
        )


def ensure_source_constraint(apps, schema_editor):
    Report = apps.get_model("tracer", "TraceInvestigationReport")
    constraint = next(
        item
        for item in Report._meta.constraints
        if item.name == "valid_inv_report_source_fields"
    )
    with schema_editor.connection.cursor() as cursor:
        existing = schema_editor.connection.introspection.get_constraints(
            cursor, Report._meta.db_table
        )
    if constraint.name not in existing:
        schema_editor.add_constraint(Report, constraint)


class Migration(migrations.Migration):
    atomic = False
    dependencies = [("tracer", "0101_backfill_legacy_scans")]
    operations = [
        migrations.AddField(
            model_name="traceinvestigationreport",
            name="has_issues",
            field=models.BooleanField(blank=True, null=True),
        ),
        migrations.RunPython(backfill_has_issues, migrations.RunPython.noop),
        migrations.RunPython(ensure_source_constraint, migrations.RunPython.noop),
    ]
