from types import SimpleNamespace

import pytest

from simulate.services import harness_sandbox
from simulate.services.harness_refusals import refusal_sentence
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
@pytest.mark.parametrize("status_code", [401])
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
    [f"{TOKEN}\n", f"{TOKEN}\r\nX-Injected: 1", f"{TOKEN}\nX-Injected: 1", f"{TOKEN}☃"],
    ids=["trailing-newline", "embedded-crlf", "embedded-lf", "non-latin-1"],
)
def test_a_malformed_token_is_unavailable_without_the_token(token):
    # The real HTTP stack raises here, with the header in its message, before any request.
    client = HarnessSandboxClient(base_url="http://127.0.0.1:9", token=token)

    with pytest.raises(HarnessSandboxUnavailable) as caught:
        client.list_jobs()

    assert TOKEN not in str(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__suppress_context__


def test_a_403_is_the_sandboxs_own_refusal_not_a_token_rejection(sandbox):
    # The sandbox refuses a source path outside its allowed roots with a 403.
    client = sandbox(403, detail="source_path is outside allowed roots")

    with pytest.raises(HarnessSandboxRejected) as caught:
        client.list_jobs()

    assert caught.value.status_code == 403
    # Only a local sandbox reads a source path; the hosted page never sends one, so
    # this reaches the page as the platform's own refusal, with its code (TH-8978).
    assert str(caught.value) == refusal_sentence("source_path is outside allowed roots")
    assert "(source_path is outside allowed roots)" in str(caught.value)


@pytest.mark.parametrize(
    "token",
    [
        f"{TOKEN}\n",
        f"{TOKEN}\r\nX-Injected: 1",
        f"{TOKEN}\nX-Injected: 1",
        f"{TOKEN}\u2603",
        f" {TOKEN}",
        f"{TOKEN} ",
    ],
    ids=[
        "trailing-newline",
        "embedded-crlf",
        "embedded-lf",
        "non-latin-1",
        "leading-space",
        "trailing-space",
    ],
)
def test_a_malformed_token_is_refused_before_any_request(monkeypatch, token):
    def no_request(*args, **kwargs):
        raise AssertionError("a malformed token must not reach the HTTP stack")

    monkeypatch.setattr(harness_sandbox.requests, "request", no_request)
    client = HarnessSandboxClient(base_url="http://sandbox.test", token=token)

    with pytest.raises(
        HarnessSandboxUnavailable, match="not a valid HTTP header value"
    ) as caught:
        client.list_jobs()

    assert TOKEN not in str(caught.value)


def test_another_value_error_is_not_called_a_token_problem(monkeypatch):
    def closed_upload(*args, **kwargs):
        raise ValueError("I/O operation on closed file.")

    monkeypatch.setattr(harness_sandbox.requests, "request", closed_upload)
    client = HarnessSandboxClient(base_url="http://sandbox.test", token=TOKEN)

    with pytest.raises(ValueError, match="closed file"):
        client.list_jobs()


def test_a_401_on_a_request_that_carried_no_token_does_not_blame_the_token(monkeypatch):
    def fake_request(method, url, *, headers, timeout, **kwargs):
        assert "Authorization" not in headers
        return SimpleNamespace(status_code=401, json=lambda: {}, text="")

    monkeypatch.setattr(harness_sandbox.requests, "request", fake_request)
    client = HarnessSandboxClient(base_url="http://sandbox.test", token=TOKEN)

    with pytest.raises(HarnessSandboxUnavailable) as caught:
        client.health()

    assert "rejected the platform's token" not in str(caught.value)
    assert "401" in str(caught.value)


# The kit's request validation answers 422 with FastAPI's list of errors. Its
# text reaches the page as the refusal's message, so it must read as a sentence.
def _validation_errors(*messages):
    return [
        {"type": "value_error", "loc": ["body"], "msg": f"Value error, {message}"}
        for message in messages
    ]


def test_a_github_source_without_a_commit_is_refused_in_a_sentence(sandbox):
    # The page resolves the commit, so a missing one is the platform's own fault:
    # the sentence says so and keeps the code for support (TH-8978).
    client = sandbox(422, detail=_validation_errors("github_commit_sha_required"))

    with pytest.raises(HarnessSandboxRejected) as caught:
        client.submit({"github_repository": "acme/agent"})

    assert caught.value.status_code == 422
    assert str(caught.value) == refusal_sentence("github_commit_sha_required")
    assert str(caught.value).startswith("We couldn't start this")


def test_a_code_the_user_can_fix_reaches_the_page_as_its_sentence(sandbox):
    client = sandbox(422, detail=_validation_errors("environment_name_invalid: MY-KEY"))

    with pytest.raises(HarnessSandboxRejected) as caught:
        client.submit({"source_id": "abc"})

    assert str(caught.value) == refusal_sentence("environment_name_invalid: MY-KEY")
    assert "MY-KEY" in str(caught.value)
    assert "environment_name_invalid" not in str(caught.value)


def test_an_endpoint_refusal_string_reaches_the_page_as_its_sentence(sandbox):
    client = sandbox(404, detail="uploaded source was not found")

    with pytest.raises(HarnessSandboxRejected) as caught:
        client.submit({"source_id": "abc"})

    assert str(caught.value) == refusal_sentence("uploaded source was not found")


def test_a_validation_list_without_a_known_code_reads_as_its_messages(sandbox):
    client = sandbox(
        422,
        detail=_validation_errors(
            "a_code_the_kit_adds_later", "A subfolder needs a GitHub repository."
        ),
    )

    with pytest.raises(HarnessSandboxRejected) as caught:
        client.submit({})

    message = str(caught.value)
    assert message == (
        "a_code_the_kit_adds_later. A subfolder needs a GitHub repository."
    )
    assert "[{" not in message and "Value error" not in message
