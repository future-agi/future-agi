"""Bounded public-destination HTTP requests, independent of request threads.

A short-lived interpreter owns DNS and the socket. Killing and reaping it on
expiry enforces one wall-clock budget even when DNS or a peer stops responding;
thread timeouts cannot cancel those operations. No proxy/environment routing,
redirects, compressed responses, or operator private-address override is used.
"""

import base64
import http.client
import ipaddress
import json as jsonlib
import math
import re
import socket
import ssl
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

import requests

DEFAULT_TIMEOUT = 30.0
MAX_BODY_BYTES = 5 * 1024 * 1024
PUBLIC_ERROR = "destination not allowed or unreachable"
# Shared across callers, including preview, conditional, create and rerun threads.
MAX_CONCURRENT_REQUESTS = 32
_WORKER_SLOTS = threading.BoundedSemaphore(MAX_CONCURRENT_REQUESTS)
_PROTOCOL_ASSIGNMENT_NETWORK = ipaddress.ip_network("192.0.0.0/24")
# Translation/tunnel forms are refused altogether, not only their private subset.
_TRANSITION_NETWORKS = tuple(
    ipaddress.ip_network(value)
    for value in (
        "::/96",
        "::ffff:0:0/96",
        "64:ff9b::/96",
        "64:ff9b:1::/48",
        "2002::/16",
        "2001::/32",
    )
)


class DestinationError(ValueError):
    """A reason code for server-side logging, not for returning to a caller."""


def _require_public(address):
    ip = ipaddress.ip_address(address)
    if (
        not ip.is_global
        or ip.is_private
        or ip.is_reserved
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_unspecified
        or (
            ip.version == 6
            and (
                ip.is_site_local
                or any(ip in network for network in _TRANSITION_NETWORKS)
            )
        )
        or (ip.version == 4 and ip in _PROTOCOL_ASSIGNMENT_NETWORK)
        or str(ip) == "168.63.129.16"
    ):
        raise DestinationError("address_class")
    return ip


