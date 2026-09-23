import json
import uuid

from django.db import transaction
from django.db.models import Prefetch
from django.utils import timezone

from simulate.models.test_execution import CallExecution, CallTranscript, TestExecution
from tracer.models.project import Project, ProjectSourceChoices
from tracer.models.trace_error_analysis import TraceErrorGroup
from tracer.models.trace_investigation import (
    InvestigationWorkload,
    TraceInvestigationAttempt,
    TraceInvestigationAttemptStatus,
    TraceInvestigationJob,
    TraceInvestigationJobState,
)


class SimulationInvestigationConflict(Exception):
    pass


def _project_for_execution(execution: TestExecution) -> Project:
    project, _ = Project.objects.get_or_create(
        organization_id=execution.run_test.organization_id,
        workspace_id=execution.run_test.workspace_id,
        name=f"Simulation Debug {execution.id}",
        deleted=False,
        trace_type="experiment",
        defaults={
            "model_type": "Numeric",
            "source": ProjectSourceChoices.SIMULATOR.value,
            "user_id": None,
            "metadata": {"simulation_test_execution_id": str(execution.id)},
        },
    )
    if project.source != ProjectSourceChoices.SIMULATOR.value or (
        project.metadata or {}
    ).get("simulation_test_execution_id") != str(execution.id):
        raise SimulationInvestigationConflict(
            "Simulation investigation project has an invalid execution scope"
        )
    return project


def ensure_simulation_investigation(execution: TestExecution) -> TraceInvestigationJob:
    """Create exactly one direct durable job for a completed execution."""
    if execution.status != TestExecution.ExecutionStatus.COMPLETED:
        raise SimulationInvestigationConflict("Test execution is not completed")
    with transaction.atomic():
        execution = (
            TestExecution.objects.select_for_update()
            .select_related("run_test")
            .get(pk=execution.pk)
        )
        if execution.status != TestExecution.ExecutionStatus.COMPLETED:
            raise SimulationInvestigationConflict("Test execution is not completed")
        terminal_statuses = (
            CallExecution.CallStatus.COMPLETED,
            CallExecution.CallStatus.FAILED,
            CallExecution.CallStatus.CANCELLED,
        )
        if (
            CallExecution.objects.filter(test_execution_id=execution.id, deleted=False)
            .exclude(status__in=terminal_statuses)
            .exists()
        ):
            raise SimulationInvestigationConflict(
                "Test execution contains nonterminal calls"
            )
        project = _project_for_execution(execution)
        job = (
            TraceInvestigationJob.objects.select_for_update(of=("self",))
            .select_related("current_report")
            .filter(
                project=project,
                test_execution=execution,
                workload_type=InvestigationWorkload.SIMULATION_TEST_EXECUTION,
            )
            .first()
        )
        if job is None:
            job = TraceInvestigationJob.objects.create(
                project=project,
                test_execution=execution,
                organization_id=execution.run_test.organization_id,
                workspace_id=execution.run_test.workspace_id,
                workload_type=InvestigationWorkload.SIMULATION_TEST_EXECUTION,
                not_before=timezone.now(),
            )
        elif job.state == TraceInvestigationJobState.CANCELLED or (
            job.current_report is not None
            and job.current_report.execution_status == "failed"
        ):
            failed_report = (
                job.current_report
                if job.current_report is not None
                and job.current_report.execution_status == "failed"
                else None
            )
            if failed_report is not None:
                failed_report.is_current = False
                failed_report.save(update_fields=["is_current", "updated_at"])
                job.current_report = None
            job.generation += 1
            job.state = TraceInvestigationJobState.WAITING
            job.not_before = timezone.now()
            job.save(
                update_fields=[
                    "generation",
                    "state",
                    "not_before",
                    "current_report",
                    "updated_at",
                ]
            )
        return job


