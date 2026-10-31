"""What the improvement engine asks the platform for during a fix: the agent's source,
new candidate versions, check runs of them, and progress reports.

Every call that changes something re-reads the fix under its row lock and refuses unless it
is running: ``close_fix_run`` takes the same lock, so nothing starts after a fix ended.
"""

import fnmatch
import io
import tarfile
from decimal import Decimal
from typing import Any

import structlog
from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone
from minio.error import S3Error

from simulate.models import (
    AgentFixCandidate,
    AgentFixRun,
    HostedHarnessJob,
    TestExecution,
)
from simulate.pydantic_schemas.agent_fix import (
    CandidateOutcome,
    CheckRunRequest,
    Ending,
    EngineReport,
    FailureOwner,
)
from simulate.selectors.agent_fix import committed_usd, fix_lineage
from simulate.services.agent_fix_agents import agent_kind
from simulate.services.agent_fix_runs import close_fix_run
from simulate.services.harness_usage import require_harness_call_usage
from simulate.services.hosted_harness import (
    HostedHarnessError,
    create_selected_harness_run,
    run_idempotency_key,
)
from simulate.temporal.client import start_hosted_harness_gateway_workflow
from tfc.settings.settings import UPLOAD_BUCKET_NAME
from tfc.utils.storage_client import ensure_bucket, get_storage_client

logger = structlog.get_logger(__name__)

_ENDING_STATES = {
    Ending.READY: AgentFixRun.State.READY,
    Ending.NO_IMPROVEMENT: AgentFixRun.State.NO_IMPROVEMENT,
    Ending.NOT_REPRODUCED: AgentFixRun.State.NOT_REPRODUCED,
    Ending.OUT_OF_BUDGET: AgentFixRun.State.OUT_OF_BUDGET,
    Ending.FAILED: AgentFixRun.State.FAILED,
}
MAX_PATCH_BYTES = 2 * 1024 * 1024


def _locked_running(fix_run_id: Any) -> AgentFixRun:
    """The fix, locked for this transaction, if it is still running."""
    locked = AgentFixRun.no_workspace_objects.select_for_update().get(id=fix_run_id)
    if locked.state != AgentFixRun.State.RUNNING:
        raise HostedHarnessError(
            "fix_run_not_running", f"the fix is {locked.state}", status_code=409
        )
    if locked.source_run_id is None:
        raise HostedHarnessError(
            "fix_source_deleted",
            "the run this fix started from was deleted",
            status_code=409,
        )
    return locked


def source_archive(fix_run: AgentFixRun) -> bytes:
    """The agent's editable files as the fix started from them: a `source/`-rooted tar.gz,
    kept for the whole fix, so every candidate is compared with the same base."""
    key = _object_key(fix_run, "base-source.tar.gz")
    body = _load_if_stored(key)
    if body is None:
        if fix_run.source_run_id is None:
            raise HostedHarnessError(
                "fix_source_deleted",
                "the run this fix started from was deleted",
                status_code=409,
            )
        body = agent_kind(fix_run.environment).source_archive(fix_run)
        _put(key, body, "application/gzip")
    return body


