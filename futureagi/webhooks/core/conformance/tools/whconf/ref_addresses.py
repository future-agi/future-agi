"""URL rules, address classes, unwrapping and redirects (DESIGN.md sections 6.1-6.3)."""

from __future__ import annotations

import ipaddress
import re

from .ref_ipclass import _private_admitted, classify

BLOCKED_PORTS = {
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

METADATA_HOSTNAMES = {
    "metadata",
    "metadata.google.internal",
    "metadata.goog",
    "instance-data",
    "instance-data.ec2.internal",
}

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

PSL_ICANN = {
    "com",
    "ai",
    "dev",
    "io",
    "de",
    "net",
    "org",
    "uk",
    "co.uk",
    "arpa",
    "app",
    "jp",
    "co.jp",
}

_DOTTED_QUAD = re.compile(
    r"(25[0-5]|2[0-4][0-9]|1[0-9][0-9]|[1-9]?[0-9])(\.(25[0-5]|2[0-4][0-9]|1[0-9][0-9]|[1-9]?[0-9])){3}"
)

_NUMERIC_LABEL = re.compile(r"0x[0-9a-f]*|[0-9]+")

_LABEL = re.compile(r"[a-z0-9_]([a-z0-9_-]{0,61}[a-z0-9_])?")


def _public_suffix(host: str) -> str | None:
    labels = host.split(".")
    best = None
    for i in range(len(labels)):
        cand = ".".join(labels[i:])
        if cand in PSL_ICANN:
            best = cand
            break
    return best


def _deny(code: str, rule: str) -> dict:
    return {"allow": False, "code": code, "rule": rule}


def url_decision(case: dict) -> dict:
    """URL rules at create, update and before every attempt (DESIGN.md sections 6.1 and 6.3)."""
    p = case["profile"]
    url = case["url"]
    if len(url) > 2048:
        return _deny("WEBHOOK_URL_INVALID", "url.length")
    m = re.fullmatch(
        r"([A-Za-z][A-Za-z0-9+.-]*)://([^/?#]*)([^?#]*)(\?[^#]*)?(#.*)?", url
    )
    if not m:
        return _deny("WEBHOOK_URL_INVALID", "url.syntax")
    scheme, authority, _path, _query, fragment = m.groups()
    # The path and query are sent byte for byte (DESIGN.md sections 5.1 and 6.1), so only visible ASCII may appear
    # there: no CR/LF (request splitting), space, tab, NUL, DEL or raw non-ASCII. The caller percent-encodes. The host is
    # judged after IDNA below, so a non-ASCII host such as bücher.de still passes.
    if any(not ("!" <= ch <= "~") for ch in _path + (_query or "")):
        return _deny("WEBHOOK_URL_INVALID", "url.characters")
    scheme = scheme.lower()
    if scheme not in ("https", "http"):
        return _deny("WEBHOOK_URL_NOT_HTTPS", "url.scheme")
    if scheme == "http" and not (p["mode"] != "hosted" and p.get("allow_http")):
        return _deny("WEBHOOK_URL_NOT_HTTPS", "url.scheme")
    if fragment is not None:
        return _deny("WEBHOOK_URL_INVALID", "url.fragment")
    if "@" in authority:
        return _deny("WEBHOOK_URL_INVALID", "url.userinfo")
    hm = re.fullmatch(r"(\[[^\]]*\]|[^:\[\]]*)(?::([0-9]*))?", authority)
    if not hm or not hm.group(1):
        return _deny("WEBHOOK_URL_INVALID", "url.syntax")
    host, port_s = hm.group(1), hm.group(2)
    if port_s is not None:
        if not port_s or len(port_s) > 5:
            return _deny("WEBHOOK_URL_INVALID", "url.port")
        port = int(port_s)
    else:
        port = 443 if scheme == "https" else 80
    if not (port == 443 or 1024 <= port <= 65535) or port in BLOCKED_PORTS:
        return _deny("WEBHOOK_URL_INVALID", "url.port")
    ip_literal = None
    if host.startswith("["):
        inner = host[1:-1]
        if "%" in inner:
            return _deny("WEBHOOK_URL_INVALID", "url.ip_literal_nonstandard")
        try:
            ip_literal = str(ipaddress.IPv6Address(inner))
        except ValueError:
            return _deny("WEBHOOK_URL_INVALID", "url.ip_literal_nonstandard")
        norm_host = host.lower()
    else:
        try:
            h = (
                host.encode("idna").decode("ascii").lower()
                if any(ord(ch) > 127 for ch in host)
                else host.lower()
            )
        except UnicodeError:
            return _deny("WEBHOOK_URL_INVALID", "url.host_syntax")
        if h.endswith("."):
            h = h[:-1]
        if not h:
            return _deny("WEBHOOK_URL_INVALID", "url.host_syntax")
        last = h.rsplit(".", 1)[-1]
        if _DOTTED_QUAD.fullmatch(h):
            ip_literal = h
        elif _NUMERIC_LABEL.fullmatch(last):
            return _deny("WEBHOOK_URL_INVALID", "url.ip_literal_nonstandard")
        elif not all(_LABEL.fullmatch(lbl) for lbl in h.split(".")) or len(h) > 253:
            return _deny("WEBHOOK_URL_INVALID", "url.host_syntax")
        norm_host = h
    if ip_literal is not None:
        c = classify(ip_literal)["class"]
        if c == "never":
            return _deny("WEBHOOK_URL_UNSAFE_ADDRESS", "addr.never")
        if c == "private":
            if not _private_admitted(ip_literal, p):
                return _deny("WEBHOOK_URL_UNSAFE_ADDRESS", "addr.private")
            if port not in p.get("private_ports", [443]):
                return _deny("WEBHOOK_URL_UNSAFE_ADDRESS", "addr.private_port")
    else:
        if norm_host == "localhost" or norm_host.endswith(".localhost"):
            return _deny("WEBHOOK_URL_UNSAFE_ADDRESS", "url.host_localhost")
        if norm_host in METADATA_HOSTNAMES:
            return _deny("WEBHOOK_URL_UNSAFE_ADDRESS", "url.host_metadata")
        escape = p["mode"] != "hosted" and norm_host in p.get(
            "allowed_private_hosts", []
        )
        if not escape:
            deny_hosts = ["futureagi.com"] + p.get("deny_hosts", [])
            if any(norm_host == d or norm_host.endswith("." + d) for d in deny_hosts):
                return _deny("WEBHOOK_URL_UNSAFE_ADDRESS", "url.host_own")
            if "." not in norm_host:
                return _deny("WEBHOOK_URL_UNSAFE_ADDRESS", "url.host_no_dot")
            if any(
                norm_host == s or norm_host.endswith("." + s)
                for s in SPECIAL_USE_SUFFIXES
            ):
                return _deny("WEBHOOK_URL_UNSAFE_ADDRESS", "url.host_internal_name")
            ps = _public_suffix(norm_host)
            if ps is None or ps == norm_host:
                return _deny("WEBHOOK_URL_UNSAFE_ADDRESS", "url.host_internal_name")
    allowed = case.get("policy", {})
    for scope in ("org_allowed_hosts", "workspace_allowed_hosts"):
        lst = allowed.get(scope) or []
        if lst and not any(_host_matches(norm_host, pat) for pat in lst):
            return _deny("WEBHOOK_HOST_NOT_ALLOWED", "policy." + scope)
    query = _query or ""
    warn = sorted(
        {
            k
            for k in re.findall(r"[?&]([^=&]+)", query)
            if re.search(r"token|key|secret|sig|auth", k, re.I)
        }
    )
    out = {"allow": True, "code": None, "rule": None, "host": norm_host, "port": port}
    if warn:
        out["warn_query_params"] = warn
    return out


def _host_matches(host: str, pattern: str) -> bool:
    if pattern.startswith("*."):
        return host.endswith(pattern[1:]) and len(host) > len(pattern) - 1
    return host == pattern


ANOTHER_ADDRESS = "another address"


def redirect_decision(case: dict) -> dict:
    """A 3xx is never followed (DESIGN.md section 6.2). The log names the Location host only when it is an absolute
    http(s) URL whose host is a DNS name passing the hostname syntax rules; anything else (an IP literal, userinfo, a
    relative or non-http Location, bad syntax) is shown as "another address", so no address ever reaches a tenant."""
    out = {"outcome": "redirect_refused", "follow": False, "retry_class": "standard"}
    loc = (case.get("location") or "").strip()
    m = re.fullmatch(r"(?i)https?://([^/?#\s]*)([/?#]\S*)?", loc)
    shown = ANOTHER_ADDRESS
    if m and "@" not in m.group(1) and not m.group(1).startswith("["):
        host = re.sub(r":[0-9]*$", "", m.group(1))
        try:
            if any(ord(ch) > 127 for ch in host):
                host = host.encode("idna").decode("ascii")
        except UnicodeError:
            host = ""
        host = host.lower().removesuffix(".")
        last = host.rsplit(".", 1)[-1]
        if (
            host
            and "." in host
            and len(host) <= 253
            and not _NUMERIC_LABEL.fullmatch(last)
            and all(_LABEL.fullmatch(lbl) for lbl in host.split("."))
        ):
            shown = host
    out["log_location_host"] = shown
    return out
