"""addresses.json (DESIGN.md sections 6.1-6.3 and 22.2; test rows WT-05 to WT-07)."""

from __future__ import annotations

from . import reference as R
from .cases_addresses_resolved import address_cases, redirect_cases
from .cases_addresses_rules import (
    HOSTED,
    HOSTED_WITH_DEV_ENV,
    PROFILES,
    SELF,
    SELF_STRICT,
    SELF_WIDE,
    address_rules,
)
from .common import CONFORMANCE_VERSION, dump


def addresses_file() -> bytes:
    url_cases = []

    def u(cid, profile_name, url, policy=None, note=""):
        profile = PROFILES[profile_name]
        c = {"profile": profile, "url": url}
        if policy:
            c["policy"] = policy
        url_cases.append(
            {
                "id": cid,
                "profile": profile_name,
                "url": url,
                **({"policy": policy} if policy else {}),
                "expected": R.url_decision(c),
                "note": note,
            }
        )

    long_ok = "https://hooks.acme.ai/" + "a" * (2048 - len("https://hooks.acme.ai/"))
    for cid, url, note in [
        ("ok-basic", "https://hooks.acme.ai/omega", ""),
        ("ok-port-8443", "https://hooks.acme.ai:8443/x", ""),
        (
            "ok-normalised",
            "https://HOOKS.Acme.AI./x",
            "lower-case, trailing dot removed",
        ),
        ("ok-idna", "https://bücher.de/hook", "IDNA: xn--bcher-kva.de"),
        ("ok-public-ipv4", "https://8.8.8.8/hook", ""),
        ("ok-public-ipv6", "https://[2606:4700:4700::1111]/hook", ""),
        ("ok-co-uk", "https://hooks.co.uk/x", ""),
        (
            "ok-query-warning",
            "https://hooks.acme.ai/x?token=abc&team=7&api_key=z",
            "allowed, with the query warning",
        ),
        ("ok-2048-chars", long_ok, "exactly 2,048 characters"),
        ("too-long", long_ok + "a", "2,049 characters"),
        ("http-hosted", "http://hooks.acme.ai/", ""),
        ("ftp", "ftp://hooks.acme.ai/", ""),
        ("userinfo", "https://user:pw@hooks.acme.ai/", "WT-05"),
        ("userinfo-empty", "https://@hooks.acme.ai/", ""),
        ("fragment", "https://hooks.acme.ai/x#frag", ""),
        ("port-6379", "https://hooks.acme.ai:6379/", "WT-05"),
        ("port-6443", "https://hooks.acme.ai:6443/", "WT-05"),
        ("port-80", "https://hooks.acme.ai:80/", "below 1024 and not 443"),
        ("port-1023", "https://hooks.acme.ai:1023/", ""),
        ("port-1024", "https://hooks.acme.ai:1024/", ""),
        ("port-65536", "https://hooks.acme.ai:65536/", ""),
        ("port-empty", "https://hooks.acme.ai:/", ""),
        ("metadata-ip", "https://169.254.169.254/", "WT-05"),
        ("mapped-metadata", "https://[::ffff:169.254.169.254]/", "WT-05"),
        ("nat64-metadata", "https://[64:ff9b::a9fe:a9fe]/", "WT-06b"),
        # 1.3.0: NAT64 holding loopback.
        (
            "nat64-loopback",
            "https://[64:ff9b::7f00:1]/",
            "NAT64 holding 127.0.0.1; ruling 76",
        ),
        (
            "nat64-local-use-metadata",
            "https://[64:ff9b:1:a9fe:a9:fe00::]/",
            "RFC 6052 /48 layout",
        ),
        ("6to4-loopback", "https://[2002:7f00:1::]/", "WT-06b"),
        ("ipv4-compatible-loopback", "https://[::127.0.0.1]/", "WT-06b"),
        (
            "teredo-loopback-client",
            "https://[2001:0:4136:e378:8000:63bf:80ff:fffe]/",
            "Teredo client 127.0.0.1",
        ),
        ("localhost", "https://localhost/", "WT-05"),
        ("sub-localhost", "https://api.localhost/", ""),
        ("metadata-name", "https://metadata.google.internal/", "WT-05"),
        ("metadata-bare", "https://metadata/", ""),
        ("rfc1918", "https://10.0.0.5/", "WT-05"),
        ("cgnat", "https://100.64.0.1/", "WT-05"),
        ("ula", "https://[fd00::1]/", "WT-05"),
        ("link-local-v6", "https://[fe80::1]/", "WT-05"),
        ("zone-id", "https://[fe80::1%25eth0]/", "a zone id is not a standard form"),
        ("decimal-ip", "https://2852039166/", "WT-05: refused before any lookup"),
        ("short-ip", "https://127.1/", "WT-05"),
        ("hex-ip", "https://0x7f.1/", "WT-05"),
        ("octal-ip", "https://0251.0376.0251.0376/", "WT-05"),
        ("hex-whole", "https://0xa9fea9fe/", ""),
        ("leading-zero-quad", "https://010.0.0.1/", ""),
        (
            "numeric-tld",
            "https://hooks.acme.123/",
            "a numeric last label is an IPv4 form",
        ),
        ("svc", "https://redis.default.svc/", "WT-05c"),
        ("dotless", "https://kubernetes/", "WT-05c"),
        ("cluster-local", "https://api.default.svc.cluster.local/", ""),
        ("dot-internal", "https://vault.internal/", ""),
        ("dot-local", "https://printer.local/", ""),
        (
            "home-arpa",
            "https://nas.home.arpa/",
            "special-use, not caught by the PSL rule alone",
        ),
        ("dot-corp", "https://intranet.corp/", "not a delegated TLD"),
        ("dot-lan", "https://router.lan/", ""),
        (
            "dot-test",
            "https://hooks.acme.test/",
            "special-use; allowed only through allowed_private_hosts off hosted",
        ),
        ("public-suffix-itself", "https://co.uk/", "not longer than its public suffix"),
        ("own-host", "https://api.futureagi.com/hook", "WT-05b"),
        ("own-apex", "https://FutureAGI.com./x", ""),
        ("own-ee-origin", "https://omega-ee.example.net/hooks", "WEBHOOKS_DENY_HOSTS"),
        (
            "own-gateway-sub",
            "https://eu.gateway.example.net/x",
            "subdomains of a denied host",
        ),
        # 1.1.0: only visible ASCII (U+0021-U+007E) in the path and query; they are sent byte for byte.
        (
            "crlf-in-path",
            "https://hooks.acme.ai/x\r\nX-Injected: 1",
            "CR/LF: a request-splitting shape, refused at create",
        ),
        ("lf-in-query", "https://hooks.acme.ai/x?a=1\nb=2", ""),
        ("space-in-path", "https://hooks.acme.ai/a b", "percent-encode it: /a%20b"),
        ("tab-in-path", "https://hooks.acme.ai/a\tb", ""),
        ("nul-in-path", "https://hooks.acme.ai/\x00", ""),
        ("del-in-path", "https://hooks.acme.ai/a\x7fb", ""),
        (
            "non-ascii-path",
            "https://hooks.acme.ai/café",
            "percent-encode it: /caf%C3%A9",
        ),
        ("non-ascii-query", "https://hooks.acme.ai/x?q=é", ""),
        ("bidi-in-path", "https://hooks.acme.ai/x‮gpj", "a bidi override"),
        (
            "space-in-query",
            "https://hooks.acme.ai/x?q=a b",
            "checked before the scheme, so the first failure is this one",
        ),
        (
            "ok-percent-encoded",
            "https://hooks.acme.ai/caf%C3%A9?q=%C3%A9%20x",
            "allowed: the caller percent-encoded the non-ASCII and the space",
        ),
        (
            "ok-idna-host-ascii-path",
            "https://bücher.de/caf%C3%A9",
            "the host is judged after IDNA, so a non-ASCII host passes",
        ),
        # 1.1.0: every name under .arpa is special-use (reverse-lookup and home.arpa names).
        ("arpa-in-addr", "https://foo.in-addr.arpa/", ""),
        ("arpa-reverse-ipv4", "https://5.0.0.10.in-addr.arpa/", ""),
        ("arpa-ip6", "https://x.ip6.arpa/", ""),
        # 1.1.0: 6to4 is private as a whole; the local-use NAT64 prefix is never.
        (
            "6to4-public",
            "https://[2002:808:808::]/",
            "6to4 (RFC 7526, deprecated) is private even around a public IPv4 address",
        ),
        (
            "nat64-local-use-96-loopback",
            "https://[64:ff9b:1:ab00::7f00:1]/",
            "a /96 layout inside 64:ff9b:1::/48 holding 127.0.0.1",
        ),
        # 1.2.0 (EE review 10-22): IPv4-translated is unwrapped; the compatible form in its hex spelling.
        (
            "ipv4-compatible-loopback-hex",
            "https://[::7f00:1]/",
            "unwrapping: IPv4-compatible, ::127.0.0.1 spelled in hex; EE review 10-22",
        ),
        (
            "ipv4-translated-loopback",
            "https://[::ffff:0:7f00:1]/",
            "unwrapping: IPv4-translated ::ffff:0:0:0/96 holding 127.0.0.1; EE review 10-22",
        ),
    ]:
        u(cid, "hosted", url, note=note)
    for cid, url, note in [
        (
            "wide-metadata-ip",
            "https://169.254.169.254/",
            "a CIDR covering everything cannot admit a NEVER address",
        ),
        ("wide-loopback", "https://127.0.0.1/", ""),
        ("wide-link-local-v6", "https://[fe80::1]/", ""),
        ("wide-mapped-metadata", "https://[::ffff:169.254.169.254]/", ""),
        ("wide-nat64-local-use", "https://[64:ff9b:1:ab00::7f00:1]/", ""),
        ("wide-6to4-loopback", "https://[2002:7f00:1::]/", ""),
        (
            "wide-ipv4-compatible-loopback-hex",
            "https://[::7f00:1]/",
            "unwrapping: IPv4-compatible, ::127.0.0.1 spelled in hex; EE review 10-22",
        ),
        (
            "wide-ipv4-translated-loopback",
            "https://[::ffff:0:7f00:1]/",
            "unwrapping: IPv4-translated ::ffff:0:0:0/96 holding 127.0.0.1; EE review 10-22",
        ),
        ("wide-rfc1918-admitted", "https://10.0.0.5/", "private and in the CIDRs"),
        ("wide-6to4-public-admitted", "https://[2002:808:808::]/", ""),
        (
            "wide-private-port-refused",
            "https://10.0.0.5:8443/",
            "admitted addresses still use FI_WEBHOOKS_PRIVATE_PORTS only",
        ),
    ]:
        u(cid, "self_hosted_wide", url, note=note)
    u(
        "hosted-ignores-allow-http",
        "hosted_with_dev_env",
        "http://hooks.acme.ai/",
        note="FI_WEBHOOKS_ALLOW_HTTP is ignored on hosted",
    )
    u(
        "hosted-ignores-private-cidrs",
        "hosted_with_dev_env",
        "https://10.0.0.5/",
        note="FI_WEBHOOKS_PRIVATE_CIDRS is ignored on hosted",
    )
    u(
        "hosted-ignores-allowed-private-hosts",
        "hosted_with_dev_env",
        "https://hooks.acme.test/",
        note="FI_WEBHOOKS_ALLOWED_PRIVATE_HOSTS is ignored on hosted",
    )
    u(
        "self-http-allowlisted-name",
        "self_hosted",
        "http://hooks.acme.test:8080/x",
        note="",
    )
    u(
        "self-docker-internal",
        "self_hosted",
        "https://host.docker.internal/x",
        note="S7",
    )
    u(
        "self-localhost-always",
        "self_hosted",
        "https://localhost:9999/",
        note="localhost is refused on every install",
    )
    u(
        "self-metadata-always",
        "self_hosted",
        "https://metadata.google.internal/",
        note="",
    )
    u(
        "self-private-admitted",
        "self_hosted",
        "http://10.20.1.5:8080/hook",
        note="in FI_WEBHOOKS_PRIVATE_CIDRS",
    )
    u(
        "self-private-port",
        "self_hosted",
        "http://10.20.1.5:9000/hook",
        note="port not in FI_WEBHOOKS_PRIVATE_PORTS",
    )
    u("self-private-outside-cidr", "self_hosted", "https://10.30.0.1/", note="")
    u(
        "self-loopback-never",
        "self_hosted",
        "https://127.0.0.1:9999/",
        note="a CIDR holding a NEVER address cannot admit it",
    )
    u(
        "self-nat64-never",
        "self_hosted",
        "https://[64:ff9b::a9fe:a9fe]/",
        note="WT-06b with 64:ff9b::/96 in the CIDRs",
    )
    u(
        "self-nat64-loopback-never",
        "self_hosted",
        "https://[64:ff9b::7f00:1]/",
        note="NAT64 holding 127.0.0.1 with 64:ff9b::/96 and 127.0.0.0/8 in the CIDRs; ruling 76",
    )
    # 1.4.0: an embedded private IPv4 address must sit inside an IPv4 CIDR itself.
    for cid, url, note in [
        (
            "self-nat64-private-outside-cidr",
            "https://[64:ff9b::a00:1]/",
            "NAT64 holding 10.0.0.1",
        ),
        (
            "self-nat64-private-in-cidr",
            "https://[64:ff9b::a14:5]/",
            "NAT64 holding 10.20.0.5",
        ),
        (
            "self-6to4-private-outside-cidr",
            "https://[2002:a00:1::]/",
            "6to4 holding 10.0.0.1",
        ),
        (
            "self-6to4-private-in-cidr",
            "https://[2002:a14:5::]/",
            "6to4 holding 10.20.0.5",
        ),
        (
            "self-teredo-client-outside-cidr",
            "https://[2001:0:808:808::f5ff:fffe]/",
            "Teredo client 10.0.0.1",
        ),
    ]:
        u(cid, "self_hosted", url, note=note + "; ruling 76 addendum")
    u("self-svc-not-allowlisted", "self_hosted", "https://redis.default.svc/", note="")
    u(
        "self-strict-http",
        "self_hosted_strict",
        "http://hooks.acme.ai:8080/",
        note="no FI_WEBHOOKS_ALLOW_HTTP",
    )
    u(
        "self-strict-docker-internal",
        "self_hosted_strict",
        "https://host.docker.internal/",
        note="not allowlisted",
    )
    org = {"org_allowed_hosts": ["*.acme.ai"]}
    u("policy-wildcard-ok", "hosted", "https://hooks.acme.ai/x", policy=org, note="")
    u(
        "policy-wildcard-deep",
        "hosted",
        "https://a.b.acme.ai/x",
        policy=org,
        note="the leftmost wildcard matches any depth",
    )
    u(
        "policy-apex-not-matched",
        "hosted",
        "https://acme.ai/x",
        policy=org,
        note="*.acme.ai does not match acme.ai",
    )
    u(
        "policy-other-host",
        "hosted",
        "https://hooks.other.ai/x",
        policy=org,
        note="WT-26",
    )
    ws_policy = {
        "org_allowed_hosts": ["*.acme.ai"],
        "workspace_allowed_hosts": ["hooks.acme.ai"],
    }
    u(
        "policy-workspace-narrows-ok",
        "hosted",
        "https://hooks.acme.ai/x",
        policy=ws_policy,
        note="",
    )
    u(
        "policy-workspace-narrows-deny",
        "hosted",
        "https://x.acme.ai/x",
        policy=ws_policy,
        note="",
    )

    return dump(
        {
            "conformance_version": CONFORMANCE_VERSION,
            "file": "addresses",
            "rules": address_rules(),
            "profiles": {
                "hosted": HOSTED,
                "hosted_with_dev_env": HOSTED_WITH_DEV_ENV,
                "self_hosted": SELF,
                "self_hosted_strict": SELF_STRICT,
                "self_hosted_wide": SELF_WIDE,
            },
            "url": url_cases,
            "address": address_cases(),
            "redirect": redirect_cases(),
        }
    )
