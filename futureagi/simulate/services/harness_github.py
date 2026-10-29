"""Unauthenticated reads of public GitHub repositories, on one shared budget.

Every caller (the head poller, push re-reads, comparisons) draws on the same hourly
count, so together they stay under GitHub's per-address limit. No token is sent.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

import requests
from django.conf import settings
from django.core.cache import cache

from simulate.services.hosted_harness_gateway import HostedSourceAcquirer

logger = logging.getLogger(__name__)

# The names a GitHub source is built from; the checks below add to them.
_REPOSITORY = HostedSourceAcquirer._REPOSITORY
_REF = HostedSourceAcquirer._REF
_TIMEOUT = (5, 15)
_HOUR = 3600
RATE_RESET_KEY = "harness:gh-rate-reset"
REMAINING_KEY = "harness:gh-remaining"


@dataclass(frozen=True)
class GitHubResponse:
    status: Literal[
        "ok", "not_modified", "not_found", "rate_limited", "budget", "error"
    ]
    body: Any = None
    etag: str | None = None


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


def _hour_key() -> str:
    return "harness:gh-calls:" + datetime.now(UTC).strftime("%Y%m%d%H")


def rate_limited_now() -> bool:
    reset = cache.get(RATE_RESET_KEY)
    return reset is not None and float(reset) > time.time()


def remaining() -> int | None:
    """GitHub's last reported remaining calls this hour, when known."""
    value = cache.get(REMAINING_KEY)
    return int(value) if value is not None else None


def _count(key: str) -> int:
    cache.add(key, 0, _HOUR)
    try:
        return cache.incr(key)
    except ValueError:
        # Expired between the add and the increment.
        cache.add(key, 1, _HOUR)
        return 1


# Setting name, one owner's hourly share and the hourly cap of each capped bucket.
# All three caps full (25 + 10 + 5) still leave the poller 10 of the default 50 calls
# an hour; with them idle it has its 30 (50 less its reserve of 20). A comparison is
# one call, two for a roll back, and is cached for ten minutes.
_BUCKETS = {"create": ("CREATE", 3, 25), "compare": ("COMPARE", 2, 5)}


def _limits(bucket: str) -> tuple[int, int]:
    """One owner's hourly share of ``bucket`` and its hourly cap; pushes' otherwise."""
    name, share, cap = _BUCKETS.get(bucket, ("WEBHOOK", 3, 10))
    return (
        int(getattr(settings, f"HARNESS_GITHUB_{name}_SHARE", share)),
        int(getattr(settings, f"HARNESS_GITHUB_{name}_BUDGET", cap)),
    )


def within_share(bucket: str, owner: str | None) -> bool:
    """Count one call against ``owner``'s hourly share of ``bucket``, then against
    the bucket's hourly cap; an owner past its share takes nothing from the cap."""
    share, cap = _limits(bucket)
    key = f"{_hour_key()}:{bucket}"
    if owner and _count(f"{key}:{owner.lower()}") > share:
        return False
    return _count(key) <= cap


def _take_call(reserve: int, purpose: str, owner: str | None = None) -> bool:
    """Count one call against the hour, unless it would eat into ``reserve``. Push
    re-reads (by repository owner, one App installation), creation checks and
    comparisons (by organization) have caps of their own, of which one owner gets a
    share, and never take a reserving caller's calls."""
    budget = int(getattr(settings, "HARNESS_GITHUB_HOURLY_BUDGET", 50))
    key = _hour_key()
    capped = ("webhook", "create", "compare")
    if reserve:
        left = remaining()
        if left is not None and left <= reserve:
            return False
        used = int(cache.get(key) or 0) - sum(
            min(int(cache.get(f"{key}:{bucket}") or 0), _limits(bucket)[1])
            for bucket in capped
        )
        if used >= budget - reserve:
            return False
    if purpose in capped and not within_share(purpose, owner):
        return False
    return _count(key) <= budget


def _reset_at(headers: Any) -> float:
    """When GitHub's window resets (X-RateLimit-Reset); 0 when not given."""
    try:
        return float(headers.get("X-RateLimit-Reset") or 0)
    except ValueError:
        return 0


def _note_rate_limit(response: requests.Response, purpose: str) -> bool:
    headers = response.headers
    retry_after = headers.get("Retry-After")
    if headers.get("X-RateLimit-Remaining") != "0" and retry_after is None:
        return False
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
    cache.set(RATE_RESET_KEY, reset_at, max(int(reset_at - time.time()) + 1, 1))
    logger.warning(
        "harness_github_rate_limited purpose=%s reset_at=%d", purpose, int(reset_at)
    )
    return True


def github_get(
    path: str,
    *,
    accept: str,
    etag: str | None = None,
    purpose: str,
    reserve: int = 0,
    owner: str | None = None,
) -> GitHubResponse:
    """``GET`` one API path built from checked names only; never authenticated."""
    if rate_limited_now():
        return GitHubResponse("rate_limited")
    if not _take_call(reserve, purpose, owner):
        logger.info("harness_github_budget_spent purpose=%s", purpose)
        return GitHubResponse("budget")
    headers = {
        "Accept": accept,
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
            "harness_github_request_failed purpose=%s error=%s",
            purpose,
            type(exc).__name__,
        )
        return GitHubResponse("error")
    left = response.headers.get("X-RateLimit-Remaining")
    if left is not None and str(left).isdigit():
        # Gone when GitHub's window resets, so a low count never outlives it.
        reset = _reset_at(response.headers)
        ttl = max(int(reset - time.time()), 1) if reset else _HOUR
        cache.set(REMAINING_KEY, int(left), ttl)
    code = response.status_code
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
    if code in (403, 429) and _note_rate_limit(response, purpose):
        return GitHubResponse("rate_limited")
    logger.warning("harness_github_unexpected purpose=%s status=%s", purpose, code)
    return GitHubResponse("error")
