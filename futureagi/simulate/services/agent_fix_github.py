"""Ship a GitHub agent's winner, or a candidate only its must-pass check refused, as a pull
request on its repository: the candidate's patch, applied to the commit the fix started
from, on a branch of its own."""

import re
import tempfile
from math import floor
from pathlib import Path
from textwrap import shorten
from typing import Any
from urllib.parse import quote

import requests
import structlog
from django.conf import settings

from simulate.models import AgentFixCandidate, HarnessAgentVersionRuntime
from simulate.selectors.agent_fix import limited_validation, overridable_regressions
from simulate.services.agent_fix_engine import load_object
from simulate.services.agent_fix_runs import (
    base_moved,
    record_override,
    require_shippable,
)
from simulate.services.harness_github import safe_repository, short_ref
from simulate.services.hosted_harness import HostedHarnessError
from simulate.services.hosted_harness_gateway import (
    GitHubAppTokenProvider,
    github_checkout,
    run_git,
)

logger = structlog.get_logger(__name__)

_COMMIT = re.compile(r"^[0-9a-fA-F]{40}$")
_AUTHOR = ("Future AGI", "fix-my-agent@futureagi.com")
_NO_DRAFTS = "draft pull requests are not supported"
_TITLE_CHARS = 120
_LIMITED_NOTE = (
    "**Limited validation.** Too few failing cases were held back to verify this fix."
    " Review it closely before you merge."
)


def open_pull_request(
    candidate: AgentFixCandidate, *, user: Any, override_reason: str = ""
) -> str:
    """Open the candidate's pull request once and return its URL. Refused when the ref the
    agent runs from (its branch or tag) moved since the fix's commit, whatever the reason
    given: the fix proved its change on that commit alone."""
    require_shippable(candidate, override_reason=override_reason)
    if candidate.pull_request_url:
        return candidate.pull_request_url
    fix_run = candidate.fix_run
    runtime = HarnessAgentVersionRuntime.no_workspace_objects.filter(
        agent_version_id=fix_run.base_agent_version_id,
        source_kind=HarnessAgentVersionRuntime.SourceKind.GITHUB,
    ).first()
    installation_id = ""
    if runtime is not None:
        access = (runtime.agent_payload or {}).get("source_access") or {}
        installation_id = str(access.get("installation_id") or "")
    if runtime is None or not installation_id:
        raise HostedHarnessError(
            "fix_github_not_connected",
            "A pull request needs the agent's repository connected through the Future AGI"
            " GitHub App; download the patch instead",
            status_code=422,
        )
    source = runtime.source or {}
    repository = str(source.get("repository") or "")
    if not safe_repository(repository):
        raise HostedHarnessError(
            "github_repository_invalid", "invalid GitHub repository", status_code=422
        )
    run = fix_run.source_run
    run_source = ((run.payload if run else None) or {}).get("source") or {}
    commit = str(run_source.get("commit_sha") or "")
    if not commit:
        # The patch was made on the commit the run checked out; a branch tip may differ.
        raise HostedHarnessError(
            "fix_github_commit_unknown",
            "the commit the run checked out is not known; download the patch instead",
            status_code=409,
        )
    patch = load_object(candidate.patch_object_key) or b""
    branch = f"futureagi/fix-{str(fix_run.id)[:8]}-{candidate.attempt}"
    ref = str(source.get("ref") or "")
    with GitHubAppTokenProvider.from_settings().credential(installation_id) as token:
        base = _base_branch(repository, ref, token)
        head = _ref_head(repository, ref, base, token)
        if head is not None and head != commit:
            name = short_ref(ref) if ref.startswith("refs/tags/") else base
            raise base_moved(f"{name} @ {commit[:7]}", f"{name} @ {head[:7]}")
        # Before the push: the commit carries the reason, so a retry pushes the same commit.
        record_override(candidate, reason=override_reason, user=user)
        title, body = _description(candidate)
        _push(repository, commit, branch, patch, candidate, title, body, token)
        url = _pull_request(
            repository,
            branch,
            base,
            title,
            body,
            token,
            draft=limited_validation(fix_run) or bool(candidate.override_reason),
        )
    AgentFixCandidate.no_workspace_objects.filter(id=candidate.id).update(
        pull_request_url=url
    )
    candidate.pull_request_url = url
    logger.info(
        "agent_fix_pull_request_opened",
        fix_run=str(fix_run.id),
        candidate=str(candidate.id),
    )
    return url


