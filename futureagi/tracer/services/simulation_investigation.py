import json
import uuid

from django.db import transaction
from django.db.models import Prefetch
from django.utils import timezone

from simulate.models.test_execution import CallExecution, CallTranscript, TestExecution
from simulate.services.harness_scenarios import authored_scenarios_for_calls
from simulate.services.run_results_v3 import build_evaluation_catalog, eval_rows
from tracer.models.project import Project, ProjectSourceChoices
from tracer.models.trace_error_analysis import TraceErrorGroup
from tracer.models.trace_investigation import (
    InvestigationWorkload,
    TraceInvestigationAttempt,
    TraceInvestigationAttemptStatus,
    TraceInvestigationFinding,
    TraceInvestigationJob,
    TraceInvestigationJobState,
)
from tracer.services.simulation_diagnosis import build_diagnosis


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


_TERMINAL_CALL_STATUSES = (
    CallExecution.CallStatus.COMPLETED,
    CallExecution.CallStatus.FAILED,
    CallExecution.CallStatus.CANCELLED,
)


def ensure_simulation_investigation(
    execution: TestExecution,
) -> list[TraceInvestigationJob]:
    """Create one durable job per call of a completed execution; re-arm failed ones."""
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
        calls = CallExecution.objects.filter(
            test_execution_id=execution.id, deleted=False
        )
        if calls.exclude(status__in=_TERMINAL_CALL_STATUSES).exists():
            raise SimulationInvestigationConflict(
                "Test execution contains nonterminal calls"
            )
        project = _project_for_execution(execution)
        existing = {
            job.call_execution_id: job
            for job in TraceInvestigationJob.objects.select_for_update(of=("self",))
            .select_related("current_report")
            .filter(
                project=project,
                test_execution=execution,
                workload_type=InvestigationWorkload.SIMULATION_TEST_EXECUTION,
            )
        }
        now = timezone.now()
        jobs = []
        for call_id in calls.order_by("created_at", "id").values_list("id", flat=True):
            job = existing.get(call_id)
            if job is None:
                job = TraceInvestigationJob.objects.create(
                    project=project,
                    test_execution=execution,
                    call_execution_id=call_id,
                    organization_id=execution.run_test.organization_id,
                    workspace_id=execution.run_test.workspace_id,
                    workload_type=InvestigationWorkload.SIMULATION_TEST_EXECUTION,
                    not_before=now,
                )
            elif job.state == TraceInvestigationJobState.CANCELLED or (
                job.current_report is not None
                and job.current_report.execution_status == "failed"
            ):
                if job.current_report is not None:
                    job.current_report.is_current = False
                    job.current_report.save(update_fields=["is_current", "updated_at"])
                    job.current_report = None
                job.generation += 1
                job.state = TraceInvestigationJobState.WAITING
                job.not_before = now
                job.save(
                    update_fields=[
                        "generation",
                        "state",
                        "not_before",
                        "current_report",
                        "updated_at",
                    ]
                )
            jobs.append(job)
        return jobs


def _finding_payload(finding, clusters: dict, goals: set[str]) -> dict:
    group = clusters.get(finding.cluster_id)
    requirement = finding.requirement.requirement_id if finding.requirement else None
    return {
        "id": str(finding.id),
        "kind": finding.kind,
        "statement": finding.statement,
        "recovery": finding.recovery,
        "category": finding.category,
        "group_label": finding.group_label,
        "fix_layer": finding.fix_layer,
        "confidence": finding.confidence,
        "goal": requirement if requirement in goals else None,
        "cluster": (
            {
                "id": str(group.id),
                "cluster_id": group.cluster_id,
                "title": group.title,
                "error_type": group.error_type,
            }
            if group
            else None
        ),
        "evidence": [
            {
                "evidence_id": link.evidence.evidence_id,
                "call_execution_id": (
                    str(link.evidence.call_execution_id)
                    if link.evidence.call_execution_id
                    else None
                ),
                "excerpt": link.evidence.excerpt,
            }
            for link in finding.traceinvestigationfindingevidence_set.all()
        ],
    }


_EMPTY_DIAGNOSIS = {"summary": None, "goals": [], "one_offs": []}


