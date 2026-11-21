"""The GitHub reader: the token each read carries, GitHub's own limits, the errors a
person sees, the workspace limit and name checks."""

import json
import time
import uuid
from unittest.mock import MagicMock, patch

import pytest
import requests
from django.core.cache import cache
from structlog.testing import capture_logs

from simulate.services import harness_github as github
from simulate.services.harness_github import (
    NO_CREDENTIAL,
    ReadCredential,
    github_get,
    read_credential,
    read_refused,
    remaining,
    safe_ref,
    safe_repository,
    short_ref,
    take_workspace_read,
    waiting_until,
)
from simulate.services.hosted_harness import HostedHarnessError

CRED = ReadCredential("tok-1", "inst-1", workspace_owned=True)
OURS = ReadCredential("ours", "inst-ours")


@pytest.fixture(autouse=True)
def clean_cache():
    cache.clear()
    yield
    cache.clear()


def _response(status_code=200, *, body="a" * 40, headers=None):
    response = MagicMock()
    response.status_code = status_code
    response.headers = headers or {}
    response.text = body
    response.json.return_value = body
    return response


def _get(*responses):
    return patch.object(github.requests, "get", side_effect=list(responses))


class TestGet:
    def test_200_is_ok_with_its_etag_and_sends_the_token(self):
        with _get(_response(headers={"ETag": '"e1"'})) as get:
            result = github_get(
                "/repos/acme/agent",
                accept="application/vnd.github.sha",
                credential=CRED,
                purpose="t",
            )
        assert (result.status, result.body, result.etag) == ("ok", "a" * 40, '"e1"')
        headers = get.call_args.kwargs["headers"]
        assert headers["Authorization"] == "Bearer tok-1"
        assert headers["X-GitHub-Api-Version"] == "2022-11-28"
        assert get.call_args.kwargs["timeout"] == (5, 15)

    def test_without_a_token_nothing_is_sent(self):
        with _get() as get:
            result = github_get("/x", accept="a", credential=NO_CREDENTIAL, purpose="t")
        assert result.status == "not_connected"
        get.assert_not_called()

    def test_etag_is_sent_and_304_is_not_modified(self):
        with _get(_response(304)) as get:
            result = github_get(
                "/repos/acme/agent",
                accept="application/vnd.github.sha",
                credential=CRED,
                etag='"e1"',
                purpose="t",
            )
        assert result.status == "not_modified"
        assert get.call_args.kwargs["headers"]["If-None-Match"] == '"e1"'

    @pytest.mark.parametrize("code", [404, 422])
    def test_missing_or_private_is_not_found(self, code):
        with _get(_response(code)):
            result = github_get("/x", accept="a", credential=CRED, purpose="t")
        assert result.status == "not_found"

    def test_rate_limit_waits_only_that_installation(self):
        reset = int(time.time()) + 900
        limited = _response(
            403,
            headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": str(reset)},
        )
        with _get(limited, _response()) as get:
            first = github_get("/x", accept="a", credential=CRED, purpose="t")
            again = github_get("/x", accept="a", credential=CRED, purpose="t")
            other = github_get("/x", accept="a", credential=OURS, purpose="t")
        assert (first.status, first.reset_at) == ("rate_limited", reset)
        assert (again.status, again.reset_at) == ("rate_limited", reset)
        assert other.status == "ok"
        assert get.call_count == 2
        assert waiting_until(CRED) == reset
        assert waiting_until(OURS) is None

    def test_retry_after_also_backs_off(self):
        with _get(_response(429, headers={"Retry-After": "120"})):
            result = github_get("/x", accept="a", credential=CRED, purpose="t")
        assert result.status == "rate_limited"
        assert result.reset_at > time.time() + 100

    def test_a_forbidden_without_rate_headers_is_an_error(self):
        with _get(_response(403)):
            result = github_get("/x", accept="a", credential=CRED, purpose="t")
        assert result.status == "error"
        assert waiting_until(CRED) is None

    def test_a_request_exception_is_an_error(self):
        with patch.object(
            github.requests, "get", side_effect=requests.ConnectionError("down")
        ):
            result = github_get("/x", accept="a", credential=CRED, purpose="t")
        assert result.status == "error"

    def test_a_rejected_workspace_token_is_revoked(self):
        with _get(_response(401)):
            result = github_get("/x", accept="a", credential=CRED, purpose="t")
        assert result.status == "revoked"

    def test_a_rejected_fallback_token_is_forgotten_and_an_error(self):
        with (
            _get(_response(401)),
            patch(
                "integrations.services.github_app.forget_fallback_read_token"
            ) as forget,
        ):
            result = github_get("/x", accept="a", credential=OURS, purpose="t")
        assert result.status == "error"
        forget.assert_called_once()

    def test_remaining_is_recorded_per_installation(self):
        with _get(_response(headers={"X-RateLimit-Remaining": "37"})):
            github_get("/x", accept="a", credential=CRED, purpose="t")
        assert remaining(CRED) == 37
        assert remaining(OURS) is None

    def test_low_headroom_is_warned_once(self):
        low = {"X-RateLimit-Remaining": "900", "X-RateLimit-Limit": "5000"}
        with (
            _get(_response(headers=low), _response(headers=low)),
            capture_logs() as logs,
        ):
            github_get("/x", accept="a", credential=CRED, purpose="t")
            github_get("/x", accept="a", credential=CRED, purpose="t")
        warned = [one for one in logs if one["event"] == "harness_github_headroom_low"]
        assert len(warned) == 1
        assert (warned[0]["remaining"], warned[0]["limit"]) == (900, 5000)

    def test_enough_headroom_is_not_warned(self):
        fine = {"X-RateLimit-Remaining": "4000", "X-RateLimit-Limit": "5000"}
        with _get(_response(headers=fine)), capture_logs() as logs:
            github_get("/x", accept="a", credential=CRED, purpose="t")
        assert not [
            one for one in logs if one["event"] == "harness_github_headroom_low"
        ]


