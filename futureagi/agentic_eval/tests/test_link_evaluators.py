"""Tests for the link-checking deterministic evaluators in ``functions.py``.

Every HTTP call is stubbed — these must not touch the network.

Location note: these live in ``agentic_eval/tests/`` rather than beside the
module under test. ``agentic_eval/pytest.ini`` already declares
``testpaths = tests``, and it is the only layout that actually collects.
``agentic_eval`` is a namespace package (no ``__init__.py``) while
``core_evals/fi_evals/__init__.py`` uses three-dot relative imports that require
``agentic_eval`` to be the top-level package. Any test placed under
``core_evals/`` makes pytest build a Package node for those directories and
import them rooted at ``agentic_eval/``, which fails with "attempted relative
import beyond top-level package".

The Django bootstrap below is a fallback for direct invocation;
``agentic_eval/conftest.py`` normally configures Django first, in which case
``settings.configured`` is already True and this block is a no-op.
"""

import sys
from pathlib import Path

# futureagi/ — the backend root, so `agentic_eval` and `tfc` are importable.
_BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

import django  # noqa: E402
from django.conf import settings  # noqa: E402

if not settings.configured:
    settings.configure(
        DEBUG=True,
        DATABASES={},
        INSTALLED_APPS=[
            "django.contrib.contenttypes",
            "django.contrib.auth",
            "accounts",
            "model_hub",
            "tracer",
        ],
        REST_FRAMEWORK={},
        USE_TZ=True,
        DEFAULT_AUTO_FIELD="django.db.models.BigAutoField",
    )
    django.setup()

from unittest.mock import MagicMock, patch  # noqa: E402

import pytest  # noqa: E402
import requests  # noqa: E402

from agentic_eval.core_evals.fi_evals.function.functions import (  # noqa: E402
    _API_CALL_TIMEOUT_SECONDS,
    _HEAD_UNSUPPORTED_STATUSES,
    _LINK_CHECK_TIMEOUT_SECONDS,
    _link_is_reachable,
    api_call,
    contains_valid_link,
    no_invalid_links,
)

MODULE = "agentic_eval.core_evals.fi_evals.function.functions"


