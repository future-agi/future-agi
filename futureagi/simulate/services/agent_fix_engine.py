"""What the improvement engine asks the platform for during a fix: the agent's source,
new candidate versions, check runs of them, progress reports and checkpoints.

Every call that changes something re-reads the fix under its row lock and refuses unless it
is running: ``close_fix_run`` takes the same lock, so nothing starts after a fix ended.
"""

import fnmatch
import io
import re
import tarfile
from decimal import Decimal
from typing import Any

import structlog
from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from simulate.models import AgentFixCandidate, AgentFixRun, HostedHarnessJob
from simulate.pydantic_schemas.agent_fix import (
    CandidateOutcome,
    CheckRunRequest,
    Ending,
    EngineReport,
)
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
_PATCH_FILE = re.compile(r"^diff --git a/(\S+) b/(\S+)$", re.MULTILINE)
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
    """The agent's editable files as a `source/`-rooted tar.gz."""
    if fix_run.source_run_id is None:
        raise HostedHarnessError(
            "fix_source_deleted",
            "the run this fix started from was deleted",
            status_code=409,
        )
    return agent_kind(fix_run.environment).source_archive(fix_run)


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
        _refuse_locked_paths(fix_run, patch)
        files, paths = _tree_files(tree)
        # Stored first: an object no candidate points at is harmless, a candidate without
        # its patch is not.
        patch_key = _object_key(fix_run, f"candidate-{attempt}.patch")
        _put(patch_key, patch, "text/x-diff")
        try:
            with transaction.atomic():
                locked = _locked_running(fix_run.id)
                version = kind.candidate_version(locked, files, paths, attempt)
                candidate = AgentFixCandidate.no_workspace_objects.create(
                    fix_run=locked,
                    attempt=attempt,
                    agent_version=version,
                    patch_object_key=patch_key,
                    summary=summary[:4000],
                )
        except IntegrityError:
            # The same attempt arrived twice at once; the first one made it.
            candidate = fix_run.candidates.get(attempt=attempt)
        logger.info(
            "agent_fix_candidate_added",
            fix_run=str(fix_run.id),
            attempt=attempt,
            agent_version=str(candidate.agent_version_id),
        )
    return candidate, kind.check(candidate.agent_version)


def _refuse_locked_paths(fix_run: AgentFixRun, patch: bytes) -> None:
    locked = fix_run.request.get("locked_paths") or []
    # Paths are the agent's own; an archive's `source/` root is not part of them.
    touched = {
        path.removeprefix("source/")
        for pair in _PATCH_FILE.findall(patch.decode("utf-8", "replace"))
        for path in pair
    }
    hits = sorted(
        path for path in touched if any(fnmatch.fnmatch(path, rule) for rule in locked)
    )
    if hits:
        raise HostedHarnessError(
            "fix_locked_path_changed",
            f"the candidate changes locked files: {', '.join(hits)}",
            status_code=422,
        )


def _tree_files(tree: bytes) -> tuple[list[io.BytesIO], list[str]]:
    """The files of an uploaded agent tree, for the same store an uploaded folder uses."""
    files, paths = [], []
    try:
        with tarfile.open(fileobj=io.BytesIO(tree), mode="r:gz") as archive:
            for member in archive.getmembers():
                if not member.isfile():
                    continue
                path = member.name.removeprefix("./").removeprefix("source/")
                extracted = archive.extractfile(member)
                if extracted is None:
                    continue
                files.append(io.BytesIO(extracted.read()))
                paths.append(path)
    except tarfile.TarError as exc:
        raise HostedHarnessError(
            "fix_tree_invalid", "the candidate's tree is not a tar.gz", status_code=400
        ) from exc
    if not files:
        raise HostedHarnessError(
            "fix_tree_empty", "the candidate's tree has no files", status_code=400
        )
    return files, paths


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
    idempotency_key = f"fix:{fix_run.id}:{request.label}"
    replay = HostedHarnessJob.no_workspace_objects.filter(
        environment=environment,
        idempotency_key=run_idempotency_key(environment, idempotency_key),
    ).exists()
    executions = len(request.scenario_keys) * request.trials
    if not replay:
        # Before the usage check, which counts against the organization's quota.
        _require_budget(fix_run, executions)
        require_harness_call_usage(environment)
    with transaction.atomic():
        locked = _locked_running(fix_run.id)
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
            transaction.on_commit(lambda: _dispatch(run))
    return run


def _dispatch(run: HostedHarnessJob) -> None:
    retry = run.payload["retry"]
    start_hosted_harness_gateway_workflow(
        str(run.id),
        str(settings.HARNESS_PUBLIC_BASE_URL).rstrip("/"),
        retry["max_infrastructure_attempts"],
        retry["initial_backoff_seconds"],
        retry["max_backoff_seconds"],
    )


def committed_usd(fix_run: AgentFixRun) -> Decimal:
    """The engine's own spend plus a worst-case figure for every check run so far."""
    executions = sum(
        HostedHarnessJob.no_workspace_objects.filter(fix_run=fix_run).values_list(
            "scenario_count", flat=True
        )
    )
    return fix_run.spent_usd + executions * _worst_case_per_execution()


def _worst_case_per_execution() -> Decimal:
    return Decimal(
        str(getattr(settings, "AGENT_FIX_WORST_CASE_USD_PER_EXECUTION", "0.05"))
    )


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
        # Spend only grows: a resumed engine may report less than it already spent.
        locked.spent_usd = max(locked.spent_usd, Decimal(str(report.spent_usd)))
        if report.ending is not None:
            locked.report = report.model_dump(mode="json", exclude={"candidates"})
        locked.save(
            update_fields=["stage", "heartbeat_at", "spent_usd", "report", "updated_at"]
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
    if report.ending is None:
        return locked
    return close_fix_run(locked.id, _ENDING_STATES[report.ending])


def store_checkpoint(fix_run: AgentFixRun, body: bytes) -> None:
    key = _object_key(fix_run, "checkpoint.json")
    _put(key, body, "application/json")
    stored = AgentFixRun.no_workspace_objects.filter(
        id=fix_run.id, state=AgentFixRun.State.RUNNING
    ).update(checkpoint_key=key, updated_at=timezone.now())
    if not stored:
        raise HostedHarnessError(
            "fix_run_not_running", "the fix is no longer running", status_code=409
        )


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
