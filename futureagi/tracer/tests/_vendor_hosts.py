"""Find URLs on a Future AGI Cloud host (futureagi.com or any subdomain) in an
API payload.

A response field that names such a host makes a self-hosted install's, or a
test stack's, browser request production (an icon) or send its user there (a
link). Only URL hosts count: a bare address such as ``someone@futureagi.com``
(the test fixtures' users) is not a host, the same rule as the cloud-exec
production guard.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any

# scheme://authority or a protocol-relative //authority. The authority stops at
# the first character that cannot be in a host[:port] (path, query, quote,
# whitespace, markdown or HTML delimiters).
_URL_AUTHORITY = re.compile(
    r"(?:\b[a-z][a-z0-9+.\-]*:)?//([^/\s\"'<>?#()\[\]`\\]+)", re.IGNORECASE
)


def _host(authority: str) -> str:
    host = authority.rsplit("@", 1)[-1]
    if host.startswith("["):
        return host.lower()
    return host.split(":", 1)[0].rstrip(".").lower()


def is_vendor_host(host: str) -> bool:
    return host == "futureagi.com" or host.endswith(".futureagi.com")


def vendor_urls(payload: Any, path: str = "$") -> Iterator[tuple[str, str]]:
    """Yield ``(json_path, matched_url)`` for every Future AGI host URL in
    ``payload``'s strings (keys included)."""
    if isinstance(payload, dict):
        for key, value in payload.items():
            yield from vendor_urls(key, f"{path}.<key>")
            yield from vendor_urls(value, f"{path}.{key}")
    elif isinstance(payload, (list, tuple)):
        for index, value in enumerate(payload):
            yield from vendor_urls(value, f"{path}[{index}]")
    elif isinstance(payload, str):
        for match in _URL_AUTHORITY.finditer(payload):
            if is_vendor_host(_host(match.group(1))):
                yield path, match.group(0)


def assert_no_vendor_urls(payload: Any, what: str) -> None:
    found = list(vendor_urls(payload))
    assert not found, f"{what} names a Future AGI Cloud host: {found}"
