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
    # Not the connect path: the kit's own fixed text is kept (Hawk M1 on #4184).
    assert str(caught.value) == "no such job"


def test_server_error_is_unavailable(sandbox):
    # ADR-010: the page shows a fixed sentence with the status; the kit's own text
    # goes to the server log only.
    client = sandbox(500, detail="boom sk-live-in-a-traceback")

    with pytest.raises(HarnessSandboxUnavailable) as caught:
        client.list_jobs()

    assert str(caught.value) == (
        "The agent sandbox couldn't take this right now (HTTP 500). Try again in a "
        "few minutes."
    )
    assert "boom" not in str(caught.value)


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
    # Not the connect path (list_jobs): the kit's own fixed text is kept (Hawk M1 on #4184).
    assert str(caught.value) == "source_path is outside allowed roots"


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
    assert str(caught.value).startswith("We couldn't start the check")


def test_a_code_the_user_can_fix_reaches_the_page_as_its_sentence(sandbox):
    client = sandbox(
        422, detail=_validation_errors("environment_value_contains_nul: API_URL")
    )

    with pytest.raises(HarnessSandboxRejected) as caught:
        client.submit({"source_id": "abc"})

    assert str(caught.value) == refusal_sentence(
        "environment_value_contains_nul: API_URL"
    )
    assert "API_URL" in str(caught.value)
    assert "environment_value_contains_nul" not in str(caught.value)


def test_an_endpoint_refusal_string_reaches_the_page_as_its_sentence(sandbox):
    client = sandbox(404, detail="uploaded source was not found")

    with pytest.raises(HarnessSandboxRejected) as caught:
        client.submit({"source_id": "abc"})

    assert str(caught.value) == refusal_sentence("uploaded source was not found")


def test_a_validation_list_without_a_known_code_never_shows_the_kits_text(sandbox):
    # ADR-010: an unknown code gets the generic sentence and its code; free text the
    # kit wrote (it can repeat what was typed) never reaches the page.
    client = sandbox(
        422,
        detail=_validation_errors(
            "a_code_the_kit_adds_later",
            "a_code_the_kit_adds_later",
            "Unexpected value sk-live-pasted-here",
        ),
    )

    with pytest.raises(HarnessSandboxRejected) as caught:
        client.submit({})

    message = str(caught.value)
    assert message == (
        refusal_sentence("a_code_the_kit_adds_later").rstrip(".")
        + ". "
        + refusal_sentence("Unexpected value sk-live-pasted-here")
    )
    assert "sk-live" not in message
    assert "[{" not in message and "Value error" not in message


# Kit #353: a refusal the sandbox's own configuration causes is a 500 whose detail
# entries are typed "sandbox_configuration". It is our bug, not an outage: the page
# shows the shared our-side sentence with the code (ADR-010, 4).
def test_a_configuration_refusal_shows_the_our_side_sentence_not_unavailable(sandbox):
    client = sandbox(
        500,
        detail=[
            {
                "type": "sandbox_configuration",
                "loc": ["sandbox"],
                "msg": "Value error, hosted_isolation_must_be_dedicated_vm",
            }
        ],
    )

    with pytest.raises(HarnessSandboxUnavailable) as caught:
        client.submit({"github_repository": "acme/agent"})

    assert str(caught.value) == refusal_sentence(
        "hosted_isolation_must_be_dedicated_vm"
    )
    assert "(hosted_isolation_must_be_dedicated_vm)" in str(caught.value)
    assert "couldn't take this right now" not in str(caught.value)


def test_a_500_list_without_a_configuration_entry_is_still_unavailable(sandbox):
    client = sandbox(
        500,
        detail=[{"type": "value_error", "loc": ["body"], "msg": "Value error, boom"}],
    )

    with pytest.raises(HarnessSandboxUnavailable) as caught:
        client.list_jobs()

    assert "couldn't take this right now (HTTP 500)" in str(caught.value)


# Hawk M1 on #4184: the sentences belong to the connect path (submit, preflight and the two
# uploads). A job's read, cancel and adjust refusals are fixed kit text, already right, and keep it.
@pytest.mark.parametrize(
    ("call", "status_code", "detail"),
    [
        (lambda client: client.get("job-1"), 404, "job not found"),
        (lambda client: client.cancel("job-1"), 404, "job not found"),
        (
            lambda client: client.adjust("job-1", {}),
            409,
            "the run is already finalizing; start a follow-up run to change it",
        ),
        (
            lambda client: client.adjust("job-1", {}),
            409,
            "a completed run cannot be adjusted",
        ),
    ],
    ids=["get", "cancel", "adjust-finalizing", "adjust-completed"],
)
def test_a_refusal_off_the_connect_path_keeps_the_kits_fixed_text(
    sandbox, call, status_code, detail
):
    client = sandbox(status_code, detail=detail)

    with pytest.raises(HarnessSandboxRejected) as caught:
        call(client)

    assert caught.value.status_code == status_code
    assert str(caught.value) == detail


@pytest.mark.parametrize(
    "call",
    [
        lambda client: client.submit({"source_id": "abc"}),
        lambda client: client.preflight({"source_id": "abc"}),
    ],
    ids=["submit", "preflight"],
)
def test_a_refusal_on_the_connect_path_reads_as_the_platforms_sentence(sandbox, call):
    client = sandbox(404, detail="uploaded source was not found")

    with pytest.raises(HarnessSandboxRejected) as caught:
        call(client)

    assert str(caught.value) == refusal_sentence("uploaded source was not found")


# Hawk L6 on #4184: a sandbox with no token configured is our configuration fault, not an
# outage: the our-side sentence, logged as an error.
def test_a_sandbox_without_its_token_reads_as_our_side_not_an_outage(sandbox):
    client = sandbox(503, detail="sandbox_token_not_configured")

    with pytest.raises(HarnessSandboxUnavailable) as caught:
        client.submit({"source_id": "abc"})

    assert str(caught.value) == refusal_sentence("sandbox_token_not_configured")
    assert "(sandbox_token_not_configured)" in str(caught.value)