def debug_analysis_state(execution: TestExecution) -> dict:
    job = (
        TraceInvestigationJob.objects.filter(
            test_execution=execution,
            workload_type=InvestigationWorkload.SIMULATION_TEST_EXECUTION,
        )
        .select_related("current_report")
        .first()
    )
    if job is None:
        return {
            "test_execution_id": str(execution.id),
            "status": "not_requested",
            "job_id": None,
            "generation": None,
            "error_message": None,
            "report": None,
            "findings": [],
        }
    report = job.current_report
    status = {
        TraceInvestigationJobState.WAITING: "pending",
        TraceInvestigationJobState.RUNNING: "running",
        TraceInvestigationJobState.COMPLETED: "completed",
        TraceInvestigationJobState.CANCELLED: "failed",
    }[job.state]
    if report is not None and report.execution_status == "failed":
        status = "failed"
    error_message = report.error_message if report and report.error_message else None
    if status == "failed" and not error_message:
        error_message = "Debug analysis failed"
    findings = []
    if report is not None:
        for finding in report.findings.order_by("ordinal"):
            cluster = None
            if finding.cluster_id:
                group = TraceErrorGroup.objects.filter(
                    id=finding.cluster_id,
                    target_type="simulation",
                    test_execution=execution,
                    deleted=False,
                ).first()
                if group:
                    cluster = {
                        "id": str(group.id),
                        "cluster_id": group.cluster_id,
                        "title": group.title,
                        "error_type": group.error_type,
                    }
            links = finding.traceinvestigationfindingevidence_set.select_related(
                "evidence"
            ).all()
            findings.append(
                {
                    "id": str(finding.id),
                    "kind": finding.kind,
                    "statement": finding.statement,
                    "recovery": finding.recovery,
                    "category": finding.category,
                    "group_label": finding.group_label,
                    "fix_layer": finding.fix_layer,
                    "confidence": finding.confidence,
                    "cluster": cluster,
                    "evidence": [
                        {
                            "evidence_id": link.evidence.evidence_id,
                            "call_execution_id": str(link.evidence.call_execution_id)
                            if link.evidence.call_execution_id
                            else None,
                            "excerpt": link.evidence.excerpt,
                        }
                        for link in links
                    ],
                }
            )
    return {
        "test_execution_id": str(execution.id),
        "status": status,
        "generation": job.generation,
        "job_id": str(job.id),
        "error_message": error_message,
        "report": (
            {
                "id": str(report.id),
                "execution_status": report.execution_status,
                "outcome": report.outcome,
                "coverage": {
                    "scope": report.coverage_scope,
                    "observed_call_count": report.observed_call_count,
                    "read_complete": report.read_complete,
                },
                "error_message": report.error_message,
                "grouping_status": report.grouping_status,
                "recorded_at": report.recorded_at.isoformat(),
            }
            if report
            else None
        ),
        "findings": findings,
    }


def simulation_evidence_page(
    *, attempt_id: uuid.UUID, lease_token: str, cursor: int
) -> dict:
    from tracer.services.trace_investigation import _token_digest

    if cursor < 0:
        raise SimulationInvestigationConflict("Invalid evidence cursor")
    attempt = (
        TraceInvestigationAttempt.no_workspace_objects.select_related(
            "job__project", "job__test_execution__run_test"
        )
        .filter(
            id=attempt_id,
            job__workload_type=InvestigationWorkload.SIMULATION_TEST_EXECUTION,
            status=TraceInvestigationAttemptStatus.CLAIMED,
            lease_expires_at__gt=timezone.now(),
            lease_token_digest=_token_digest(lease_token),
        )
        .first()
    )
    if attempt is None:
        raise SimulationInvestigationConflict("Attempt is not active")
    execution = attempt.job.test_execution
    if execution is None or execution.status != TestExecution.ExecutionStatus.COMPLETED:
        raise SimulationInvestigationConflict("Execution evidence is unavailable")
    terminal_statuses = (
        CallExecution.CallStatus.COMPLETED,
        CallExecution.CallStatus.FAILED,
        CallExecution.CallStatus.CANCELLED,
    )
    job = attempt.job
    project = job.project
    if (
        job.organization_id != execution.run_test.organization_id
        or job.workspace_id != execution.run_test.workspace_id
        or project.organization_id != job.organization_id
        or project.workspace_id != job.workspace_id
        or project.source != ProjectSourceChoices.SIMULATOR.value
        or (project.metadata or {}).get("simulation_test_execution_id")
        != str(execution.id)
    ):
        raise SimulationInvestigationConflict(
            "Execution evidence is outside claim scope"
        )
    if (
        CallExecution.no_workspace_objects.filter(
            test_execution_id=execution.id, deleted=False
        )
        .exclude(status__in=terminal_statuses)
        .exists()
    ):
        raise SimulationInvestigationConflict("Execution contains nonterminal calls")
    calls_qs = (
        CallExecution.no_workspace_objects.filter(
            test_execution_id=execution.id,
            status__in=terminal_statuses,
            deleted=False,
        )
        .select_related("scenario")
        .prefetch_related(
            Prefetch(
                "transcripts",
                queryset=CallTranscript.no_workspace_objects.order_by(
                    "start_time_ms", "id"
                ),
            )
        )
        .order_by("created_at", "id")
    )
    total = calls_qs.count()
    if cursor > total:
        raise SimulationInvestigationConflict("Evidence cursor is outside the result")
    rows = list(calls_qs[cursor : cursor + 20])
    calls = []
    for call in rows:
        calls.append(
            {
                "call_execution_id": str(call.id),
                "status": call.status,
                "simulation_call_type": call.simulation_call_type,
                "scenario": call.scenario.name,
                "call_summary": call.call_summary,
                "error_message": call.error_message,
                "ended_reason": call.ended_reason,
                "transcript": [
                    {
                        "id": str(entry.id),
                        "speaker": entry.speaker_role,
                        "content": entry.content,
                        "start_time": entry.start_time_ms / 1000,
                        "end_time": entry.end_time_ms / 1000,
                    }
                    for entry in call.transcripts.all()
                ],
            }
        )
    payload = {"calls": calls, "next_cursor": cursor + len(calls), "total_calls": total}
    if len(json.dumps(payload, separators=(",", ":")).encode()) > 1_800_000:
        raise SimulationInvestigationConflict(
            "Evidence page exceeds the control payload limit"
        )
    return payload
