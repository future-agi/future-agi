"""SSRF-safe URL fetch primitive.

The IP that gets validated is the exact IP that gets connected to (no
DNS-rebinding TOCTOU gap), and every redirect hop is re-validated.

Transport: a hosted deployment fetches over HTTPS only, on every hop; no
deployment follows an HTTPS -> HTTP downgrade; caller headers other than
``_FORWARDABLE_HEADERS`` reach only the exact HTTPS URL the caller named.
The two transport refusals raise ``HttpsRequired`` with text meant for the
user (``HOSTED_HTTPS_REQUIRED_MESSAGE`` / ``HTTPS_DOWNGRADE_MESSAGE``).

``guarded_post`` is the POST counterpart for tenant-configured model
endpoints (custom models), under ``model_endpoint_policy``, and
``guarded_httpx_client`` the httpx client for the SDKs (litellm, OpenAI) that
call a tenant's base URL themselves: see the section at the end of this
module.
"""

import base64
import enum
import functools
import ipaddress
import json as _json
import os
import re
import socket
import ssl
import urllib.request
from http import HTTPStatus
from urllib.parse import unquote, urljoin, urlparse, urlsplit

import certifi
import httpcore
import httpx
import urllib3
from urllib3.util.ssl_match_hostname import CertificateError as _HostnameMismatch

_MAX_REDIRECTS = 5
_DEFAULT_TIMEOUT_SECONDS = 5
_DEFAULT_MAX_BODY_BYTES = 25 * 1024 * 1024
# The only caller headers sent on a redirect hop (same host or not) or over
# cleartext HTTP. Everything else (Authorization, Cookie, X-Api-Key,
# X-Secret-Key, Proxy-Authorization, any custom auth header) goes only to the
# exact HTTPS URL the caller named. Compared case-insensitively.
_FORWARDABLE_HEADERS = frozenset(
    {"accept", "accept-encoding", "accept-language", "user-agent"}
)


class SsrfBlocked(ValueError):
    """Permanent SSRF rejection — never retriable.

    Subclasses ValueError so existing `except ValueError` sites keep working,
    but retry loops can catch it specifically and fail fast instead of
    burning the exponential-backoff budget on a URL that will never succeed.
    """


# User-facing text for the two transport refusals (release-lead ruling #8):
# the hosted rule names https:// so the user knows what to change, and the
# downgrade keeps its own wording on every deployment.
HOSTED_HTTPS_REQUIRED_MESSAGE = (
    "This URL must use https:// on Future AGI Cloud (http:// URLs are not fetched)."
)
HTTPS_DOWNGRADE_MESSAGE = "Redirect from https:// to http:// refused."


class HttpsRequired(SsrfBlocked):
    """Refused by the transport rule, not by the address check: a hosted
    deployment was given an http:// URL, or a redirect went from https:// to
    http://. Its text is meant for the user, so a caller that hides other
    SsrfBlocked details behind a generic error should let this one through.
    """


def https_refusal_message(exc: BaseException) -> str | None:
    """The user-facing text of an HttpsRequired anywhere in ``exc``'s chain.

    Storage helpers wrap the refusal (``raise ValueError(...) from e``), so a
    view that answers with a fixed generic error can use this to show the
    transport rule instead. Follows ``__cause__``, then ``__context__`` unless
    the context was suppressed (``from None``), like a traceback does; any
    other failure, including an address refusal, gives None.
    """
    seen = set()
    current = exc
    while current is not None and id(current) not in seen:
        if isinstance(current, HttpsRequired):
            return str(current)
        seen.add(id(current))
        if current.__cause__ is not None:
            current = current.__cause__
        elif current.__suppress_context__:
            current = None
        else:
            current = current.__context__
    return None


class IPClass(enum.Enum):
    """What an address is to a request made on someone else's say-so."""

    PUBLIC = "public"
    # Private, LAN and reserved ranges: refused unless a caller lets the
    # operator opt in (a local model server, say).
    PRIVATE = "private"
    # Loopback, link-local (cloud metadata answers on 169.254.169.254),
    # multicast, broadcast, "this network", and the metadata services outside
    # link-local: never reachable.
    NEVER = "never"


# Never reachable, beyond what ipaddress flags: "this network", broadcast,
# and the metadata services outside link-local (Alibaba's inside CGNAT, AWS
# IMDS and GCP's metadata server over IPv6 inside fc00::/7, Azure's
# WireServer in public space): netguard's forbiddenNets.
_NEVER_ALLOWED_NETWORKS = tuple(
    ipaddress.ip_network(n)
    for n in (
        "0.0.0.0/8",
        "255.255.255.255/32",
        "100.100.100.200/32",
        "fd00:ec2::254/128",
        "fd20:ce::254/128",
        "168.63.129.16/32",
    )
)
# 100.64.0.0/10 is RFC 6598 carrier-grade NAT (used by some cloud providers,
# Tailscale). Not covered by ipaddress.is_private/is_reserved.
_CGNAT_NETWORK = ipaddress.ip_network("100.64.0.0/10")
# Metadata endpoints by name, for callers that refuse them before any lookup.
METADATA_HOSTNAMES = frozenset(
    {
        "metadata",
        "metadata.google.internal",
        "metadata.goog",
        "instance-data",
        "instance-data.ec2.internal",
    }
)


class SsrfResponse:
    """Small requests.Response-like object returned by safe_fetch.

    `content` is fully buffered in memory (bounded by max_bytes). `iter_content`
    yields chunks of that already-loaded buffer — it does NOT stream; keeping
    the method for drop-in compatibility with callers migrated off requests.
    """

    __slots__ = ("status_code", "headers", "content", "final_url")

    def __init__(self, status_code, headers, content, final_url):
        self.status_code = status_code
        # HTTPHeaderDict is case-insensitive (matches requests.Response's
        # CaseInsensitiveDict contract): callers can look up 'Content-Type'
        # regardless of the casing the server sent (or a test constructed).
        self.headers = (
            urllib3.HTTPHeaderDict(headers)
            if headers is not None
            else urllib3.HTTPHeaderDict()
        )
        self.content = content
        self.final_url = final_url

    def iter_content(self, chunk_size=8192):
        for i in range(0, len(self.content), chunk_size):
            yield self.content[i : i + chunk_size]

    @property
    def text(self) -> str:
        """``content`` decoded with the Content-Type charset (UTF-8 when it
        names none or an unknown one), undecodable bytes replaced."""
        charset = None
        for param in str(self.headers.get("Content-Type", "")).split(";")[1:]:
            key, _, value = param.strip().partition("=")
            if key.strip().lower() == "charset" and value.strip():
                charset = value.strip().strip("\"'")
        try:
            return self.content.decode(charset or "utf-8", errors="replace")
        except LookupError:
            return self.content.decode("utf-8", errors="replace")

    def raise_for_status(self):
        if self.status_code >= 400:
            raise urllib3.exceptions.HTTPError(f"HTTP {self.status_code}")

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


