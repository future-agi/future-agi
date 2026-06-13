"""Unit tests for ``tfc.utils.http_timeouts`` and regression coverage that
outbound HTTP wrappers actually pass the timeout through.

See #499.

Run with: pytest tfc/utils/tests/test_http_timeouts.py -v
"""

from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Constants module
# ---------------------------------------------------------------------------


class TestHttpTimeoutConstants:
    """The constants are part of the contract — code-bases import them by
    name. These tests pin the shape so an accidental rename or type change
    can't slip through review."""

    @pytest.mark.unit
    def test_constants_are_two_int_tuples(self):
        from tfc.utils import http_timeouts

        for name in (
            "DEFAULT_HTTP_TIMEOUT",
            "LLM_HTTP_TIMEOUT",
            "HEALTHCHECK_HTTP_TIMEOUT",
            "LINK_CHECK_HTTP_TIMEOUT",
        ):
            value = getattr(http_timeouts, name)
            assert isinstance(value, tuple), f"{name} must be a tuple"
            assert len(value) == 2, f"{name} must be (connect, read)"
            connect, read = value
            assert isinstance(connect, int) and connect > 0
            assert isinstance(read, int) and read > 0
            # connect should always be the shorter of the two — failing fast
            # on connection is the whole point of splitting them.
            assert connect <= read, f"{name}: connect must be ≤ read"

    @pytest.mark.unit
    def test_llm_timeout_is_longest(self):
        """LLM/TTS calls take the longest legitimately — make sure no other
        bucket has a larger read budget by mistake."""
        from tfc.utils.http_timeouts import (
            DEFAULT_HTTP_TIMEOUT,
            HEALTHCHECK_HTTP_TIMEOUT,
            LINK_CHECK_HTTP_TIMEOUT,
            LLM_HTTP_TIMEOUT,
        )

        llm_read = LLM_HTTP_TIMEOUT[1]
        assert llm_read >= DEFAULT_HTTP_TIMEOUT[1]
        assert llm_read >= HEALTHCHECK_HTTP_TIMEOUT[1]
        assert llm_read >= LINK_CHECK_HTTP_TIMEOUT[1]


# ---------------------------------------------------------------------------
# Regression: callers do pass the timeout through
# ---------------------------------------------------------------------------


class TestCurlPassesTimeout:
    """``Curl`` was the most insidious offender — its ``timeout`` kwarg
    defaulted to ``None`` (unbounded). The default now resolves to
    ``DEFAULT_HTTP_TIMEOUT`` so callers that never opt-in still get a
    bounded wait."""

    def _mock_response(self, body=None):
        body = body or {"ok": True}
        resp = MagicMock()
        resp.json.return_value = body
        resp.status_code = 200
        resp.request.headers = {}
        return resp

    @pytest.mark.unit
    @patch("tfc.utils.curl.requests.get")
    def test_get_uses_default_timeout(self, mock_get):
        from tfc.utils.curl import Curl
        from tfc.utils.http_timeouts import DEFAULT_HTTP_TIMEOUT

        mock_get.return_value = self._mock_response()

        Curl().get("https://example.test/ping")

        assert mock_get.called
        kwargs = mock_get.call_args.kwargs
        assert kwargs["timeout"] == DEFAULT_HTTP_TIMEOUT

    @pytest.mark.unit
    @patch("tfc.utils.curl.requests.post")
    def test_post_uses_default_timeout(self, mock_post):
        from tfc.utils.curl import Curl
        from tfc.utils.http_timeouts import DEFAULT_HTTP_TIMEOUT

        mock_post.return_value = self._mock_response()

        Curl().post("https://example.test/ping", params={"x": 1})

        assert mock_post.called
        kwargs = mock_post.call_args.kwargs
        assert kwargs["timeout"] == DEFAULT_HTTP_TIMEOUT

    @pytest.mark.unit
    @patch("tfc.utils.curl.requests.post")
    def test_caller_override_wins(self, mock_post):
        from tfc.utils.curl import Curl

        mock_post.return_value = self._mock_response()

        Curl().post("https://example.test/ping", timeout=2)

        kwargs = mock_post.call_args.kwargs
        assert kwargs["timeout"] == 2


class TestModelServingClientPassesTimeout:
    """Representative service client — proves a non-``Curl`` call site
    threads the constant through. Picked because it's a thin wrapper
    (small surface area) used from agentic_eval workers."""

    @pytest.mark.unit
    @patch("agentic_eval.clients.model_serving_client.requests.get")
    def test_get_passes_timeout(self, mock_get):
        from agentic_eval.clients.model_serving_client import ModelServingClient
        from tfc.utils.http_timeouts import DEFAULT_HTTP_TIMEOUT

        resp = MagicMock()
        resp.json.return_value = {"ok": True}
        resp.raise_for_status.return_value = None
        mock_get.return_value = resp

        ModelServingClient("https://example.test").get("models")

        kwargs = mock_get.call_args.kwargs
        assert kwargs["timeout"] == DEFAULT_HTTP_TIMEOUT

    @pytest.mark.unit
    @patch("agentic_eval.clients.model_serving_client.requests.post")
    def test_post_passes_timeout(self, mock_post):
        from agentic_eval.clients.model_serving_client import ModelServingClient
        from tfc.utils.http_timeouts import DEFAULT_HTTP_TIMEOUT

        resp = MagicMock()
        resp.json.return_value = {"ok": True}
        resp.raise_for_status.return_value = None
        mock_post.return_value = resp

        ModelServingClient("https://example.test").post("models", json={"x": 1})

        kwargs = mock_post.call_args.kwargs
        assert kwargs["timeout"] == DEFAULT_HTTP_TIMEOUT


# ---------------------------------------------------------------------------
# AST guard: no new unbounded requests.* call slips back in
# ---------------------------------------------------------------------------


class TestNoUnboundedRequestsCalls:
    """Static check across the whole backend tree. If this fails, somebody
    added a ``requests.<verb>(...)`` without a ``timeout=`` kwarg — wire
    one in from ``tfc.utils.http_timeouts`` and re-run."""

    @pytest.mark.unit
    def test_all_requests_calls_have_timeout(self):
        import ast
        import pathlib

        repo_root = pathlib.Path(__file__).resolve().parents[3]
        offenders = []
        for path in repo_root.rglob("*.py"):
            parts = set(path.parts)
            if parts & {
                "__pycache__",
                "tests",
                "migrations",
                ".venv",
                "venv",
                ".git",
            }:
                continue
            if path.name.startswith("test_"):
                continue
            try:
                tree = ast.parse(path.read_text())
            except (SyntaxError, UnicodeDecodeError):
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                if (
                    isinstance(func, ast.Attribute)
                    and isinstance(func.value, ast.Name)
                    and func.value.id == "requests"
                    and func.attr
                    in {"get", "post", "put", "patch", "delete", "head", "request"}
                ):
                    if "timeout" not in {kw.arg for kw in node.keywords}:
                        offenders.append(
                            f"{path.relative_to(repo_root)}:{node.lineno} "
                            f"requests.{func.attr}"
                        )

        assert not offenders, (
            "Unbounded outbound HTTP calls would let workers hang. "
            "Pass timeout=... from tfc.utils.http_timeouts:\n  "
            + "\n  ".join(offenders)
        )
