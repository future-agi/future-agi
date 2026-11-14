"""The shared, unauthenticated GitHub client: budget, backoff and name checks."""

import time
from unittest.mock import MagicMock, patch

import pytest
import requests
from django.core.cache import cache

from simulate.services import harness_github as github
from simulate.services.harness_github import (
    github_get,
    safe_ref,
    safe_repository,
    short_ref,
)


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
    def test_200_is_ok_with_its_etag(self):
        with _get(_response(headers={"ETag": '"e1"'})) as get:
            result = github_get(
                "/repos/acme/agent", accept="application/vnd.github.sha", purpose="t"
            )
        assert (result.status, result.body, result.etag) == ("ok", "a" * 40, '"e1"')
        headers = get.call_args.kwargs["headers"]
        assert "Authorization" not in headers
        assert headers["X-GitHub-Api-Version"] == "2022-11-28"
        assert get.call_args.kwargs["timeout"] == (5, 15)

    def test_etag_is_sent_and_304_is_not_modified(self):
        with _get(_response(304)) as get:
            result = github_get(
                "/repos/acme/agent",
                accept="application/vnd.github.sha",
                etag='"e1"',
                purpose="t",
            )
        assert result.status == "not_modified"
        assert get.call_args.kwargs["headers"]["If-None-Match"] == '"e1"'

    @pytest.mark.parametrize("code", [404, 422])
    def test_missing_or_private_is_not_found(self, code):
        with _get(_response(code)):
            assert github_get("/x", accept="a", purpose="t").status == "not_found"

    def test_rate_limit_sets_the_reset_and_stops_later_calls(self):
        reset = int(time.time()) + 900
        limited = _response(
            403,
            headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": str(reset)},
        )
        with _get(limited) as get:
            assert github_get("/x", accept="a", purpose="t").status == "rate_limited"
            assert cache.get(github.RATE_RESET_KEY) == reset
            # While the reset is ahead, nothing is sent.
            assert github_get("/x", accept="a", purpose="t").status == "rate_limited"
        assert get.call_count == 1

    def test_retry_after_also_backs_off(self):
        with _get(_response(429, headers={"Retry-After": "120"})):
            assert github_get("/x", accept="a", purpose="t").status == "rate_limited"
        assert cache.get(github.RATE_RESET_KEY) > time.time() + 100

    def test_a_forbidden_without_rate_headers_is_an_error(self):
        with _get(_response(403)):
            assert github_get("/x", accept="a", purpose="t").status == "error"
        assert cache.get(github.RATE_RESET_KEY) is None

    def test_a_request_exception_is_an_error(self):
        with patch.object(
            github.requests, "get", side_effect=requests.ConnectionError("down")
        ):
            assert github_get("/x", accept="a", purpose="t").status == "error"

    def test_remaining_is_recorded(self):
        with _get(_response(headers={"X-RateLimit-Remaining": "37"})):
            github_get("/x", accept="a", purpose="t")
        assert github.remaining() == 37

    @pytest.mark.parametrize(
        "reset, timeout",
        [(lambda: str(int(time.time()) + 300), 300), (lambda: None, 3600)],
    )
    def test_remaining_is_kept_until_the_window_resets(self, reset, timeout):
        headers = {"X-RateLimit-Remaining": "3"}
        if reset() is not None:
            headers["X-RateLimit-Reset"] = reset()
        with (
            _get(_response(headers=headers)),
            patch.object(github.cache, "set", wraps=github.cache.set) as stored,
        ):
            github_get("/x", accept="a", purpose="t")
        [kept] = [
            one
            for one in stored.call_args_list
            if one.args[0] == "harness:gh-remaining"
        ]
        assert timeout - 2 <= kept.args[2] <= timeout


