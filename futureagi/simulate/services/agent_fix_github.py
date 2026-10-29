"""Ship a GitHub agent's winner as a pull request on its repository: the candidate's patch,
applied to the commit the fix started from, on a branch of its own."""

import re
import tempfile
from pathlib import Path

import requests
import structlog
from django.conf import settings

from simulate.models import AgentFixCandidate, HarnessAgentVersionRuntime
from simulate.services.agent_fix_engine import load_object
from simulate.services.agent_fix_runs import require_shippable
from simulate.services.harness_github import short_ref
from simulate.services.hosted_harness import HostedHarnessError
from simulate.services.hosted_harness_gateway import (
    GitHubAppTokenProvider,
    github_checkout,
    run_git,
)

logger = structlog.get_logger(__name__)

_COMMIT = re.compile(r"^[0-9a-fA-F]{40}$")
_AUTHOR = ("Future AGI", "fix-my-agent@futureagi.com")


def open_pull_request(candidate: AgentFixCandidate) -> str:
    """Open the candidate's pull request once and return its URL."""
    require_shippable(candidate)
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
    title, body = _description(candidate)
    with GitHubAppTokenProvider.from_settings().credential(installation_id) as token:
        _push(repository, commit, branch, patch, candidate, title, body, token)
        base = _base_branch(repository, str(source.get("ref") or ""), token)
        url = _pull_request(repository, branch, base, title, body, token)
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


def _description(candidate: AgentFixCandidate) -> tuple[str, str]:
    summary = candidate.summary.strip()
    first = summary.splitlines()[0] if summary else f"attempt {candidate.attempt}"
    reason = (candidate.verdict or {}).get("reason") or ""
    body = "\n\n".join(
        part
        for part in (
            summary,
            f"Proven by Fix my agent: {reason}" if reason else "",
            "Opened by Future AGI Fix my agent.",
        )
        if part
    )
    return f"Fix my agent: {first}"[:120], body


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


def _pull_request(
    repository: str, branch: str, base: str, title: str, body: str, token: str
) -> str:
    """Open the pull request, or find the one already open for ``branch``."""
    response = _api(
        "POST",
        f"/repos/{repository}/pulls",
        token,
        json={"title": title, "head": branch, "base": base, "body": body},
    )
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