def add_candidate(
    fix_run: AgentFixRun, *, attempt: int, summary: str, tree: bytes, patch: bytes
) -> tuple[AgentFixCandidate, str]:
    """Store one candidate as a new agent version that is never made the default, check it,
    and return it with its check status. The same attempt again returns the candidate made
    the first time, checked again if its check never finished."""
    kind = agent_kind(fix_run.environment)
    candidate = fix_run.candidates.filter(attempt=attempt).first()
    if candidate is None:
        if len(patch) > MAX_PATCH_BYTES:
            raise HostedHarnessError(
                "fix_patch_too_large",
                "the candidate's patch is too large",
                status_code=413,
            )
        files = archive_files(tree)
        _refuse_locked_changes(fix_run, files)
        # Storage first, outside the fix's lock: an object no candidate points at is
        # harmless, a candidate without its files is not.
        prepared = kind.prepare_candidate(fix_run, files, attempt)
        patch_key = _object_key(fix_run, f"candidate-{attempt}.patch")
        _put(patch_key, patch, "text/x-diff")
        try:
            with transaction.atomic():
                locked = _locked_running(fix_run.id)
                version = kind.candidate_version(locked, prepared, attempt)
                candidate = AgentFixCandidate.no_workspace_objects.create(
                    fix_run=locked,
                    attempt=attempt,
                    agent_version=version,
                    patch_object_key=patch_key,
                    summary=summary[:4000],
                )
        except IntegrityError as exc:
            if "uniq_agent_fix_candidate_attempt" not in str(exc):
                raise
            # The same attempt arrived twice at once; the first one made it.
            candidate = fix_run.candidates.get(attempt=attempt)
        logger.info(
            "agent_fix_candidate_added",
            fix_run=str(fix_run.id),
            attempt=attempt,
            agent_version=str(candidate.agent_version_id),
        )
    return candidate, kind.check(candidate.agent_version)


def _refuse_locked_changes(fix_run: AgentFixRun, files: dict[str, bytes]) -> None:
    """Refuse a candidate whose files differ from the base on any locked path. The tree is
    what becomes the version, so it is what is compared, not the patch."""
    locked = fix_run.request.get("locked_paths") or []
    if not locked:
        return
    base = archive_files(source_archive(fix_run))
    hits = sorted(
        path
        for path in set(base) | set(files)
        if any(fnmatch.fnmatch(path, rule) for rule in locked)
        and base.get(path) != files.get(path)
    )
    if hits:
        raise HostedHarnessError(
            "fix_locked_path_changed",
            f"the candidate changes locked files: {', '.join(hits)}",
            status_code=422,
        )


def archive_files(body: bytes) -> dict[str, bytes]:
    """An agent tree's files by their path from the agent's root, refusing a tree whose
    unpacked size or file count is over the limit before reading it."""
    max_bytes = settings.AGENT_FIX_MAX_TREE_BYTES
    max_files = settings.AGENT_FIX_MAX_TREE_FILES
    try:
        with tarfile.open(fileobj=io.BytesIO(body), mode="r:gz") as archive:
            members = [member for member in archive.getmembers() if member.isfile()]
            if (
                len(members) > max_files
                or sum(member.size for member in members) > max_bytes
            ):
                raise HostedHarnessError(
                    "fix_tree_too_large",
                    "the candidate's tree is over the size limit",
                    status_code=413,
                )
            files = {}
            for member in members:
                extracted = archive.extractfile(member)
                if extracted is not None:
                    path = member.name.removeprefix("./").removeprefix("source/")
                    files[path] = extracted.read()
    except tarfile.TarError as exc:
        raise HostedHarnessError(
            "fix_tree_invalid", "the candidate's tree is not a tar.gz", status_code=400
        ) from exc
    if not files:
        raise HostedHarnessError(
            "fix_tree_empty", "the candidate's tree has no files", status_code=400
        )
    return files


def start_check_run(fix_run: AgentFixRun, request: CheckRunRequest) -> HostedHarnessJob:
    """Run some of the environment's scenarios on the base version or a candidate, the
    same way the failed run ran. The same label returns the same run, so an engine that
    resumes does not pay twice."""
    version_id = str(request.agent_version_id)
    allowed = {str(fix_run.base_agent_version_id)} | {
        str(one)
        for one in fix_run.candidates.values_list("agent_version_id", flat=True)
    }
    if version_id not in allowed:
        raise HostedHarnessError(
            "fix_version_unknown",
            "check runs may use the base version or this fix's candidates",
            status_code=422,
        )
    environment = fix_run.environment
    kind = agent_kind(environment)
    finished = _finished_earlier(
        fix_run, request, kind.run_options(fix_run, version_id)
    )
    idempotency_key = f"fix:{fix_run.id}:{request.label}"
    replay = (
        finished is not None
        or HostedHarnessJob.no_workspace_objects.filter(
            environment=environment,
            idempotency_key=run_idempotency_key(environment, idempotency_key),
        ).exists()
    )
    executions = len(request.scenario_keys) * request.trials
    if not replay:
        # Before the usage check, which counts against the organization's quota.
        _require_budget(fix_run, executions)
        require_harness_call_usage(environment)
    with transaction.atomic():
        locked = _locked_running(fix_run.id)
        if finished is not None:
            return finished
        if not replay:
            _require_budget(locked, executions)
        run, created = create_selected_harness_run(
            environment,
            scenario_keys=request.scenario_keys,
            trials=request.trials,
            idempotency_key=idempotency_key,
            fix_run=locked,
            **kind.run_options(locked, version_id),
        )
        if created:
            # The engine's name for the run: the read says which candidate and check it served.
            HostedHarnessJob.no_workspace_objects.filter(id=run.id).update(
                fix_label=request.label
            )
            run.fix_label = request.label
            transaction.on_commit(lambda: _dispatch(run))
    return run


