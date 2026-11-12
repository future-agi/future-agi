"""The one SSRF-guarded fetcher for Django-side MCP fetches (M25x, decision 39).

The upstream OAuth discovery here (protected-resource and authorization-server
metadata), the token, refresh, revocation and registration calls, and M24x's
client metadata document fetch all go through ``fetch``. It judges addresses
as the gateway's guard does (``agentcc-gateway/internal/mcp/egress`` over
``internal/netguard``), so a URL one side refuses the other refuses too:

- Forbidden addresses (link-local, where cloud metadata answers; "this
  network"; multicast; broadcast; and the metadata services that sit inside
  other ranges) are never reached, whatever the operator listed. A metadata
  host name is refused before it is looked up.
- Private and loopback addresses are reached only when an entry of the
  operator's list ``AGENTCC_MCP_EGRESS_PRIVATE_ALLOW`` matches: the same
  variable, and the same entry forms (a CIDR, or an IP or a host name with an
  optional port), the gateway reads for ``mcp.egress.private_allow``. A
  host-name entry matches the name dialled, never the address it resolves to.
  An entry that does not parse opens nothing.
- HTTPS is required, except to an address the list matches (the local mocks).

The host is resolved once per hop, every address it resolves to is checked,
and the connection goes to the checked address, with the certificate checked
against the host name, so a second lookup cannot move it (DNS rebinding).
Redirects are not followed unless the caller asks, and only for GET and
HEAD; each hop is then checked again and credentials are dropped when the
origin changes. Bodies are capped at ``max_bytes``, and reads time out.

A refusal is ``EgressRefused`` with a ``reason``. Its message never names the
address a host resolved to: the caller may have chosen the host, and the
address would map the operator's network for them.
"""

import ipaddress
import json
import os
import re
import socket
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode, urljoin, urlsplit

import certifi
import urllib3

from tfc.utils.ssrf_guard import METADATA_HOSTNAMES

PRIVATE_ALLOW_ENV = "AGENTCC_MCP_EGRESS_PRIVATE_ALLOW"
DEFAULT_TIMEOUT_SECONDS = 10.0
DEFAULT_MAX_BYTES = 1 << 20
MAX_REDIRECTS = 3
USER_AGENT = "futureagi-mcp-connect/1"

_REDIRECTS = frozenset({301, 302, 303, 307, 308})
_CREDENTIAL_HEADERS = frozenset({"authorization", "cookie", "proxy-authorization"})
_DEFAULT_PORTS = {"http": 80, "https": 443}
_HOST_NAME = re.compile(
    r"^(?=.{1,253}$)([a-z0-9_]([a-z0-9_-]{0,61}[a-z0-9_])?)(\.[a-z0-9_]([a-z0-9_-]{0,61}[a-z0-9_])?)*$"
)

# netguard's range table (agentcc-gateway/internal/netguard/netguard.go).
_FORBIDDEN = tuple(
    ipaddress.ip_network(n)
    for n in (
        "0.0.0.0/8",
        "169.254.0.0/16",
        "224.0.0.0/4",
        "255.255.255.255/32",
        "::/128",
        "fe80::/10",
        "ff00::/8",
        "100.100.100.200/32",
        "fd00:ec2::254/128",
        "fd20:ce::254/128",
        "168.63.129.16/32",
    )
)
_LOOPBACK = tuple(ipaddress.ip_network(n) for n in ("127.0.0.0/8", "::1/128"))
_PRIVATE = tuple(
    ipaddress.ip_network(n)
    for n in (
        "10.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "100.64.0.0/10",
        "fc00::/7",
    )
)
_NAT64 = ipaddress.ip_network("64:ff9b::/96")
_IPV4_COMPATIBLE = ipaddress.ip_network("::/96")
_IPV6_SPECIAL = ipaddress.ip_network("::/127")

PUBLIC, PRIVATE, LOOPBACK, FORBIDDEN = "public", "private", "loopback", "forbidden"
_STRICTNESS = {PUBLIC: 0, PRIVATE: 1, LOOPBACK: 2, FORBIDDEN: 3}

_MESSAGES = {
    "invalid_url": "egress refused: the URL is not an http(s) URL with a host",
    "unresolvable": "egress refused: the host does not resolve",
    "forbidden": "egress refused: an address that is never reachable",
    "not_allowed": (
        f"egress refused: a private or loopback address that {PRIVATE_ALLOW_ENV} "
        "does not list"
    ),
    "https_required": (
        "egress refused: plaintext http is allowed only to an address in "
        f"{PRIVATE_ALLOW_ENV}; use https"
    ),
    "redirect": "egress refused: too many redirects",
    "too_large": "egress refused: the response is larger than the limit",
    "timeout": "egress refused: the response took longer than the limit",
    "transport": "egress refused: the request failed",
}


class EgressRefused(Exception):
    """A fetch the guard refused, or one that failed on the way."""

    def __init__(self, reason: str, detail: str = ""):
        self.reason = reason
        message = _MESSAGES[reason]
        super().__init__(f"{message} ({detail})" if detail else message)