def _parse_url(url):
    if not isinstance(url, str) or re.search(r"[\x00-\x20\x7f\\]", url):
        raise DestinationError("url_syntax")
    try:
        parsed = urlsplit(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise DestinationError("url_scheme")
        if parsed.username is not None or parsed.password is not None:
            raise DestinationError("url_userinfo")
        host = parsed.hostname.encode("idna").decode("ascii")
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        if "%" in host or parsed.port == 0:
            raise DestinationError("url_authority")
        try:
            literal = ipaddress.ip_address(host)
        except ValueError:
            # inet_aton recognizes integer, abbreviated, hex and octal IPv4.
            try:
                socket.inet_aton(host.rstrip("."))
            except OSError:
                if not re.fullmatch(r"[A-Za-z0-9.-]+", host) or any(
                    not re.fullmatch(
                        r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label
                    )
                    for label in host.rstrip(".").split(".")
                ):
                    raise DestinationError("url_hostname") from None
            else:
                raise DestinationError("url_numeric_spelling")
        else:
            _require_public(literal)
        return parsed, host, port
    except (ValueError, UnicodeError) as exc:
        if isinstance(exc, DestinationError):
            raise
        raise DestinationError("url_syntax") from None


def _resolve(host, port):
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        # Exactly one resolver invocation; check ALL answers before choosing one.
        records = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        addresses = [record[4][0] for record in records]
    else:
        addresses = [str(literal)]
    if not addresses:
        raise DestinationError("dns_empty")
    checked = [_require_public(address) for address in addresses]
    return checked[0]


def _connect(address, port, timeout):
    family = socket.AF_INET6 if address.version == 6 else socket.AF_INET
    sock = socket.socket(family, socket.SOCK_STREAM)
    try:
        sock.settimeout(timeout)
        # A numeric sockaddr, never a hostname: no second DNS lookup.
        sock.connect((str(address), port))
        return sock
    except BaseException:
        sock.close()
        raise


def _exchange(payload):
    _parse_url(payload["url"])
    # Request.prepare() alone does not merge the defaults of requests.request().
    headers = requests.utils.default_headers()
    headers.update(payload.get("headers") or {})
    headers = {name: value for name, value in headers.items() if value is not None}
    prepared = requests.Request(
        method=payload["method"],
        url=payload["url"],
        params=payload.get("params"),
        headers=headers,
        json=payload.get("json"),
        data=payload.get("data"),
    ).prepare()
    parsed, host, port = _parse_url(prepared.url)
    address = _resolve(host, port)
    if prepared.method not in {
        "GET",
        "HEAD",
        "POST",
        "PUT",
        "PATCH",
        "DELETE",
        "OPTIONS",
    }:
        raise DestinationError("method")
    headers = prepared.headers.copy()
    # Routing and message framing are transport-owned, including mixed casing.
    for name in (
        "Host",
        "Accept-Encoding",
        "Proxy-Authorization",
        "Proxy-Connection",
        "Connection",
        "Transfer-Encoding",
        "Content-Length",
    ):
        headers.pop(name, None)
    authority = f"[{host}]" if ":" in host else host
    default_port = 443 if parsed.scheme == "https" else 80
    headers["Host"] = authority if port == default_port else f"{authority}:{port}"
    headers["Accept-Encoding"] = "identity"
    headers["Connection"] = "close"
    if parsed.scheme == "https":
        connection = http.client.HTTPSConnection(
            host,
            port,
            timeout=payload["timeout"],
            context=ssl.create_default_context(cafile=requests.certs.where()),
        )
    else:
        connection = http.client.HTTPConnection(host, port, timeout=payload["timeout"])
    # HTTPSConnection still verifies the certificate and sends SNI for `host`.
    connection._create_connection = lambda *args, **kwargs: _connect(
        address, port, payload["timeout"]
    )
    try:
        path = parsed.path or "/"
        if parsed.query:
            path += "?" + parsed.query
        body = prepared.body
        if isinstance(body, str):
            body = body.encode("utf-8")
        connection.request(prepared.method, path, body=body, headers=headers)
        response = connection.getresponse()
        if response.status in (301, 302, 303, 307, 308):
            raise DestinationError("redirect")
        if response.getheader("Content-Encoding", "identity").strip().lower() not in (
            "",
            "identity",
        ):
            raise DestinationError("content_encoding")
        limit = payload["max_bytes"]
        length = response.getheader("Content-Length")
        if length is not None and int(length) > limit:
            raise DestinationError("body_limit")
        content = bytearray()
        while True:
            chunk = response.read(min(64 * 1024, limit + 1 - len(content)))
            if not chunk:
                break
            content.extend(chunk)
            if len(content) > limit:
                raise DestinationError("body_limit")
        return {
            "status": response.status,
            "headers": dict(response.getheaders()),
            "body": base64.b64encode(content).decode("ascii"),
        }
    finally:
        connection.close()


def _worker():
    try:
        result = _exchange(jsonlib.load(sys.stdin))
    except Exception as exc:
        # Do not copy exception text: it can contain a URL, credentials or data.
        result = {
            "error": str(exc)
            if isinstance(exc, DestinationError)
            else type(exc).__name__
        }
    sys.stdout.write(jsonlib.dumps(result))


def request(
    *,
    method,
    url,
    params=None,
    headers=None,
    json=None,
    data=None,
    timeout=DEFAULT_TIMEOUT,
    max_bytes=MAX_BODY_BYTES,
):
    """Return a bounded requests.Response or raise DestinationError.

    The timeout includes capacity waiting, child startup, DNS, TLS and body reads.
    Secrets travel over a private pipe, never command-line arguments or files.
    """
    started = time.monotonic()
    if not math.isfinite(timeout) or not 0 < timeout <= DEFAULT_TIMEOUT:
        raise DestinationError("deadline_budget")
    if not isinstance(max_bytes, int) or not 0 < max_bytes <= MAX_BODY_BYTES:
        raise DestinationError("body_budget")
    _parse_url(url)
    payload = {
        "method": method,
        "url": url,
        "params": params,
        "headers": headers,
        "json": json,
        "data": data,
        "timeout": timeout,
        "max_bytes": max_bytes,
    }
    encoded = jsonlib.dumps(payload).encode("utf-8")
    remaining = timeout - (time.monotonic() - started)
    if remaining <= 0 or not _WORKER_SLOTS.acquire(timeout=remaining):
        raise DestinationError("deadline")
    try:
        try:
            with subprocess.Popen(
                [sys.executable, "-I", str(Path(__file__).resolve())],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            ) as process:
                try:
                    remaining = timeout - (time.monotonic() - started)
                    if remaining <= 0:
                        raise subprocess.TimeoutExpired(process.args, timeout)
                    output, _ = process.communicate(encoded, timeout=remaining)
                except BaseException:
                    process.kill()
                    process.communicate()
                    raise
                if process.returncode != 0:
                    raise DestinationError("worker_exit")
        finally:
            _WORKER_SLOTS.release()
        result = jsonlib.loads(output)
        if "error" in result:
            raise DestinationError(result["error"])
        response = requests.Response()
        response.status_code = result["status"]
        response.headers = requests.structures.CaseInsensitiveDict(result["headers"])
        response._content = base64.b64decode(result["body"], validate=True)
        response.encoding = requests.utils.get_encoding_from_headers(response.headers)
        response.url = url
        return response
    except subprocess.TimeoutExpired:
        raise DestinationError("deadline") from None
    except (OSError, ValueError) as exc:
        if isinstance(exc, DestinationError):
            raise
        raise DestinationError(type(exc).__name__) from None


if __name__ == "__main__":
    _worker()