def is_valid_url(url_string: str) -> bool:
    """Cheap syntactic URL check (no network). Does not imply safety."""
    try:
        parsed = urlparse(url_string)
    except Exception:
        return False
    return parsed.scheme in ("http", "https") and bool(parsed.hostname)


_RANK = {IPClass.PUBLIC: 0, IPClass.PRIVATE: 1, IPClass.NEVER: 2}
# RFC 8215 local-use NAT64: the embedded IPv4 address cannot be read reliably
# (RFC 6052 allows several layouts), so nothing inside it is ever reachable.
_LOCAL_USE_NAT64 = ipaddress.ip_network("64:ff9b:1::/48")


def classify_ip(ip: str | ipaddress.IPv4Address | ipaddress.IPv6Address) -> IPClass:
    """Classify an address.

    IPv6 addresses that carry an IPv4 address (IPv4-mapped ::ffff:0:0/96,
    IPv4-translated ::ffff:0:0:0/96 (RFC 6145 SIIT), IPv4-compatible ::/96,
    NAT64 64:ff9b::/96, 6to4 2002::/16, Teredo 2001::/32 server and client)
    count as the stricter of the outer and the embedded class, so
    ``64:ff9b::a9fe:a9fe`` is NEVER like 169.254.169.254, and
    ``::ffff:0:7f00:1`` and ``::7f00:1`` are NEVER like 127.0.0.1 (webhooks
    DESIGN.md section 6.2, S6; conformance 1.2.0). The unwrapping rule is the
    webhooks core's, shared so there is one copy of it.
    """
    from webhooks.core.ipclass import embedded_ipv4

    ip = ipaddress.ip_address(ip)
    if ip.version == 6 and ip in _LOCAL_USE_NAT64:
        return IPClass.NEVER
    cls = _classify_plain(ip)
    for inner in embedded_ipv4(ip):
        inner_cls = _classify_plain(inner)
        if _RANK[inner_cls] > _RANK[cls]:
            cls = inner_cls
    return cls


