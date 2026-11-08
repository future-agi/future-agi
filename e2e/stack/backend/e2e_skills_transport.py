"""Installed only in the E2E overlay. Never included in a production image.

Only transport is replaced: scoped connection resolution, protocol normalization,
job reservations, workflows, source parsing/rights/CAS and egress budgets stay real.
No URL/key setting in the product enables this adapter.
"""

from __future__ import annotations

import base64
import re
import time
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


class SkillsFixtureConfig(AppConfig):
    name = "tfc.e2e_skills_transport"
    label = "e2e_skills_transport"

    def ready(self):
        import litellm
        from tfc.utils import github_public

        litellm.acompletion = fixture_completion(litellm.acompletion)
        github_public.safe_fetch = fixture_fetch(github_public.safe_fetch)