def _finished_earlier(
    fix_run: AgentFixRun, request: CheckRunRequest, options: dict[str, Any]
) -> HostedHarnessJob | None:
    """The same check, finished by a failed fix this one retries: the user paid for it
    once, so it is read again instead of run again. Same label, versions, scenarios and
    trials; a run that failed or was canceled is run again."""
    earlier = fix_lineage(fix_run)[1:]
    picks = options.get("picks")
    if not earlier or picks is None:
        return None
    wanted = tuple(
        str(one)
        for one in (
            picks.agent_version_id,
            picks.environment_version_id,
            picks.suite_version_id,
        )
    )
    for run in HostedHarnessJob.no_workspace_objects.filter(
        fix_run_id__in=earlier,
        fix_label=request.label,
        state=HostedHarnessJob.State.COMPLETED,
        deleted=False,
    ).order_by("-created_at"):
        metadata = (run.payload or {}).get("metadata") or {}
        pins = (run.agent_version_id, run.environment_version_id, run.suite_version_id)
        if (
            tuple(str(one) for one in pins) == wanted
            and metadata.get("selected_scenario_keys") == request.scenario_keys
            and metadata.get("trials") == request.trials
        ):
            return run
    return None


def _dispatch(run: HostedHarnessJob) -> None:
    """Start the check run's workflow; if it cannot start, the run fails at once instead of
    waiting queued for a workflow that will never come."""
    retry = run.payload["retry"]
    try:
        start_hosted_harness_gateway_workflow(
            str(run.id),
            str(settings.HARNESS_PUBLIC_BASE_URL).rstrip("/"),
            retry["max_infrastructure_attempts"],
            retry["initial_backoff_seconds"],
            retry["max_backoff_seconds"],
        )
    except Exception:
        logger.exception("agent_fix_check_run_dispatch_failed", run=str(run.id))
        _fail_unscheduled(run)


def _fail_unscheduled(run: HostedHarnessJob) -> None:
    message = "the check run could not be scheduled"
    now = timezone.now()
    with transaction.atomic():
        marked = HostedHarnessJob.no_workspace_objects.filter(
            id=run.id, state=HostedHarnessJob.State.QUEUED
        ).update(
            state=HostedHarnessJob.State.FAILED,
            current_stage="failed",
            terminal_at=now,
            failure={
                "domain": "infrastructure",
                "stage": "queued",
                "code": "scheduler_unavailable",
                "message": message,
            },
        )
        if marked:
            TestExecution.no_workspace_objects.filter(id=run.test_execution_id).update(
                status=TestExecution.ExecutionStatus.FAILED,
                completed_at=now,
                error_reason=message,
            )


def _worst_case_per_execution() -> Decimal:
    return Decimal(settings.AGENT_FIX_WORST_CASE_USD_PER_EXECUTION)


def _require_budget(fix_run: AgentFixRun, executions: int) -> None:
    budget = Decimal(str(fix_run.request["budget_usd"]))
    needed = committed_usd(fix_run) + executions * _worst_case_per_execution()
    if needed > budget:
        raise HostedHarnessError(
            "fix_budget_exhausted",
            f"this check would bring the fix to ${needed:.2f} of its ${budget:.2f} budget",
            status_code=402,
        )


