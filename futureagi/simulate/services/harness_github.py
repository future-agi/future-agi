"""Reads of GitHub repositories through the GitHub App.

Every read carries an installation token: the workspace's own installation when it
has connected GitHub, Future AGI's own installation otherwise. Without either, no
call is made. GitHub alone limits each installation, and when it asks one to wait,
only that installation's reads wait.
"""

from __future__ import annotations

import math
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

import requests
import structlog
from django.conf import settings
from django.core.cache import cache

from simulate.services.hosted_harness import HostedHarnessError
from simulate.services.hosted_harness_gateway import HostedSourceAcquirer

logger = structlog.get_logger(__name__)

# The names a GitHub source is built from; the checks below add to them.
_REPOSITORY = HostedSourceAcquirer._REPOSITORY
_REF = HostedSourceAcquirer._REF
_TIMEOUT = (5, 15)
_HOUR = 3600
_HEADROOM_SHARE = 0.2
_HEADROOM_WARNED_FOR = 600

ReadStatus = Literal[
    "ok",
    "not_modified",
    "not_found",
    "rate_limited",
    "not_connected",
    "revoked",
    "error",
]


@dataclass(frozen=True)
class GitHubResponse:
    status: ReadStatus
    body: Any = None
    etag: str | None = None
    reset_at: float | None = None


@dataclass(frozen=True)
class ReadCredential:
    token: str | None = field(default=None, repr=False)
    installation_id: str | None = None
    workspace_owned: bool = False


NO_CREDENTIAL = ReadCredential()


def safe_repository(repository: str) -> bool:
    """``owner/name`` with no ``.`` or ``..`` segment."""
    text = str(repository or "")
    return bool(_REPOSITORY.fullmatch(text)) and not any(
        part in (".", "..") for part in text.split("/")
    )


def safe_ref(ref: str) -> bool:
    """A branch or tag name that can't climb out of the path it is put in."""
    text = str(ref or "")
    return (
        bool(_REF.fullmatch(text))
        and ".." not in text
        and not text.startswith(("/", "-"))
        and "." not in text.split("/")
    )


def short_ref(ref: str | None) -> str | None:
    """A branch or tag by its short name; ``None`` for the default branch."""
    text = str(ref or "").strip()
    for prefix in ("refs/heads/", "refs/tags/"):
        if text.startswith(prefix):
            text = text[len(prefix) :]
            break
    return text if text and text != "HEAD" else None


def _workspace_installation(organization_id: Any, workspace_id: Any) -> str | None:
    if not organization_id:
        return None
    from integrations.models import (
        ConnectionStatus,
        IntegrationConnection,
        IntegrationPlatform,
    )

    installation_id = (
        IntegrationConnection.no_workspace_objects.filter(
            organization_id=organization_id,
            workspace_id=workspace_id,
            platform=IntegrationPlatform.GITHUB,
            status=ConnectionStatus.ACTIVE,
        )
        .values_list("external_project_name", flat=True)
        .first()
    )
    return str(installation_id) if installation_id else None


@contextmanager
def read_credential(
    organization_id: Any = None, workspace_id: Any = None
) -> Iterator[ReadCredential]:
    from integrations.services.github_app import (
        GitHubAppError,
        fallback_read_token,
        installation_token,
        revoke_installation_token,
    )

    installation_id = _workspace_installation(organization_id, workspace_id)
    token = None
    if installation_id:
        try:
            token = installation_token(installation_id)
        except GitHubAppError as exc:
            logger.warning(
                "harness_github_workspace_token_unavailable",
                installation_id=installation_id,
                code=exc.code,
            )
    if token:
        try:
            yield ReadCredential(token, installation_id, workspace_owned=True)
        finally:
            revoke_installation_token(token)
        return
    fallback_token = fallback_read_token()
    if fallback_token:
        yield ReadCredential(
            fallback_token,
            str(getattr(settings, "GITHUB_APP_FALLBACK_INSTALLATION_ID", "")),
        )
        return
    yield NO_CREDENTIAL


def _reset_key(installation_id: str | None) -> str:
    return f"harness:gh-rate-reset:{installation_id}"


def _remaining_key(installation_id: str | None) -> str:
    return f"harness:gh-remaining:{installation_id}"


def waiting_until(credential: ReadCredential) -> float | None:
    if not credential.token:
        return None
    reset = cache.get(_reset_key(credential.installation_id))
    return float(reset) if reset is not None and float(reset) > time.time() else None


def remaining(credential: ReadCredential) -> int | None:
    """GitHub's last reported calls left this hour for the credential's installation."""
    value = cache.get(_remaining_key(credential.installation_id))
    return int(value) if value is not None else None


def _header_int(headers: Any, name: str) -> int | None:
    value = headers.get(name)
    return int(value) if value is not None and str(value).isdigit() else None


def _reset_at(headers: Any) -> float:
    """When GitHub's window resets (X-RateLimit-Reset); 0 when not given."""
    try:
        return float(headers.get("X-RateLimit-Reset") or 0)
    except ValueError:
        return 0


def _note_remaining(
    response: requests.Response, credential: ReadCredential, purpose: str
) -> None:
    left = _header_int(response.headers, "X-RateLimit-Remaining")
    if left is None:
        return
    reset = _reset_at(response.headers)
    ttl = max(int(reset - time.time()), 1) if reset else _HOUR
    cache.set(_remaining_key(credential.installation_id), left, ttl)
    limit = _header_int(response.headers, "X-RateLimit-Limit")
    if (
        limit
        and left < limit * _HEADROOM_SHARE
        and cache.add(
            f"harness:gh-headroom-warned:{credential.installation_id}",
            1,
            _HEADROOM_WARNED_FOR,
        )
    ):
        logger.warning(
            "harness_github_headroom_low",
            installation_id=credential.installation_id,
            workspace_owned=credential.workspace_owned,
            remaining=left,
            limit=limit,
            purpose=purpose,
        )