@dataclass(frozen=True)
class FetchResponse:
    status: int
    headers: urllib3.HTTPHeaderDict
    content: bytes
    url: str

    def json(self) -> Any:
        """The body as JSON; ``ValueError`` when it is not."""
        return json.loads(self.content.decode("utf-8"))


@dataclass(frozen=True)
class _AllowEntry:
    network: Any = None  # an ip_network: any address in it
    ip: Any = None  # an ip_address: that address
    host: str = ""  # a host name, as dialled
    port: int | None = None  # None matches any port

    def matches(self, host: str, ip, port: int) -> bool:
        if self.port is not None and self.port != port:
            return False
        if self.network is not None:
            return ip.version == self.network.version and ip in self.network
        if self.ip is not None:
            return ip == self.ip
        return self.host == host


def classify(ip) -> str:
    """netguard's class of ``ip``: an IPv4-mapped address is its IPv4
    address, and one that carries an IPv4 address for a translator or tunnel
    (NAT64, 6to4, Teredo, IPv4-compatible) is in the stricter of the two."""
    ip = ipaddress.ip_address(ip)
    if ip.version == 6 and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    found = _classify_address(ip)
    if ip.version == 6:
        for embedded in _embedded_ipv4(ip):
            other = _classify_address(embedded)
            if _STRICTNESS[other] > _STRICTNESS[found]:
                found = other
    return found


def private_allow() -> tuple[_AllowEntry, ...]:
    """The operator's list, read at each fetch. An entry that does not parse,
    or names a metadata host, opens nothing."""
    entries = []
    for raw in os.environ.get(PRIVATE_ALLOW_ENV, "").split(","):
        entry = _parse_entry(raw)
        if entry is not None:
            entries.append(entry)
    return tuple(entries)


def fetch(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    data: dict[str, Any] | None = None,
    json_body: Any = None,
    max_bytes: int = DEFAULT_MAX_BYTES,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    allow_redirects: bool = False,
) -> FetchResponse:
    """Fetch ``url`` through the guard (module docstring).

    ``data`` is sent as a form, ``json_body`` as JSON. A redirect is returned
    as it is unless ``allow_redirects``, which only GET and HEAD may ask for.
    Raises ``EgressRefused``.
    """
    method = method.upper()
    if allow_redirects and method not in ("GET", "HEAD"):
        raise ValueError("only GET and HEAD may follow redirects")
    if data is not None and json_body is not None:
        raise ValueError("send a form or JSON, not both")
    body, sent = _encode(data, json_body)
    sent = {**sent, **(headers or {})}
    allow = private_allow()
    deadline = time.monotonic() + timeout
    origin = _origin(url)
    current = url
    for hop in range(MAX_REDIRECTS + 1):
        response = _fetch_once(
            current,
            method,
            _hop_headers(sent, origin, current),
            body,
            allow=allow,
            max_bytes=max_bytes,
            timeout=timeout,
            deadline=deadline,
        )
        location = response.headers.get("Location")
        if not (allow_redirects and response.status in _REDIRECTS and location):
            return response
        if hop == MAX_REDIRECTS:
            raise EgressRefused("redirect")
        current = urljoin(current, location)
    raise EgressRefused("redirect")  # pragma: no cover - the loop returns or raises


# --- One hop -----------------------------------------------------------------


def _fetch_once(url, method, headers, body, *, allow, max_bytes, timeout, deadline):
    scheme, host, port, target = _split(url)
    ip = _resolve_checked(host, port, allow)
    if scheme == "http" and not any(e.matches(host, ip, port) for e in allow):
        raise EgressRefused("https_required")
    host_header = host if port == _DEFAULT_PORTS[scheme] else f"{host}:{port}"
    pool = _open_pool(scheme, str(ip), host, port, timeout)
    response = None
    try:
        response = pool.request(
            method,
            target,
            body=body,
            headers={**headers, "Host": host_header},
            redirect=False,
            retries=False,
            preload_content=False,
        )
        content = _read(response, max_bytes, deadline)
        return FetchResponse(
            status=response.status,
            headers=urllib3.HTTPHeaderDict(response.headers),
            content=content,
            url=url,
        )
    except (urllib3.exceptions.HTTPError, OSError) as error:
        # The exception text can carry the address; only its type is kept.
        raise EgressRefused("transport", type(error).__name__) from None
    finally:
        if response is not None:
            response.release_conn()
        pool.close()


def _split(url: str):
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        raise EgressRefused("invalid_url") from None
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").rstrip(".").lower()
    if scheme not in _DEFAULT_PORTS or not host:
        raise EgressRefused("invalid_url")
    target = parts.path or "/"
    if parts.query:
        target = f"{target}?{parts.query}"
    return scheme, host, port or _DEFAULT_PORTS[scheme], target