def _classify_plain(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> IPClass:
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    if (
        ip.is_loopback
        or ip.is_link_local  # covers 169.254.169.254 metadata endpoint
        or ip.is_multicast
        or ip.is_unspecified
        or any(ip in net for net in _NEVER_ALLOWED_NETWORKS)
    ):
        return IPClass.NEVER
    if (
        ip.is_private
        or ip.is_reserved
        or ip in _CGNAT_NETWORK
        # Deprecated IPv6 site-local (fec0::/10, RFC 3879): still routed
        # inside some networks, and ipaddress flags it neither private nor
        # reserved.
        or (ip.version == 6 and ip.is_site_local)
    ):
        return IPClass.PRIVATE
    return IPClass.PUBLIC


def _reject_unsafe_ip(ip_str: str, host: str) -> None:
    if classify_ip(ip_str) is not IPClass.PUBLIC:
        raise SsrfBlocked(f"URL host '{host}' resolves to a private/internal address.")


def _resolve_pinned_ip(host: str) -> str:
    """Resolve `host` and validate every A/AAAA record. Returns one pinned IP."""
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as e:
        raise SsrfBlocked(f"Cannot resolve URL host '{host}': {e}") from None

    resolved_ips = sorted({info[4][0] for info in infos})
    if not resolved_ips:
        raise SsrfBlocked(f"Cannot resolve URL host '{host}'.")

    for ip_str in resolved_ips:
        _reject_unsafe_ip(ip_str, host)
    return resolved_ips[0]


def assert_url_host_public(url: str) -> None:
    """Raise SsrfBlocked unless `url`'s host resolves entirely to public IPs.

    A pre-flight guard for streaming/async callers that do their own fetch and
    only need the reject, not safe_fetch's buffered body. Because the fetch
    re-resolves at connect time this keeps a small DNS-rebinding TOCTOU gap that
    safe_fetch closes — acceptable when the URL is provider-returned over
    authenticated TLS (e.g. a recording URL inside a provider's API response).
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise SsrfBlocked(f"Unsupported URL scheme: '{parsed.scheme}'.")
    if not parsed.hostname:
        raise SsrfBlocked("Invalid URL: missing host.")
    _resolve_pinned_ip(parsed.hostname)  # resolves + rejects every A/AAAA record


def _https_required() -> bool:
    """Whether every hop must be HTTPS: CLOUD_DEPLOYMENT names a Future AGI
    Cloud region. Region only (as agentcc.services.url_safety), not
    DeploymentMode.is_cloud(), so a missing deployment secret cannot reopen
    cleartext fetching on a hosted pod."""
    from django.conf import settings

    from tfc.ee_loader import is_cloud_env

    deployment = (
        getattr(settings, "CLOUD_DEPLOYMENT", None) if settings.configured else None
    )
    return is_cloud_env(deployment)


def _open_pinned_pool(scheme, pinned_ip, host, port, timeout):
    if scheme == "https":
        return urllib3.HTTPSConnectionPool(
            pinned_ip,
            port=port,
            timeout=timeout,
            retries=False,
            assert_hostname=host,
            server_hostname=host,
            cert_reqs="CERT_REQUIRED",
            ca_certs=certifi.where(),
        )
    return urllib3.HTTPConnectionPool(
        pinned_ip, port=port, timeout=timeout, retries=False
    )


def safe_fetch(
    url: str,
    *,
    method: str = "HEAD",
    timeout: float = _DEFAULT_TIMEOUT_SECONDS,
    max_bytes: int = _DEFAULT_MAX_BODY_BYTES,
    headers: dict = None,
) -> SsrfResponse:
    """Fetch `url` with SSRF protection.

    Method: HEAD or GET. For GET, the body is buffered up to `max_bytes`.
    Redirects are followed manually (5 hops max), re-validating each hop.
    Raises ValueError on any SSRF-relevant rejection; HttpsRequired (an
    SsrfBlocked) when a redirect downgrades https:// to http:// on any
    deployment (HTTPS_DOWNGRADE_MESSAGE), or when a hosted deployment is
    given an http:// URL (HOSTED_HTTPS_REQUIRED_MESSAGE).
    """
    current_url = url
    caller_headers = headers or {}
    https_required = _https_required()
    seen_https = False
    for hop in range(_MAX_REDIRECTS):
        parsed = urlparse(current_url)
        if parsed.scheme not in ("http", "https"):
            raise SsrfBlocked(f"Unsupported URL scheme: '{parsed.scheme}'.")
        # Refused before DNS or connect: public-IP filtering is not transport
        # confidentiality. The downgrade is checked first so a hosted user
        # who gave an https:// URL is told about the redirect, not asked to
        # use the https:// they already used.
        if parsed.scheme == "http":
            if seen_https:
                raise HttpsRequired(HTTPS_DOWNGRADE_MESSAGE)
            if https_required:
                raise HttpsRequired(HOSTED_HTTPS_REQUIRED_MESSAGE)
        else:
            seen_https = True
        host = parsed.hostname
        if not host:
            raise SsrfBlocked("Invalid URL: missing host.")
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        pinned_ip = _resolve_pinned_ip(host)

        path = parsed.path or "/"
        if parsed.query:
            path = f"{path}?{parsed.query}"

        # Host header must include the port for non-default ports so
        # vhost-routed backends match the correct server_name.
        default_port = 443 if parsed.scheme == "https" else 80
        host_header = host if port == default_port else f"{host}:{port}"

        # Credentials go only to the exact HTTPS URL the caller named: a
        # redirect hop (same host or not) or a cleartext hop gets only the
        # non-sensitive allowlist.
        if hop or parsed.scheme == "http":
            hop_headers = {
                name: value
                for name, value in caller_headers.items()
                if str(name).lower() in _FORWARDABLE_HEADERS
            }
        else:
            hop_headers = dict(caller_headers)

        pool = _open_pinned_pool(parsed.scheme, pinned_ip, host, port, timeout)
        response = None
        try:
            response = pool.request(
                method,
                path,
                headers={"Host": host_header, **hop_headers},
                redirect=False,
                preload_content=False,
            )

            if response.status in (301, 302, 303, 307, 308):
                location = response.headers.get("Location")
                if not location:
                    raise ValueError("Redirect with no Location header.")
                current_url = urljoin(current_url, location)
                if not is_valid_url(current_url):
                    raise SsrfBlocked("Redirect target is not a valid URL.")
                continue

            content = b""
            if method == "GET" and response.status == 200:
                body = bytearray()
                for chunk in response.stream(64 * 1024):
                    body.extend(chunk)
                    if len(body) > max_bytes:
                        raise ValueError(f"URL body exceeds {max_bytes} byte limit.")
                content = bytes(body)

            return SsrfResponse(
                status_code=response.status,
                headers=response.headers,
                content=content,
                final_url=current_url,
            )
        except urllib3.exceptions.HTTPError as e:
            raise ValueError(f"Cannot access URL: {e}") from None
        finally:
            if response is not None:
                response.release_conn()
            pool.close()

    raise ValueError("Too many redirects.")


# ---------------------------------------------------------------------------
# Model endpoints: a pinned, policy-checked POST (custom models)
# ---------------------------------------------------------------------------
#
# A custom model's endpoint is a URL a workspace member types, which the
# backend then POSTs to with headers the member also chose, returning the
# body to them. ``guarded_post`` makes that safe the way ``safe_fetch`` does
# for GET: every A/AAAA answer is checked and the connection goes to the
# checked address (TLS still verifies the name), redirects are refused rather
# than followed, metadata and forwarding headers are dropped, the body is
# capped, and errors carry a category with generic text (no host, address or
# body). Unlike ``safe_fetch`` it never requires https:// (plain http public
# endpoints keep working everywhere) and its address rule depends on the
# deployment (``model_endpoint_policy``):
#
# * Hosted (CLOUD_DEPLOYMENT names a Future AGI Cloud region): public
#   addresses only, as ``classify_ip`` defines them.
# * Self-hosted, by default: loopback and private/LAN addresses are allowed
#   with no setting, so a model on localhost or the LAN keeps working as in
#   v1.56.1. Link-local (169.254.0.0/16, fe80::/10) and the cloud metadata
#   services (169.254.169.254, 169.254.170.2, 169.254.170.23, fd00:ec2::254,
#   fd00:ec2::23, fd20:ce::254, 100.100.100.200, 168.63.129.16, the
#   ``METADATA_HOSTNAMES``) are refused: no model server listens there, and
#   they hand cloud instance credentials to every workspace member. An
#   operator whose model host is only reachable on a link-local address
#   (rootless Podman maps ``host.containers.internal`` to 169.254.1.2) lists
#   that range in CUSTOM_MODEL_ALLOWED_LINK_LOCAL_CIDRS; 169.254.169.0/24 and
#   169.254.170.0/24 are never admitted.
# * Self-hosted with CUSTOM_MODEL_DENY_PRIVATE_ENDPOINTS=true: public only,
#   as on hosted (the link-local setting is then ignored, as on hosted).
#
# Both ``guarded_post`` and ``guarded_httpx_client`` send through the
# operator's env proxy (HTTP_PROXY, HTTPS_PROXY, ALL_PROXY, NO_PROXY, as
# requests reads them) when one applies, after checking every DNS answer for
# the target; the proxy resolves the name again, a window that exists only
# behind the operator's own proxy.

DENY_PRIVATE_MODEL_ENDPOINTS_ENV = "CUSTOM_MODEL_DENY_PRIVATE_ENDPOINTS"
LINK_LOCAL_ALLOWED_ENV = "CUSTOM_MODEL_ALLOWED_LINK_LOCAL_CIDRS"
MODEL_ENDPOINT_MAX_RESPONSE_BYTES = 2 * 1024 * 1024
_MODEL_ENDPOINT_CONNECT_TIMEOUT_CAP = 30.0
_MODEL_ENDPOINT_DEFAULT_TIMEOUT = 600.0
# Checked addresses tried in turn when one cannot be connected to.
_MODEL_ENDPOINT_MAX_ADDRESSES = 2
_TRUTHY = frozenset({"1", "t", "true", "y", "yes", "on"})

# Refused on every deployment even where private addresses are allowed: the
# metadata and credential services outside link-local (Alibaba's in CGNAT,
# AWS IMDS and EKS Pod Identity over IPv6 and GCP's metadata server inside
# fc00::/7, Azure's WireServer in public space), and broadcast. Link-local
# and multicast are tested with ipaddress's flags; IPv6 forms that embed an
# IPv4 address are unwrapped first (``embedded_ipv4``), so
# ::ffff:169.254.169.254 and 64:ff9b::a9fe:a9fe count as 169.254.169.254.
_ALWAYS_REFUSED_NETWORKS = tuple(
    ipaddress.ip_network(n)
    for n in (
        "100.100.100.200/32",
        "fd00:ec2::254/128",
        "fd00:ec2::23/128",
        "fd20:ce::254/128",
        "168.63.129.16/32",
        "255.255.255.255/32",
    )
)
_LINK_LOCAL_NETWORKS = (
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("fe80::/10"),
)
# Link-local ranges the cloud metadata, credential and instance services
# answer in (AWS IMDS, DNS and NTP and the GCP, Azure and OCI metadata
# servers in 169.254.169.0/24; ECS task credentials and EKS Pod Identity in
# 169.254.170.0/24): never admitted, whatever the operator lists.
_NEVER_ADMITTED_LINK_LOCAL = (
    ipaddress.ip_network("169.254.169.0/24"),
    ipaddress.ip_network("169.254.170.0/24"),
)

# Caller headers never sent to a model endpoint, compared case-insensitively.
# The ones metadata services and internal proxies trust (also prefixes,
# below), dropped by every guarded client:
_METADATA_AND_FORWARDING_HEADERS = frozenset(
    {
        "metadata-flavor",
        "x-google-metadata-request",
        "metadata",
        "forwarded",
        "x-real-ip",
        "x-scope-orgid",
    }
)
# ...and, for ``guarded_post``, the ones it sets itself and hop-by-hop
# headers (an httpx client sets these itself, from the request).
_STRIPPED_ENDPOINT_HEADERS = _METADATA_AND_FORWARDING_HEADERS | frozenset(
    {
        "host",
        "content-length",
        "transfer-encoding",
        "connection",
        "keep-alive",
        "te",
        "trailer",
        "upgrade",
        "expect",
    }
)
_STRIPPED_ENDPOINT_HEADER_PREFIXES = (
    "x-aws-ec2-metadata-token",
    "x-forwarded-",
    "proxy-",
    "x-clickhouse-",
    "x-envoy-",
)

ENDPOINT_ERROR_MESSAGES = {
    "invalid_url": (
        "The endpoint URL must be an http:// or https:// URL with a host name."
    ),
    "blocked": (
        "The endpoint URL is not allowed: its host is, or resolves to, a "
        "private, loopback, link-local or cloud metadata address."
    ),
    "unresolvable": "The endpoint host name could not be resolved.",
    "redirect": (
        "The endpoint answered with a redirect, which is not followed. Use the "
        "endpoint's final URL."
    ),
    "timeout": "The endpoint did not answer in time.",
    "connect": "Could not connect to the endpoint.",
    "tls": "The TLS connection to the endpoint failed (certificate or handshake).",
    "bad_response": "The endpoint's response could not be read.",
    "too_large": "The endpoint's response is larger than the allowed size.",
    "invalid_request": (
        "The request to the endpoint could not be sent; check the configured headers."
    ),
    "unsupported": "This model type cannot be called at a custom endpoint URL.",
    "failed": "The call to the model endpoint failed.",
}


class EndpointPolicy(enum.Enum):
    """Which addresses a model endpoint may resolve to."""

    PUBLIC_ONLY = "public_only"
    PRIVATE_ALLOWED = "private_allowed"


class EndpointBlocked(SsrfBlocked):
    """The endpoint URL is refused before any connection. ``category`` is
    ``invalid_url``, ``blocked``, ``unresolvable`` or ``unsupported`` (a
    litellm provider whose routes the guard does not cover); ``str()`` is
    the generic ``ENDPOINT_ERROR_MESSAGES`` text, safe to show the tenant."""

    def __init__(self, category: str = "blocked"):
        self.category = category
        super().__init__(ENDPOINT_ERROR_MESSAGES[category])


class GuardedRequestError(ValueError):
    """``guarded_post`` connected (or tried to) and failed. ``category`` is
    ``redirect``, ``timeout``, ``connect``, ``tls``, ``bad_response``,
    ``too_large`` or ``invalid_request``; ``str()`` is generic."""

    def __init__(self, category: str):
        self.category = category
        super().__init__(ENDPOINT_ERROR_MESSAGES[category])


def _is_hosted() -> bool:
    from django.conf import settings

    from tfc.ee_loader import is_cloud_env

    deployment = (
        getattr(settings, "CLOUD_DEPLOYMENT", None) if settings.configured else None
    )
    return is_cloud_env(deployment)


def model_endpoint_policy() -> EndpointPolicy:
    """The address policy for model endpoints on this deployment, read on
    every call: public only on hosted (no setting can widen it) and on a
    self-hosted install with CUSTOM_MODEL_DENY_PRIVATE_ENDPOINTS=true;
    otherwise loopback and private networks are allowed."""
    if _is_hosted():
        return EndpointPolicy.PUBLIC_ONLY
    raw = os.environ.get(DENY_PRIVATE_MODEL_ENDPOINTS_ENV, "")
    if raw.strip().lower() in _TRUTHY:
        return EndpointPolicy.PUBLIC_ONLY
    return EndpointPolicy.PRIVATE_ALLOWED


def _operator_link_local_networks() -> tuple:
    """The link-local ranges CUSTOM_MODEL_ALLOWED_LINK_LOCAL_CIDRS lists
    (comma, semicolon or space separated), read on every call. Entries that
    do not parse, or are not inside 169.254.0.0/16 or fe80::/10, are
    ignored: the setting can only re-admit link-local addresses."""
    networks = []
    for item in re.split(r"[\s,;]+", os.environ.get(LINK_LOCAL_ALLOWED_ENV, "")):
        if not item:
            continue
        try:
            network = ipaddress.ip_network(item, strict=False)
        except ValueError:
            continue
        if any(
            network.version == link_local.version and network.subnet_of(link_local)
            for link_local in _LINK_LOCAL_NETWORKS
        ):
            networks.append(network)
    return tuple(networks)


def _link_local_admitted(ip, admitted) -> bool:
    if ip.version == 4 and any(ip in net for net in _NEVER_ADMITTED_LINK_LOCAL):
        return False
    return any(ip.version == net.version and ip in net for net in admitted)


def _always_refused(
    ip: ipaddress.IPv4Address | ipaddress.IPv6Address, admitted_link_local=()
) -> bool:
    from webhooks.core.ipclass import embedded_ipv4

    if ip.version == 6 and ip in _LOCAL_USE_NAT64:
        return True
    for candidate in (ip, *embedded_ipv4(ip)):
        if candidate.version == 6 and candidate.ipv4_mapped:
            candidate = candidate.ipv4_mapped
        if candidate.is_multicast or any(
            candidate in net for net in _ALWAYS_REFUSED_NETWORKS
        ):
            return True
        if candidate.is_link_local and not _link_local_admitted(
            candidate, admitted_link_local
        ):
            return True
    return False


def endpoint_address_allowed(ip, policy: EndpointPolicy) -> bool:
    """Whether a model endpoint may connect to ``ip`` under ``policy``.

    Under PRIVATE_ALLOWED (self-hosted default) a link-local address the
    operator listed in CUSTOM_MODEL_ALLOWED_LINK_LOCAL_CIDRS is allowed,
    unless it is in a metadata range; PUBLIC_ONLY ignores the setting."""
    ip = ipaddress.ip_address(ip)
    admitted = (
        _operator_link_local_networks()
        if policy is EndpointPolicy.PRIVATE_ALLOWED
        else ()
    )
    if _always_refused(ip, admitted):
        return False
    if policy is EndpointPolicy.PUBLIC_ONLY:
        return classify_ip(ip) is IPClass.PUBLIC
    return True


def is_stripped_endpoint_header(name) -> bool:
    """Whether ``guarded_post`` drops a caller header of this name."""
    lowered = str(name).strip().lower()
    return lowered in _STRIPPED_ENDPOINT_HEADERS or lowered.startswith(
        _STRIPPED_ENDPOINT_HEADER_PREFIXES
    )


def is_metadata_or_forwarding_header(name) -> bool:
    """Whether a caller header of this name is one metadata services or
    internal proxies trust (Metadata-Flavor, X-aws-ec2-metadata-token*,
    X-Forwarded-*, Proxy-*, ...). Every guarded client drops these."""
    lowered = str(name).strip().lower()
    return lowered in _METADATA_AND_FORWARDING_HEADERS or lowered.startswith(
        _STRIPPED_ENDPOINT_HEADER_PREFIXES
    )


def _url_host(host: str) -> str:
    return f"[{host}]" if ":" in host else host


def _origin(scheme: str, host: str, port: int) -> str:
    default_port = 443 if scheme == "https" else 80
    netloc = _url_host(host) if port == default_port else f"{_url_host(host)}:{port}"
    return f"{scheme}://{netloc}"


def _env_proxy_for(url: str) -> str | None:
    """The proxy ``requests`` would send ``url`` through, from the
    environment only (HTTP_PROXY, HTTPS_PROXY, ALL_PROXY and NO_PROXY, either
    case; never a desktop's system proxy settings), or None."""
    from requests.utils import (
        prepend_scheme_if_needed,
        select_proxy,
        should_bypass_proxies,
    )

    proxies = {
        scheme: value
        for scheme, value in urllib.request.getproxies_environment().items()
        if scheme != "no" and value
    }
    if not proxies:
        return None
    try:
        if should_bypass_proxies(url, no_proxy=None):
            return None
    except Exception:
        return None
    proxy = select_proxy(url, proxies)
    if not proxy:
        return None
    return prepend_scheme_if_needed(proxy, "http")


def _resolution_optional(proxy_url, policy) -> bool:
    """Behind an env proxy the proxy resolves the target; a name this
    container cannot resolve (split-horizon egress) may then pass on a
    self-hosted install. Hosted always requires the answers it checks."""
    return proxy_url is not None and policy is EndpointPolicy.PRIVATE_ALLOWED


def _parse_endpoint(url):
    """(scheme, host, port, split URL) of an http(s) URL; EndpointBlocked
    ("invalid_url") for anything else, including whitespace or control
    characters anywhere in it."""
    if not isinstance(url, str):
        raise EndpointBlocked("invalid_url")
    url = url.strip()
    if not url or any(ch.isspace() or not ch.isprintable() for ch in url):
        raise EndpointBlocked("invalid_url")
    try:
        parts = urlsplit(url)
        host = parts.hostname
        port = parts.port
    except ValueError:
        raise EndpointBlocked("invalid_url") from None
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https") or not host:
        raise EndpointBlocked("invalid_url")
    if not host.isascii():
        try:
            host = host.encode("idna").decode("ascii")
        except UnicodeError:
            raise EndpointBlocked("invalid_url") from None
    return scheme, host, port or (443 if scheme == "https" else 80), parts


def _endpoint_addresses(host, policy, *, allow_unresolvable=False):
    """Every address ``host`` resolves to, in resolver order, once each is
    allowed under ``policy``; EndpointBlocked otherwise. Metadata names and
    address literals are decided without DNS."""
    if host.rstrip(".").lower() in METADATA_HOSTNAMES:
        raise EndpointBlocked("blocked")
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        if not endpoint_address_allowed(literal, policy):
            raise EndpointBlocked("blocked")
        return [host]
    try:
        infos = socket.getaddrinfo(host, None, 0, socket.SOCK_STREAM)
    except (OSError, UnicodeError):
        infos = []
    addresses = []
    for info in infos:
        address = info[4][0]
        if address not in addresses:
            addresses.append(address)
    if not addresses:
        if allow_unresolvable:
            return []
        raise EndpointBlocked("unresolvable")
    for address in addresses:
        try:
            allowed = endpoint_address_allowed(address, policy)
        except ValueError:
            allowed = False
        if not allowed:
            raise EndpointBlocked("blocked")
    return addresses


def check_endpoint_url(url, *, policy=None, allow_unresolvable=False) -> None:
    """Raise EndpointBlocked unless ``url`` is an http(s) URL whose host may
    be called under ``policy`` (default: ``model_endpoint_policy()``).

    The pre-flight for saving a configuration and for a call made through a
    client that cannot pin its own connection; ``guarded_post`` and
    ``guarded_httpx_client`` check again at connect time. ``allow_unresolvable``
    lets a name this container cannot resolve pass (saving should not depend
    on local DNS; the call is checked again); a self-hosted install also lets
    it pass when an env proxy will carry the request (the proxy resolves)."""
    scheme, host, port, _parts = _parse_endpoint(url)
    policy = policy or model_endpoint_policy()
    _endpoint_addresses(
        host,
        policy,
        allow_unresolvable=allow_unresolvable
        or _resolution_optional(_env_proxy_for(_origin(scheme, host, port)), policy),
    )


def _ca_bundle_kwargs() -> dict:
    """The CA trust ``requests`` used for these calls before (it honours
    REQUESTS_CA_BUNDLE, then CURL_CA_BUNDLE), so a self-hosted model behind
    a private CA keeps verifying; certifi otherwise."""
    bundle = os.environ.get("REQUESTS_CA_BUNDLE") or os.environ.get("CURL_CA_BUNDLE")
    if bundle and os.path.isdir(bundle):
        return {"ca_cert_dir": bundle}
    return {"ca_certs": bundle or certifi.where()}


def _open_endpoint_pool(scheme, pinned_ip, host, port, timeout):
    pool_timeout = urllib3.Timeout(
        connect=min(timeout, _MODEL_ENDPOINT_CONNECT_TIMEOUT_CAP), read=timeout
    )
    if scheme == "https":
        return urllib3.HTTPSConnectionPool(
            pinned_ip,
            port=port,
            timeout=pool_timeout,
            retries=False,
            assert_hostname=host,
            server_hostname=host,
            cert_reqs="CERT_REQUIRED",
            **_ca_bundle_kwargs(),
        )
    return urllib3.HTTPConnectionPool(
        pinned_ip, port=port, timeout=pool_timeout, retries=False
    )


def _endpoint_headers(headers, parts, scheme, host, port, has_body):
    sent = {}
    for name, value in (headers or {}).items():
        if value is None or is_stripped_endpoint_header(name):
            continue
        if isinstance(value, bytes):
            value = value.decode("latin-1")
        elif not isinstance(value, str):
            value = str(value)
        sent[str(name)] = value
    present = {name.lower() for name in sent}
    if parts.username is not None and "authorization" not in present:
        # requests turned URL credentials into Basic auth; keep doing so.
        userinfo = f"{unquote(parts.username)}:{unquote(parts.password or '')}"
        token = base64.b64encode(userinfo.encode("utf-8")).decode("ascii")
        sent["Authorization"] = f"Basic {token}"
    if has_body and "content-type" not in present:
        sent["Content-Type"] = "application/json"
    if "accept" not in present:
        sent["Accept"] = "*/*"
    if "accept-encoding" not in present:
        sent["Accept-Encoding"] = "identity"
    if "user-agent" not in present:
        sent["User-Agent"] = "FutureAGI-model-endpoint"
    host_name = f"[{host}]" if ":" in host else host
    default_port = 443 if scheme == "https" else 80
    sent["Host"] = host_name if port == default_port else f"{host_name}:{port}"
    return sent


def _transport_error_category(exc: BaseException) -> str | None:
    """The ``GuardedRequestError`` category of a transport exception, or None
    for one that is not about the connection. Order matters: urllib3's
    SSLError is an HTTPError, ssl errors and TimeoutError are OSErrors, a
    certificate name mismatch is a ValueError, and NewConnectionError (a
    refused or unreachable address) subclasses ConnectTimeoutError."""
    if isinstance(
        exc,
        (urllib3.exceptions.SSLError, ssl.SSLError, ssl.CertificateError),
    ) or isinstance(exc, _HostnameMismatch):
        return "tls"
    if isinstance(exc, urllib3.exceptions.NewConnectionError):
        return "connect"
    if isinstance(
        exc,
        (
            urllib3.exceptions.ConnectTimeoutError,
            urllib3.exceptions.ReadTimeoutError,
            TimeoutError,
        ),
    ):
        return "timeout"
    if isinstance(
        exc, (urllib3.exceptions.ProtocolError, urllib3.exceptions.DecodeError)
    ):
        return "bad_response"
    if isinstance(exc, (urllib3.exceptions.HTTPError, OSError)):
        return "connect"
    if isinstance(exc, ValueError):
        # http.client refuses a header name or value with a line break.
        return "invalid_request"
    return None


def _read_endpoint_response(response, url, max_bytes) -> SsrfResponse:
    if 300 <= response.status < 400:
        raise GuardedRequestError("redirect")
    content = b""
    if 200 <= response.status < 300:
        buffer = bytearray()
        for chunk in response.stream(64 * 1024):
            buffer.extend(chunk)
            if len(buffer) > max_bytes:
                raise GuardedRequestError("too_large")
        content = bytes(buffer)
    return SsrfResponse(
        status_code=response.status,
        headers=response.headers,
        content=content,
        final_url=url,
    )


def _open_proxy_manager(proxy_url, timeout):
    """A urllib3 manager for the operator's proxy: credentials in the proxy
    URL become Proxy-Authorization (as requests does), certificates are
    verified with the same CA trust as a direct call."""
    pool_timeout = urllib3.Timeout(
        connect=min(timeout, _MODEL_ENDPOINT_CONNECT_TIMEOUT_CAP), read=timeout
    )
    common = {
        "timeout": pool_timeout,
        "retries": False,
        "cert_reqs": "CERT_REQUIRED",
        **_ca_bundle_kwargs(),
    }
    try:
        parsed = urllib3.util.parse_url(proxy_url)
    except ValueError:
        raise GuardedRequestError("connect") from None
    if (parsed.scheme or "").lower().startswith("socks"):
        try:
            from urllib3.contrib.socks import SOCKSProxyManager
        except ImportError:
            raise GuardedRequestError("connect") from None
        return SOCKSProxyManager(proxy_url, **common)
    proxy_headers = None
    if parsed.auth:
        user, _, password = parsed.auth.partition(":")
        proxy_headers = urllib3.util.make_headers(
            proxy_basic_auth=f"{unquote(user)}:{unquote(password)}"
        )
        parsed = parsed._replace(auth=None)
    return urllib3.ProxyManager(parsed.url, proxy_headers=proxy_headers, **common)


def _post_through_proxy(
    proxy_url, absolute_url, body, headers, timeout, max_bytes, url
):
    manager = _open_proxy_manager(proxy_url, timeout)
    try:
        try:
            response = manager.urlopen(
                "POST",
                absolute_url,
                body=body,
                headers=headers,
                redirect=False,
                retries=False,
                preload_content=False,
            )
        except Exception as exc:
            category = _transport_error_category(exc)
            if category is None:
                raise
            raise GuardedRequestError(category) from None
        try:
            return _read_endpoint_response(response, url, max_bytes)
        except GuardedRequestError:
            raise
        except Exception as exc:
            category = _transport_error_category(exc)
            if category is None:
                raise
            raise GuardedRequestError(category) from None
        finally:
            response.release_conn()
    finally:
        manager.clear()


def guarded_post(
    url,
    *,
    json=None,
    headers=None,
    timeout: float = 30,
    max_bytes: int = MODEL_ENDPOINT_MAX_RESPONSE_BYTES,
    policy: EndpointPolicy | None = None,
) -> SsrfResponse:
    """POST ``json`` to a tenant-configured model endpoint.

    Every A/AAAA answer for the host must be allowed under ``policy``
    (default ``model_endpoint_policy()``), and the connection goes to one of
    them: the first, or the second when the first cannot be connected to (a
    dual-stack name seen from an IPv4-only container). https verifies the
    certificate against the URL's host (SNI and Host header carry the name).
    Never follows a redirect: a 3xx is GuardedRequestError("redirect"). Drops
    ``is_stripped_endpoint_header`` headers. A 2xx body is read up to
    ``max_bytes`` (more is GuardedRequestError("too_large")); other statuses
    return with an empty body. Raises EndpointBlocked before connecting and
    GuardedRequestError after; both have a ``category`` and generic text.

    When an env proxy applies to the URL (as ``requests`` decides it), the
    request goes through that proxy after the same address check: CONNECT
    to the host name for https (TLS verifies the name end to end), the
    absolute URL for http.
    """
    policy = policy or model_endpoint_policy()
    scheme, host, port, parts = _parse_endpoint(url)
    proxy_url = _env_proxy_for(_origin(scheme, host, port))
    addresses = _endpoint_addresses(
        host, policy, allow_unresolvable=_resolution_optional(proxy_url, policy)
    )
    body = b""
    if json is not None:
        try:
            body = _json.dumps(json, allow_nan=False).encode("utf-8")
        except (TypeError, ValueError):
            raise GuardedRequestError("invalid_request") from None
    send_headers = _endpoint_headers(
        headers, parts, scheme, host, port, has_body=json is not None
    )
    target = parts.path or "/"
    if parts.query:
        target = f"{target}?{parts.query}"

    if proxy_url is not None:
        return _post_through_proxy(
            proxy_url,
            f"{scheme}://{send_headers['Host']}{target}",
            body,
            send_headers,
            timeout,
            max_bytes,
            url,
        )

    last_category = "connect"
    for pinned_ip in addresses[:_MODEL_ENDPOINT_MAX_ADDRESSES]:
        pool = _open_endpoint_pool(scheme, pinned_ip, host, port, timeout)
        try:
            try:
                response = pool.urlopen(
                    "POST",
                    target,
                    body=body,
                    headers=send_headers,
                    redirect=False,
                    retries=False,
                    preload_content=False,
                    assert_same_host=False,
                )
            except (
                urllib3.exceptions.NewConnectionError,
                urllib3.exceptions.ConnectTimeoutError,
            ) as exc:
                # Nothing was sent: try the next checked address.
                last_category = _transport_error_category(exc)
                continue
            except Exception as exc:
                category = _transport_error_category(exc)
                if category is None:
                    raise
                raise GuardedRequestError(category) from None
            try:
                return _read_endpoint_response(response, url, max_bytes)
            except GuardedRequestError:
                raise
            except Exception as exc:
                category = _transport_error_category(exc)
                if category is None:
                    raise
                raise GuardedRequestError(category) from None
            finally:
                response.release_conn()
        finally:
            pool.close()
    raise GuardedRequestError(last_category)


def endpoint_status_message(status_code: int) -> str:
    """Generic text for an error status from a model endpoint: the code and
    its standard phrase, never the server's reason text or body."""
    try:
        phrase = HTTPStatus(status_code).phrase
    except ValueError:
        phrase = ""
    return f"The endpoint answered HTTP {status_code} {phrase}".rstrip() + "."


# ---------------------------------------------------------------------------
# Model endpoints called by an SDK: the guarded httpx transport
# ---------------------------------------------------------------------------
#
# litellm and the OpenAI SDK call an openai/azure custom model's base URL
# with their own httpx client, which resolves the name itself and follows
# redirects, so a pre-flight check of the URL could be redirected or
# rebound past. ``guarded_httpx_client`` gives those SDKs a client whose
# connections are made by ``_PinnedNetworkBackend``: it resolves the host,
# checks every answer under the policy and connects to a checked address in
# the same step (TLS still verifies the host name, which httpcore sends as
# SNI). The transport refuses any 3xx (GuardedRequestError("redirect"), so a
# per-request follow_redirects cannot reopen it), drops
# ``is_metadata_or_forwarding_header`` headers, sets Host from the URL, and
# honours the env proxy after the same check, like ``guarded_post``.
#
# The transports replace httpx.HTTPTransport's private ``_pool`` with a pool
# on the pinned backend; tests/test_ssrf_guard_httpx.py fails loudly (a
# refused address must open no connection) if an httpx upgrade changes that.


class _PinnedNetworkBackend(httpcore.NetworkBackend):
    """Connects only to an address ``_endpoint_addresses`` allowed for the
    host, the second one only when the first cannot be connected to."""

    def __init__(self, policy: EndpointPolicy):
        self._policy = policy
        self._inner = httpcore.SyncBackend()

    def connect_tcp(
        self, host, port, timeout=None, local_address=None, socket_options=None
    ):
        addresses = _endpoint_addresses(host, self._policy)
        last_error = None
        for address in addresses[:_MODEL_ENDPOINT_MAX_ADDRESSES]:
            try:
                return self._inner.connect_tcp(
                    address,
                    port,
                    timeout=timeout,
                    local_address=local_address,
                    socket_options=socket_options,
                )
            except (httpcore.ConnectError, httpcore.ConnectTimeout) as exc:
                last_error = exc
        raise last_error or httpcore.ConnectError("No address to connect to.")

    def connect_unix_socket(self, path, timeout=None, socket_options=None):
        raise httpcore.ConnectError("A model endpoint is not a Unix socket.")

    def sleep(self, seconds):
        self._inner.sleep(seconds)


class _AsyncPinnedNetworkBackend(httpcore.AsyncNetworkBackend):
    """The asyncio counterpart of ``_PinnedNetworkBackend`` (the lookup runs
    in a worker thread)."""

    def __init__(self, policy: EndpointPolicy):
        self._policy = policy
        self._inner = httpcore.AnyIOBackend()

    async def connect_tcp(
        self, host, port, timeout=None, local_address=None, socket_options=None
    ):
        import anyio

        addresses = await anyio.to_thread.run_sync(
            functools.partial(_endpoint_addresses, host, self._policy)
        )
        last_error = None
        for address in addresses[:_MODEL_ENDPOINT_MAX_ADDRESSES]:
            try:
                return await self._inner.connect_tcp(
                    address,
                    port,
                    timeout=timeout,
                    local_address=local_address,
                    socket_options=socket_options,
                )
            except (httpcore.ConnectError, httpcore.ConnectTimeout) as exc:
                last_error = exc
        raise last_error or httpcore.ConnectError("No address to connect to.")

    async def connect_unix_socket(self, path, timeout=None, socket_options=None):
        raise httpcore.ConnectError("A model endpoint is not a Unix socket.")

    async def sleep(self, seconds):
        await self._inner.sleep(seconds)


_NO_KEEPALIVE = httpx.Limits(max_keepalive_connections=0)


def _ssl_context(verify):
    if isinstance(verify, ssl.SSLContext):
        return verify
    return httpx.create_ssl_context(verify=verify)


def _prepare_endpoint_request(request: "httpx.Request", policy) -> str | None:
    """Check the request's URL shape, drop metadata and forwarding headers,
    set Host from the URL; return the env proxy for it (None: direct). A
    proxied target is checked here, since the proxy makes the connection."""
    url = request.url
    scheme = url.scheme.lower()
    host = url.raw_host.decode("ascii", "replace") if url.raw_host else ""
    if scheme not in ("http", "https") or not host:
        raise EndpointBlocked("invalid_url")
    if host.rstrip(".").lower() in METADATA_HOSTNAMES:
        raise EndpointBlocked("blocked")
    for name in list(request.headers.keys()):
        if is_metadata_or_forwarding_header(name):
            del request.headers[name]
    request.headers["Host"] = url.netloc.decode("ascii")
    port = url.port or (443 if scheme == "https" else 80)
    proxy_url = _env_proxy_for(_origin(scheme, host, port))
    if proxy_url is not None:
        _endpoint_addresses(
            host, policy, allow_unresolvable=_resolution_optional(proxy_url, policy)
        )
    return proxy_url


def _proxy_transport_error(exc: ImportError) -> GuardedRequestError:
    # A socks proxy without httpx's socks extra installed.
    return GuardedRequestError("connect")


class GuardedHTTPTransport(httpx.HTTPTransport):
    """httpx transport for a tenant-configured model endpoint (see above).
    ``policy`` defaults to ``model_endpoint_policy()`` when constructed."""

    def __init__(self, policy: EndpointPolicy | None = None, *, verify=True):
        self._policy = policy or model_endpoint_policy()
        self._ssl = _ssl_context(verify)
        super().__init__(verify=self._ssl, retries=0, limits=_NO_KEEPALIVE)
        self._pool.close()
        self._pool = httpcore.ConnectionPool(
            ssl_context=self._ssl,
            max_keepalive_connections=0,
            retries=0,
            network_backend=_PinnedNetworkBackend(self._policy),
        )
        self._proxied = {}

    def _through(self, proxy_url):
        if proxy_url not in self._proxied:
            try:
                self._proxied[proxy_url] = httpx.HTTPTransport(
                    verify=self._ssl, proxy=proxy_url, retries=0, limits=_NO_KEEPALIVE
                )
            except ImportError as exc:
                raise _proxy_transport_error(exc) from None
        return self._proxied[proxy_url]

    def handle_request(self, request):
        proxy_url = _prepare_endpoint_request(request, self._policy)
        if proxy_url is None:
            response = super().handle_request(request)
        else:
            response = self._through(proxy_url).handle_request(request)
        if 300 <= response.status_code < 400:
            response.close()
            raise GuardedRequestError("redirect")
        return response

    def close(self):
        super().close()
        for transport in self._proxied.values():
            transport.close()


class AsyncGuardedHTTPTransport(httpx.AsyncHTTPTransport):
    """The asyncio counterpart of ``GuardedHTTPTransport``."""

    def __init__(self, policy: EndpointPolicy | None = None, *, verify=True):
        self._policy = policy or model_endpoint_policy()
        self._ssl = _ssl_context(verify)
        super().__init__(verify=self._ssl, retries=0, limits=_NO_KEEPALIVE)
        self._pool = httpcore.AsyncConnectionPool(
            ssl_context=self._ssl,
            max_keepalive_connections=0,
            retries=0,
            network_backend=_AsyncPinnedNetworkBackend(self._policy),
        )
        self._proxied = {}

    def _through(self, proxy_url):
        if proxy_url not in self._proxied:
            try:
                self._proxied[proxy_url] = httpx.AsyncHTTPTransport(
                    verify=self._ssl, proxy=proxy_url, retries=0, limits=_NO_KEEPALIVE
                )
            except ImportError as exc:
                raise _proxy_transport_error(exc) from None
        return self._proxied[proxy_url]

    async def handle_async_request(self, request):
        proxy_url = await _run_in_thread(
            _prepare_endpoint_request, request, self._policy
        )
        if proxy_url is None:
            response = await super().handle_async_request(request)
        else:
            response = await self._through(proxy_url).handle_async_request(request)
        if 300 <= response.status_code < 400:
            await response.aclose()
            raise GuardedRequestError("redirect")
        return response

    async def aclose(self):
        await super().aclose()
        for transport in self._proxied.values():
            await transport.aclose()


async def _run_in_thread(function, *args):
    import anyio

    return await anyio.to_thread.run_sync(functools.partial(function, *args))


def _endpoint_client_kwargs(timeout):
    return {
        "follow_redirects": False,
        # No .netrc credentials and no env proxy mounts: the transport
        # applies the env proxy itself, after its address check.
        "trust_env": False,
        "timeout": timeout
        if timeout is not None
        else httpx.Timeout(
            _MODEL_ENDPOINT_DEFAULT_TIMEOUT,
            connect=_MODEL_ENDPOINT_CONNECT_TIMEOUT_CAP,
        ),
    }


def guarded_httpx_client(
    policy: EndpointPolicy | None = None, *, verify=True, timeout=None
) -> "httpx.Client":
    """An httpx client for calls to a tenant-configured model endpoint:
    pinned, policy-checked connections, no redirects, metadata headers
    dropped, env proxy honoured. Raises EndpointBlocked or
    GuardedRequestError (generic text) from a request it refuses."""
    return httpx.Client(
        transport=GuardedHTTPTransport(policy, verify=verify),
        **_endpoint_client_kwargs(timeout),
    )


def guarded_async_httpx_client(
    policy: EndpointPolicy | None = None, *, verify=True, timeout=None
) -> "httpx.AsyncClient":
    """The asyncio counterpart of ``guarded_httpx_client``."""
    return httpx.AsyncClient(
        transport=AsyncGuardedHTTPTransport(policy, verify=verify),
        **_endpoint_client_kwargs(timeout),
    )
