"""addresses.json: resolved-answer cases (DESIGN.md section 6.2; WT-06) and redirect cases (section 6.2; WT-07)."""

from __future__ import annotations

from . import reference as R
from .cases_addresses_rules import PROFILES


def address_cases() -> list[dict]:
    addr_cases = []

    def a(cid, profile_name, answers, port=443, note=""):
        profile = PROFILES[profile_name]
        c = {"profile": profile, "answers": answers, "port": port}
        addr_cases.append(
            {
                "id": cid,
                "profile": profile_name,
                "answers": answers,
                "port": port,
                "expected": R.address_decision(c),
                "note": note,
            }
        )

    for cid, ans, note in [
        ("public-v4", ["93.184.215.14"], ""),
        (
            "public-v4-v6",
            ["93.184.215.14", "2606:2800:21f:cb07:6820:80da:af6b:8b2c"],
            "",
        ),
        (
            "three-public",
            ["93.184.215.14", "93.184.215.15", "93.184.215.16"],
            "at most 2 addresses are tried",
        ),
        (
            "duplicate-answers",
            ["93.184.215.14", "93.184.215.14", "93.184.215.15"],
            "duplicates are tried once",
        ),
        (
            "public-and-private",
            ["93.184.215.14", "10.0.0.5"],
            "EVERY answer must be allowed (rebinding)",
        ),
        ("loopback", ["127.0.0.1"], "WT-06"),
        ("metadata", ["169.254.169.254"], "WT-06"),
        ("mapped-metadata", ["::ffff:169.254.169.254"], ""),
        ("nat64-metadata", ["64:ff9b::a9fe:a9fe"], "WT-06b"),
        # 1.3.0: NAT64 holding loopback.
        ("nat64-loopback", ["64:ff9b::7f00:1"], "NAT64 holding 127.0.0.1; ruling 76"),
        ("nat64-local-use", ["64:ff9b:1:a9fe:a9:fe00::"], ""),
        ("6to4-loopback", ["2002:7f00:1::"], "WT-06b"),
        ("v4-compatible-loopback", ["::127.0.0.1"], "WT-06b"),
        (
            "teredo-test-net",
            ["2001:0:4136:e378:8000:63bf:3fff:fdd2"],
            "Teredo, client 192.0.2.45",
        ),
        (
            "teredo-loopback",
            ["2001:0:4136:e378:8000:63bf:80ff:fffe"],
            "Teredo, client 127.0.0.1",
        ),
        ("alibaba-metadata", ["100.100.100.200"], ""),
        ("azure-wireserver", ["168.63.129.16"], ""),
        ("aws-imds-v6", ["fd00:ec2::254"], ""),
        ("cgnat", ["100.64.0.1"], ""),
        ("class-e", ["240.0.0.1"], ""),
        ("broadcast", ["255.255.255.255"], ""),
        ("this-network", ["0.0.0.0"], ""),
        ("this-network-2", ["0.1.2.3"], ""),
        ("multicast-v4", ["224.0.0.1"], ""),
        ("multicast-v6", ["ff02::1"], ""),
        ("unspecified-v6", ["::"], ""),
        ("loopback-v6", ["::1"], ""),
        ("ula", ["fc00::1"], ""),
        ("doc-v6", ["2001:db8::1"], ""),
        ("doc-v6-3fff", ["3fff::1"], "RFC 9637"),
        ("test-net-1", ["192.0.2.10"], ""),
        ("benchmark", ["198.18.0.1"], ""),
        ("ietf-192-0-0", ["192.0.0.8"], ""),
        ("srv6-sid", ["5f00::1"], "outside 2000::/3"),
        ("outside-2000-3", ["4000::1"], "outside 2000::/3"),
        ("rfc1918-172", ["172.31.255.255"], ""),
        ("rfc1918-edge-public", ["172.32.0.1"], "just outside 172.16.0.0/12"),
        ("no-answer", [], "an empty answer set is a dns_error, never a connect"),
        # 1.1.0
        ("link-local-v4", ["169.254.1.1"], ""),
        ("link-local-v6", ["fe80::1"], ""),
        ("6to4-public", ["2002:808:808::"], "RFC 7526: private as a whole"),
        (
            "nat64-local-use-96-loopback",
            ["64:ff9b:1:ab00::7f00:1"],
            "a /96 layout inside the local-use prefix",
        ),
        (
            "nat64-local-use-64-metadata",
            ["64:ff9b:1:cd00:a9:fea9:fe00:0"],
            "a /64 layout holding 169.254.169.254",
        ),
        (
            "nat64-local-use-public",
            ["64:ff9b:1:808:8:800::"],
            "the /48 layout of 8.8.8.8: still never, the layout cannot be known",
        ),
        ("nat64-wkp-public", ["64:ff9b::808:808"], "outside 2000::/3: private"),
        # 1.2.0 (EE review 10-22): the IPv4-translated prefix is unwrapped; the compatible form in its hex spelling.
        (
            "translated-loopback",
            ["::ffff:0:7f00:1"],
            "unwrapping: IPv4-translated ::ffff:0:0:0/96 holding 127.0.0.1; EE review 10-22",
        ),
        (
            "v4-compatible-loopback-hex",
            ["::7f00:1"],
            "unwrapping: IPv4-compatible, ::127.0.0.1 spelled in hex; EE review 10-22",
        ),
        (
            "translated-rfc1918",
            ["::ffff:0:a00:1"],
            "unwrapping: IPv4-translated holding 10.0.0.1; EE review 10-22",
        ),
        (
            "translated-public",
            ["::ffff:0:808:808"],
            "unwrapping: IPv4-translated holding 8.8.8.8 is private, the strictest of the outer (outside 2000::/3) "
            "and the embedded public address; EE review 10-22",
        ),
    ]:
        a(cid, "hosted", ans, note=note)
    for cid, ans, note in [
        ("wide-loopback", ["127.0.0.1"], "NEVER beats a CIDR covering everything"),
        ("wide-this-network", ["0.0.0.0"], ""),
        ("wide-metadata", ["169.254.169.254"], ""),
        ("wide-link-local-v4", ["169.254.1.1"], ""),
        ("wide-alibaba-metadata", ["100.100.100.200"], ""),
        ("wide-azure-wireserver", ["168.63.129.16"], ""),
        ("wide-aws-imds-v6", ["fd00:ec2::254"], ""),
        ("wide-broadcast", ["255.255.255.255"], ""),
        ("wide-multicast-v4", ["224.0.0.1"], ""),
        ("wide-loopback-v6", ["::1"], ""),
        ("wide-unspecified-v6", ["::"], ""),
        ("wide-link-local-v6", ["fe80::1"], ""),
        ("wide-multicast-v6", ["ff02::1"], ""),
        ("wide-mapped-loopback", ["::ffff:127.0.0.1"], "unwrapping: IPv4-mapped"),
        ("wide-compatible-loopback", ["::127.0.0.1"], "unwrapping: IPv4-compatible"),
        ("wide-nat64-metadata", ["64:ff9b::a9fe:a9fe"], "unwrapping: NAT64 /96"),
        ("wide-nat64-local-use", ["64:ff9b:1:ab00::7f00:1"], "local-use NAT64"),
        ("wide-6to4-metadata", ["2002:a9fe:a9fe::"], "unwrapping: 6to4"),
        (
            "wide-teredo-server-loopback",
            ["2001:0:7f00:1::f7f7:f7f7"],
            "unwrapping: Teredo server 127.0.0.1 (its client is 8.8.8.8)",
        ),
        (
            "wide-teredo-client-loopback",
            ["2001:0:4136:e378:8000:63bf:80ff:fffe"],
            "unwrapping: Teredo client 127.0.0.1",
        ),
        (
            "wide-mixed",
            ["10.0.0.5", "169.254.169.254"],
            "one NEVER answer blocks the attempt",
        ),
        ("wide-rfc1918", ["10.0.0.5"], "admitted"),
        ("wide-cgnat", ["100.64.0.1"], "admitted"),
        ("wide-ula", ["fd00::1"], "admitted"),
        ("wide-6to4-public", ["2002:808:808::"], "admitted"),
        ("wide-teredo-test-net", ["2001:0:4136:e378:8000:63bf:3fff:fdd2"], "admitted"),
        # 1.2.0 (EE review 10-22)
        (
            "wide-translated-loopback",
            ["::ffff:0:7f00:1"],
            "unwrapping: IPv4-translated ::ffff:0:0:0/96 holding 127.0.0.1; EE review 10-22",
        ),
        (
            "wide-compatible-loopback-hex",
            ["::7f00:1"],
            "unwrapping: IPv4-compatible, ::127.0.0.1 spelled in hex; EE review 10-22",
        ),
        (
            "wide-translated-rfc1918",
            ["::ffff:0:a00:1"],
            "unwrapping: IPv4-translated holding 10.0.0.1, private: admitted; EE review 10-22",
        ),
        (
            "wide-translated-public",
            ["::ffff:0:808:808"],
            "unwrapping: IPv4-translated holding 8.8.8.8, private: admitted; EE review 10-22",
        ),
    ]:
        a(cid, "self_hosted_wide", ans, note=note)
    a("self-cidr-admitted", "self_hosted", ["10.20.1.5"], 443, "")
    a("self-cidr-port-8080", "self_hosted", ["10.20.1.5"], 8080, "")
    a(
        "self-cidr-port-9000",
        "self_hosted",
        ["10.20.1.5"],
        9000,
        "FI_WEBHOOKS_PRIVATE_PORTS",
    )
    a("self-outside-cidr", "self_hosted", ["10.30.0.1"], 443, "")
    a(
        "self-loopback-in-cidr",
        "self_hosted",
        ["127.0.0.1"],
        443,
        "NEVER beats any CIDR",
    )
    a("self-nat64-in-cidr", "self_hosted", ["64:ff9b::a9fe:a9fe"], 443, "WT-06b")
    # 1.3.0: an opt-in of 64:ff9b::/96 (and 127.0.0.0/8) cannot admit loopback carried over NAT64.
    a(
        "self-nat64-loopback-in-cidr",
        "self_hosted",
        ["64:ff9b::7f00:1"],
        443,
        "NAT64 holding 127.0.0.1 with 64:ff9b::/96 and 127.0.0.0/8 in the CIDRs: NEVER beats any CIDR; ruling 76",
    )
    # 1.4.0: an embedded IPv4 address is judged as that IPv4 address. A translator prefix in the
    # CIDRs admits public targets behind it; a private one must sit inside an IPv4 CIDR itself (10.20.0.0/16 here).
    for cid, ans, note in [
        (
            "self-nat64-private-outside-cidr",
            "64:ff9b::a00:1",
            "NAT64 holding 10.0.0.1, outside 10.20.0.0/16",
        ),
        (
            "self-nat64-private-in-cidr",
            "64:ff9b::a14:5",
            "NAT64 holding 10.20.0.5, inside 10.20.0.0/16",
        ),
        (
            "self-nat64-public",
            "64:ff9b::808:808",
            "NAT64 holding 8.8.8.8: the translator for a public target",
        ),
        (
            "self-6to4-private-outside-cidr",
            "2002:a00:1::",
            "6to4 holding 10.0.0.1, outside 10.20.0.0/16",
        ),
        (
            "self-6to4-private-in-cidr",
            "2002:a14:5::",
            "6to4 holding 10.20.0.5, inside 10.20.0.0/16",
        ),
        ("self-6to4-public", "2002:808:808::", "6to4 holding 8.8.8.8: a public target"),
        (
            "self-teredo-client-outside-cidr",
            "2001:0:808:808::f5ff:fffe",
            "Teredo, server 8.8.8.8, client 10.0.0.1 (outside 10.20.0.0/16)",
        ),
        (
            "self-teredo-server-outside-cidr",
            "2001:0:a00:1::f7f7:f7f7",
            "Teredo, server 10.0.0.1 (outside 10.20.0.0/16), client 8.8.8.8",
        ),
        (
            "self-teredo-in-cidr",
            "2001:0:808:808::f5eb:fffa",
            "Teredo, server 8.8.8.8, client 10.20.0.5 (inside 10.20.0.0/16)",
        ),
    ]:
        a(cid, "self_hosted", [ans], 443, note + "; ruling 76 addendum")
    a("self-public", "self_hosted", ["93.184.215.14"], 443, "")
    return addr_cases


def redirect_cases() -> list[dict]:
    out = []
    for cid, status, location, note in [
        ("named-host", 301, "https://hooks.acme.ai/new", "the log names the new host"),
        (
            "port-and-dot",
            308,
            "https://HOOKS.acme.ai.:8443/x",
            "normalised; the port is not shown",
        ),
        (
            "private-ip-wt07",
            302,
            "https://10.0.0.5/",
            "WT-07: never fetched; no address shown",
        ),
        ("ipv6-literal", 307, "https://[::1]/", ""),
        ("userinfo", 302, "https://user:pw@evil.example.com/", ""),
        ("relative", 302, "/v2/hooks", ""),
        ("idna", 301, "http://bücher.de/", ""),
        ("javascript", 302, "javascript:alert(1)", ""),
        ("missing", 302, None, "no Location header"),
        ("space", 302, "https://exa mple.com/", ""),
        ("bad-label", 302, "https://-bad-.example.com/", ""),
        ("bidi", 302, "https://evil‮gpj.example.com/", "a bidi control character"),
        ("decimal-ip", 302, "https://2852039166/", ""),
    ]:
        c = {"status": status, "location": location}
        out.append({"id": cid, **c, "expected": R.redirect_decision(c), "note": note})
    return out
