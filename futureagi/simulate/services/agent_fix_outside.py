"""Submit a fix a person's own coding agent made: an outside attempt, a verify-only Epoch on
the finished run it fixes. Its engine calls no model; it checks the submitted tree."""

import uuid
from typing import Any

import structlog
from django.db import transaction

from simulate.models import AgentFixRun, HostedHarnessJob
from simulate.selectors.agent_fix import base_commit, launch_blocker
from simulate.selectors.agent_fix_outside import (
    attempts_used,
    outside_attempt_request,
    outside_attempts,
    used_attempts,
)
from simulate.services.agent_fix_agents import agent_kind
from simulate.services.agent_fix_engine import (
    archive_files,
    candidate_patch,
    refuse_unwritable,
    run_source_archive,
    store_outside_trees,
    submitted_archive,
)
from simulate.services.agent_fix_runs import create_fix_run
from simulate.services.harness_usage import require_agent_fix_usage
from simulate.services.hosted_harness import HostedHarnessError

logger = structlog.get_logger(__name__)


def submit_outside_attempt(
    source_run: HostedHarnessJob,
    *,
    tree: bytes,
    summary: str,
    sent_base_commit: str,
    locked_paths: list[str] | None,
    budget_credits: int | None,
    user: Any,
) -> AgentFixRun:
    """Start the next outside attempt on a finished run, linked to the one before it so
    the check runs that one finished are reused. Every refusal comes before any sandbox:
    what would refuse a launch, the run's attempts used up, a GitHub agent's commit that
    moved, and a tree that cannot be read, changes nothing, changes or deletes a locked
    file, or makes too large a patch. A file left out of the tree is deleted, and new
    files are allowed."""
    environment = source_run.environment
    if environment is None:
        raise HostedHarnessError(
            "fix_source_not_a_run", "Epoch starts from a run", status_code=422
        )
    if blocked := launch_blocker(source_run, usage=False):
        raise blocked
    if refused := attempts_used(used_attempts(source_run).count()):
        raise refused
    commit = base_commit(source_run)
    if commit and sent_base_commit != commit:
        raise HostedHarnessError(
            "fix_base_commit_moved",
            f"This run ran commit {commit}. Build your change on that commit and submit"
            " it again.",
            status_code=409,
        )
    files = archive_files(tree)
    base_body = run_source_archive(source_run)
    base = archive_files(base_body)
    if files == base:
        raise HostedHarnessError(
            "fix_outside_tree_unchanged",
            "the submitted tree changes no file of the agent",
            status_code=422,
        )
    refuse_unwritable(base, files)
    _refuse_unrebuildable(base, files)
    require_agent_fix_usage(str(environment.organization_id))
    fix_run_id = uuid.uuid4()
    # Storage first, outside the run's lock: trees no fix points at are harmless.
    store_outside_trees(
        environment.organization_id, fix_run_id, base=base_body, submitted=files
    )
    with transaction.atomic():
        # Two submissions at once take their numbers one after the other.
        HostedHarnessJob.no_workspace_objects.select_for_update().get(id=source_run.id)
        attempts = list(outside_attempts(source_run))
        counted = list(used_attempts(source_run))
        used = len(counted)
        if refused := attempts_used(used):
            raise refused
        _refuse_checked_already(counted, files)
        previous = attempts[-1] if attempts else None
        request = outside_attempt_request(
            source_run,
            previous,
            number=used + 1,
            summary=summary,
            locked_paths=locked_paths,
            budget_credits=budget_credits,
        )
        # Here, with the locks the previous attempt leaves this one.
        candidate_patch(base, files, request.locked_paths)
        fix_run = create_fix_run(
            source_run,
            base_version_id=previous.base_agent_version_id
            if previous
            else agent_kind(environment).base_version_id(source_run),
            request=request.model_dump(mode="json"),
            user=user,
            suite_version_id=previous.suite_version_id if previous else None,
            attempt_of=previous,
            fix_run_id=fix_run_id,
        )
    logger.info(
        "agent_fix_outside_attempt_submitted",
        fix_run=str(fix_run.id),
        number=used + 1,
        files=len(files),
    )
    return fix_run


def _refuse_checked_already(
    counted: list[AgentFixRun], files: dict[str, bytes]
) -> None:
    """Refuse the tree an attempt that counted already submitted: its verdict would be
    the same, and it would use up one of the run's attempts. A tree whose attempt failed
    with no verdict was never checked, so it may be sent again."""
    for attempt in counted:
        stored = submitted_archive(attempt)
        if stored is not None and archive_files(stored) == files:
            number = attempt.request["outside_candidate"]["number"]
            raise HostedHarnessError(
                "fix_outside_tree_checked",
                f"This exact change was already checked as attempt {number}. Change"
                " something, then submit again.",
                status_code=409,
            )


# Folders git keeps out of a diff, so a change in them would not reach the engine.
UNTRACKED_FOLDERS = frozenset({"__pycache__"})
# Attributes that make git rewrite a file on checkout or commit (line endings, filters).
REWRITING_ATTRIBUTES = frozenset({"text", "eol", "crlf", "filter"})


def _refuse_unrebuildable(base: dict[str, bytes], files: dict[str, bytes]) -> None:
    """Refuse a tree the engine could not rebuild as sent. It applies the tree's change to
    the base commit as a git diff, so a change git keeps out of the diff, text it cannot
    carry, or attributes that rewrite files would come back different, and the attempt
    would fail instead of getting a verdict."""
    changed = sorted(path for path in files if base.get(path) != files[path])
    if hidden := [path for path in changed if UNTRACKED_FOLDERS & set(path.split("/"))]:
        raise HostedHarnessError(
            "fix_outside_tree_untracked",
            "the tree changes files that git does not track, under"
            f" __pycache__: {', '.join(hidden[:10])}",
            status_code=422,
        )
    if not_utf8 := [path for path in changed if not _utf8_or_binary(files[path])]:
        raise HostedHarnessError(
            "fix_outside_tree_encoding",
            "the tree changes text files that are not UTF-8:"
            f" {', '.join(not_utf8[:10])}",
            status_code=422,
        )
    # The base's own attributes are in its checkout already: only a changed one differs.
    if rewriting := [
        path
        for path in changed
        if path.rsplit("/", 1)[-1] == ".gitattributes" and _rewrites_files(files[path])
    ]:
        raise HostedHarnessError(
            "fix_outside_tree_attributes",
            "the tree changes a .gitattributes to change line endings or set a filter:"
            f" {', '.join(rewriting)}",
            status_code=422,
        )


def _utf8_or_binary(data: bytes) -> bool:
    """A file with a NUL byte is binary, and goes in the diff as such."""
    if b"\0" in data:
        return True
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def _rewrites_files(attributes: bytes) -> bool:
    for line in attributes.decode("utf-8", "replace").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        for setting in line.split()[1:]:
            # "-text" and "!eol" unset an attribute: nothing is rewritten.
            if setting.startswith(("-", "!")):
                continue
            if setting.split("=", 1)[0] in REWRITING_ATTRIBUTES:
                return True
    return False