def _percent(rate: float) -> str:
    # Half up, as the fix page rounds: 62.5% reads 63% in both places.
    return f"{floor(rate * 100 + 0.5)}%"


def _evidence(verdict: dict) -> str:
    """The pass rates a reviewer weighs, as the fix page shows them."""
    return ", ".join(
        f"{name} cases {_percent(before)} → {_percent(after)}"
        for name, (before, after) in (
            ("held-back", verdict.get("held_back_pass_rate") or (None, None)),
            ("target", verdict.get("target_pass_rate") or (None, None)),
        )
        if after is not None
    )


def _override_note(candidate: AgentFixCandidate) -> str:
    """What a candidate the must-pass check refused ships with, and the user's reason."""
    checks = "\n".join(f"- `{check}`" for check in overridable_regressions(candidate))
    reason = "\n".join(f"> {line}" for line in candidate.override_reason.splitlines())
    return (
        "**Known regression.** Fix my agent did not accept this change: these must-pass"
        f" checks regressed.\n\n{checks}\n\nIt ships anyway, for this reason:\n\n{reason}"
    )


def _description(candidate: AgentFixCandidate) -> tuple[str, str]:
    """The title and body, which say so when too few cases were held back to verify it,
    and when it ships with a known regression on the user's reason."""
    summary = candidate.summary.strip()
    first = summary.splitlines()[0] if summary else f"attempt {candidate.attempt}"
    verdict = candidate.verdict or {}
    reason = "; ".join(
        part for part in (_evidence(verdict), verdict.get("reason")) if part
    )
    limited = limited_validation(candidate.fix_run)
    overridden = bool(candidate.override_reason)
    proof = "Checked" if limited or overridden else "Proven"
    body = "\n\n".join(
        part
        for part in (
            _override_note(candidate) if overridden else "",
            _LIMITED_NOTE if limited else "",
            summary,
            f"{proof} by Fix my agent: {reason}" if reason else "",
            "Opened by Future AGI Fix my agent.",
        )
        if part
    )
    marks = ", ".join(
        mark
        for mark, applies in (
            ("limited validation", limited),
            ("known regression", overridden),
        )
        if applies
    )
    name = f"Fix my agent ({marks})" if marks else "Fix my agent"
    return shorten(f"{name}: {first}", width=_TITLE_CHARS, placeholder="…"), body


def _push(
    repository: str,
    commit: str,
    branch: str,
    patch: bytes,
    candidate: AgentFixCandidate,
    title: str,
    body: str,
    token: str,
) -> None:
    """Commit the patch on ``commit`` and push it to ``branch``. The commit is the same on
    every call, so a push the branch already has succeeds without changing it, and a branch
    someone changed since is refused, never overwritten."""
    with tempfile.TemporaryDirectory(prefix=f"agent-fix-{candidate.id}-") as temp:
        checkout = Path(temp) / "source"
        environment = github_checkout(repository, commit, checkout, token)
        patch_file = Path(temp) / "candidate.patch"
        patch_file.write_bytes(patch)
        stamp = f"{int(candidate.created_at.timestamp())} +0000"
        environment.update(
            {
                "GIT_AUTHOR_NAME": _AUTHOR[0],
                "GIT_AUTHOR_EMAIL": _AUTHOR[1],
                "GIT_AUTHOR_DATE": stamp,
                "GIT_COMMITTER_NAME": _AUTHOR[0],
                "GIT_COMMITTER_EMAIL": _AUTHOR[1],
                "GIT_COMMITTER_DATE": stamp,
            }
        )
        git = ["git", "-C", str(checkout)]
        for command, code, status_code in (
            ([*git, "apply", "--index", "--binary", str(patch_file)], "patch", 409),
            ([*git, "commit", "--no-verify", "-m", title, "-m", body], "patch", 409),
            ([*git, "push", "origin", f"HEAD:refs/heads/{branch}"], "push", 502),
        ):
            returncode, detail = run_git(command, environment, token)
            if returncode:
                raise _git_error(repository, branch, code, status_code, detail)


