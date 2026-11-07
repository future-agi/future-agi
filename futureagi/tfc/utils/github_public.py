"""Anonymous GitHub reads, immutable caches and the harness' shared egress budget.

No simulation imports, credentials, ambient proxies or alternative HTTP transport.
The integration-owned safe_fetch restrictions must exist before this slice runs.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import logging
import math
import re
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from urllib.parse import quote, urlsplit

from django.conf import settings
from django.core.cache import cache
from redis.exceptions import RedisError

from model_hub.services.skills_hub.errors import SkillsHubError
from tfc.utils.ssrf_guard import SsrfResponse, safe_fetch

logger = logging.getLogger(__name__)
RATE_RESET_KEY = "harness:gh-rate-reset"
REMAINING_KEY = "harness:gh-remaining"
_HOUR = 3600
_SHA = re.compile(r"^[0-9a-f]{40}$")
_REPOSITORY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9_.-]+$")
_BUCKETS = {"create": ("CREATE", 3, 25), "compare": ("COMPARE", 2, 5)}


def safe_repository(repository: str) -> bool:
    return (
        isinstance(repository, str)
        and len(repository) <= 200
        and bool(_REPOSITORY.fullmatch(repository))
        and not any(part in {".", ".."} for part in repository.split("/"))
    )


def safe_ref(ref: str) -> bool:
    return (
        isinstance(ref, str)
        and 0 < len(ref) <= 255
        and not ref.startswith(("/", "-"))
        and not ref.endswith(("/", ".", ".lock"))
        and ".." not in ref
        and "@{" not in ref
        and not any(part in {"", ".", ".."} for part in ref.split("/"))
        and not re.search(r"[\s\x00-\x1f\x7f\\~^:?*\[\]%#]", ref)
    )


def short_ref(ref: str | None) -> str | None:
    text = str(ref or "").strip()
    for prefix in ("refs/heads/", "refs/tags/"):
        if text.startswith(prefix):
            text = text[len(prefix) :]
            break
    return text if text and text != "HEAD" else None


def _hour_key() -> str:
    # Keep these identities byte-for-byte compatible with existing harness traffic.
    return "harness:gh-calls:" + datetime.now(UTC).strftime("%Y%m%d%H")


def rate_limited_now() -> bool:
    reset = cache.get(RATE_RESET_KEY)
    return reset is not None and float(reset) > time.time()


def remaining() -> int | None:
    value = cache.get(REMAINING_KEY)
    return int(value) if value is not None else None


def _count(key: str) -> int:
    cache.add(key, 0, _HOUR)
    # An increment failure is never replaced with a guessed count of one.
    return cache.incr(key)


def _limits(bucket: str) -> tuple[int, int]:
    if bucket == "skills":
        return (
            max(
                0, min(3, int(getattr(settings, "SKILLS_GITHUB_ORG_HOURLY_BUDGET", 3)))
            ),
            max(0, min(10, int(getattr(settings, "SKILLS_GITHUB_HOURLY_BUDGET", 10)))),
        )
    name, share, cap = _BUCKETS.get(bucket, ("WEBHOOK", 3, 10))
    return (
        int(getattr(settings, f"HARNESS_GITHUB_{name}_SHARE", share)),
        int(getattr(settings, f"HARNESS_GITHUB_{name}_BUDGET", cap)),
    )


def within_share(bucket: str, owner: str | None) -> bool:
    share, cap = _limits(bucket)
    key = f"{_hour_key()}:{bucket}"
    if owner and _count(f"{key}:{owner.lower()}") > share:
        return False
    return _count(key) <= cap


def take_call(reserve: int, purpose: str, owner: str | None = None) -> bool:
    """Compatibility entry point for the harness and the Skills REST allocation."""
    budget = min(50, int(getattr(settings, "HARNESS_GITHUB_HOURLY_BUDGET", 50)))
    key = _hour_key()
    capped = ("webhook", "create", "compare", "skills")
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


def _unavailable() -> SkillsHubError:
    return SkillsHubError(
        "github_budget_unavailable",
        "GitHub's shared request budget is unavailable.",
        status=503,
    )


def _cache_ready() -> None:
    backend = settings.CACHES["default"]["BACKEND"]
    if "redis" not in backend.lower() and "memcached" not in backend.lower():
        raise _unavailable()


def _cache_get(key: str) -> object:
    try:
        return cache.get(key)
    except (OSError, ValueError, RuntimeError, RedisError) as exc:
        logger.warning("github_cache_unavailable", exc_info=True)
        raise _unavailable() from exc


def _cache_set(key: str, value: object, ttl: int) -> None:
    try:
        cache.set(key, value, ttl)
    except (OSError, ValueError, RuntimeError, RedisError) as exc:
        logger.warning("github_cache_unavailable", exc_info=True)
        raise _unavailable() from exc


def _rate_error(reset: float) -> SkillsHubError:
    return SkillsHubError(
        "github_rate_limited",
        "GitHub requests are temporarily limited.",
        status=429,
        detail={"retry_at": datetime.fromtimestamp(reset, UTC).isoformat()},
    )


def _admit(organization_id: str) -> None:
    _cache_ready()
    try:
        reset = cache.get(RATE_RESET_KEY)
        if reset and float(reset) > time.time():
            raise _rate_error(float(reset))
        if not take_call(0, "skills", organization_id):
            raise _rate_error((int(time.time()) // _HOUR + 1) * _HOUR)
    except (OSError, ValueError, RuntimeError, RedisError) as exc:
        logger.warning("github_budget_unavailable", exc_info=True)
        raise _unavailable() from exc


def _retry_at(headers: dict) -> float:
    now = time.time()
    values = [now + 60]
    for name, relative in (("Retry-After", True), ("X-RateLimit-Reset", False)):
        raw = headers.get(name, "0")
        try:
            value = float(raw)
            reset = now + value if relative else value
        except (ValueError, TypeError):
            try:
                reset = parsedate_to_datetime(raw).timestamp() if relative else 0
            except (ValueError, TypeError, OverflowError):
                continue
        if math.isfinite(reset) and 0 <= reset <= 253402300799:
            values.append(reset)
    # Explicit Retry-After/reset values are not shortened by a local hourly cap.
    return max(values)


def _key(kind: str, identity: str) -> str:
    return "skills:github:" + kind + ":" + hashlib.sha256(identity.encode()).hexdigest()


@contextmanager
def _single_flight(key: str, deadline: float) -> Iterator[None]:
    """A cache lease coalesces identical anonymous reads across workers."""
    lock = key + ":lock"
    _cache_ready()
    try:
        while not cache.add(lock, True, 65):
            if time.monotonic() >= deadline:
                raise SkillsHubError(
                    "github_source_unavailable", "GitHub preview timed out.", status=404
                )
            time.sleep(min(0.05, max(0, deadline - time.monotonic())))
        try:
            yield
        finally:
            # Requests have a shorter, enforced deadline than this lease.
            if time.monotonic() < deadline:
                cache.delete(lock)
    except (OSError, ValueError, RuntimeError, RedisError) as exc:
        logger.warning("github_coalescing_unavailable", exc_info=True)
        raise _unavailable() from exc


def _fetch(
    url: str,
    *,
    organization_id: str,
    deadline: float,
    max_bytes: int,
    accept: str,
    etag: str | None = None,
    allow_not_found: bool = False,
) -> SsrfResponse:
    required = {
        "allowed_hosts",
        "allowed_schemes",
        "allowed_ports",
        "deadline",
        "allowed_urls",
        "allowed_content_encodings",
    }
    if not required.issubset(inspect.signature(safe_fetch).parameters):
        raise SkillsHubError(
            "skills_unavailable",
            "Restricted GitHub transport is not enabled.",
            status=503,
        )
    if time.monotonic() >= deadline:
        raise SkillsHubError(
            "github_source_unavailable", "GitHub preview timed out.", status=404
        )
    # Codeload is not a REST request; API requests, including conditional ones, share the cap.
    if urlsplit(url).hostname == "api.github.com":
        _admit(organization_id)
    headers = {
        "Accept": accept,
        "Accept-Encoding": "identity",
        "User-Agent": "futureagi-skills",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if etag:
        headers["If-None-Match"] = etag
    try:
        response = safe_fetch(
            url,
            method="GET",
            headers=headers,
            timeout=min(15, deadline - time.monotonic()),
            max_bytes=max_bytes,
            allowed_hosts={"api.github.com", "codeload.github.com"},
            allowed_schemes={"https"},
            allowed_ports={443},
            deadline=deadline,
            allowed_urls={url},
            allowed_content_encodings={"identity", ""},
        )
    except ValueError as exc:
        # Preserve the existing guard's byte-limit signal without echoing details.
        if str(exc).startswith("URL body exceeds "):
            code = (
                "github_archive_too_large"
                if accept == "application/zip"
                else "payload_too_large"
            )
            raise SkillsHubError(
                code, "The GitHub response exceeds its byte limit.", status=413
            ) from exc
        # Do not return upstream bodies, arbitrary redirect targets or raw errors.
        raise SkillsHubError(
            "github_source_unavailable", "GitHub could not be read safely.", status=404
        ) from exc
    if time.monotonic() >= deadline:
        raise SkillsHubError(
            "github_source_unavailable", "GitHub preview timed out.", status=404
        )
    if len(response.content) > max_bytes:
        code = (
            "github_archive_too_large"
            if accept == "application/zip"
            else "payload_too_large"
        )
        raise SkillsHubError(
            code, "The GitHub response exceeds its byte limit.", status=413
        )
    if response.status_code in (403, 429):
        reset = _retry_at(response.headers)
        _cache_set(RATE_RESET_KEY, reset, max(1, int(reset - time.time()) + 1))
        raise _rate_error(reset)
    left = response.headers.get("X-RateLimit-Remaining")
    if left is not None and str(left).isdigit():
        _cache_set(REMAINING_KEY, int(left), _HOUR)
        if int(left) == 0:
            reset = _retry_at(response.headers)
            _cache_set(RATE_RESET_KEY, reset, max(1, int(reset - time.time()) + 1))
    if response.status_code not in (200, 304) and not (
        allow_not_found and response.status_code == 404
    ):
        raise SkillsHubError(
            "github_source_unavailable",
            "The public GitHub source is unavailable.",
            status=404,
        )
    return response


def resolve_ref_kind(
    *, repo: str, ref: str, default_branch: str, organization_id: str, deadline: float
) -> str:
    """Choose an explicit ref namespace before the single commit resolution."""
    if not safe_repository(repo) or not safe_ref(ref):
        raise SkillsHubError(
            "github_source_invalid", "Use a valid repository and ref.", status=400
        )
    if ref == "HEAD":
        return "default"
    if _SHA.fullmatch(ref):
        return "commit"
    if ref.startswith("refs/tags/"):
        return "tag"
    if ref.startswith("refs/heads/") or ref == default_branch:
        return "branch"
    key = _key("ref-kind", repo.lower() + ":" + ref)
    with _single_flight(key, deadline):
        cached = _cache_get(key)
        if cached:
            return cached
        response = _fetch(
            f"https://api.github.com/repos/{repo}/git/ref/heads/{quote(ref, safe='')}",
            organization_id=organization_id,
            deadline=deadline,
            max_bytes=256 * 1024,
            accept="application/vnd.github+json",
            allow_not_found=True,
        )
        if response.status_code == 404:
            # Resolve the exact tags namespace next; a missing tag fails closed.
            kind = "tag"
        else:
            try:
                value = json.loads(response.content)
                if value["ref"] != "refs/heads/" + ref or not _SHA.fullmatch(
                    value["object"]["sha"]
                ):
                    raise ValueError("Invalid branch metadata")
            except (ValueError, KeyError, TypeError) as exc:
                raise SkillsHubError(
                    "github_source_unavailable",
                    "GitHub returned invalid ref metadata.",
                    status=404,
                ) from exc
            kind = "branch"
        _cache_set(key, kind, 60)
        return kind


def repository_metadata(*, repo: str, organization_id: str, deadline: float) -> dict:
    if not safe_repository(repo):
        raise SkillsHubError(
            "github_source_invalid", "Use a public GitHub repository.", status=400
        )
    key = _key("metadata", repo.lower())
    with _single_flight(key, deadline):
        cached = _cache_get(key)
        if cached:
            return cached
        response = _fetch(
            f"https://api.github.com/repos/{repo}",
            organization_id=organization_id,
            deadline=deadline,
            max_bytes=256 * 1024,
            accept="application/vnd.github+json",
        )
        try:
            value = json.loads(response.content)
            canonical, repository_id, branch = (
                value["full_name"],
                value["id"],
                value["default_branch"],
            )
            if (
                not safe_repository(canonical)
                or type(repository_id) is not int
                or repository_id <= 0
                or not safe_ref(branch)
                or value.get("private") is not False
            ):
                raise ValueError("Invalid metadata")
        except (ValueError, KeyError, TypeError) as exc:
            raise SkillsHubError(
                "github_source_unavailable",
                "GitHub returned invalid repository metadata.",
                status=404,
            ) from exc
        result = {
            "repo": canonical,
            "repository_id": repository_id,
            "default_branch": branch,
        }
        _cache_set(key, result, 60)
        return result


def sha_response(
    *,
    status_code: int,
    body: bytes,
    etag: str | None,
    cached: dict | None,
    expected_sha: str | None = None,
) -> dict:
    """Shared SHA/ETag pairing for Skills and the harness compatibility wrapper."""
    if status_code == 304:
        if (
            not cached
            or not cached.get("etag")
            or not _SHA.fullmatch(cached.get("sha", ""))
        ):
            raise SkillsHubError(
                "github_source_unavailable",
                "GitHub returned an unpaired conditional response.",
                status=404,
            )
        sha = cached["sha"]
    elif status_code == 200:
        sha = body.decode("ascii", errors="replace").strip()
    else:
        raise SkillsHubError(
            "github_source_unavailable",
            "The public GitHub ref is unavailable.",
            status=404,
        )
    if not _SHA.fullmatch(sha) or (expected_sha and expected_sha != sha):
        raise SkillsHubError(
            "github_source_unavailable",
            "GitHub returned an invalid commit identity.",
            status=404,
        )
    paired_etag = etag if status_code == 200 else etag or cached["etag"]
    return {"sha": sha, "etag": paired_etag}


def resolve_sha(*, repo: str, ref: str, organization_id: str, deadline: float) -> dict:
    """Keep ETag and SHA paired; fresh heads last 60s, conditional receipts 24h."""
    if not safe_repository(repo) or not safe_ref(ref):
        raise SkillsHubError(
            "github_source_invalid", "Use a valid repository and ref.", status=400
        )
    key = _key("sha", repo.lower() + ":" + ref)
    with _single_flight(key, deadline):
        cached = _cache_get(key)
        if cached and cached["fresh_until"] > time.time():
            return cached
        response = _fetch(
            f"https://api.github.com/repos/{repo}/commits/{quote(ref, safe='')}",
            organization_id=organization_id,
            deadline=deadline,
            max_bytes=4096,
            accept="application/vnd.github.sha",
            etag=cached["etag"] if cached else None,
        )
        result = sha_response(
            status_code=response.status_code,
            body=response.content,
            etag=response.headers.get("ETag"),
            cached=cached,
            expected_sha=ref if _SHA.fullmatch(ref) else None,
        )
        result["fresh_until"] = time.time() + 60
        _cache_set(key, result, 24 * _HOUR)
        return result


def download_archive(
    *, repo: str, sha: str, organization_id: str, deadline: float
) -> bytes:
    if not safe_repository(repo) or not _SHA.fullmatch(sha):
        raise SkillsHubError(
            "github_source_invalid", "Use an immutable GitHub commit.", status=400
        )
    key = _key("archive", repo.lower() + ":" + sha)
    with _single_flight(key, deadline):
        cached = _cache_get(key)
        if cached is not None:
            return cached
        response = _fetch(
            f"https://codeload.github.com/{repo}/zip/{sha}",
            organization_id=organization_id,
            deadline=deadline,
            max_bytes=10 * 1024 * 1024,
            accept="application/zip",
        )
        if response.status_code != 200 or response.headers.get(
            "Content-Encoding", "identity"
        ).lower() not in ("", "identity"):
            raise SkillsHubError(
                "github_source_unavailable",
                "GitHub returned an unsupported archive encoding.",
                status=404,
            )
        _cache_set(key, response.content, 24 * _HOUR)
        return response.content
