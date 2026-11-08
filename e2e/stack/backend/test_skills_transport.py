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