def debug_analysis_state(execution: TestExecution) -> dict:
    """The run's analysis: every call's current report rolled up into one view."""
    jobs = list(
        TraceInvestigationJob.objects.filter(
            test_execution=execution,
            workload_type=InvestigationWorkload.SIMULATION_TEST_EXECUTION,
        )
        .select_related("current_report", "call_execution")
        .order_by("call_execution__created_at", "call_execution_id")
    )
    if not jobs:
        return {
            "test_execution_id": str(execution.id),
            "status": "not_requested",
            "job_id": None,
            "generation": None,
            "error_message": None,
            "report": None,
            "findings": [],
            **_EMPTY_DIAGNOSIS,
        }
    reports = [job.current_report for job in jobs if job.current_report is not None]
    succeeded = [report for report in reports if report.execution_status != "failed"]
    in_flight = [
        job
        for job in jobs
        if job.state
        in (TraceInvestigationJobState.WAITING, TraceInvestigationJobState.RUNNING)
    ]
    if any(job.state == TraceInvestigationJobState.RUNNING for job in in_flight):
        status = "running"
    elif in_flight:
        status = "pending"
    elif not succeeded:
        status = "failed"
    else:
        status = "completed"
    error_message = None
    if status == "failed":
        error_message = next(
            (report.error_message for report in reports if report.error_message),
            "Debug analysis failed",
        )

    # Findings follow the run's call order, then each report's own order.
    order = {report.id: index for index, report in enumerate(succeeded)}
    findings = sorted(
        TraceInvestigationFinding.objects.filter(report__in=succeeded)
        .select_related("requirement", "report__job")
        .prefetch_related(
            "traceinvestigationfindingevidence_set__evidence", "attributions"
        ),
        key=lambda finding: (order[finding.report_id], finding.ordinal),
    )
    clusters = {
        group.id: group
        for group in TraceErrorGroup.objects.filter(
            id__in={f.cluster_id for f in findings if f.cluster_id},
            target_type="simulation",
            test_execution=execution,
            deleted=False,
        )
    }
    # A finding is filed under a goal only when that goal is one its call was
    # authored to test, so a model-invented requirement name never becomes one.
    reported = [job for job in jobs if job.current_report in succeeded]
    authored = _authored_goals(execution, [job.call_execution for job in reported])
    goals_by_report = {
        job.current_report_id: set(
            (authored.get(job.call_execution_id) or {}).get("sub_goals", [])
        )
        for job in reported
    }
    finding_goal = {
        finding.id: finding.requirement.requirement_id
        for finding in findings
        if finding.requirement
        and finding.requirement.requirement_id in goals_by_report[finding.report_id]
    }
    # A call whose own analysis failed is not "no issues": it is unread.
    unanalyzed = {
        job.call_execution_id
        for job in jobs
        if job not in in_flight and job.current_report not in succeeded
    }
    diagnosis = build_diagnosis(
        execution, findings, finding_goal, clusters, unanalyzed=unanalyzed
    )
    grouping = {report.grouping_status for report in succeeded}
    latest = max(reports, key=lambda report: report.recorded_at) if reports else None
    outcomes = {report.outcome for report in succeeded}
    return {
        "test_execution_id": str(execution.id),
        "status": status,
        "generation": max(job.generation for job in jobs),
        "job_id": None,
        "error_message": error_message,
        "report": (
            {
                "id": str(latest.id),
                "execution_status": "completed" if succeeded else "failed",
                "outcome": (
                    "failure"
                    if "failure" in outcomes
                    else "success"
                    if outcomes == {"success"}
                    else "unknown"
                ),
                "coverage": {
                    "scope": "simulation_test_execution",
                    "observed_call_count": len(reports),
                    "read_complete": not in_flight
                    and len(reports) == len(jobs)
                    and all(report.read_complete for report in succeeded),
                },
                "error_message": error_message,
                "grouping_status": (
                    "pending"
                    if "pending" in grouping
                    else "failed"
                    if "failed" in grouping
                    else "completed"
                ),
                "recorded_at": latest.recorded_at.isoformat(),
            }
            if latest
            else None
        ),
        "findings": [
            _finding_payload(finding, clusters, goals_by_report[finding.report_id])
            for finding in findings
        ],
        **diagnosis,
    }


def _authored_goals(execution: TestExecution, calls: list) -> dict:
    """What each call was authored to test, so findings name the goal they break."""
    return {
        call_id: _goals_payload(scenario)
        for call_id, scenario in authored_scenarios_for_calls(
            execution.run_test_id, calls
        ).items()
    }


def _goals_payload(scenario) -> dict | None:
    if scenario is None:
        return None
    return {
        "use_case": scenario.use_case or None,
        "sub_goals": [str(goal) for goal in scenario.sub_goals or []],
        "expected_outcome": scenario.tests or None,
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
    # The job investigates one call, the way a trace job investigates one trace.
    calls_qs = (
        CallExecution.no_workspace_objects.filter(
            id=job.call_execution_id,
            test_execution_id=execution.id,
            status__in=_TERMINAL_CALL_STATUSES,
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
    authored = _authored_goals(execution, rows)
    _, live_eval_ids = build_evaluation_catalog(execution)
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
                "goals": authored.get(call.id),
                # The run's own verdicts: Omega explains how a goal broke, the
                # evals decide whether it did.
                "evaluations": [
                    {
                        "name": row["name"],
                        "value": None if row["value"] is None else str(row["value"]),
                        "passed": row["passed"],
                        "reason": row["reason"],
                    }
                    for row in eval_rows(call, live_eval_ids)
                ],
            }
        )
    payload = {"calls": calls, "next_cursor": cursor + len(calls), "total_calls": total}
    if len(json.dumps(payload, separators=(",", ":")).encode()) > 1_800_000:
        raise SimulationInvestigationConflict(
            "Evidence page exceeds the control payload limit"
        )
    return payload
