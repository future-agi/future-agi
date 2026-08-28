"""Tests for the HTTP Request runner: SSRF guard, interpolation, retries."""

from unittest.mock import MagicMock, patch

import pytest
import requests

from agent_playground.services.engine.node_runner import get_runner, has_runner
from agent_playground.services.engine.runners.http_request import (
    HttpRequestRunner,
    SSRFError,
    interpolate_structure,
    interpolate_template,
    validate_url,
)


@pytest.mark.unit
class TestRunnerRegistration:
    def test_runner_registered(self):
        assert has_runner("http_request")

    def test_get_runner_returns_instance(self):
        runner = get_runner("http_request")
        assert isinstance(runner, HttpRequestRunner)


@pytest.mark.unit
class TestValidateUrl:
    def test_blocks_file_scheme(self):
        with pytest.raises(SSRFError, match="scheme"):
            validate_url("file:///etc/passwd")

    def test_blocks_ftp_scheme(self):
        with pytest.raises(SSRFError, match="scheme"):
            validate_url("ftp://example.com/data")

    def test_blocks_gopher_scheme(self):
        with pytest.raises(SSRFError, match="scheme"):
            validate_url("gopher://example.com/")

    def test_blocks_missing_hostname(self):
        with pytest.raises(SSRFError, match="no hostname"):
            validate_url("http://")

    def test_blocks_loopback(self):
        with pytest.raises(SSRFError, match="private/reserved"):
            validate_url("http://127.0.0.1/admin")

    def test_blocks_private_range(self):
        with pytest.raises(SSRFError, match="private/reserved"):
            validate_url("http://10.0.0.5/internal")

    def test_blocks_192_168(self):
        with pytest.raises(SSRFError, match="private/reserved"):
            validate_url("http://192.168.1.1/router")

    def test_blocks_169_254_link_local(self):
        with pytest.raises(SSRFError, match="private/reserved"):
            validate_url("http://169.254.169.254/latest/meta-data/")

    def test_blocks_ipv6_loopback(self):
        with pytest.raises(SSRFError, match="private/reserved"):
            validate_url("http://[::1]/admin")

    def test_blocks_unresolvable_hostname(self):
        with pytest.raises(SSRFError, match="Cannot resolve"):
            validate_url("http://this-host-does-not-exist-xyz123.invalid/")

    def test_allows_public_hostname(self):
        with patch(
            "agent_playground.services.engine.runners.http_request.socket.getaddrinfo",
            return_value=[(2, 1, 6, "", ("93.184.216.34", 443))],
        ):
            validate_url("https://example.com/api")


@pytest.mark.unit
class TestInterpolation:
    def test_simple_variable(self):
        result = interpolate_template(
            "https://api.example.com/users/{{user_id}}", {"user_id": "42"}
        )
        assert result == "https://api.example.com/users/42"

    def test_whitespace_in_placeholder(self):
        result = interpolate_template("Hello {{ name }}", {"name": "Ada"})
        assert result == "Hello Ada"

    def test_multiple_variables(self):
        result = interpolate_template(
            "{{a}}-{{b}}-{{a}}", {"a": "1", "b": "2"}
        )
        assert result == "1-2-1"

    def test_dict_value_serialized_as_json(self):
        result = interpolate_template("{{payload}}", {"payload": {"k": "v"}})
        assert result == '{"k": "v"}'

    def test_unresolved_variable_raises(self):
        with pytest.raises(ValueError):
            interpolate_template("{{missing}}", {})

    def test_interpolate_structure_nested(self):
        body = {
            "name": "{{name}}",
            "tags": ["{{tag}}", "static"],
            "nested": {"id": "{{id}}"},
        }
        result = interpolate_structure(
            body, {"name": "Ada", "tag": "x", "id": "7"}
        )
        assert result == {
            "name": "Ada",
            "tags": ["x", "static"],
            "nested": {"id": "7"},
        }

    def test_interpolate_structure_non_string_passthrough(self):
        assert interpolate_structure(42, {}) == 42
        assert interpolate_structure(None, {}) is None


def _mock_response(status_code=200, json_body=None, text_body=None, content_type="application/json"):
    resp = MagicMock(spec=requests.Response)
    resp.status_code = status_code
    resp.headers = {"Content-Type": content_type}
    if json_body is not None:
        import json as _json

        resp.content = _json.dumps(json_body).encode()
    else:
        resp.content = (text_body or "").encode()
    return resp


