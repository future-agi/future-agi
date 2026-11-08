"""Installed only in the E2E overlay. Never included in a production image.

Transport and scenario-scoped GitHub policy time/cache namespaces are substituted.
Scoped connection resolution, protocol normalization, job reservations, workflows,
source parsing/rights/CAS and the egress budget algorithm remain real. No URL/key
setting in the product enables this adapter.
"""

from __future__ import annotations

import base64
import re
import time
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import wraps
from urllib.parse import urlsplit

from django.apps import AppConfig

FIXTURE_ORIGIN = "http://skills-fakes:8080"
SYNTHETIC_KEY = re.compile(r"^e2e-skills-[a-z0-9-]{8,80}$")


def fixture_completion(real_completion):
    @wraps(real_completion)
    async def complete(*args, **kwargs):
        if kwargs.get("api_base") == "https://api.openai.com/v1":
            if not SYNTHETIC_KEY.fullmatch(kwargs.get("api_key", "")):
                raise ValueError("E2E Skills transport requires a synthetic credential")
            kwargs = {**kwargs, "api_base": FIXTURE_ORIGIN + "/v1"}
        return await real_completion(*args, **kwargs)

    return complete


def fixture_fetch(real_fetch):
    # Preserve the restricted transport signature inspected by github_public._fetch.
    @wraps(real_fetch)
    def fetch(url, **kwargs):
        import httpx
        from tfc.utils.ssrf_guard import SsrfResponse

        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or parsed.hostname not in {"api.github.com", "codeload.github.com"}
            or parsed.port not in {None, 443}
            or parsed.username
            or parsed.password
            or kwargs.get("allowed_urls") != {url}
            or kwargs.get("allowed_hosts") != {"api.github.com", "codeload.github.com"}
            or kwargs.get("allowed_schemes") != {"https"}
            or kwargs.get("allowed_ports") != {443}
            or kwargs.get("allowed_content_encodings") != {"identity", ""}
        ):
            raise ValueError("E2E GitHub request lacks the production restrictions")
        remaining = kwargs["deadline"] - time.monotonic()
        if remaining <= 0:
            raise ValueError("E2E GitHub deadline elapsed")
        # Fixed address only, no redirects, environment proxies or arbitrary URLs.
        with httpx.Client(trust_env=False, follow_redirects=False) as client:
            response = client.post(
                FIXTURE_ORIGIN + "/github",
                json={"url": url, "headers": kwargs.get("headers", {})},
                timeout=min(kwargs["timeout"], remaining),
            )
        if response.status_code != 200:
            raise ValueError("Unrecorded GitHub request; live egress is forbidden")
        recording = response.json()
        raw = base64.b64decode(recording["body_base64"], validate=True)
        if len(raw) > kwargs["max_bytes"]:
            raise ValueError("URL body exceeds fixture byte limit")
        headers = recording.get("headers", {})
        if any(
            key.lower() == "content-encoding" and value not in {"", "identity"}
            for key, value in headers.items()
        ):
            raise ValueError("Unexpected recorded content encoding")
        if 300 <= recording["status"] < 400 and recording["status"] != 304:
            raise ValueError("Recorded redirect is not an allowed exact URL")
        return SsrfResponse(recording["status"], headers, raw, url)

    return fetch


@dataclass(frozen=True)
class GitHubClock:
    namespace: str
    now: int


_GITHUB_CLOCK: ContextVar[GitHubClock | None] = ContextVar(
    "e2e_github_clock", default=None
)


def github_clock(repo: str) -> GitHubClock:
    """Read one operation's frozen policy from its recorded repository owner."""
    import httpx
    from model_hub.services.skills_hub.errors import SkillsHubError

    try:
        with httpx.Client(trust_env=False, follow_redirects=False) as client:
            response = client.post(
                FIXTURE_ORIGIN + "/github/clock", json={"repo": repo}, timeout=5
            )
        response.raise_for_status()
        value = response.json()
        if (
            not SYNTHETIC_KEY.fullmatch(value["namespace"])
            or type(value["now"]) is not int
            or not 1893456000 <= value["now"] <= 1894060800
        ):
            raise ValueError("Invalid fixture clock")
        return GitHubClock(value["namespace"], value["now"])
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        raise SkillsHubError(
            "github_budget_unavailable",
            "The recorded GitHub clock is unavailable.",
            status=503,
        ) from exc


def fixture_github_operation(operation):
    @wraps(operation)
    def wrapped(*args, **kwargs):
        clock = github_clock(kwargs["repo"])
        token = _GITHUB_CLOCK.set(clock)
        try:
            return operation(*args, **kwargs)
        finally:
            _GITHUB_CLOCK.reset(token)

    return wrapped


class FixtureTime:
    """Only GitHub wall time is virtual; socket deadlines and leases stay real."""

    def time(self):
        clock = _GITHUB_CLOCK.get()
        return clock.now if clock else time.time()

    def __getattr__(self, name):
        return getattr(time, name)


class FixtureGitHubCache:
    """Real distributed cache/counters, isolated by scenario, with virtual data TTLs.

    The production 60s freshness and 3/10/50 REST budgets are unchanged. Moving a
    scenario's hour creates its next bucket; no shared cache/counter is cleared.
    Single-flight locks retain real expiry so a stopped worker cannot hang a test.
    """

    def __init__(self, backend):
        self.backend = backend

    def key(self, key):
        clock = _GITHUB_CLOCK.get()
        return f"{clock.namespace}:{key}" if clock else key

    def get(self, key, default=None):
        value = self.backend.get(self.key(key), default)
        if (
            _GITHUB_CLOCK.get()
            and isinstance(value, dict)
            and "e2e_expires_at" in value
        ):
            return (
                value["value"]
                if value["e2e_expires_at"] > _GITHUB_CLOCK.get().now
                else default
            )
        return value

    def set(self, key, value, timeout):
        clock = _GITHUB_CLOCK.get()
        if clock:
            value = {"e2e_expires_at": clock.now + timeout, "value": value}
            timeout = 24 * 3600  # bounded real cleanup, not the test's policy clock
        return self.backend.set(self.key(key), value, timeout)

    def add(self, key, value, timeout):
        return self.backend.add(self.key(key), value, timeout)

    def incr(self, key):
        return self.backend.incr(self.key(key))

    def delete(self, key):
        return self.backend.delete(self.key(key))


def install_github_clock(module):
    real_hour_key = module._hour_key

    def hour_key():
        clock = _GITHUB_CLOCK.get()
        if clock:
            return "harness:gh-calls:" + datetime.fromtimestamp(
                clock.now, UTC
            ).strftime("%Y%m%d%H")
        return real_hour_key()

    module.cache = FixtureGitHubCache(module.cache)
    module.time = FixtureTime()
    module._hour_key = hour_key
    for name in (
        "repository_metadata",
        "resolve_ref_kind",
        "resolve_sha",
        "download_archive",
    ):
        setattr(module, name, fixture_github_operation(getattr(module, name)))


class SkillsFixtureConfig(AppConfig):
    name = "tfc.e2e_skills_transport"
    label = "e2e_skills_transport"

    def ready(self):
        import litellm
        from tfc.utils import github_public

        litellm.acompletion = fixture_completion(litellm.acompletion)
        github_public.safe_fetch = fixture_fetch(github_public.safe_fetch)
        install_github_clock(github_public)
