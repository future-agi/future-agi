"""Pinned POST for webhook deliveries (webhooks DESIGN.md sections 6.2 and 13.1), next to ``ssrf_guard``.

``ssrf_guard`` classifies addresses (``classify_ip``) and fetches; this module resolves a name to every answer
(``resolve_all``) and POSTs to addresses the caller already vetted (``safe_post``). It extends the guard rather than
forking it: the address classes live in one place.
"""

import socket
import ssl

import certifi
import urllib3
from urllib3.util.ssl_ import create_urllib3_context
from urllib3.util.ssl_match_hostname import CertificateError as HostnameMismatch

from tfc.utils.ssrf_guard import SsrfBlocked

TLS_ERRORS = (
    urllib3.exceptions.SSLError,
    ssl.SSLError,
    # A certificate for another name. urllib3 checks the name itself (``assert_hostname``, since the socket goes to a
    # pinned address) and raises its own CertificateError, a ValueError that is not an SSLError.
    HostnameMismatch,
    ssl.CertificateError,
)
"""Every way a TLS handshake or certificate check fails: each ends the attempt as ``tls_error`` (WT-28)."""


def tls_context(extra_ca_file: str | None = None) -> ssl.SSLContext:
    """The client context for one pinned POST: urllib3's defaults (TLS 1.2 or later, its options), certificates
    required, trusting the certifi bundle **plus** ``extra_ca_file`` when given (DESIGN.md section 6.2: a private CA on
    self-hosted and dev is added to the public roots, it never replaces them). The name is checked by
    urllib3 against the URL host, from the subject alternative names only, as its own default context does. Raises
    OSError or ssl.SSLError when a CA file cannot be loaded."""
    ctx = create_urllib3_context(cert_reqs=ssl.CERT_REQUIRED)
    ctx.load_verify_locations(cafile=certifi.where())
    if extra_ca_file:
        ctx.load_verify_locations(cafile=extra_ca_file)
    ctx.hostname_checks_common_name = False
    return ctx


class ResolveFailed(SsrfBlocked):
    """The name did not resolve inside its budget."""


def resolve_all(host: str, timeout_s: float) -> list[str]:
    """Every A and AAAA answer for ``host`` as an ABSOLUTE name (a trailing
    dot, so no search list applies), deduplicated in answer order, within
    ``timeout_s``. Raises ResolveFailed. getaddrinfo cannot be cancelled, so
    it runs on a daemon thread and the caller stops waiting at the budget."""
    import threading

    name = host if host.endswith(".") else host + "."
    out: dict = {}

    def run():
        try:
            out["infos"] = socket.getaddrinfo(
                name, None, socket.AF_UNSPEC, socket.SOCK_STREAM
            )
        except OSError as e:  # gaierror is an OSError
            out["error"] = e

    th = threading.Thread(target=run, name="webhooks-resolve", daemon=True)
    th.start()
    th.join(max(0.0, timeout_s))
    if th.is_alive():
        raise ResolveFailed(f"Resolving '{host}' took longer than {timeout_s:.1f} s.")
    if "error" in out:
        raise ResolveFailed(f"Cannot resolve '{host}': {out['error']}")
    answers: list[str] = []
    for info in out.get("infos", ()):
        ip = info[4][0]
        if ip not in answers:
            answers.append(ip)
    return answers


class PostResult:
    """What one pinned POST observed. ``error`` is ``timeout``,
    ``connect_error`` or ``tls_error`` when no answer was read."""

    __slots__ = (
        "status",
        "headers",
        "body",
        "error",
        "connected_ip",
        "ttfb_ms",
        "detail",
    )

    def __init__(
        self,
        status=None,
        headers=None,
        body=b"",
        error=None,
        connected_ip=None,
        ttfb_ms=None,
        detail=None,
    ):
        self.status = status
        self.headers = dict(headers or {})
        self.body = body
        self.error = error
        self.connected_ip = connected_ip
        self.ttfb_ms = ttfb_ms
        self.detail = detail


def _shutdown(conn, holder: dict) -> None:
    # http.client drops conn.sock once a response that will close is read, but
    # the response still reads from that socket: shut down the one captured
    # at connect time.
    sock = holder.get("sock") or getattr(conn, "sock", None)
    if sock is not None:
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass


def safe_post(
    *,
    scheme: str,
    host: str,
    port: int,
    target: str,
    addresses,
    headers,
    body: bytes,
    deadline: float,
    max_read: int = 4096,
    watchdog=None,
    ca_certs: str | None = None,
    monotonic=None,
    abort=None,
) -> PostResult:
    """POST ``body`` to ``host`` through already vetted ``addresses`` (at most
    2, tried in order on connect failure). The caller vets every address; this
    function never resolves, never follows a redirect, sends no proxy, cookie
    or forwarding header, sets Host (with the port when not default),
    Content-Length and ``Accept-Encoding: identity``, verifies TLS against
    ``host`` (certifi, plus ``ca_certs`` when given: ``tls_context``) and reads
    at most ``max_read`` raw bytes. Every TLS failure (an untrusted chain, a
    certificate for another name, a CA file that cannot be loaded) is
    ``tls_error``, never an exception. ``deadline`` (monotonic seconds) bounds
    the whole attempt: ``watchdog.register(deadline, cb)`` shuts the socket down
    when it passes, so a receiver that trickles bytes cannot hold the attempt."""
    import time

    now = monotonic or time.monotonic
    default_port = 443 if scheme == "https" else 80
    send = [("Host", host if port == default_port else f"{host}:{port}")]
    send += [(k, v) for k, v in headers]
    send += [("Content-Length", str(len(body))), ("Accept-Encoding", "identity")]
    last = PostResult(error="connect_error", detail="no address to connect to")
    context = None
    if scheme == "https":
        try:
            context = tls_context(ca_certs)
        except (OSError, ssl.SSLError):
            # The operator's FI_WEBHOOKS_EXTRA_CA_FILE is missing or not PEM: every HTTPS attempt fails the same
            # way until it is fixed. The detail names the setting, never the path.
            return PostResult(
                error="tls_error",
                detail="The extra CA file (FI_WEBHOOKS_EXTRA_CA_FILE) could not be loaded.",
            )
    for ip in list(addresses)[:2]:
        remaining = deadline - now()
        if remaining <= 0:
            return PostResult(error="timeout", detail="deadline passed before connect")
        if scheme == "https":
            conn = urllib3.connection.HTTPSConnection(
                ip,
                port=port,
                timeout=remaining,
                server_hostname=host,
                assert_hostname=host,
                cert_reqs="CERT_REQUIRED",
                ssl_context=context,
            )
        else:
            conn = urllib3.connection.HTTPConnection(ip, port=port, timeout=remaining)
        holder: dict = {}
        token = (
            watchdog.register(deadline, lambda c=conn, h=holder: _shutdown(c, h))
            if watchdog
            else None
        )
        if abort is not None:
            # The dispatcher lost this delivery's lease: close the socket now.
            abort.bind(lambda c=conn, h=holder: _shutdown(c, h))
        started = now()
        try:
            try:
                conn.connect()
                holder["sock"] = conn.sock
                if abort is not None and abort.aborted:
                    return PostResult(
                        error="aborted", connected_ip=ip, detail="lease lost"
                    )
            except TLS_ERRORS as e:
                return PostResult(
                    error="tls_error", connected_ip=ip, detail=str(e)[:200]
                )
            except urllib3.exceptions.NewConnectionError as e:
                # NewConnectionError subclasses ConnectTimeoutError: a refused or
                # unreachable address, not a timeout.
                last = PostResult(error="connect_error", detail=str(e)[:200])
                continue
            except (urllib3.exceptions.ConnectTimeoutError, TimeoutError) as e:
                last = PostResult(error="timeout", detail=str(e)[:200])
                if now() >= deadline:
                    return last
                continue
            except OSError as e:
                last = PostResult(error="connect_error", detail=str(e)[:200])
                continue
            try:
                # Host and Accept-Encoding are given, so urllib3 adds neither; the
                # target is sent byte for byte (the URL rules allow only visible ASCII).
                conn.request(
                    "POST",
                    target,
                    body=body,
                    headers=dict(send),
                    preload_content=False,
                    decode_content=False,
                    enforce_content_length=False,
                )
                resp = conn.getresponse()
                ttfb = int((now() - started) * 1000)
                data = resp.read(max_read, decode_content=False) or b""
                if abort is not None and abort.aborted:
                    return PostResult(
                        error="aborted", connected_ip=ip, detail="lease lost"
                    )
                if token is not None and watchdog.fired(token):
                    # The body was still arriving at the deadline: the read was cut, so the
                    # attempt is a timeout even though a status line came back.
                    return PostResult(
                        error="timeout",
                        connected_ip=ip,
                        ttfb_ms=ttfb,
                        detail="deadline passed while reading",
                    )
                return PostResult(
                    status=resp.status,
                    headers=dict(resp.headers.items()),
                    body=data[:max_read],
                    connected_ip=ip,
                    ttfb_ms=ttfb,
                )
            except TLS_ERRORS as e:
                return PostResult(
                    error="tls_error", connected_ip=ip, detail=str(e)[:200]
                )
            except (OSError, urllib3.exceptions.HTTPError, ValueError) as e:
                if abort is not None and abort.aborted:
                    return PostResult(
                        error="aborted", connected_ip=ip, detail=str(e)[:200]
                    )
                kind = (
                    "timeout"
                    if now() >= deadline or isinstance(e, TimeoutError)
                    else "connect_error"
                )
                return PostResult(error=kind, connected_ip=ip, detail=str(e)[:200])
        finally:
            if token is not None:
                watchdog.cancel(token)
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
    return last
