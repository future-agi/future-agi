"""
HTTP Request Runner for Graph Execution Engine.

This runner executes http_request nodes by making an HTTP call to an external
API using the node's config (method, url, headers, body, auth, timeout,
retries). Template variables in the URL, headers, and body are resolved from
input ports using the same {{variable}} / dot-notation resolution as the
llm_prompt runner.

Security:
    - SSRF protection: requests to private, loopback, link-local, and
      reserved IP ranges are rejected. DNS resolution is checked before
      the request is made.

Output:
    - response: dict with keys:
        - status_code: int
        - headers: dict
        - body: parsed JSON (dict/list) when the response is JSON,
                otherwise the raw text string
"""

import ipaddress
import json
import re
import socket
from typing import Any
from urllib.parse import urlparse

import requests

from agent_playground.services.engine.node_runner import BaseNodeRunner, register_runner
from agent_playground.services.engine.utils.json_path import resolve_variable

_PLACEHOLDER_PATTERN = re.compile(r"\{\{\s*(?P<placeholder>.*?)\s*\}\}")

_MAX_RESPONSE_BYTES = 5 * 1024 * 1024

_ALLOWED_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}


class SSRFError(ValueError):
    """Raised when a request targets a blocked network address."""


def _is_blocked_ip(ip_str: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return True
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def validate_url(url: str) -> None:
    """
    Validate that a URL is safe to request.

    Raises:
        SSRFError: If the URL scheme is not http/https or the host resolves
                   to a blocked IP address.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise SSRFError(f"Blocked URL scheme '{parsed.scheme}': only http/https allowed")

    hostname = parsed.hostname
    if not hostname:
        raise SSRFError("URL has no hostname")

    try:
        addr_info = socket.getaddrinfo(hostname, parsed.port or (443 if parsed.scheme == "https" else 80))
    except socket.gaierror as e:
        raise SSRFError(f"Cannot resolve hostname '{hostname}': {e}") from e

    for _family, _, _, _, sockaddr in addr_info:
        ip_str = str(sockaddr[0])
        if _is_blocked_ip(ip_str):
            raise SSRFError(
                f"Blocked request to '{hostname}': resolves to private/reserved address {ip_str}"
            )


def interpolate_template(text: str, inputs: dict[str, Any]) -> str:
    """
    Replace {{variable}} placeholders in a string with resolved input values.

    Uses the same resolution logic as the llm_prompt runner (dot notation,
    global variable fallback).

    Raises:
        ValueError: If a placeholder cannot be resolved.
    """

    def _replace(match: re.Match) -> str:
        variable_name = match.group("placeholder")
        value = resolve_variable(variable_name, inputs)
        if isinstance(value, (dict, list)):
            return json.dumps(value)
        return str(value)

    return _PLACEHOLDER_PATTERN.sub(_replace, text)


def interpolate_structure(value: Any, inputs: dict[str, Any]) -> Any:
    """
    Recursively interpolate {{variable}} placeholders in strings nested
    inside dicts and lists.
    """
    if isinstance(value, str):
        return interpolate_template(value, inputs)
    if isinstance(value, dict):
        return {k: interpolate_structure(v, inputs) for k, v in value.items()}
    if isinstance(value, list):
        return [interpolate_structure(item, inputs) for item in value]
    return value


class HttpRequestRunner(BaseNodeRunner):
    """
    Runner for http_request template nodes.

    Reads method/url/headers/body/auth/timeout/retries from node config,
    interpolates input variables, performs the request with SSRF protection,
    and returns the parsed response.
    """

    def run(
        self,
        config: dict[str, Any],
        inputs: dict[str, Any],
        execution_context: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Execute an HTTP request.

        Args:
            config: Node config with method, url, headers, body, auth,
                    timeout, retries.
            inputs: Input port values used to fill {{variable}} placeholders.
            execution_context: Execution-level context (unused).

        Returns:
            Dict with "response" key containing status_code, headers, body.

        Raises:
            ValueError: If config is invalid or a variable cannot be resolved.
            SSRFError: If the URL targets a blocked address.
            requests.RequestException: If the request fails after retries.
        """
        method = (config.get("method") or "GET").upper()
        if method not in _ALLOWED_METHODS:
            raise ValueError(f"Unsupported HTTP method '{method}'")

        url = config.get("url")
        if not url:
            raise ValueError("HTTP request config missing 'url'")

        url = interpolate_template(url, inputs)

        headers = {}
        raw_headers = config.get("headers") or {}
        for key, value in raw_headers.items():
            headers[key] = interpolate_template(str(value), inputs)

        auth = config.get("auth") or {}
        auth_type = auth.get("type", "none")
        request_auth = None
        if auth_type == "bearer":
            token = interpolate_template(auth.get("token", ""), inputs)
            if token:
                headers["Authorization"] = f"Bearer {token}"
        elif auth_type == "basic":
            username = interpolate_template(auth.get("username", ""), inputs)
            password = interpolate_template(auth.get("password", ""), inputs)
            request_auth = (username, password)

        body = config.get("body")
        request_body = None
        if body is not None:
            request_body = interpolate_structure(body, inputs)

        timeout = config.get("timeout") or 30
        retries = config.get("retries") or 0

        validate_url(url)

        last_error: Exception | None = None
        for attempt in range(retries + 1):
            try:
                response = requests.request(
                    method=method,
                    url=url,
                    headers=headers,
                    json=request_body if isinstance(request_body, (dict, list)) else None,
                    data=request_body if isinstance(request_body, str) else None,
                    auth=request_auth,
                    timeout=timeout,
                    allow_redirects=False,
                )
                return self._build_response(response)
            except SSRFError:
                raise
            except requests.RequestException as e:
                last_error = e
                if attempt < retries:
                    continue
                raise

        raise RuntimeError("HTTP request failed") from last_error

    def _build_response(self, response: requests.Response) -> dict[str, Any]:
        content = response.content[:_MAX_RESPONSE_BYTES]
        content_type = response.headers.get("Content-Type", "")

        body: Any
        if "json" in content_type.lower():
            try:
                body = json.loads(content)
            except (json.JSONDecodeError, UnicodeDecodeError):
                body = content.decode("utf-8", errors="replace")
        else:
            text = content.decode("utf-8", errors="replace")
            try:
                body = json.loads(text)
            except (json.JSONDecodeError, ValueError):
                body = text

        return {
            "response": {
                "status_code": response.status_code,
                "headers": dict(response.headers),
                "body": body,
            }
        }


register_runner("http_request", HttpRequestRunner())