class TestReadCredential:
    def _patched(self, *, installation, token=None, token_error=None, fallback=None):
        from integrations.services import github_app

        mint = MagicMock(return_value=token, side_effect=token_error)
        revoke = MagicMock()
        return (
            patch.object(github, "_workspace_installation", return_value=installation),
            patch.object(github_app, "installation_token", mint),
            patch.object(github_app, "revoke_installation_token", revoke),
            patch.object(github_app, "fallback_read_token", return_value=fallback),
            mint,
            revoke,
        )

    def test_a_connected_workspace_reads_with_its_own_token_and_revokes_it(
        self, settings
    ):
        settings.GITHUB_APP_FALLBACK_INSTALLATION_ID = "ours"
        *patches, mint, revoke = self._patched(
            installation="42", token="ws-tok", fallback="ours-tok"
        )
        with patches[0], patches[1], patches[2], patches[3]:
            with read_credential("org", "ws") as credential:
                assert credential == ReadCredential(
                    "ws-tok", "42", workspace_owned=True
                )
                revoke.assert_not_called()
        mint.assert_called_once_with("42")
        revoke.assert_called_once_with("ws-tok")

    def test_a_workspace_token_github_refuses_falls_back_to_ours(self, settings):
        from integrations.services.github_app import GitHubAppError

        settings.GITHUB_APP_FALLBACK_INSTALLATION_ID = "ours"
        *patches, _mint, revoke = self._patched(
            installation="42",
            token_error=GitHubAppError("github_installation_token_failed", "no"),
            fallback="ours-tok",
        )
        with patches[0], patches[1], patches[2], patches[3]:
            with read_credential("org", "ws") as credential:
                assert credential == ReadCredential("ours-tok", "ours")
        revoke.assert_not_called()

    def test_without_a_connection_ours_is_used(self, settings):
        settings.GITHUB_APP_FALLBACK_INSTALLATION_ID = "ours"
        *patches, mint, _revoke = self._patched(installation=None, fallback="ours-tok")
        with patches[0], patches[1], patches[2], patches[3]:
            with read_credential("org", "ws") as credential:
                assert credential == ReadCredential("ours-tok", "ours")
        mint.assert_not_called()

    def test_with_neither_there_is_no_credential(self):
        *patches, _mint, _revoke = self._patched(installation=None, fallback=None)
        with patches[0], patches[1], patches[2], patches[3]:
            with read_credential("org", "ws") as credential:
                assert credential == NO_CREDENTIAL

    def test_the_workspace_token_is_revoked_when_the_read_fails(self):
        *patches, _mint, revoke = self._patched(installation="42", token="ws-tok")
        with patches[0], patches[1], patches[2], patches[3]:
            with pytest.raises(RuntimeError), read_credential("org", "ws"):
                raise RuntimeError("read failed")
        revoke.assert_called_once_with("ws-tok")


class TestReadRefused:
    def test_not_connected(self):
        error = read_refused("not_connected")
        assert (error.code, error.status_code) == ("github_not_connected", 422)
        assert "Connect GitHub" in error.message

    def test_revoked(self):
        error = read_refused("revoked")
        assert (error.code, error.status_code) == ("github_installation_revoked", 422)
        assert "reconnect GitHub" in error.message

    def test_rate_limited_says_when(self):
        error = read_refused("rate_limited", time.time() + 610)
        assert (error.code, error.status_code) == ("github_rate_limited", 429)
        assert error.retryable
        assert "try again in 11 minutes" in error.message
        assert 600 <= error.details["retry_after"] <= 611
        assert error.headers() == {"Retry-After": str(error.details["retry_after"])}

    def test_rate_limited_never_says_less_than_a_minute(self):
        error = read_refused("rate_limited", time.time() + 5)
        assert "try again in 1 minute" in error.message
        assert error.details["retry_after"] == 60

    def test_error(self):
        error = read_refused("error")
        assert (error.code, error.status_code) == ("source_unreachable", 503)
        assert error.retryable

    @pytest.mark.parametrize("status", ["ok", "not_modified", "not_found"])
    def test_an_answer_is_not_refused(self, status):
        assert read_refused(status) is None


