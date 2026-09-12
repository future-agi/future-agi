"""Explicitly acknowledged current-stack verification; never executes supplied code."""

import os

from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Inspect or enable only the marked synthetic Omega E2E fixture."
    requires_system_checks = []

    def add_arguments(self, parser):
        parser.add_argument("operation", choices=["preflight", "activate", "state"])

    def handle(self, *args, **options):
        if os.environ.get("OMEGA_E2E_RUN_ACK") != "CREATE_ONE_SYNTHETIC_OMEGA_PROJECT":
            raise CommandError(
                "Explicit synthetic current-stack E2E acknowledgement required"
            )
        {"preflight": preflight, "activate": activate, "state": state}[
            options["operation"]
        ]()


def preflight():
    import json
    import os

    from django.conf import settings

    from accounts.models import Organization, OrgApiKey, User
    from accounts.models.workspace import Workspace
    from tracer.models.trace_investigation import (
        TraceInvestigationDelivery,
        TraceInvestigationJob,
        TraceInvestigationReport,
        TraceInvestigationUsageReceipt,
    )
    from tracer.models.trace_scan import TraceScanConfig

    organization_id = os.environ["OMEGA_E2E_ORGANIZATION_ID"]
    workspace_id = os.environ["OMEGA_E2E_WORKSPACE_ID"]
    api_key = (
        OrgApiKey.no_workspace_objects.filter(
            api_key=os.environ["OMEGA_E2E_API_KEY"],
            organization_id=organization_id,
            workspace_id=workspace_id,
            enabled=True,
            deleted=False,
        )
        .select_related("user", "workspace")
        .first()
    )
    api_user = None
    if api_key is not None:
        api_user = (
            User.objects.filter(organization_id=organization_id, is_active=True)
            .order_by("created_at")
            .first()
            if api_key.type == "system"
            else api_key.user
        )
    for model in (
        TraceScanConfig,
        TraceInvestigationDelivery,
        TraceInvestigationJob,
        TraceInvestigationReport,
        TraceInvestigationUsageReceipt,
    ):
        model.no_workspace_objects.values_list("pk", flat=True).first()
    state = {
        "billing_emission_enabled": settings.ERROR_FEED_OMEGA_BILLING_EMIT_ENABLED,
        "omega_enabled": settings.ERROR_FEED_OMEGA_ENABLED,
        "omega_tables_ready": True,
        "api_key_scoped": api_key is not None,
        "api_user_can_write": bool(
            api_user is not None
            and api_user.is_active
            and api_user.can_write_to_workspace(api_key.workspace)
        ),
        "organization_exists": Organization.objects.filter(id=organization_id).exists(),
        "workspace_exists": Workspace.no_workspace_objects.filter(
            id=workspace_id,
            organization_id=organization_id,
            is_active=True,
            deleted=False,
        ).exists(),
    }
    print("OMEGA_E2E_STATE=" + json.dumps(state, sort_keys=True))


def activate():
    import json
    import os

    from django.conf import settings
    from django.db import transaction

    from accounts.models import Organization
    from accounts.models.workspace import Workspace
    from tracer.models.project import Project
    from tracer.models.trace_scan import TraceScanConfig, TraceScanEngine

    project_id = os.environ["OMEGA_E2E_PROJECT_ID"]
    organization_id = os.environ["OMEGA_E2E_ORGANIZATION_ID"]
    workspace_id = os.environ["OMEGA_E2E_WORKSPACE_ID"]
    project_name = os.environ["OMEGA_E2E_PROJECT_NAME"]
    engine_version = os.environ["OMEGA_E2E_ENGINE_VERSION"]

    if not settings.ERROR_FEED_OMEGA_ENABLED:
        raise RuntimeError("ERROR_FEED_OMEGA_ENABLED is false")
    if settings.ERROR_FEED_OMEGA_BILLING_EMIT_ENABLED:
        raise RuntimeError("billing emission must be false for this check")
    if not Organization.objects.filter(id=organization_id).exists():
        raise RuntimeError("organization scope does not exist")
    if not Workspace.no_workspace_objects.filter(
        id=workspace_id,
        organization_id=organization_id,
        is_active=True,
        deleted=False,
    ).exists():
        raise RuntimeError("active workspace scope does not exist")

    with transaction.atomic():
        project = Project.no_workspace_objects.select_for_update().get(
            id=project_id,
            organization_id=organization_id,
            workspace_id=workspace_id,
            trace_type="observe",
            deleted=False,
        )
        if project.name != project_name or project.metadata != {
            "fixture": "omega-current-stack-e2e/v1"
        }:
            raise RuntimeError("refusing to activate a project not created by this run")
        config, _ = TraceScanConfig.no_workspace_objects.get_or_create(project=project)
        config.enabled = True
        config.engine = TraceScanEngine.OMEGA
        config.scan_version = engine_version
        config.sampling_rate = 1.0
        config.omega_limits = {
            "deadline_seconds": 180,
            "max_model_calls": 8,
            "max_children": 1,
            "max_parallel_children": 1,
            "max_input_tokens_total": 20000,
            "max_output_tokens_total": 4000,
            "max_evidence_bytes": 1048576,
            "max_tool_result_bytes": 8192,
        }
        config.save(
            update_fields=[
                "enabled",
                "engine",
                "scan_version",
                "sampling_rate",
                "omega_limits",
                "updated_at",
            ]
        )

    print(
        "OMEGA_E2E_STATE="
        + json.dumps(
            {
                "billing_emission_enabled": settings.ERROR_FEED_OMEGA_BILLING_EMIT_ENABLED,
                "engine": config.engine,
                "engine_version": config.scan_version,
                "limits": config.omega_limits,
                "project_id": str(project.id),
            },
            sort_keys=True,
        )
    )


