"""URL rules at create, update and before every attempt, and the redirect log rule (DESIGN.md sections 6.1-6.3;
conformance ``addresses.json``, ``url`` and ``redirect`` cases).

Pure predicates. The Public Suffix List is injected (``public_suffix``): the core holds no copy of it and never fetches
it. The address classes and the decision over resolved answers are in :mod:`webhooks.core.ipclass`.
"""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field

from .ipclass import NEVER, PRIVATE, Profile, classify, private_admitted

MAX_URL_CHARS = 2048
BLOCKED_PORTS = frozenset(
    {
        2375,
        2376,
        2379,
        3306,
        4191,
        5432,
        6379,
        6443,
        8500,
        9200,
        10250,
        10255,
        11211,
        15000,
        15020,
        15021,
        27017,
    }
)
METADATA_HOSTNAMES = frozenset(
    {
        "metadata",
        "metadata.google.internal",
        "metadata.goog",
        "instance-data",
        "instance-data.ec2.internal",
    }
)
SPECIAL_USE_SUFFIXES = (
    "arpa",
    "local",
    "localhost",
    "internal",
    "test",
    "example",
    "invalid",
    "onion",
    "alt",
)
OWN_HOSTS = ("futureagi.com",)

CODE_INVALID = "WEBHOOK_URL_INVALID"
CODE_NOT_HTTPS = "WEBHOOK_URL_NOT_HTTPS"
CODE_UNSAFE = "WEBHOOK_URL_UNSAFE_ADDRESS"
CODE_HOST_NOT_ALLOWED = "WEBHOOK_HOST_NOT_ALLOWED"

_URL = re.compile(r"([A-Za-z][A-Za-z0-9+.-]*)://([^/?#]*)([^?#]*)(\?[^#]*)?(#.*)?")
_AUTHORITY = re.compile(r"(\[[^\]]*\]|[^:\[\]]*)(?::([0-9]*))?")
_DOTTED_QUAD = re.compile(
    r"(25[0-5]|2[0-4][0-9]|1[0-9][0-9]|[1-9]?[0-9])(\.(25[0-5]|2[0-4][0-9]|1[0-9][0-9]|[1-9]?[0-9])){3}"
)
_NUMERIC_LABEL = re.compile(r"0x[0-9a-f]*|[0-9]+")
_LABEL = re.compile(r"[a-z0-9_]([a-z0-9_-]{0,61}[a-z0-9_])?")
_QUERY_NAME = re.compile(r"[?&]([^=&]+)")
_SECRETISH = re.compile(r"token|key|secret|sig|auth", re.I)

PublicSuffix = Callable[[str], "str | None"]
"""``public_suffix(host)`` -> the longest ICANN-section public suffix of ``host`` or None."""


@dataclass(frozen=True)
class UrlDecision:
    allow: bool
    code: str | None = None
    rule: str | None = None
    host: str | None = None
    port: int | None = None
    scheme: str | None = None
    ip_literal: str | None = None
    target: str | None = None
    """The request target sent byte for byte: the path (``/`` when empty) and the query."""
    warn_query_params: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        if not self.allow:
            return {"allow": False, "code": self.code, "rule": self.rule}
        out = {
            "allow": True,
            "code": None,
            "rule": None,
            "host": self.host,
            "port": self.port,
        }
        if self.warn_query_params:
            out["warn_query_params"] = list(self.warn_query_params)
        return out


def _deny(code: str, rule: str) -> UrlDecision:
    return UrlDecision(False, code, rule)


def _host_matches(host: str, pattern: str) -> bool:
    """``*.x`` matches any depth below ``x`` but not ``x`` itself; anything else is exact."""
    if pattern.startswith("*."):
        return host.endswith(pattern[1:]) and len(host) > len(pattern) - 1
    return host == pattern


def _normal_name(host: str) -> str | None:
    """Lower-case, IDNA, trailing dot removed; None when IDNA refuses it."""
    try:
        h = (
            host.encode("idna").decode("ascii")
            if any(ord(c) > 127 for c in host)
            else host
        )
    except UnicodeError:
        return None
    return h.lower().removesuffix(".")


def _name_problem(
    name: str, profile: Profile, public_suffix: PublicSuffix
) -> str | None:
    if name == "localhost" or name.endswith(".localhost"):
        return "url.host_localhost"
    if name in METADATA_HOSTNAMES:
        return "url.host_metadata"
    if not profile.hosted and name in profile.allowed_private_hosts:
        return None
    for own in OWN_HOSTS + tuple(profile.deny_hosts):
        if name == own or name.endswith("." + own):
            return "url.host_own"
    if "." not in name:
        return "url.host_no_dot"
    if any(name == s or name.endswith("." + s) for s in SPECIAL_USE_SUFFIXES):
        return "url.host_internal_name"
    suffix = public_suffix(name)
    if suffix is None or suffix == name:
        return "url.host_internal_name"
    return None


