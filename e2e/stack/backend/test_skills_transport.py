"""Socket-free qualification of the test-image transport, not product behavior."""

import asyncio
import base64
import inspect
import time
from unittest.mock import AsyncMock, MagicMock

import pytest
from e2e_skills_transport import FIXTURE_ORIGIN, fixture_completion, fixture_fetch


def test_completion_keeps_protocol_and_refuses_non_synthetic_keys():
    transport = AsyncMock(return_value="fixture-response")
    wrapped = fixture_completion(transport)
    payload = {
        "api_base": "https://api.openai.com/v1",
        "api_key": "e2e-skills-qualified-0001",
        "messages": [{"role": "assistant", "content": None, "tool_calls": []}],
        "tools": [{"type": "function", "function": {"name": "load_skill"}}],
        "num_retries": 0,
        "timeout": 1,
    }
    assert asyncio.run(wrapped(**payload)) == "fixture-response"
    assert transport.call_args.kwargs == {
        **payload,
        "api_base": FIXTURE_ORIGIN + "/v1",
    }
    assert payload["api_base"] == "https://api.openai.com/v1"
    with pytest.raises(ValueError, match="synthetic credential"):
        asyncio.run(wrapped(**{**payload, "api_key": "not-a-fixture"}))
    assert transport.call_count == 1
    asyncio.run(wrapped(**{**payload, "api_base": "http://mock-llm:8080/v1"}))
    assert transport.call_args.kwargs["api_base"] == "http://mock-llm:8080/v1"


@pytest.fixture
def recorded(monkeypatch):
    import httpx
    from tfc.utils.ssrf_guard import safe_fetch

    client = MagicMock()
    constructor = MagicMock()
    constructor.return_value.__enter__.return_value = client
    monkeypatch.setattr(httpx, "Client", constructor)
    url = "https://api.github.com/repos/synthetic/qualification"
    result = {
        "status": 200,
        "headers": {"ETag": "synthetic"},
        "body_base64": base64.b64encode(b"synthetic bytes").decode(),
    }
    client.post.return_value.status_code = 200
    client.post.return_value.json.return_value = result
    options = {
        "method": "GET",
        "headers": {"Accept": "application/json"},
        "timeout": 2,
        "max_bytes": 1024,
        "allowed_urls": {url},
        "allowed_hosts": {"api.github.com", "codeload.github.com"},
        "allowed_schemes": {"https"},
        "allowed_ports": {443},
        "allowed_content_encodings": {"identity", ""},
        "deadline": time.monotonic() + 10,
    }
    return (
        fixture_fetch(safe_fetch),
        safe_fetch,
        url,
        options,
        client,
        constructor,
        result,
    )


def test_recording_preserves_restricted_signature_and_fixed_destination(recorded):
    fetch, original, url, options, client, constructor, _ = recorded
    assert inspect.signature(fetch) == inspect.signature(original)
    response = fetch(url, **options)
    assert response.content == b"synthetic bytes"
    assert response.status_code == 200
    assert response.headers["etag"] == "synthetic"
    assert response.final_url == url
    constructor.assert_called_once_with(trust_env=False, follow_redirects=False)
    assert client.post.call_args.args == (FIXTURE_ORIGIN + "/github",)
    assert client.post.call_args.kwargs["json"] == {
        "url": url,
        "headers": options["headers"],
    }


@pytest.mark.parametrize(
    "change",
    [
        {"allowed_hosts": {"api.github.com"}},
        {"allowed_urls": set()},
        {"allowed_ports": {80}},
        {"allowed_schemes": {"http"}},
        {"deadline": 0},
    ],
)
def test_transport_rejects_missing_policy_before_request(recorded, change):
    fetch, _, url, options, client, _, _ = recorded
    with pytest.raises(ValueError):
        fetch(url, **{**options, **change})
    client.post.assert_not_called()


@pytest.mark.parametrize("status", [301, 302, 307, 308])
def test_recorded_redirects_fail_without_following(recorded, status):
    fetch, _, url, options, client, _, result = recorded
    result.update(status=status, headers={"Location": "https://evil.invalid"})
    with pytest.raises(ValueError, match="redirect"):
        fetch(url, **options)
    assert client.post.call_count == 1