def state():
    import json
    import os

    from django.conf import settings

    from tracer.models.trace_error_analysis import ErrorClusterTraces
    from tracer.models.trace_investigation import (
        TraceInvestigationDelivery,
        TraceInvestigationJob,
        TraceInvestigationReport,
        TraceInvestigationUsageReceipt,
    )
    from tracer.models.trace_scan import TraceScanIssue, TraceScanResult

    project_id = os.environ["OMEGA_E2E_PROJECT_ID"]
    trace_id = os.environ["OMEGA_E2E_TRACE_ID"]
    job = (
        TraceInvestigationJob.no_workspace_objects.filter(
            project_id=project_id, trace_id=trace_id
        )
        .order_by("created_at")
        .first()
    )
    report = None
    if job is not None:
        report = (
            TraceInvestigationReport.no_workspace_objects.filter(
                job=job, attempt__generation=job.generation
            )
            .order_by("-created_at")
            .first()
        )
    projection = TraceScanResult.no_workspace_objects.filter(
        project_id=project_id, trace_id=trace_id
    ).first()
    issues = []
    if projection is not None:
        issues = list(
            TraceScanIssue.no_workspace_objects.filter(scan_result=projection).values(
                "id", "category", "group", "brief", "cluster__cluster_id"
            )
        )
    receipt = None
    if report is not None:
        receipt = TraceInvestigationUsageReceipt.no_workspace_objects.filter(
            report=report
        ).first()
    result = (
        report.result if report is not None and isinstance(report.result, dict) else {}
    )
    accounting = result.get("gateway_accounting", [])
    usage = result.get("usage", {})
    payload = receipt.event_payload if receipt is not None else {}
    state = {
        "billing_emission_enabled": settings.ERROR_FEED_OMEGA_BILLING_EMIT_ENABLED,
        "delivery_count": TraceInvestigationDelivery.no_workspace_objects.filter(
            project_id=project_id
        ).count(),
        "delivery_topics": list(
            TraceInvestigationDelivery.no_workspace_objects.filter(
                project_id=project_id
            ).values_list("topic", flat=True)
        ),
        "job_id": str(job.id) if job else None,
        "job_state": job.state if job else None,
        "report_id": str(report.id) if report else None,
        "active_projection_updated": report.active_projection_updated
        if report
        else None,
        "grouping_status": report.grouping_status if report else None,
        "execution_status": result.get("execution_status"),
        "outcome": result.get("outcome"),
        "findings": [
            {
                "finding_id": finding.get("finding_id"),
                "kind": finding.get("kind"),
                "statement": finding.get("statement"),
            }
            for finding in result.get("findings", [])
            if isinstance(finding, dict)
        ],
        "model_calls": usage.get("model_calls") if isinstance(usage, dict) else None,
        "reported_cost_usd": usage.get("cost_usd") if isinstance(usage, dict) else None,
        "gateway_accounting": [
            {"cost": row.get("cost"), "model_used": row.get("model_used")}
            for row in accounting
            if isinstance(row, dict)
        ]
        if isinstance(accounting, list)
        else None,
        "issues": [
            {
                "id": str(issue["id"]),
                "category": issue["category"],
                "group": issue["group"],
                "brief": issue["brief"],
                "cluster_id": issue["cluster__cluster_id"],
            }
            for issue in issues
        ],
        "membership_count": ErrorClusterTraces.no_workspace_objects.filter(
            scan_issue_id__in=[issue["id"] for issue in issues], trace_id=trace_id
        ).count(),
        "receipt": None
        if receipt is None
        else {
            "event_id": str(receipt.event_id),
            "organization_id": str(receipt.organization_id),
            "project_id": str(receipt.project_id),
            "report_id": str(receipt.report_id),
            "raw_cost_usd": str(receipt.raw_cost_usd)
            if receipt.raw_cost_usd is not None
            else None,
            "credit_amount": str(receipt.credit_amount)
            if receipt.credit_amount is not None
            else None,
            "status": receipt.status,
            "status_reason": receipt.status_reason,
            "delivery_attempts": receipt.delivery_attempts,
            "emitted_at": receipt.emitted_at.isoformat()
            if receipt.emitted_at
            else None,
            "event_payload": {
                "event_id": payload.get("event_id"),
                "org_id": payload.get("org_id"),
                "event_type": payload.get("event_type"),
                "report_id": (payload.get("properties") or {}).get("report_id"),
                "project_id": (payload.get("properties") or {}).get("project_id"),
                "source": (payload.get("properties") or {}).get("source"),
            }
            if payload
            else {},
        },
    }
    print("OMEGA_E2E_STATE=" + json.dumps(state, sort_keys=True, default=str))