def _resolve_checked(host: str, port: int, allow):
    """The address to connect to: every address ``host`` resolves to is
    checked, and the first is pinned."""
    if host in METADATA_HOSTNAMES:
        raise EgressRefused("forbidden")
    try:
        literal = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        literal = None
    if literal is not None:
        addresses = [literal]
    else:
        try:
            infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except (socket.gaierror, UnicodeError):
            raise EgressRefused("unresolvable") from None
        addresses = sorted(
            {ipaddress.ip_address(info[4][0].split("%", 1)[0]) for info in infos},
            key=lambda a: (a.version, int(a)),
        )
    if not addresses:
        raise EgressRefused("unresolvable")
    for ip in addresses:
        found = classify(ip)
        if found == FORBIDDEN:
            raise EgressRefused("forbidden")
        if found != PUBLIC and not any(e.matches(host, ip, port) for e in allow):
            raise EgressRefused("not_allowed")
    return addresses[0]


def _open_pool(scheme: str, ip: str, host: str, port: int, timeout: float):
    """A connection pool to the pinned ``ip``; TLS checks ``host``'s name."""
    limits = urllib3.Timeout(connect=timeout, read=timeout)
    if scheme == "https":
        return urllib3.HTTPSConnectionPool(
            ip,
            port=port,
            timeout=limits,
            retries=False,
            maxsize=1,
            assert_hostname=host,
            server_hostname=host,
            cert_reqs="CERT_REQUIRED",
            ca_certs=certifi.where(),
        )
    return urllib3.HTTPConnectionPool(
        ip, port=port, timeout=limits, retries=False, maxsize=1
    )


def _read(response, max_bytes: int, deadline: float) -> bytes:
    declared = response.headers.get("Content-Length")
    if declared and declared.isdigit() and int(declared) > max_bytes:
        raise EgressRefused("too_large")
    body = bytearray()
    for chunk in response.stream(64 * 1024):
        body.extend(chunk)
        if len(body) > max_bytes:
            raise EgressRefused("too_large")
        if time.monotonic() > deadline:
            raise EgressRefused("timeout")
    return bytes(body)


# --- Helpers -----------------------------------------------------------------


def _encode(data, json_body) -> tuple[bytes | None, dict[str, str]]:
    headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
    if data is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        return urlencode(data, doseq=True).encode("ascii"), headers
    if json_body is not None:
        headers["Content-Type"] = "application/json"
        return json.dumps(json_body).encode("utf-8"), headers
    return None, headers


def _origin(url: str):
    try:
        parts = urlsplit(url)
        scheme = parts.scheme.lower()
        return (
            scheme,
            (parts.hostname or "").lower(),
            parts.port or _DEFAULT_PORTS.get(scheme),
        )
    except ValueError:
        return None


def _hop_headers(headers: dict[str, str], origin, url: str) -> dict[str, str]:
    """``headers`` for a hop to ``url``: credentials only to the first origin."""
    if _origin(url) == origin:
        return dict(headers)
    return {k: v for k, v in headers.items() if k.lower() not in _CREDENTIAL_HEADERS}


def _classify_address(ip) -> str:
    if any(ip.version == n.version and ip in n for n in _FORBIDDEN):
        return FORBIDDEN
    if any(ip.version == n.version and ip in n for n in _LOOPBACK):
        return LOOPBACK
    if any(ip.version == n.version and ip in n for n in _PRIVATE):
        return PRIVATE
    return PUBLIC


def _embedded_ipv4(ip: ipaddress.IPv6Address) -> list[ipaddress.IPv4Address]:
    """netguard's embeddedIPv4: NAT64's well-known prefix, 6to4, Teredo (both
    addresses) and IPv4-compatible (not ``::`` or ``::1``)."""
    if ip in _NAT64:
        return [ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)]
    if ip.sixtofour is not None:
        return [ip.sixtofour]
    if ip.teredo is not None:
        return list(ip.teredo)
    if ip in _IPV4_COMPATIBLE and ip not in _IPV6_SPECIAL:
        return [ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)]
    return []


def _parse_entry(raw: str) -> _AllowEntry | None:
    entry = raw.strip().lower()
    if not entry:
        return None
    if "/" in entry:
        try:
            return _AllowEntry(network=ipaddress.ip_network(entry, strict=False))
        except ValueError:
            return None
    host, port = entry, None
    if entry.startswith("["):  # [v6]:port
        close = entry.find("]")
        if close == -1:
            return None
        host, rest = entry[1:close], entry[close + 1 :]
        if rest:
            if not rest.startswith(":"):
                return None
            port = _port(rest[1:])
            if port is None:
                return None
    elif entry.count(":") == 1:
        host, _, digits = entry.partition(":")
        port = _port(digits)
        if port is None:
            return None
    host = host.rstrip(".")
    if not host:
        return None
    try:
        return _AllowEntry(ip=ipaddress.ip_address(host), port=port)
    except ValueError:
        pass
    if host in METADATA_HOSTNAMES or not _HOST_NAME.fullmatch(host):
        return None
    return _AllowEntry(host=host, port=port)


def _port(digits: str) -> int | None:
    if not digits.isdigit():
        return None
    port = int(digits)
    return port if 1 <= port <= 65535 else None
