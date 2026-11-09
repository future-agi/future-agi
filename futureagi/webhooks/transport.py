"""The hosted Transport and Resolver adapters (DESIGN.md sections 6.2 and 13.1, R1): ``tfc.utils.ssrf_post``'s
pinned POST and absolute-name resolver behind the core's ports, with one deadline watchdog per process.

No Django import here, so these adapters are tested without a database.
"""

from __future__ import annotations

import threading
import time

from tfc.utils import ssrf_guard, ssrf_post
from webhooks.core.ports import PinnedPost, ResolveError, TransportResult
from webhooks.core.redact import clear_exception_frames
from webhooks.core.watchdog import Watchdog

_watchdog: Watchdog | None = None
_lock = threading.Lock()


def watchdog() -> Watchdog:
    global _watchdog
    with _lock:
        if _watchdog is None:
            _watchdog = Watchdog(time.monotonic)
        return _watchdog


class PinnedTransport:
    """Connects only to the addresses the core vetted; TLS is checked against the URL host."""

    def __init__(self, extra_ca_file: str | None = None, monotonic=time.monotonic):
        self.extra_ca_file = extra_ca_file
        self.monotonic = monotonic

    def post(self, request: PinnedPost) -> TransportResult:
        try:
            r = ssrf_post.safe_post(
                scheme=request.scheme,
                host=request.host,
                port=request.port,
                target=request.target,
                addresses=request.addresses,
                headers=request.headers,
                body=request.body,
                deadline=request.deadline,
                max_read=request.max_read,
                watchdog=watchdog(),
                ca_certs=self.extra_ca_file,
                monotonic=self.monotonic,
                abort=request.abort,
            )
        except Exception as exc:
            # safe_post and urllib3 hold the signed headers, the custom header values, the target and the body in
            # their frames: drop those variables before the exception reaches any error reporter (K2).
            clear_exception_frames(exc)
            raise
        return TransportResult(
            status=r.status,
            headers=r.headers,
            body=r.body,
            error=r.error,
            connected_ip=r.connected_ip,
            ttfb_ms=r.ttfb_ms,
            detail=r.detail,
        )


class SystemResolver:
    """The system resolver on the absolute name. Hosted resolution through ``WEBHOOKS_RESOLVERS`` (public recursive
    resolvers behind the webhook NAT) needs a DNS client library and lands with WH-1c's network work."""

    def resolve(self, host: str, timeout_s: float) -> list[str]:
        try:
            return ssrf_post.resolve_all(host, timeout_s)
        except ssrf_post.ResolveFailed as e:
            raise ResolveError(str(e)) from None


def hosted_address_guard(address: str) -> bool:
    """Hosted belt-and-braces (section 6.2): the address must be ``is_global`` on this runtime's Python and PUBLIC
    to the platform's general SSRF guard, on top of the core's normative tables."""
    import ipaddress

    ip = ipaddress.ip_address(address)
    return ip.is_global and ssrf_guard.classify_ip(ip) is ssrf_guard.IPClass.PUBLIC


class SystemClock:
    def now_ms(self) -> int:
        return time.time_ns() // 1_000_000

    def monotonic(self) -> float:
        return time.monotonic()
