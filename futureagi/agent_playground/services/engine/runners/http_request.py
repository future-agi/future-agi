"""
HTTP Request Runner for Graph Execution Engine.

This runner executes HTTP requests (GET, POST, PUT, PATCH, DELETE) with
configurable method, URL, headers, body, authentication, timeout, and retry
support. Includes SSRF protection to block requests to internal/private
network addresses.

Variable resolution:
    - ${variable} patterns in URL and headers are replaced with values from
      the inputs dict (workflow context).

Output:
    - status: int - HTTP status code (0 on failure)
    - headers: dict - Response headers
    - body: str - Response body text
    - success: bool - Whether the request succeeded
    - error: str | None - Error message if failed
"""

import base64
import ipaddress
import json
import re
import socket
import time
from urllib.parse import urlparse

import requests
from agent_playground.services.engine.node_runner import BaseNodeRunner, register_runner


def _is_safe_url(url: str) -> bool:
    """Check if the URL points to an external, non-private address."""
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname or ""
        if not re.match(r"^https?://", url, re.IGNORECASE):
            return False
        if not hostname:
            return False

        # Resolve hostname to IP addresses to prevent DNS rebinding/SSRF
        addr_infos = socket.getaddrinfo(hostname, None)
        for info in addr_infos:
            ip_str = info[4][0]
            try:
                addr = ipaddress.ip_address(ip_str)
                if addr.is_private or addr.is_loopback or addr.is_reserved:
                    return False
            except ValueError:
                # If IP parsing fails, treat as unsafe to be conservative
                return False
        return True
    except (socket.gaierror, socket.herror):
        # DNS resolution failed, treat as unsafe
        return False


def interpolate_variables(text: str, inputs: dict) -> str:
    """Replace ${variable} patterns with values from the workflow context."""
    if not text:
        return ""

    def replacer(match):
        var_name = match.group(1)
        return str(inputs.get(var_name, match.group(0)))

    return re.sub(r"\$\{([^}]+)\}", replacer, str(text))


class HttpRequestRunner(BaseNodeRunner):
    """Executes HTTP requests with configurable method, headers, body, auth, and timeout."""

    def run(self, config: dict, inputs: dict, execution_context: dict) -> dict:
        method = config.get("method", "GET").upper()
        url = config.get("url", "")
        headers_config = config.get("headers") or []
        body = config.get("body", "")
        body_type = config.get("bodyType", "json")
        timeout = config.get("timeout", 30)
        retries = config.get("retries", 0)

        # Variable interpolation for URL and headers
        interpolated_url = interpolate_variables(url, inputs)
        interpolated_headers = {
            interpolate_variables(h["key"], inputs): interpolate_variables(h["value"], inputs)
            for h in headers_config
        }

        # SSRF Protection
        if not _is_safe_url(interpolated_url):
            return {
                "status": 0,
                "headers": {},
                "body": "",
                "success": False,
                "error": "Request blocked: Access to internal or private network addresses is not allowed.",
            }

        # Authentication handling
        auth_type = config.get("authType", "none")
        final_headers = dict(interpolated_headers)

        if auth_type == "bearer":
            token = interpolate_variables(config.get("authToken", ""), inputs)
            final_headers["Authorization"] = f"Bearer {token}"
        elif auth_type == "basic":
            token = interpolate_variables(config.get("authToken", ""), inputs)
            if ":" in token:
                username, password = token.split(":", 1)
            else:
                username = token
                password = ""
            creds = base64.b64encode(f"{username}:{password}".encode()).decode()
            final_headers["Authorization"] = f"Basic {creds}"
        elif auth_type == "api_key":
            key_name = interpolate_variables(config.get("apiKeyName", ""), inputs)
            key_value = interpolate_variables(config.get("apiKeyValue", ""), inputs)
            final_headers[key_name] = key_value

        # Execute request with retry logic
        last_exception = None
        for attempt in range(retries + 1):
            try:
                prepared_data = None
                prepared_json = None
                if body:
                    if body_type == "json":
                        try:
                            prepared_json = json.loads(body)
                        except json.JSONDecodeError:
                            prepared_data = body
                    elif body_type == "form":
                        prepared_data = dict(
                            pair.split("=", 1) for pair in body.split("&") if "=" in pair
                        )
                    else:  # raw
                        prepared_data = body

                response = requests.request(
                    method=method,
                    url=interpolated_url,
                    headers=final_headers,
                    data=prepared_data,
                    json=prepared_json,
                    timeout=timeout,
                    allow_redirects=False,
                )
                return {
                    "status": response.status_code,
                    "headers": dict(response.headers),
                    "body": response.text,
                    "success": True,
                }
            except requests.exceptions.RequestException as e:
                last_exception = str(e)
                if attempt < retries:
                    time.sleep(2 ** attempt)
                    continue
                break

        return {
            "status": 0,
            "headers": {},
            "body": "",
            "success": False,
            "error": "Could not complete the request. Please check configuration and try again.",
        }


# Self-register on module import
register_runner("http_request", HttpRequestRunner())