class TestBudget:
    @pytest.fixture(autouse=True)
    def one_hour(self):
        with patch.object(github, "_hour_key", return_value="h:fixed"):
            yield

    def test_the_51st_call_in_an_hour_is_refused_unsent(self, settings):
        settings.HARNESS_GITHUB_HOURLY_BUDGET = 50
        with patch.object(github.requests, "get", return_value=_response()) as get:
            results = [
                github_get("/x", accept="a", purpose="t").status for _ in range(51)
            ]
        assert results[:50] == ["ok"] * 50
        assert results[50] == "budget"
        assert get.call_count == 50

    def test_a_reserve_leaves_calls_for_others(self):
        with patch.object(github.requests, "get", return_value=_response()) as get:
            results = [
                github_get("/x", accept="a", purpose="poll", reserve=20).status
                for _ in range(31)
            ]
            # Without a reserve the rest of the hour is still there.
            assert github_get("/x", accept="a", purpose="t").status == "ok"
        assert results.count("ok") == 30
        assert results[30] == "budget"
        assert get.call_count == 31

    def test_push_re_reads_have_a_cap_of_their_own(self, settings):
        settings.HARNESS_GITHUB_WEBHOOK_BUDGET = 10
        with patch.object(github.requests, "get", return_value=_response()):
            pushes = [
                github_get("/x", accept="a", purpose="webhook").status
                for _ in range(11)
            ]
            # The poller still gets every call it is left.
            polls = [
                github_get("/x", accept="a", purpose="poll", reserve=20).status
                for _ in range(31)
            ]
        assert pushes == ["ok"] * 10 + ["budget"]
        assert polls == ["ok"] * 30 + ["budget"]

    def test_one_owner_gets_a_share_of_the_push_re_reads(self, settings):
        settings.HARNESS_GITHUB_WEBHOOK_BUDGET = 4
        settings.HARNESS_GITHUB_WEBHOOK_SHARE = 2
        with patch.object(github.requests, "get", return_value=_response()) as get:
            greedy = [
                github_get("/x", accept="a", purpose="webhook", owner="Evil").status
                for _ in range(5)
            ]
            others = [
                github_get("/x", accept="a", purpose="webhook", owner=owner).status
                for owner in ("acme", "acme", "beta")
            ]
        assert greedy == ["ok", "ok", "budget", "budget", "budget"]
        # The share sits inside the cap the push re-reads have in all.
        assert others == ["ok", "ok", "budget"]
        assert get.call_count == 4

    def test_comparisons_have_a_cap_and_an_org_share_and_leave_the_poller_its_calls(
        self,
    ):
        with patch.object(github.requests, "get", return_value=_response()):
            greedy = [
                github_get("/x", accept="a", purpose="compare", owner="org-a").status
                for _ in range(3)
            ]
            others = [
                github_get("/x", accept="a", purpose="compare", owner=owner).status
                for owner in ("org-b", "org-b", "org-c", "org-d")
            ]
            polls = [
                github_get("/x", accept="a", purpose="poll", reserve=20).status
                for _ in range(31)
            ]
        assert greedy == ["ok", "ok", "budget"]
        assert others == ["ok", "ok", "ok", "budget"]
        assert polls == ["ok"] * 30 + ["budget"]

    def test_creation_checks_have_a_cap_and_an_org_share_of_their_own(self, settings):
        settings.HARNESS_GITHUB_CREATE_BUDGET = 4
        settings.HARNESS_GITHUB_CREATE_SHARE = 2
        with patch.object(github.requests, "get", return_value=_response()) as get:
            greedy = [
                github_get("/x", accept="a", purpose="create", owner="org-a").status
                for _ in range(5)
            ]
            others = [
                github_get("/x", accept="a", purpose="create", owner=owner).status
                for owner in ("org-b", "org-b", "org-c")
            ]
            # The poller still gets every call it is left.
            polls = [
                github_get("/x", accept="a", purpose="poll", reserve=20).status
                for _ in range(31)
            ]
        assert greedy == ["ok", "ok", "budget", "budget", "budget"]
        assert others == ["ok", "ok", "budget"]
        assert polls == ["ok"] * 30 + ["budget"]
        assert get.call_count == 34

    def test_creation_checks_by_default_still_leave_the_poller_calls(self):
        with patch.object(github.requests, "get", return_value=_response()):
            creates = [
                github_get("/x", accept="a", purpose="create", owner=f"org-{n}").status
                for n in range(26)
            ]
            for _ in range(10):
                github_get("/x", accept="a", purpose="webhook")
            polls = [
                github_get("/x", accept="a", purpose="poll", reserve=20).status
                for _ in range(16)
            ]
        assert creates == ["ok"] * 25 + ["budget"]
        assert polls == ["ok"] * 15 + ["budget"]

    def test_every_cap_full_by_default_still_leaves_the_poller_calls(self):
        with patch.object(github.requests, "get", return_value=_response()):
            for n in range(25):
                github_get("/x", accept="a", purpose="create", owner=f"org-{n}")
            for _ in range(10):
                github_get("/x", accept="a", purpose="webhook")
            compares = [
                github_get("/x", accept="a", purpose="compare", owner=f"org-{n}").status
                for n in range(6)
            ]
            polls = [
                github_get("/x", accept="a", purpose="poll", reserve=20).status
                for _ in range(11)
            ]
        assert compares == ["ok"] * 5 + ["budget"]
        assert polls == ["ok"] * 10 + ["budget"]

    def test_a_new_hour_starts_a_new_count(self):
        with patch.object(github.requests, "get", return_value=_response()):
            with patch.object(github, "_hour_key", return_value="h:2026101610"):
                for _ in range(50):
                    github_get("/x", accept="a", purpose="t")
                assert github_get("/x", accept="a", purpose="t").status == "budget"
            with patch.object(github, "_hour_key", return_value="h:2026101611"):
                assert github_get("/x", accept="a", purpose="t").status == "ok"

    def test_low_remaining_stops_a_reserving_caller(self):
        cache.set(github.REMAINING_KEY, 20, 3600)
        with patch.object(github.requests, "get") as get:
            assert (
                github_get("/x", accept="a", purpose="poll", reserve=20).status
                == "budget"
            )
        get.assert_not_called()


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
