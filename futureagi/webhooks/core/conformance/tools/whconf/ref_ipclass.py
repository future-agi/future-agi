"""Address classes: NEVER and PRIVATE tables, embedded-IPv4 unwrapping, and the resolved-answer decision
(DESIGN.md section 6.2)."""

from __future__ import annotations

import ipaddress

NEVER_NETS = [
    ipaddress.ip_network(n)
    for n in (
        "0.0.0.0/8",
        "127.0.0.0/8",
        "169.254.0.0/16",
        "224.0.0.0/4",
        "255.255.255.255/32",
        "100.100.100.200/32",
        "168.63.129.16/32",
        "::/128",
        "::1/128",
        "fe80::/10",
        "ff00::/8",
        "fd00:ec2::254/128",
        # The local-use NAT64 prefix (RFC 8215). Its network-specific prefix length is the operator's choice (RFC 6052
        # section 2.2 allows /48 to /96 inside it), so the embedded IPv4 address cannot be read reliably: a /96 layout
        # can hold 127.0.0.1 where a /48 reading sees 0.0.0.127. No CIDR may admit it.
        "64:ff9b:1::/48",
    )
]

PRIVATE_NETS = [
    ipaddress.ip_network(n)
    for n in (
        "10.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "100.64.0.0/10",
        "192.0.0.0/24",
        "192.0.2.0/24",
        "198.18.0.0/15",
        "198.51.100.0/24",
        "203.0.113.0/24",
        "240.0.0.0/4",
        "fc00::/7",
        "2001::/23",
        "2001:db8::/32",
        "3fff::/20",
        "100::/64",
        "5f00::/16",
        # 6to4, deprecated by RFC 7526; Python's is_global is False for it. Private as a whole, and its embedded IPv4
        # address is still unwrapped below, so 2002:7f00:1:: stays never.
        "2002::/16",
    )
]

GLOBAL_UNICAST_V6 = ipaddress.ip_network("2000::/3")

RANK = {"public": 0, "private": 1, "never": 2}


def _class_plain(ip: ipaddress._BaseAddress) -> str:
    if any(ip in n for n in NEVER_NETS if n.version == ip.version):
        return "never"
    if any(ip in n for n in PRIVATE_NETS if n.version == ip.version):
        return "private"
    if ip.version == 6 and ip not in GLOBAL_UNICAST_V6:
        return "private"
    return "public"


def embedded_ipv4(ip: ipaddress._BaseAddress) -> list[ipaddress.IPv4Address]:
    """IPv4 addresses embedded in an IPv6 address (DESIGN.md section 6.2, S6).

    The last 32 bits of an IPv4-mapped (::ffff:0:0/96), IPv4-translated (::ffff:0:0:0/96, RFC 2765 / RFC 6145 SIIT
    ::ffff:0:a.b.c.d; added in conformance 1.2.0 after the EE review of 10-22), IPv4-compatible (::/96 except :: and
    ::1) or NAT64 (64:ff9b::/96) address; bits 16-47 of 6to4; Teredo server bits 32-63 and client NOT bits 96-127."""
    if ip.version != 6:
        return []
    v = int(ip)
    out = []
    if ip in ipaddress.ip_network("::ffff:0:0/96"):
        out.append(ipaddress.IPv4Address(v & 0xFFFFFFFF))
    elif ip in ipaddress.ip_network("::ffff:0:0:0/96"):
        out.append(ipaddress.IPv4Address(v & 0xFFFFFFFF))
    elif ip in ipaddress.ip_network("::/96") and v > 1:
        out.append(ipaddress.IPv4Address(v & 0xFFFFFFFF))
    if ip in ipaddress.ip_network("64:ff9b::/96"):
        out.append(ipaddress.IPv4Address(v & 0xFFFFFFFF))
    if ip in ipaddress.ip_network("2002::/16"):
        out.append(ipaddress.IPv4Address((v >> 80) & 0xFFFFFFFF))
    if ip in ipaddress.ip_network("2001::/32"):
        out.append(ipaddress.IPv4Address((v >> 64) & 0xFFFFFFFF))  # Teredo server
        out.append(
            ipaddress.IPv4Address((~v) & 0xFFFFFFFF)
        )  # Teredo client (obfuscated)
    return out


def classify(addr: str) -> dict:
    ip = ipaddress.ip_address(addr)
    cls = _class_plain(ip)
    emb = embedded_ipv4(ip)
    for e in emb:
        ec = _class_plain(e)
        if RANK[ec] > RANK[cls]:
            cls = ec
    return {"class": cls, "embedded_ipv4": [str(e) for e in emb]}


def _in_cidrs(addr: str, cidrs: list[str]) -> bool:
    ip = ipaddress.ip_address(addr)
    return any(
        ip in ipaddress.ip_network(c)
        for c in cidrs
        if ipaddress.ip_network(c).version == ip.version
    )


def _private_admitted(addr: str, p: dict) -> bool:
    """Off hosted, inside private_cidrs, and every embedded IPv4 address public or inside an IPv4 private_cidrs
    entry (conformance 1.4.0): a translator prefix opt-in admits public targets behind it only."""
    cidrs = p.get("private_cidrs", [])
    if p["mode"] != "self_hosted" or not _in_cidrs(addr, cidrs):
        return False
    return all(
        _class_plain(e) == "public" or _in_cidrs(str(e), cidrs)
        for e in embedded_ipv4(ipaddress.ip_address(addr))
    )


def address_decision(case: dict) -> dict:
    """Every resolved answer must be allowed; else the attempt is ssrf_blocked and nothing connects."""
    p = case["profile"]
    port = case["port"]
    per = []
    blocked = None
    for a in case["answers"]:
        c = classify(a)
        if c["class"] == "never":
            d = {"address": a, "class": "never", "allow": False, "rule": "addr.never"}
        elif c["class"] == "private":
            admitted = _private_admitted(a, p)
            if not admitted:
                d = {
                    "address": a,
                    "class": "private",
                    "allow": False,
                    "rule": "addr.private",
                }
            elif port not in p.get("private_ports", [443]):
                d = {
                    "address": a,
                    "class": "private",
                    "allow": False,
                    "rule": "addr.private_port",
                }
            else:
                d = {
                    "address": a,
                    "class": "private",
                    "allow": True,
                    "rule": "addr.private_cidr_admitted",
                }
        else:
            d = {"address": a, "class": "public", "allow": True, "rule": "addr.public"}
        per.append(d)
        if not d["allow"] and blocked is None:
            blocked = d["rule"]
    if not case["answers"]:
        return {
            "allow": False,
            "outcome": "dns_error",
            "rule": "addr.no_answer",
            "answers": [],
            "connect": [],
        }
    if blocked:
        return {
            "allow": False,
            "outcome": "ssrf_blocked",
            "rule": blocked,
            "answers": per,
            "connect": [],
        }
    seen: list[str] = []
    for a in case["answers"]:
        if a not in seen:
            seen.append(a)
    return {
        "allow": True,
        "outcome": None,
        "rule": None,
        "answers": per,
        "connect": seen[:2],
    }