def test_unrecorded_or_oversized_response_never_falls_back(recorded):
    fetch, _, url, options, client, _, result = recorded
    client.post.return_value.status_code = 404
    with pytest.raises(ValueError, match="live egress"):
        fetch(url, **options)
    client.post.return_value.status_code = 200
    with pytest.raises(ValueError, match="URL body exceeds"):
        fetch(url, **{**options, "max_bytes": 1})
    result["headers"] = {"Content-Encoding": "gzip"}
    with pytest.raises(ValueError, match="encoding"):
        fetch(url, **options)


@pytest.fixture
def clocked_github(monkeypatch):
    import importlib.util
    from functools import wraps
    from types import SimpleNamespace
    from uuid import uuid4

    import e2e_skills_transport as overlay
    from django.core.cache.backends.locmem import LocMemCache
    from tfc.utils import github_public
    from tfc.utils.ssrf_guard import SsrfResponse, safe_fetch

    # Execute the actual production module in isolation; only its last transport,
    # cache backend and clock are substituted. No resolver/budget outcome is mocked.
    spec = importlib.util.spec_from_file_location(
        "isolated_github", github_public.__file__
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.settings = SimpleNamespace(
        CACHES={"default": {"BACKEND": "django_redis.cache.RedisCache"}}
    )
    backend = LocMemCache(str(uuid4()), {})
    module.cache = backend
    states = {
        repo: overlay.GitHubClock(f"e2e-skills-clock-{repo}", 1893456000)
        for repo in ("first", "second")
    }
    requests = []
    response = [SsrfResponse(200, {"ETag": '"a"'}, b"a" * 40, "synthetic")]

    @wraps(safe_fetch)
    def fetch(url, **kwargs):
        requests.append((url, kwargs))
        return response[0]

    module.safe_fetch = fetch
    overlay.install_github_clock(module)
    # Repository validation remains real; use valid synthetic names at the wrapper.
    monkeypatch.setattr(
        overlay, "github_clock", lambda repo: states[repo.split("/")[-1]]
    )

    def advance(repo, seconds):
        previous = states[repo]
        states[repo] = overlay.GitHubClock(previous.namespace, previous.now + seconds)

    def resolve(repo="first", organization_id="org"):
        return module.resolve_sha(
            repo="synthetic/" + repo,
            ref="main",
            organization_id=organization_id,
            deadline=time.monotonic() + 10,
        )

    return module, backend, requests, response, advance, resolve


def test_real_ref_freshness_conditional_pair_and_period_budget(clocked_github):
    from model_hub.services.skills_hub.errors import SkillsHubError
    from tfc.utils.ssrf_guard import SsrfResponse

    module, backend, requests, response, advance, resolve = clocked_github
    first = resolve()
    response[0] = SsrfResponse(200, {"ETag": '"b"'}, b"b" * 40, "synthetic")
    advance("first", 59)
    assert resolve() == first
    assert len(requests) == 1  # moving the fixture alone does not invalidate freshness
    advance("first", 1)
    assert resolve()["sha"] == "b" * 40
    assert requests[-1][1]["headers"]["If-None-Match"] == '"a"'
    response[0] = SsrfResponse(304, {}, b"", "synthetic")
    advance("first", 60)
    assert resolve()["sha"] == "b" * 40
    assert len(requests) == 3  # conditional REST requests consume the real allowance
    advance("first", 60)
    with pytest.raises(SkillsHubError) as error:
        resolve()
    assert error.value.code == "github_rate_limited"
    assert len(requests) == 3
    advance("first", 3420)  # next hour, not a raised budget or reset counter
    assert resolve()["sha"] == "b" * 40
    assert len(requests) == 4
    assert module._limits("skills") == (3, 10)
    assert backend.get("harness:gh-calls:2030010100") is None
    assert (
        backend.get("e2e-skills-clock-first:harness:gh-calls:2030010100:skills:org")
        == 4
    )
    assert (
        backend.get("e2e-skills-clock-first:harness:gh-calls:2030010101:skills:org")
        == 1
    )


def test_virtual_data_expiry_namespace_isolation_and_real_deadlines(clocked_github):
    import e2e_skills_transport as overlay
    from model_hub.services.skills_hub.errors import SkillsHubError

    module, _, requests, _, advance, resolve = clocked_github
    assert resolve("first")["sha"] == resolve("second")["sha"]
    assert len(requests) == 2
    advance("first", 3600)
    resolve("first")
    resolve("second")
    assert len(requests) == 3
    assert overlay._GITHUB_CLOCK.get() is None
    assert abs(module.time.time() - time.time()) < 1
    assert module.time.monotonic is time.monotonic
    # Even a virtual clock years ahead cannot make an elapsed socket deadline pass.
    with pytest.raises(SkillsHubError):
        module.resolve_sha(
            repo="synthetic/first", ref="expired", organization_id="org", deadline=0
        )
    assert len(requests) == 3
    assert overlay._GITHUB_CLOCK.get() is None

    clock = overlay.GitHubClock("e2e-skills-cache-0001", 1893456000)
    token = overlay._GITHUB_CLOCK.set(clock)
    try:
        module.cache.set("metadata", {"id": 123}, 60)
        assert module.cache.get("metadata") == {"id": 123}
        overlay._GITHUB_CLOCK.set(overlay.GitHubClock(clock.namespace, clock.now + 60))
        assert module.cache.get("metadata") is None
    finally:
        overlay._GITHUB_CLOCK.reset(token)


def test_concurrent_fixture_clocks_do_not_leak_between_workers(clocked_github):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    import e2e_skills_transport as overlay

    module, _, _, _, advance, _ = clocked_github
    advance("second", 3600)
    barrier = Barrier(2)

    @overlay.fixture_github_operation
    def operation(*, repo):
        barrier.wait(timeout=5)
        module.cache.set("same-key", repo, 60)
        barrier.wait(timeout=5)
        return module.time.time(), module.cache.get("same-key")

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(operation, repo="synthetic/first")
        second = pool.submit(operation, repo="synthetic/second")
        assert first.result() == (1893456000, "synthetic/first")
        assert second.result() == (1893459600, "synthetic/second")
    assert overlay._GITHUB_CLOCK.get() is None


def test_clock_does_not_raise_shared_or_deployment_caps(clocked_github):
    import e2e_skills_transport as overlay

    module, _, _, _, _, _ = clocked_github
    token = overlay._GITHUB_CLOCK.set(
        overlay.GitHubClock("e2e-skills-caps-0001", 1893456000)
    )
    try:
        assert all(module.take_call(0, "skills", str(owner)) for owner in range(10))
        assert not module.take_call(0, "skills", "new-owner")  # ten across orgs
        module.settings.HARNESS_GITHUB_HOURLY_BUDGET = 11
        assert module.take_call(0, "other")
        assert not module.take_call(
            0, "other"
        )  # common global allowance still enforced
        module.settings.SKILLS_GITHUB_ORG_HOURLY_BUDGET = 0
        assert not module.take_call(0, "skills", "another-owner")
    finally:
        overlay._GITHUB_CLOCK.reset(token)


def test_clock_failure_never_falls_back_to_real_egress(monkeypatch):
    import httpx
    from e2e_skills_transport import github_clock
    from model_hub.services.skills_hub.errors import SkillsHubError

    client = MagicMock()
    constructor = MagicMock()
    constructor.return_value.__enter__.return_value = client
    monkeypatch.setattr(httpx, "Client", constructor)
    client.post.side_effect = httpx.ConnectError("synthetic unavailable")
    with pytest.raises(SkillsHubError) as error:
        github_clock("synthetic/first")
    assert error.value.code == "github_budget_unavailable"
    constructor.assert_called_once_with(trust_env=False, follow_redirects=False)
    assert client.post.call_args.args == (FIXTURE_ORIGIN + "/github/clock",)


@pytest.mark.parametrize(
    "value",
    [
        {},
        {"namespace": "real-key", "now": 1893456000},
        {"namespace": "e2e-skills-clock-0001", "now": True},
        {"namespace": "e2e-skills-clock-0001", "now": 1894060801},
    ],
)
def test_clock_refuses_invalid_recorded_policy(monkeypatch, value):
    import httpx
    from e2e_skills_transport import github_clock
    from model_hub.services.skills_hub.errors import SkillsHubError

    client = MagicMock()
    constructor = MagicMock()
    constructor.return_value.__enter__.return_value = client
    monkeypatch.setattr(httpx, "Client", constructor)
    client.post.return_value.json.return_value = value
    with pytest.raises(SkillsHubError):
        github_clock("synthetic/first")
