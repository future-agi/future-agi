"""Reconcile local databases that ran the unshipped Omega prototype.

Production databases have never had the prototype tables, so this is a no-op
after the canonical 0099 migration. Existing local installations retain their
reports and receive the normalized schema before the legacy scan backfill.
"""

import json
import uuid

from django.db import migrations


def reconcile(apps, schema_editor):
    connection = schema_editor.connection
    quote = schema_editor.quote_name
    Report = apps.get_model("tracer", "TraceInvestigationReport")
    Job = apps.get_model("tracer", "TraceInvestigationJob")
    Membership = apps.get_model("tracer", "ErrorClusterTraces")
    table = Report._meta.db_table
    with connection.cursor() as cursor:
        columns = {col.name for col in connection.introspection.get_table_description(cursor, table)}
    if "result" not in columns:
        return

    # The old report JSON remains in place as an audit copy. New writes use
    # normalized rows; no old report or report identity is discarded.
    for field in Report._meta.local_fields:
        if field.column not in columns:
            schema_editor.execute(
                f"ALTER TABLE {quote(table)} ADD COLUMN {quote(field.column)} "
                f"{field.db_type(connection)}"
            )
    schema_editor.execute(
        f"ALTER TABLE {quote(table)} ALTER COLUMN job_id DROP NOT NULL, "
        "ALTER COLUMN attempt_id DROP NOT NULL, "
        "ALTER COLUMN idempotency_key DROP NOT NULL, "
        "ALTER COLUMN result_digest DROP NOT NULL, "
        "ALTER COLUMN result SET DEFAULT '{}'::jsonb, "
        "ALTER COLUMN occurrences SET DEFAULT '[]'::jsonb, "
        "ALTER COLUMN active_projection_updated SET DEFAULT false"
    )
    schema_editor.execute(
        "ALTER TABLE tracer_trace_scan_config ALTER COLUMN engine SET DEFAULT 'omega'"
    )
    with connection.cursor() as cursor:
        job_columns = {col.name for col in connection.introspection.get_table_description(cursor, Job._meta.db_table)}
    if "current_report_id" not in job_columns:
        schema_editor.add_field(Job, Job._meta.get_field("current_report"))

    for name in (
        "TraceInvestigationRequirementCheck",
        "TraceInvestigationFinding",
        "TraceInvestigationKeyMoment",
        "TraceInvestigationTool",
        "TraceInvestigationEvidenceReceipt",
        "TraceInvestigationAttribution",
        "TraceInvestigationFindingEvidence",
        "TraceInvestigationAttributionEvidence",
        "TraceInvestigationRequirementEvidence",
        "TraceInvestigationVerificationReceipt",
        "TraceInvestigationGatewayCall",
    ):
        model = apps.get_model("tracer", name)
        if model._meta.db_table not in connection.introspection.table_names():
            schema_editor.create_model(model)
    with connection.cursor() as cursor:
        member_columns = {col.name for col in connection.introspection.get_table_description(cursor, Membership._meta.db_table)}
    if "finding_id" not in member_columns:
        schema_editor.add_field(Membership, Membership._meta.get_field("finding"))

    schema_editor.execute(
        """UPDATE tracer_trace_investigation_report AS report
           SET trace_id = (report.result->>'trace_id')::uuid,
               source = 'omega',
               recorded_at = report.created_at,
               is_current = false,
               contract_version = report.result->>'contract_version',
               evidence_digest = report.result->>'evidence_digest',
               execution_status = report.result->>'execution_status',
               outcome = report.result->>'outcome',
               coverage_scope = report.result->'coverage'->>'scope',
               observed_span_count = (report.result->'coverage'->>'observed_span_count')::integer,
               read_complete = (report.result->'coverage'->>'read_complete')::boolean,
               future_arrivals_known = (report.result->'coverage'->>'future_arrivals_known')::boolean,
               model_calls = (report.result->'usage'->>'model_calls')::integer,
               input_tokens = (report.result->'usage'->>'input_tokens')::bigint,
               output_tokens = (report.result->'usage'->>'output_tokens')::bigint,
               cost_usd = (report.result->'usage'->>'cost_usd')::numeric,
               cost_status = report.result->'usage'->>'cost_status'
        """
    )
    schema_editor.execute(
        """UPDATE tracer_trace_investigation_job AS job
           SET current_report_id = (
               SELECT report.id
               FROM tracer_trace_investigation_report AS report
               WHERE report.job_id = job.id AND report.active_projection_updated
               ORDER BY report.created_at DESC, report.id DESC LIMIT 1
           )"""
    )
    schema_editor.execute(
        """UPDATE tracer_trace_investigation_report AS report
           SET is_current = true FROM tracer_trace_investigation_job AS job
           WHERE job.current_report_id = report.id"""
    )
    schema_editor.execute(
        "ALTER TABLE tracer_trace_investigation_report "
        "ALTER COLUMN trace_id SET NOT NULL, ALTER COLUMN source SET NOT NULL, "
        "ALTER COLUMN recorded_at SET NOT NULL, ALTER COLUMN is_current SET NOT NULL, "
        "ALTER COLUMN execution_status SET NOT NULL"
    )
    schema_editor.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS unique_current_trace_investigation "
        "ON tracer_trace_investigation_report (project_id, trace_id) "
        "WHERE is_current AND NOT deleted"
    )
    schema_editor.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS unique_inv_source_record "
        "ON tracer_trace_investigation_report (source, source_record_id) "
        "WHERE source_record_id IS NOT NULL"
    )

    Check = apps.get_model("tracer", "TraceInvestigationRequirementCheck")
    Finding = apps.get_model("tracer", "TraceInvestigationFinding")
    Evidence = apps.get_model("tracer", "TraceInvestigationEvidenceReceipt")
    RequirementEvidence = apps.get_model("tracer", "TraceInvestigationRequirementEvidence")
    FindingEvidence = apps.get_model("tracer", "TraceInvestigationFindingEvidence")
    Attribution = apps.get_model("tracer", "TraceInvestigationAttribution")
    AttributionEvidence = apps.get_model("tracer", "TraceInvestigationAttributionEvidence")
    Verification = apps.get_model("tracer", "TraceInvestigationVerificationReceipt")
    GatewayCall = apps.get_model("tracer", "TraceInvestigationGatewayCall")
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT id, result FROM tracer_trace_investigation_report "
            "WHERE source = 'omega' AND source_version IS NULL ORDER BY id"
        )
        old_results = cursor.fetchall()
    for report_id, result in old_results:
        if isinstance(result, str):
            result = json.loads(result)
        report = Report.objects.using(connection.alias).get(id=report_id)
        # A failed non-atomic migration may have written only some children.
        # All five roots cascade to their citation/attribution junctions.
        Check.objects.using(connection.alias).filter(report_id=report.id).delete()
        Finding.objects.using(connection.alias).filter(report_id=report.id).delete()
        Evidence.objects.using(connection.alias).filter(report_id=report.id).delete()
        Verification.objects.using(connection.alias).filter(report_id=report.id).delete()
        GatewayCall.objects.using(connection.alias).filter(report_id=report.id).delete()
        checks = {}
        for ordinal, row in enumerate(result["requirement_checks"]):
            checks[row["requirement_id"]] = Check.objects.using(connection.alias).create(
                report_id=report.id, requirement_id=row["requirement_id"], ordinal=ordinal,
                requirement=row["requirement"], status=row["status"]
            )
        evidence = {}
        for ordinal, row in enumerate(result["evidence_receipts"]):
            evidence[row["evidence_id"]] = Evidence.objects.using(connection.alias).create(
                report_id=report.id, evidence_id=row["evidence_id"], ordinal=ordinal,
                span_id=row["span_id"], parent_span_id=row.get("parent_span_id"),
                excerpt=row["excerpt"], end_time=row.get("end_time")
            )
        for row in result["requirement_checks"]:
            for evidence_id in row["evidence_ids"]:
                RequirementEvidence.objects.using(connection.alias).create(
                    requirement=checks[row["requirement_id"]], evidence=evidence[evidence_id]
                )
        for ordinal, row in enumerate(result["findings"]):
            finding = Finding.objects.using(connection.alias).create(
                id=uuid.uuid5(report.id, row["finding_id"]), report_id=report.id,
                finding_id=row["finding_id"], ordinal=ordinal, kind=row["kind"],
                statement=row["statement"], recovery=row["recovery"],
                requirement=checks.get(row.get("requirement_id"))
            )
            for evidence_id in row["evidence_ids"]:
                FindingEvidence.objects.using(connection.alias).create(
                    finding=finding, evidence=evidence[evidence_id]
                )
            for role in ("origin", "decisive", "symptom"):
                value = row["attribution"][role]
                attribution = Attribution.objects.using(connection.alias).create(
                    finding=finding, role=role, status=value["status"],
                    span_id=value.get("span_id")
                )
                for evidence_id in value["evidence_ids"]:
                    AttributionEvidence.objects.using(connection.alias).create(
                        attribution=attribution, evidence=evidence[evidence_id]
                    )
        for ordinal, row in enumerate(result["verification_receipts"]):
            Verification.objects.using(connection.alias).create(
                report_id=report.id, receipt_id=row["receipt_id"], ordinal=ordinal,
                executed=row["executed"]
            )
        for ordinal, row in enumerate(result["gateway_accounting"]):
            raw = row.get("raw") or {}
            usage = raw.get("usage") or {}
            GatewayCall.objects.using(connection.alias).create(
                report_id=report.id, ordinal=ordinal, request_id=row.get("request_id"),
                model_used=row["model_used"], cost_usd=row.get("cost"),
                input_tokens=row.get("input_tokens", usage.get("prompt_tokens")),
                output_tokens=row.get("output_tokens", usage.get("completion_tokens")),
                total_tokens=usage.get("total_tokens"),
                cached_input_tokens=(usage.get("prompt_tokens_details") or {}).get("cached_tokens"),
                reasoning_output_tokens=(usage.get("completion_tokens_details") or {}).get("reasoning_tokens"),
                cache_status=raw.get("cache_status"), status=raw.get("status"),
                http_status=raw.get("http_status"), retry_of=raw.get("retry_of"),
                retry_delay_ms=raw.get("retry_delay_ms")
            )
        Report.objects.using(connection.alias).filter(id=report.id).update(
            source_version="pre-release-omega"
        )


class Migration(migrations.Migration):
    atomic = False
    dependencies = [("tracer", "0099_trace_investigation_control")]
    operations = [migrations.RunPython(reconcile, migrations.RunPython.noop)]