class TestWorkspaceLimit:
    def test_past_the_limit_is_refused_until_the_next_hour(self, settings):
        settings.HARNESS_GITHUB_WORKSPACE_HOURLY_LIMIT = 2
        workspace = uuid.uuid4()
        take_workspace_read(workspace)
        take_workspace_read(workspace)
        with pytest.raises(HostedHarnessError) as refused:
            take_workspace_read(workspace)
        error = refused.value
        assert (error.code, error.status_code) == ("github_workspace_limit", 429)
        assert 0 < error.details["retry_after"] <= 3600
        assert error.headers() == {"Retry-After": str(error.details["retry_after"])}

    def test_each_workspace_counts_on_its_own(self, settings):
        settings.HARNESS_GITHUB_WORKSPACE_HOURLY_LIMIT = 1
        take_workspace_read(uuid.uuid4())
        take_workspace_read(uuid.uuid4())

    def test_a_limit_of_zero_or_no_workspace_counts_nothing(self, settings):
        settings.HARNESS_GITHUB_WORKSPACE_HOURLY_LIMIT = 0
        workspace = uuid.uuid4()
        for _ in range(5):
            take_workspace_read(workspace)
        settings.HARNESS_GITHUB_WORKSPACE_HOURLY_LIMIT = 1
        for _ in range(5):
            take_workspace_read(None)


class TestErrorHeaders:
    def test_only_a_429_with_a_wait_has_retry_after(self):
        assert HostedHarnessError(
            "x", "m", status_code=429, details={"retry_after": 30}
        ).headers() == {"Retry-After": "30"}
        assert HostedHarnessError("x", "m", status_code=429).headers() == {}
        assert (
            HostedHarnessError(
                "x", "m", status_code=503, details={"retry_after": 30}
            ).headers()
            == {}
        )


class TestCredentialNeverReachesSentry:
    def test_the_token_is_not_in_the_repr(self):
        assert "tok-1" not in repr(CRED)
        assert "inst-1" in repr(CRED)

    @pytest.mark.parametrize("workspace_owned", [True, False])
    def test_an_error_inside_a_read_sends_no_token_to_sentry(
        self, settings, workspace_owned
    ):
        import sentry_sdk
        from sentry_sdk.transport import Transport

        from integrations.services import github_app
        from tfc.logging.sentry import _event_scrubber, _get_before_send

        secret = "ghs_" + "s3cr3tV4lue" * 3
        events = []

        class Capture(Transport):
            def capture_envelope(self, envelope):
                events.append(envelope.get_event())

        client = sentry_sdk.Client(
            dsn="https://key@sentry.example.com/1",
            transport=Capture,
            include_local_variables=True,
            send_default_pii=True,
            event_scrubber=_event_scrubber(),
            before_send=_get_before_send(),
            default_integrations=False,
        )
        settings.GITHUB_APP_FALLBACK_INSTALLATION_ID = "ours"
        with (
            patch.object(
                github,
                "_workspace_installation",
                return_value="42" if workspace_owned else None,
            ),
            patch.object(github_app, "installation_token", return_value=secret),
            patch.object(github_app, "revoke_installation_token"),
            patch.object(github_app, "fallback_read_token", return_value=secret),
            patch.object(
                github.requests, "get", side_effect=RuntimeError("read failed")
            ),
            sentry_sdk.isolation_scope() as scope,
        ):
            scope.set_client(client)
            try:
                with read_credential("org", "ws") as credential:
                    github_get("/x", accept="a", credential=credential, purpose="t")
            except RuntimeError:
                sentry_sdk.capture_exception()

        [event] = events
        frames = [
            frame
            for value in event["exception"]["values"]
            for frame in value["stacktrace"]["frames"]
        ]
        assert any("credential" in (frame.get("vars") or {}) for frame in frames)
        assert secret not in json.dumps(event, default=str)


class TestNames:
    @pytest.mark.parametrize(
        "name", ["acme/agent", "acme/agent.py", "a-b/c_d", "Acme/Agent"]
    )
    def test_safe_repositories(self, name):
        assert safe_repository(name)

    @pytest.mark.parametrize(
        "name", ["../x", "a/..", "./a", "a/.", "acme", "a/b/c", "a b/c", ""]
    )
    def test_unsafe_repositories(self, name):
        assert not safe_repository(name)

    @pytest.mark.parametrize("ref", ["main", "feature/x", "v1.2", "release-1"])
    def test_safe_refs(self, ref):
        assert safe_ref(ref)

    @pytest.mark.parametrize(
        "ref", ["../x", "a/..", "main/../x", "-x", "/x", "a/./b", "a#b", "a%2e", ""]
    )
    def test_unsafe_refs(self, ref):
        assert not safe_ref(ref)

    def test_short_ref(self):
        assert short_ref("refs/heads/main") == "main"
        assert short_ref("refs/heads/feature/x") == "feature/x"
        assert short_ref("refs/tags/v1") == "v1"
        assert short_ref("main") == "main"
        assert short_ref("HEAD") is None
        assert short_ref(None) is None
        assert short_ref("") is None