def _response(status_code):
    """A stand-in for requests.Response that also works as a context manager."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.__enter__ = MagicMock(return_value=resp)
    resp.__exit__ = MagicMock(return_value=False)
    return resp


class TestLinkIsReachable:
    """Unit tests for the shared liveness probe."""

    @patch(f"{MODULE}.requests.head")
    def test_head_200_is_reachable(self, mock_head):
        mock_head.return_value = _response(200)
        assert _link_is_reachable("https://example.com") is True

    @patch(f"{MODULE}.requests.head")
    def test_head_is_called_with_a_timeout(self, mock_head):
        """Regression: requests defaults to no timeout, which can pin a worker."""
        mock_head.return_value = _response(200)
        _link_is_reachable("https://example.com")

        assert mock_head.call_args.kwargs["timeout"] == _LINK_CHECK_TIMEOUT_SECONDS
        assert 0 < _LINK_CHECK_TIMEOUT_SECONDS <= 30

    @patch(f"{MODULE}.requests.head")
    def test_head_follows_redirects(self, mock_head):
        """Regression: allow_redirects defaults to False for HEAD (True for GET).

        Without it, every http->https upgrade and bare-domain->www redirect
        returned a 3xx and was reported as a broken link.
        """
        mock_head.return_value = _response(200)
        _link_is_reachable("http://example.com")

        assert mock_head.call_args.kwargs["allow_redirects"] is True

    @pytest.mark.parametrize("status", sorted(_HEAD_UNSUPPORTED_STATUSES))
    @patch(f"{MODULE}.requests.get")
    @patch(f"{MODULE}.requests.head")
    def test_falls_back_to_get_when_head_is_refused(self, mock_head, mock_get, status):
        """405/403/501 mean 'no HEAD here', not 'broken link'."""
        mock_head.return_value = _response(status)
        mock_get.return_value = _response(200)

        assert _link_is_reachable("https://example.com") is True

        assert mock_get.called
        assert mock_get.call_args.kwargs["timeout"] == _LINK_CHECK_TIMEOUT_SECONDS
        assert mock_get.call_args.kwargs["allow_redirects"] is True
        # Body must not be pulled down just to read a status code.
        assert mock_get.call_args.kwargs["stream"] is True

    @patch(f"{MODULE}.requests.get")
    @patch(f"{MODULE}.requests.head")
    def test_get_fallback_still_reports_failure(self, mock_head, mock_get):
        mock_head.return_value = _response(405)
        mock_get.return_value = _response(404)
        assert _link_is_reachable("https://example.com") is False

    @pytest.mark.parametrize("status", [200, 201, 204, 301, 302, 399])
    @patch(f"{MODULE}.requests.head")
    def test_non_error_statuses_are_reachable(self, mock_head, status):
        mock_head.return_value = _response(status)
        assert _link_is_reachable("https://example.com") is True

    @pytest.mark.parametrize("status", [400, 404, 410, 500, 503])
    @patch(f"{MODULE}.requests.head")
    def test_error_statuses_are_not_reachable(self, mock_head, status):
        mock_head.return_value = _response(status)
        assert _link_is_reachable("https://example.com") is False

    @pytest.mark.parametrize(
        "exc",
        [
            requests.ConnectionError("refused"),
            requests.Timeout("timed out"),
            requests.TooManyRedirects("loop"),
        ],
    )
    @patch(f"{MODULE}.requests.head")
    def test_network_failures_are_unreachable_not_raised(self, mock_head, exc):
        mock_head.side_effect = exc
        assert _link_is_reachable("https://example.com") is False

    @patch(f"{MODULE}.requests.head")
    def test_keyboard_interrupt_propagates(self, mock_head):
        """Regression: the old bare `except:` swallowed BaseException.

        On worker shutdown that turned a cancellation into a silent
        'link is invalid' verdict.
        """
        mock_head.side_effect = KeyboardInterrupt()
        with pytest.raises(KeyboardInterrupt):
            _link_is_reachable("https://example.com")

    @patch(f"{MODULE}.requests.head")
    def test_unexpected_errors_propagate(self, mock_head):
        """A bug in this function must not read as 'the host is down'."""
        mock_head.side_effect = AttributeError("bug in the probe")
        with pytest.raises(AttributeError):
            _link_is_reachable("https://example.com")


class TestContainsValidLink:
    @patch(f"{MODULE}.requests.head")
    def test_redirecting_url_is_valid(self, mock_head):
        """The bug from #1945: a live URL that redirects was scored invalid."""
        mock_head.return_value = _response(200)  # after following the redirect

        result = contains_valid_link("see http://github.com for details")

        assert result["result"] is True
        assert "is valid" in result["reason"]

    @patch(f"{MODULE}.requests.head")
    def test_dead_url_is_invalid(self, mock_head):
        mock_head.return_value = _response(404)
        result = contains_valid_link("see http://example.com/gone for details")
        assert result["result"] is False
        assert "but is invalid" in result["reason"]

    @patch(f"{MODULE}.requests.head")
    def test_unreachable_host_is_invalid(self, mock_head):
        mock_head.side_effect = requests.ConnectionError("no route")
        result = contains_valid_link("see http://nope.invalid for details")
        assert result["result"] is False

    def test_no_link_in_text_makes_no_request(self):
        with patch(f"{MODULE}.requests.head") as mock_head:
            result = contains_valid_link("there is no link here")
            assert result["result"] is False
            assert result["reason"] == "no link found in output"
            assert not mock_head.called

    @patch(f"{MODULE}.requests.head")
    def test_text_parameter_is_not_clobbered_by_the_response(self, mock_head):
        """The old code did `text = requests.head(...)`, shadowing its own arg."""
        mock_head.return_value = _response(200)
        result = contains_valid_link("visit http://example.com now")
        # The reason echoes the matched URL, which is only correct if the
        # original text was still intact when the message was built.
        assert "http://example.com" in result["reason"]


class TestNoInvalidLinks:
    @patch(f"{MODULE}.requests.head")
    def test_redirecting_url_passes(self, mock_head):
        """Polarity check: a good redirecting link must not fail this evaluator."""
        mock_head.return_value = _response(200)
        result = no_invalid_links("see http://github.com for details")
        assert result["result"] is True

    @patch(f"{MODULE}.requests.head")
    def test_broken_url_fails(self, mock_head):
        mock_head.return_value = _response(404)
        result = no_invalid_links("see http://example.com/gone for details")
        assert result["result"] is False

    def test_text_without_links_passes(self):
        result = no_invalid_links("nothing to see here")
        assert result["result"] is True
        assert result["reason"] == "no invalid link found in output"


class TestApiCallTimeout:
    @patch(f"{MODULE}.requests.post")
    def test_post_is_called_with_a_timeout(self, mock_post):
        """Regression: this POST had no timeout bound."""
        mock_post.return_value = MagicMock(
            status_code=200,
            json=MagicMock(return_value={"result": True, "reason": "ok"}),
        )

        api_call(url="https://example.com/eval", response="hello")

        assert mock_post.call_args.kwargs["timeout"] == _API_CALL_TIMEOUT_SECONDS
        assert 0 < _API_CALL_TIMEOUT_SECONDS <= 60

    @patch(f"{MODULE}.requests.post")
    def test_timeout_is_reported_not_raised(self, mock_post):
        mock_post.side_effect = requests.Timeout("timed out")
        result = api_call(url="https://example.com/eval", response="hello")
        assert result["result"] is False
        assert "API Request Exception" in result["reason"]
