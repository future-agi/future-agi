"""addresses.json: the profiles the cases run under and the rules block (DESIGN.md sections 6.1-6.3)."""

from __future__ import annotations

from . import reference as R

HOSTED = {
    "mode": "hosted",
    "deny_hosts": ["omega-ee.example.net", "gateway.example.net"],
}

HOSTED_WITH_DEV_ENV = {
    "mode": "hosted",
    "allow_http": True,
    "private_cidrs": ["10.0.0.0/8"],
    "allowed_private_hosts": ["hooks.acme.test"],
    "deny_hosts": [],
}

SELF = {
    "mode": "self_hosted",
    "allow_http": True,
    "private_cidrs": [
        "10.20.0.0/16",
        "127.0.0.0/8",
        "64:ff9b::/96",
        "2002::/16",
        "2001::/32",
    ],
    "private_ports": [443, 8080],
    "allowed_private_hosts": ["host.docker.internal", "hooks.acme.test"],
    "deny_hosts": [],
}

SELF_STRICT = {"mode": "self_hosted", "deny_hosts": []}

# A self-hosted install whose FI_WEBHOOKS_PRIVATE_CIDRS covers every address: each private address is then admitted,
# and each NEVER address (directly or through any unwrapping) must still be refused.
SELF_WIDE = {
    "mode": "self_hosted",
    "private_cidrs": ["0.0.0.0/0", "::/0"],
    "private_ports": [443],
    "deny_hosts": [],
}

PROFILES = {
    "hosted": HOSTED,
    "hosted_with_dev_env": HOSTED_WITH_DEV_ENV,
    "self_hosted": SELF,
    "self_hosted_strict": SELF_STRICT,
    "self_hosted_wide": SELF_WIDE,
}


def address_rules() -> dict:
    rules = {
        "order": "URL rules run in this order and the first failure is reported: length, syntax, characters, "
        "scheme, fragment, userinfo, port, host form (IP literal or name), then the IP-literal class or the name "
        "rules, then the org and workspace allowlists",
        "characters": "the path and the query (everything after the authority, up to a '#') may hold only "
        "U+0021-U+007E: no control character (CR, LF, tab, NUL, DEL), no space and no raw non-ASCII, else "
        "url.characters. They are sent byte for byte, so the caller percent-encodes (/caf%C3%A9, %20). The scheme "
        "is already letters, digits, '+', '-' and '.'. The host is judged by the host rules after IDNA, so a "
        "non-ASCII host (bücher.de) still passes; the userinfo and fragment are refused anyway",
        "codes": {
            "WEBHOOK_URL_NOT_HTTPS": ["url.scheme"],
            "WEBHOOK_URL_INVALID": [
                "url.length",
                "url.syntax",
                "url.characters",
                "url.fragment",
                "url.userinfo",
                "url.port",
                "url.ip_literal_nonstandard",
                "url.host_syntax",
            ],
            "WEBHOOK_URL_UNSAFE_ADDRESS": [
                "addr.never",
                "addr.private",
                "addr.private_port",
                "url.host_localhost",
                "url.host_metadata",
                "url.host_own",
                "url.host_no_dot",
                "url.host_internal_name",
            ],
            "WEBHOOK_HOST_NOT_ALLOWED": [
                "policy.org_allowed_hosts",
                "policy.workspace_allowed_hosts",
            ],
        },
        "port": "the explicit port, else 443 for https and 80 for http; allowed: 443 or 1024-65535, minus "
        + ", ".join(str(p) for p in sorted(R.BLOCKED_PORTS)),
        "ip_literal": "a host is an IP literal only as a standard dotted quad (no leading zeros) or a bracketed IPv6 "
        "address without a zone id; any other host whose last label is all digits or starts with 0x "
        "is refused before any lookup (url.ip_literal_nonstandard)",
        "names": "lower-case, IDNA, trailing dot removed. Always refused: localhost and *.localhost, the metadata "
        "names. Refused unless (off hosted) listed in allowed_private_hosts: futureagi.com, deny_hosts and "
        "their subdomains; a name without a dot; a name under a special-use suffix ("
        + ", ".join(R.SPECIAL_USE_SUFFIXES)
        + "); a name whose public suffix (ICANN section of the PSL) is "
        "missing or is the whole name",
        "metadata_names": sorted(R.METADATA_HOSTNAMES),
        "never_cidrs": [str(n) for n in R.NEVER_NETS],
        "private_cidrs": [str(n) for n in R.PRIVATE_NETS]
        + ["every IPv6 address outside 2000::/3 that is not NEVER"],
        "unwrapping": "IPv4-mapped (::ffff:0:0/96), IPv4-translated (::ffff:0:0:0/96, the RFC 2765 / RFC 6145 SIIT "
        "form ::ffff:0:a.b.c.d; from 1.2.0), IPv4-compatible (::/96 except :: and ::1, however it is spelled: "
        "::127.0.0.1 is ::7f00:1) and NAT64 (64:ff9b::/96), each with the IPv4 address in its last 32 bits; 6to4 "
        "(2002::/16, bits 16-47); Teredo (2001::/32: server bits 32-63, client = NOT bits 96-127). The class is the "
        "strictest of the outer address and every embedded IPv4 address (never > private > public), so "
        "::ffff:0:7f00:1 and ::7f00:1 are never, like 127.0.0.1. The local-use NAT64 prefix 64:ff9b:1::/48 (RFC "
        "8215) is not unwrapped: it is NEVER as "
        "a whole, because its RFC 6052 layout (/48 to /96) is the operator's choice and so its embedded address "
        "cannot be read reliably",
        "admission": "private addresses are admitted only off hosted, only inside private_cidrs, and only on "
        "private_ports (default [443]); an address that carries embedded IPv4 addresses (see unwrapping) is "
        "admitted only if each embedded IPv4 address is public or inside an IPv4 private_cidrs entry itself, so "
        "opting in a translator prefix (NAT64 64:ff9b::/96, 6to4 2002::/16, Teredo 2001::/32) admits public targets "
        "behind it, not every private IPv4 address behind it (from 1.4.0, ruling 76 addendum); never addresses are "
        "never admitted",
        "resolution": "every answer must be allowed, else ssrf_blocked and nothing connects; duplicates removed in "
        "order; at most the first 2 are tried; no answer is dns_error",
        "policy": "exact host or a leftmost '*.' wildcard matching one or more labels (not the apex); a workspace "
        "list can only narrow the org list",
        "query_warning": "query parameter names matching /token|key|secret|sig|auth/i are listed in "
        "warn_query_params (the dialog warns; the URL is still allowed)",
        "classification_source": "the CIDR tables above are normative and complete; do not rely on a language's "
        "'is_private' flags, which change between versions (Python 3.11 vs 3.13). The tables are at least as "
        "strict as Python 3.11-3.14 is_global (2002::/16 is PRIVATE for that reason), so a core that also "
        "applies is_global on hosted (DESIGN.md section 6.2) gives the same answers",
        "redirects": "a 3xx is never followed: outcome redirect_refused, standard class, and the Location is never "
        "fetched. The log names the Location host only for an absolute http(s) URL whose host is a DNS name "
        "(no userinfo, not an IP literal or numeric form, valid labels, IDNA, lower-case, port and trailing "
        "dot dropped); otherwise it says 'another address'",
    }
    return rules
