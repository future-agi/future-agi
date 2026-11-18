"""Bounded, pinned transport for user-configured custom model endpoints.

Only public destinations are supported. Redirects are refused rather than
forwarding arbitrary custom credentials or prompt bodies to another endpoint.
The shared URL guard supplies the IP policy, DNS pinning and TLS verification.
"""

import json
import time
from urllib.parse import urlsplit

import requests
import urllib3

from tfc.utils.ssrf_guard import (
    METADATA_HOSTNAMES,
    _open_pinned_pool,
    _resolve_pinned_ip,
)

MAX_RESPONSE_BYTES = 4 * 1024 * 1024
REQUEST_TIMEOUT_SECONDS = 60


class CustomEndpointError(ValueError):
    """A safe, credential-free message that can be returned to the caller."""


def post_custom_endpoint(
    *, url: str, headers: dict, payload: dict
) -> requests.Response:
    """POST once, to a validated IP, with bounded response size and deadlines.

    Header routing/framing overrides and URL userinfo are not supported. The
    caller must never log the supplied URL, headers, response body or a raw
    transport exception; any of these can contain endpoint credentials.
    """
    pool = None
    response = None
    try:
        parsed = urlsplit(url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
            or any(ord(char) < 33 or ord(char) == 127 for char in url)
            or parsed.hostname.rstrip(".").lower() in METADATA_HOSTNAMES
        ):
            raise CustomEndpointError("Invalid custom model endpoint URL.")
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        if not isinstance(headers, dict):
            raise CustomEndpointError("Custom model headers must be an object.")
        forbidden = {
            "host",
            "content-length",
            "transfer-encoding",
            "connection",
            "proxy-authorization",
            "proxy-connection",
            "trailer",
            "upgrade",
        }
        for name, value in headers.items():
            if (
                not isinstance(name, str)
                or not isinstance(value, str)
                or not name
                or any(
                    not (
                        char.isascii() and (char.isalnum() or char in "!#$%&'*+-.^_`|~")
                    )
                    for char in name
                )
                or name.lower() in forbidden
                or any(ord(char) < 32 or ord(char) == 127 for char in value)
            ):
                raise CustomEndpointError("Invalid custom model request headers.")
        # Validation covers every A/AAAA answer. The pool connects to this
        # literal IP and retains the original hostname for TLS verification.
        pinned_ip = _resolve_pinned_ip(parsed.hostname)
        request_headers = {
            name: value
            for name, value in headers.items()
            if name.lower() != "content-type"
        }
        host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
        request_headers["Host"] = (
            host
            if port == (443 if parsed.scheme == "https" else 80)
            else f"{host}:{port}"
        )
        request_headers["Content-Type"] = "application/json"
        path = parsed.path or "/"
        if parsed.query:
            path += "?" + parsed.query
        timeout = urllib3.Timeout(
            connect=5, read=REQUEST_TIMEOUT_SECONDS, total=REQUEST_TIMEOUT_SECONDS
        )
        pool = _open_pinned_pool(
            parsed.scheme, pinned_ip, parsed.hostname, port, timeout
        )
        deadline = time.monotonic() + REQUEST_TIMEOUT_SECONDS
        response = pool.request(
            "POST",
            path,
            headers=request_headers,
            body=json.dumps(payload).encode("utf-8"),
            redirect=False,
            retries=False,
            preload_content=False,
            decode_content=False,
        )
        if response.headers.get("Content-Encoding", "identity").lower() != "identity":
            raise CustomEndpointError(
                "Compressed custom model responses are not supported."
            )
        if 300 <= response.status < 400:
            raise CustomEndpointError(
                "Custom model endpoint redirects are not supported."
            )
        content = bytearray()
        while True:
            # read1 bounds each socket read rather than waiting to fill a large
            # chunk indefinitely from a peer that trickles bytes.
            chunk = response.read1(64 * 1024)
            if time.monotonic() > deadline:
                raise CustomEndpointError("Custom model endpoint request timed out.")
            if not chunk:
                break
            content.extend(chunk)
            if len(content) > MAX_RESPONSE_BYTES:
                raise CustomEndpointError(
                    "Custom model endpoint response exceeds the 4 MiB limit."
                )
        result = requests.Response()
        result.status_code = response.status
        # Error diagnostics deliberately omit the endpoint's reason phrase,
        # headers, URL and body: a server can echo credentials in any of them.
        result._content = bytes(content)
        result.encoding = "utf-8"
        return result
    except CustomEndpointError:
        raise
    except urllib3.exceptions.TimeoutError:
        raise CustomEndpointError("Custom model endpoint request timed out.") from None
    except Exception:
        raise CustomEndpointError(
            "Custom model endpoint request refused or unavailable."
        ) from None
    finally:
        if response is not None:
            response.close()
        if pool is not None:
            pool.close()