def url_decision(
    url: str,
    profile: Profile,
    public_suffix: PublicSuffix,
    allowed_hosts: Mapping[str, Iterable[str]] | None = None,
) -> UrlDecision:
    """The section 6.1 rules in the vectors' order; the first failure is reported. ``allowed_hosts`` holds the policy
    lists ``org_allowed_hosts`` and ``workspace_allowed_hosts`` (empty = any public host)."""
    if not isinstance(url, str) or len(url) > MAX_URL_CHARS:
        return _deny(CODE_INVALID, "url.length")
    m = _URL.fullmatch(url)
    if not m:
        return _deny(CODE_INVALID, "url.syntax")
    scheme, authority, path, query, fragment = m.groups()
    query = query or ""
    if any(not ("!" <= c <= "~") for c in path + query):
        return _deny(CODE_INVALID, "url.characters")
    scheme = scheme.lower()
    if scheme not in ("https", "http") or (
        scheme == "http" and (profile.hosted or not profile.allow_http)
    ):
        return _deny(CODE_NOT_HTTPS, "url.scheme")
    if fragment is not None:
        return _deny(CODE_INVALID, "url.fragment")
    if "@" in authority:
        return _deny(CODE_INVALID, "url.userinfo")
    am = _AUTHORITY.fullmatch(authority)
    if not am or not am.group(1):
        return _deny(CODE_INVALID, "url.syntax")
    host, port_s = am.group(1), am.group(2)
    if port_s is not None:
        if not port_s or len(port_s) > 5:
            return _deny(CODE_INVALID, "url.port")
        port = int(port_s)
    else:
        port = 443 if scheme == "https" else 80
    if not (port == 443 or 1024 <= port <= 65535) or port in BLOCKED_PORTS:
        return _deny(CODE_INVALID, "url.port")

    ip_literal = None
    if host.startswith("["):
        inner = host[1:-1]
        if "%" in inner:
            return _deny(CODE_INVALID, "url.ip_literal_nonstandard")
        try:
            ip_literal = str(ipaddress.IPv6Address(inner))
        except ValueError:
            return _deny(CODE_INVALID, "url.ip_literal_nonstandard")
        name = host.lower()
    else:
        name = _normal_name(host)
        if not name:
            return _deny(CODE_INVALID, "url.host_syntax")
        if _DOTTED_QUAD.fullmatch(name):
            ip_literal = name
        elif _NUMERIC_LABEL.fullmatch(name.rsplit(".", 1)[-1]):
            return _deny(CODE_INVALID, "url.ip_literal_nonstandard")
        elif len(name) > 253 or not all(
            _LABEL.fullmatch(lbl) for lbl in name.split(".")
        ):
            return _deny(CODE_INVALID, "url.host_syntax")

    if ip_literal is not None:
        cls = classify(ip_literal)
        if cls == NEVER:
            return _deny(CODE_UNSAFE, "addr.never")
        if cls == PRIVATE:
            if not private_admitted(ip_literal, profile):
                return _deny(CODE_UNSAFE, "addr.private")
            if port not in profile.private_ports:
                return _deny(CODE_UNSAFE, "addr.private_port")
    else:
        problem = _name_problem(name, profile, public_suffix)
        if problem:
            return _deny(CODE_UNSAFE, problem)

    lists = allowed_hosts or {}
    for scope in ("org_allowed_hosts", "workspace_allowed_hosts"):
        patterns = list(lists.get(scope) or [])
        if patterns and not any(_host_matches(name, p) for p in patterns):
            return _deny(CODE_HOST_NOT_ALLOWED, "policy." + scope)

    warn = sorted({k for k in _QUERY_NAME.findall(query) if _SECRETISH.search(k)})
    return UrlDecision(
        True,
        host=name,
        port=port,
        scheme=scheme,
        ip_literal=ip_literal,
        target=(path or "/") + query,
        warn_query_params=warn,
    )


def display_url(url: str) -> str:
    """``https://hooks.acme.ai/omega?token=…``: every query value masked (section 4.1, ``url_display``)."""
    base, sep, query = url.partition("?")
    if not sep:
        return base
    parts = []
    for item in query.split("&"):
        k, eq, _v = item.partition("=")
        parts.append(k + ("=…" if eq else ""))
    return base + "?" + "&".join(parts)


ANOTHER_ADDRESS = "another address"
_LOCATION = re.compile(r"(?i)https?://([^/?#\s]*)([/?#]\S*)?")


def redirect_log_host(location: str | None) -> str:
    """A 3xx is never followed (section 6.2). The log names the Location host only for an absolute http(s) URL whose
    host is a DNS name passing the hostname syntax rules; anything else is "another address", so no address ever
    reaches a tenant."""
    loc = (location or "").strip()
    m = _LOCATION.fullmatch(loc)
    if not m or "@" in m.group(1) or m.group(1).startswith("["):
        return ANOTHER_ADDRESS
    host = re.sub(r":[0-9]*$", "", m.group(1))
    try:
        if any(ord(c) > 127 for c in host):
            host = host.encode("idna").decode("ascii")
    except UnicodeError:
        return ANOTHER_ADDRESS
    host = host.lower().removesuffix(".")
    if (
        host
        and "." in host
        and len(host) <= 253
        and not _NUMERIC_LABEL.fullmatch(host.rsplit(".", 1)[-1])
        and all(_LABEL.fullmatch(lbl) for lbl in host.split("."))
    ):
        return host
    return ANOTHER_ADDRESS


def redirect_decision(location: str | None) -> dict:
    return {
        "outcome": "redirect_refused",
        "follow": False,
        "retry_class": "standard",
        "log_location_host": redirect_log_host(location),
    }
