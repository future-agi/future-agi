from types import SimpleNamespace

import pytest

from simulate.services import harness_sandbox
from simulate.services.harness_sandbox import (
    HarnessSandboxClient,
    HarnessSandboxRejected,
    HarnessSandboxUnavailable,
)

TOKEN = "tok-sentinel-9548"


@pytest.fixture
def sandbox(monkeypatch):
    sent = {}

    def respond_with(status_code, detail="refused"):
        def fake_request(method, url, *, headers, timeout, **kwargs):
            sent["headers"] = headers
            return SimpleNamespace(
                status_code=status_code,
                json=lambda: {"detail": detail},
                text=detail,
            )

        monkeypatch.setattr(harness_sandbox.requests, "request", fake_request)
        return HarnessSandboxClient(base_url="http://sandbox.test", token=TOKEN)

    respond_with.sent = sent
    return respond_with


# The frontend logs the user out on any 401, so a token the sandbox refuses
# has to surface as "unavailable" (a 503), never as a pass-through 401.
@pytest.mark.parametrize("status_code", [401, 403])
def test_token_refusal_is_unavailable_without_the_token(sandbox, status_code):
    # The upstream detail echoes the token, so a message built from it would leak it.
    client = sandbox(status_code, detail=f"invalid token {TOKEN}")

    with pytest.raises(HarnessSandboxUnavailable) as caught:
        client.list_jobs()

    assert sandbox.sent["headers"]["Authorization"] == f"Bearer {TOKEN}"
    assert str(status_code) in str(caught.value)
    assert TOKEN not in str(caught.value)


@pytest.mark.parametrize("status_code", [400, 404, 422])
def test_other_client_errors_are_still_rejected(sandbox, status_code):
    client = sandbox(status_code, detail="no such job")

    with pytest.raises(HarnessSandboxRejected) as caught:
        client.get("missing")

    assert caught.value.status_code == status_code
    assert str(caught.value) == "no such job"


def test_server_error_is_unavailable(sandbox):
    client = sandbox(500, detail="boom")

    with pytest.raises(HarnessSandboxUnavailable, match="returned 500: boom"):
        client.list_jobs()


def test_a_refusal_with_no_token_set_names_the_missing_setting(monkeypatch):
    def fake_request(method, url, *, headers, timeout, **kwargs):
        assert "Authorization" not in headers
        return SimpleNamespace(status_code=401, json=lambda: {}, text="")

    monkeypatch.setattr(harness_sandbox.requests, "request", fake_request)
    client = HarnessSandboxClient(base_url="http://sandbox.test", token="")

    with pytest.raises(
        HarnessSandboxUnavailable, match="ALK_HARNESS_SANDBOX_TOKEN is not set"
    ):
        client.list_jobs()


@pytest.mark.parametrize(
    "token",
    [f"{TOKEN}\n", f"{TOKEN}\r\nX-Injected: 1", f"{TOKEN}☃"],
    ids=["trailing-newline", "embedded-crlf", "non-latin-1"],
)
def test_a_malformed_token_is_unavailable_without_the_token(token):
    # The real HTTP stack raises here, with the header in its message, before any request.
    client = HarnessSandboxClient(base_url="http://127.0.0.1:9", token=token)

    with pytest.raises(HarnessSandboxUnavailable) as caught:
        client.list_jobs()

    assert TOKEN not in str(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__suppress_context__