@pytest.mark.unit
class TestRunnerExecution:
    def _run(self, config, inputs=None):
        runner = HttpRequestRunner()
        return runner.run(config, inputs or {}, {"node_id": "n1"})

    @patch("agent_playground.services.engine.runners.http_request.validate_url")
    @patch("agent_playground.services.engine.runners.http_request.requests.request")
    def test_get_request_returns_parsed_json(self, mock_request, mock_validate):
        mock_request.return_value = _mock_response(
            status_code=200, json_body={"ok": True}
        )
        result = self._run({"method": "GET", "url": "https://api.example.com/x"})
        assert result["response"]["status_code"] == 200
        assert result["response"]["body"] == {"ok": True}
        mock_validate.assert_called_once_with("https://api.example.com/x")

    @patch("agent_playground.services.engine.runners.http_request.validate_url")
    @patch("agent_playground.services.engine.runners.http_request.requests.request")
    def test_url_interpolation_before_request(self, mock_request, mock_validate):
        mock_request.return_value = _mock_response(json_body={})
        self._run(
            {"method": "GET", "url": "https://api.example.com/users/{{uid}}"},
            {"uid": "99"},
        )
        mock_validate.assert_called_once_with("https://api.example.com/users/99")
        _, kwargs = mock_request.call_args
        assert kwargs["url"] == "https://api.example.com/users/99"

    @patch("agent_playground.services.engine.runners.http_request.validate_url")
    @patch("agent_playground.services.engine.runners.http_request.requests.request")
    def test_bearer_auth_sets_header(self, mock_request, mock_validate):
        mock_request.return_value = _mock_response(json_body={})
        self._run(
            {
                "method": "GET",
                "url": "https://api.example.com/x",
                "auth": {"type": "bearer", "token": "secret-token"},
            }
        )
        _, kwargs = mock_request.call_args
        assert kwargs["headers"]["Authorization"] == "Bearer secret-token"

    @patch("agent_playground.services.engine.runners.http_request.validate_url")
    @patch("agent_playground.services.engine.runners.http_request.requests.request")
    def test_basic_auth_tuple(self, mock_request, mock_validate):
        mock_request.return_value = _mock_response(json_body={})
        self._run(
            {
                "method": "GET",
                "url": "https://api.example.com/x",
                "auth": {"type": "basic", "username": "u", "password": "p"},
            }
        )
        _, kwargs = mock_request.call_args
        assert kwargs["auth"] == ("u", "p")

    @patch("agent_playground.services.engine.runners.http_request.validate_url")
    @patch("agent_playground.services.engine.runners.http_request.requests.request")
    def test_dict_body_sent_as_json(self, mock_request, mock_validate):
        mock_request.return_value = _mock_response(json_body={})
        self._run(
            {
                "method": "POST",
                "url": "https://api.example.com/x",
                "body": {"name": "{{name}}"},
            },
            {"name": "Ada"},
        )
        _, kwargs = mock_request.call_args
        assert kwargs["json"] == {"name": "Ada"}
        assert kwargs["data"] is None

    @patch("agent_playground.services.engine.runners.http_request.validate_url")
    @patch("agent_playground.services.engine.runners.http_request.requests.request")
    def test_string_body_sent_as_data(self, mock_request, mock_validate):
        mock_request.return_value = _mock_response(json_body={})
        self._run(
            {
                "method": "POST",
                "url": "https://api.example.com/x",
                "body": "raw={{val}}",
            },
            {"val": "1"},
        )
        _, kwargs = mock_request.call_args
        assert kwargs["data"] == "raw=1"
        assert kwargs["json"] is None

    @patch("agent_playground.services.engine.runners.http_request.validate_url")
    @patch("agent_playground.services.engine.runners.http_request.requests.request")
    def test_retries_on_failure_then_succeeds(self, mock_request, mock_validate):
        mock_request.side_effect = [
            requests.ConnectionError("boom"),
            _mock_response(json_body={"ok": True}),
        ]
        result = self._run(
            {"method": "GET", "url": "https://api.example.com/x", "retries": 1}
        )
        assert result["response"]["body"] == {"ok": True}
        assert mock_request.call_count == 2

    @patch("agent_playground.services.engine.runners.http_request.validate_url")
    @patch("agent_playground.services.engine.runners.http_request.requests.request")
    def test_retries_exhausted_raises(self, mock_request, mock_validate):
        mock_request.side_effect = requests.ConnectionError("boom")
        with pytest.raises(requests.ConnectionError):
            self._run(
                {"method": "GET", "url": "https://api.example.com/x", "retries": 2}
            )
        assert mock_request.call_count == 3

    def test_missing_url_raises(self):
        with pytest.raises(ValueError, match="url"):
            self._run({"method": "GET"})

    def test_unsupported_method_raises(self):
        with pytest.raises(ValueError, match="method"):
            self._run({"method": "OPTIONS", "url": "https://api.example.com/x"})

    @patch("agent_playground.services.engine.runners.http_request.requests.request")
    def test_ssrf_check_runs_before_request(self, mock_request):
        with pytest.raises(SSRFError):
            self._run({"method": "GET", "url": "http://127.0.0.1/admin"})
        mock_request.assert_not_called()

    @patch("agent_playground.services.engine.runners.http_request.validate_url")
    @patch("agent_playground.services.engine.runners.http_request.requests.request")
    def test_redirects_not_followed(self, mock_request, mock_validate):
        mock_request.return_value = _mock_response(status_code=302, json_body={})
        self._run({"method": "GET", "url": "https://api.example.com/x"})
        _, kwargs = mock_request.call_args
        assert kwargs["allow_redirects"] is False

    @patch("agent_playground.services.engine.runners.http_request.validate_url")
    @patch("agent_playground.services.engine.runners.http_request.requests.request")
    def test_non_json_response_returns_text(self, mock_request, mock_validate):
        mock_request.return_value = _mock_response(
            text_body="hello world", content_type="text/plain"
        )
        result = self._run({"method": "GET", "url": "https://api.example.com/x"})
        assert result["response"]["body"] == "hello world"

    @patch("agent_playground.services.engine.runners.http_request.validate_url")
    @patch("agent_playground.services.engine.runners.http_request.requests.request")
    def test_timeout_passed_through(self, mock_request, mock_validate):
        mock_request.return_value = _mock_response(json_body={})
        self._run(
            {"method": "GET", "url": "https://api.example.com/x", "timeout": 5}
        )
        _, kwargs = mock_request.call_args
        assert kwargs["timeout"] == 5