def apply_report(fix_run: AgentFixRun, report: EngineReport) -> AgentFixRun:
    """Record the engine's progress; a report with an ending closes the run."""
    with transaction.atomic():
        locked = _locked_running(fix_run.id)
        locked.stage = report.stage
        locked.heartbeat_at = timezone.now()
        # An engine that does not send them keeps what an earlier report said.
        if report.step is not None:
            locked.step = report.step
        if report.plan is not None:
            locked.plan = report.plan.model_dump(mode="json")
        if report.diagnosis is not None:
            locked.diagnosis = report.diagnosis.model_dump(mode="json")
        if report.ending is not None:
            locked.report = report.model_dump(
                mode="json", exclude={"candidates", "step", "plan", "diagnosis"}
            )
        locked.save(
            update_fields=[
                "stage",
                "step",
                "plan",
                "diagnosis",
                "heartbeat_at",
                "report",
                "updated_at",
            ]
        )
        for update in report.candidates:
            AgentFixCandidate.no_workspace_objects.filter(
                fix_run=locked, id=update.candidate_id
            ).update(
                outcome=update.outcome,
                verdict=update.verdict.model_dump(mode="json")
                if update.verdict
                else None,
                updated_at=timezone.now(),
            )
        if report.winner_candidate_id:
            AgentFixCandidate.no_workspace_objects.filter(
                fix_run=locked, id=report.winner_candidate_id
            ).update(outcome=CandidateOutcome.WINNER, updated_at=timezone.now())
        if report.ending is not None:
            # A launch that died left candidates no later report names: none of them won.
            AgentFixCandidate.no_workspace_objects.filter(
                fix_run=locked, outcome=CandidateOutcome.PENDING
            ).exclude(id__in=[one.candidate_id for one in report.candidates]).update(
                outcome=CandidateOutcome.REJECTED, updated_at=timezone.now()
            )
    if report.ending is None:
        return locked
    return close_fix_run(
        locked.id,
        _ENDING_STATES[report.ending],
        failure=_ending_failure(locked, report),
    )


def _ending_failure(
    fix_run: AgentFixRun, report: EngineReport
) -> dict[str, str] | None:
    """What the read says a failed fix failed on. A failure the user can fix keeps the
    platform's own words for it; ours gets fixed copy and an alert, since the summary is
    the engine's own error text."""
    if report.ending != Ending.FAILED:
        return None
    if report.failure is not None and report.failure.owner == FailureOwner.USER:
        return {"code": report.failure.code, "message": report.failure.message}
    logger.error(
        "agent_fix_engine_failed",
        fix_run=str(fix_run.id),
        failure=report.failure.model_dump(mode="json") if report.failure else None,
        summary=report.summary[:1000],
    )
    return {
        "code": "fix_engine_failed",
        "message": "the improvement engine hit an error it could not recover from",
    }


def load_object(key: str) -> bytes | None:
    if not key:
        return None
    response = None
    try:
        response = get_storage_client().get_object(UPLOAD_BUCKET_NAME, key)
        return response.read()
    finally:
        if response is not None:
            response.close()
            response.release_conn()


def _load_if_stored(key: str) -> bytes | None:
    try:
        return load_object(key)
    except S3Error as exc:
        if exc.code == "NoSuchKey":
            return None
        raise


def _object_key(fix_run: AgentFixRun, name: str) -> str:
    return f"agent-fix/{fix_run.organization_id}/{fix_run.id}/{name}"


def _put(key: str, body: bytes, content_type: str) -> None:
    client: Any = get_storage_client()
    ensure_bucket(client, UPLOAD_BUCKET_NAME)
    client.put_object(
        bucket_name=UPLOAD_BUCKET_NAME,
        object_name=key,
        data=io.BytesIO(body),
        length=len(body),
        content_type=content_type,
    )
