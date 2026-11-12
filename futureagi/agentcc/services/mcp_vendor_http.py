"""Bounded, no-redirect partner transport with fixed-code failures."""

import re
from urllib.parse import urlsplit

from agentcc.services import mcp_safe_fetch
from agentcc.services.mcp_revocation import Revocation


class VendorUnavailable(Exception):
    def __init__(self):
        super().__init__("vendor_unavailable")


def reference(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,256}", value):
        raise VendorUnavailable()
    return value


def origin(url):
    if (
        not isinstance(url, str)
        or not url
        or len(url) > 4096
        or any(ord(char) <= 32 for char in url)
    ):
        raise VendorUnavailable()
    try:
        parsed = urlsplit(url)
        if (
            parsed.scheme not in ("https", "http")
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.fragment
        ):
            raise VendorUnavailable()
        return (
            parsed.scheme,
            parsed.hostname.lower(),
            parsed.port or (443 if parsed.scheme == "https" else 80),
        )
    except (TypeError, ValueError):
        raise VendorUnavailable() from None


def connect_link(url, hosts):
    scheme, host, port = origin(url)
    if scheme != "https" or host not in hosts or port != 443:
        raise VendorUnavailable()
    return url


def fetch(base, path, *, method="GET", headers=None, body=None, form=None):
    try:
        return mcp_safe_fetch.fetch(
            base.rstrip("/") + path,
            method=method,
            headers=headers,
            json_body=body,
            data=form,
            max_bytes=64 * 1024,
            timeout=5,
            allow_redirects=False,
        )
    except Exception:
        # Neither transport errors nor upstream response text leave this boundary.
        raise VendorUnavailable() from None


def object_body(response, statuses=(200,)):
    if response.status not in statuses:
        raise VendorUnavailable()
    try:
        body = response.json()
        if not isinstance(body, dict):
            raise VendorUnavailable()
        return body
    except (ValueError, UnicodeError):
        raise VendorUnavailable() from None


def revocation_status(status, success=(200, 204)):
    if status in success:
        return Revocation("confirmed")
    if status in (408, 429) or status >= 500 or status < 400:
        return Revocation("pending", "vendor_unavailable")
    return Revocation("unconfirmed", f"http_{status}")