def _note_rate_limit(
    response: requests.Response, credential: ReadCredential, purpose: str
) -> float | None:
    headers = response.headers
    retry_after = headers.get("Retry-After")
    if headers.get("X-RateLimit-Remaining") != "0" and retry_after is None:
        return None
    try:
        reset_at = (
            time.time() + float(retry_after)
            if retry_after is not None
            else _reset_at(headers)
        )
    except ValueError:
        reset_at = 0
    if reset_at <= time.time():
        reset_at = time.time() + 60
    cache.set(
        _reset_key(credential.installation_id),
        reset_at,
        max(int(reset_at - time.time()) + 1, 1),
    )
    logger.warning(
        "harness_github_rate_limited",
        installation_id=credential.installation_id,
        workspace_owned=credential.workspace_owned,
        purpose=purpose,
        reset_at=int(reset_at),
    )
    return reset_at


def github_get(
    path: str,
    *,
    accept: str,
    credential: ReadCredential,
    etag: str | None = None,
    purpose: str,
) -> GitHubResponse:
    """``GET`` one API path built from checked names only, with the credential's
    installation token; nothing is sent without one."""
    if not credential.token:
        logger.info("harness_github_not_connected", purpose=purpose)
        return GitHubResponse("not_connected")
    waiting = waiting_until(credential)
    if waiting:
        return GitHubResponse("rate_limited", reset_at=waiting)
    headers = {
        "Accept": accept,
        "Authorization": f"Bearer {credential.token}",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "futureagi-harness",
    }
    if etag:
        headers["If-None-Match"] = etag
    base = str(getattr(settings, "GITHUB_API_ENDPOINT", "https://api.github.com"))
    try:
        response = requests.get(
            base.rstrip("/") + path, headers=headers, timeout=_TIMEOUT
        )
    except requests.RequestException as exc:
        logger.warning(
            "harness_github_request_failed",
            purpose=purpose,
            error=type(exc).__name__,
        )
        return GitHubResponse("error")
    _note_remaining(response, credential, purpose)
    code = response.status_code
    if code == 401:
        if credential.workspace_owned:
            logger.warning(
                "harness_github_token_rejected",
                installation_id=credential.installation_id,
                purpose=purpose,
            )
            return GitHubResponse("revoked")
        from integrations.services.github_app import forget_fallback_read_token

        forget_fallback_read_token()
        logger.error("harness_github_fallback_token_rejected", purpose=purpose)
        return GitHubResponse("error")
    if code == 200:
        try:
            body = response.json() if "json" in accept else response.text
        except ValueError:
            return GitHubResponse("error")
        return GitHubResponse("ok", body, response.headers.get("ETag"))
    if code == 304:
        return GitHubResponse("not_modified", etag=etag)
    if code in (404, 422):
        return GitHubResponse("not_found")
    if code in (403, 429):
        limited_until = _note_rate_limit(response, credential, purpose)
        if limited_until:
            return GitHubResponse("rate_limited", reset_at=limited_until)
    logger.warning("harness_github_unexpected", purpose=purpose, status=code)
    return GitHubResponse("error")


def _wait_text(seconds: int) -> str:
    minutes = max(1, math.ceil(seconds / 60))
    return "1 minute" if minutes == 1 else f"{minutes} minutes"


def _seconds_until(at: float | None) -> int:
    return max(int(math.ceil((at or 0) - time.time())), 60)


def read_refused(
    status: str, reset_at: float | None = None
) -> HostedHarnessError | None:
    if status == "not_connected":
        return HostedHarnessError(
            "github_not_connected",
            "Connect GitHub in Settings > Integrations to read this repository",
            status_code=422,
        )
    if status == "revoked":
        return HostedHarnessError(
            "github_installation_revoked",
            "The GitHub App was removed or suspended; reconnect GitHub in Settings >"
            " Integrations",
            status_code=422,
        )
    if status == "rate_limited":
        wait = _seconds_until(reset_at)
        return HostedHarnessError(
            "github_rate_limited",
            f"GitHub's request limit is reached; try again in {_wait_text(wait)}",
            status_code=429,
            retryable=True,
            details={"retry_after": wait},
        )
    if status == "error":
        return HostedHarnessError(
            "source_unreachable",
            "GitHub didn't respond; try again in a minute",
            status_code=503,
            retryable=True,
        )
    return None


def take_workspace_read(workspace_id: Any) -> None:
    limit = int(getattr(settings, "HARNESS_GITHUB_WORKSPACE_HOURLY_LIMIT", 200))
    if not workspace_id or limit <= 0:
        return
    now = datetime.now(UTC)
    key = f"harness:gh-workspace-reads:{workspace_id}:{now:%Y%m%d%H}"
    cache.add(key, 0, _HOUR)
    try:
        used = cache.incr(key)
    except ValueError:
        cache.add(key, 1, _HOUR)
        used = 1
    if used <= limit:
        return
    wait = _HOUR - (now.minute * 60 + now.second)
    logger.warning(
        "harness_github_workspace_limit", workspace_id=str(workspace_id), limit=limit
    )
    raise HostedHarnessError(
        "github_workspace_limit",
        "Too many repository reads from this workspace this hour; try again in"
        f" {_wait_text(wait)}",
        status_code=429,
        retryable=True,
        details={"retry_after": wait},
    )