def _git_error(
    repository: str, branch: str, step: str, status_code: int, detail: str
) -> HostedHarnessError:
    lowered = detail.lower()
    if step == "push" and ("403" in lowered or "permission to" in lowered):
        return _no_write_access(repository)
    if step == "push" and ("rejected" in lowered or "non-fast-forward" in lowered):
        return HostedHarnessError(
            "fix_github_branch_changed",
            f"the branch {branch} changed on GitHub since the pull request was made",
            status_code=409,
        )
    return HostedHarnessError(
        f"fix_github_{step}_failed",
        detail.strip()[:500],
        status_code=status_code,
        retryable=status_code >= 500,
    )


def _no_write_access(repository: str) -> HostedHarnessError:
    return HostedHarnessError(
        "fix_github_no_write_access",
        f"the Future AGI GitHub App cannot write to {repository}: approve its request for"
        " contents and pull requests write access",
        status_code=422,
    )


def _base_branch(repository: str, ref: str, token: str) -> str:
    """The branch the agent is connected to; the repository's default when it names a tag
    or a commit, or nothing."""
    name = short_ref(ref)
    if name and not ref.startswith("refs/tags/") and not _COMMIT.fullmatch(name):
        return name
    return _api("GET", f"/repos/{repository}", token).json()["default_branch"]


def _ref_head(repository: str, ref: str, branch: str, token: str) -> str | None:
    """The commit the agent's ref points at on GitHub now: a tag's commit, else the head
    of ``branch``, the branch it runs from. None for a ref pinned to a commit, which never
    moves."""
    name = short_ref(ref)
    if name and _COMMIT.fullmatch(name):
        return None
    if ref.startswith("refs/tags/"):
        return _api("GET", f"/repos/{repository}/commits/{quote(name)}", token).json()[
            "sha"
        ]
    return _api(
        "GET", f"/repos/{repository}/git/ref/heads/{quote(branch, safe='/')}", token
    ).json()["object"]["sha"]


def _pull_request(
    repository: str,
    branch: str,
    base: str,
    title: str,
    body: str,
    token: str,
    *,
    draft: bool,
) -> str:
    """Open the pull request, or find the one already open for ``branch``. A repository
    without draft pull requests gets a normal one, and its title still says what it is."""
    path = f"/repos/{repository}/pulls"
    pull = {"title": title, "head": branch, "base": base, "body": body, "draft": draft}
    response = _api("POST", path, token, json=pull)
    if response.status_code == 422 and _NO_DRAFTS in response.text.lower():
        response = _api("POST", path, token, json={**pull, "draft": False})
    if response.status_code == 201:
        return response.json()["html_url"]
    if response.status_code == 403:
        raise _no_write_access(repository)
    if response.status_code == 422 and "already exists" in response.text:
        owner = repository.split("/")[0]
        found = _api(
            "GET",
            f"/repos/{repository}/pulls",
            token,
            params={"head": f"{owner}:{branch}", "state": "all"},
        ).json()
        if found:
            return found[0]["html_url"]
    raise HostedHarnessError(
        "fix_github_pull_request_failed",
        f"GitHub refused the pull request: {response.text[:300]}",
        status_code=502,
        retryable=response.status_code >= 500,
    )


def _api(method: str, path: str, token: str, **kwargs) -> requests.Response:
    try:
        response = requests.request(
            method,
            settings.GITHUB_API_ENDPOINT.rstrip("/") + path,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=(5, 30),
            **kwargs,
        )
    except requests.RequestException as exc:
        raise HostedHarnessError(
            "fix_github_unreachable",
            "GitHub could not be reached",
            status_code=502,
            retryable=True,
        ) from exc
    if method == "GET" and response.status_code != 200:
        raise HostedHarnessError(
            "fix_github_pull_request_failed",
            f"GitHub refused {path}: {response.status_code}",
            status_code=502,
            retryable=response.status_code >= 500,
        )
    return response
