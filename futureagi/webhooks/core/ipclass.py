"""Address classes: the NEVER and PRIVATE tables, embedded-IPv4 unwrapping, and the decision over a resolved answer
(DESIGN.md section 6.2, S6; conformance ``addresses.json``, ``address`` cases).

Unwrapping covers IPv4-mapped (``::ffff:0:0/96``), IPv4-translated (``::ffff:0:0:0/96``, the RFC 2765 / RFC 6145 SIIT
form ``::ffff:0:a.b.c.d``; conformance 1.2.0), IPv4-compatible (``::/96`` except ``::`` and ``::1``), NAT64
(``64:ff9b::/96``), 6to4 and Teredo; the class is the strictest of the outer address and every embedded one, so
``::ffff:0:7f00:1`` and ``::7f00:1`` are NEVER like 127.0.0.1.

The tables are normative and complete. They do not lean on ``ipaddress``'s ``is_private`` / ``is_global`` flags,
which changed between Python 3.11 and 3.13, and they are at least as strict as ``is_global`` on 3.11-3.14.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address

NEVER = "never"
PRIVATE = "private"
PUBLIC = "public"
RANK = {PUBLIC: 0, PRIVATE: 1, NEVER: 2}

NEVER_CIDRS = (
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
    # RFC 8215 local-use NAT64: the embedded address cannot be read reliably, so no CIDR may admit it.
    "64:ff9b:1::/48",
)
PRIVATE_CIDRS = (
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
    "2002::/16",
)
NEVER_NETS = tuple(ipaddress.ip_network(n) for n in NEVER_CIDRS)
PRIVATE_NETS = tuple(ipaddress.ip_network(n) for n in PRIVATE_CIDRS)
GLOBAL_UNICAST_V6 = ipaddress.ip_network("2000::/3")
_MAPPED = ipaddress.ip_network("::ffff:0:0/96")
_TRANSLATED = ipaddress.ip_network("::ffff:0:0:0/96")
_COMPAT = ipaddress.ip_network("::/96")
_NAT64 = ipaddress.ip_network("64:ff9b::/96")
_6TO4 = ipaddress.ip_network("2002::/16")
_TEREDO = ipaddress.ip_network("2001::/32")


def _plain_class(ip: IPAddress) -> str:
    if any(ip in n for n in NEVER_NETS if n.version == ip.version):
        return NEVER
    if any(ip in n for n in PRIVATE_NETS if n.version == ip.version):
        return PRIVATE
    if ip.version == 6 and ip not in GLOBAL_UNICAST_V6:
        return PRIVATE
    return PUBLIC


def embedded_ipv4(ip: IPAddress) -> list[ipaddress.IPv4Address]:
    """IPv4 addresses carried inside an IPv6 address: mapped (``::ffff:0:0/96``), translated (``::ffff:0:0:0/96``),
    compatible (``::/96`` except ``::`` and ``::1``) and NAT64 /96, each the last 32 bits; 6to4 (bits 16-47); Teredo
    server (bits 32-63) and client (NOT bits 96-127)."""
    if ip.version != 6:
        return []
    v = int(ip)
    out = []
    if ip in _MAPPED or ip in _TRANSLATED or (ip in _COMPAT and v > 1):
        out.append(ipaddress.IPv4Address(v & 0xFFFFFFFF))
    if ip in _NAT64:
        out.append(ipaddress.IPv4Address(v & 0xFFFFFFFF))
    if ip in _6TO4:
        out.append(ipaddress.IPv4Address((v >> 80) & 0xFFFFFFFF))
    if ip in _TEREDO:
        out.append(ipaddress.IPv4Address((v >> 64) & 0xFFFFFFFF))
        out.append(ipaddress.IPv4Address((~v) & 0xFFFFFFFF))
    return out


def classify(addr: str | IPAddress) -> str:
    """``never`` > ``private`` > ``public``: the strictest of the outer address and every embedded IPv4 address."""
    ip = ipaddress.ip_address(addr)
    cls = _plain_class(ip)
    for e in embedded_ipv4(ip):
        ec = _plain_class(e)
        if RANK[ec] > RANK[cls]:
            cls = ec
    return cls


def in_cidrs(addr: str | IPAddress, cidrs: Iterable[str]) -> bool:
    ip = ipaddress.ip_address(addr)
    for c in cidrs:
        net = ipaddress.ip_network(c, strict=False)
        if net.version == ip.version and ip in net:
            return True
    return False


def private_admitted(addr: str | IPAddress, profile: Profile) -> bool:
    """Whether a ``private`` address passes the operator's CIDR opt-in: only off hosted, only when the address is
    inside ``private_cidrs``, and only when every embedded IPv4 address is public or inside an IPv4
    ``private_cidrs`` entry itself. Opting in a translator prefix (NAT64, 6to4, Teredo) admits public targets behind
    it, not every private IPv4 address behind it (conformance 1.4.0)."""
    if profile.hosted or not in_cidrs(addr, profile.private_cidrs):
        return False
    ip = ipaddress.ip_address(addr)
    return all(
        _plain_class(e) == PUBLIC or in_cidrs(e, profile.private_cidrs)
        for e in embedded_ipv4(ip)
    )


@dataclass(frozen=True)
class Profile:
    """Where the rules run. ``mode`` is ``hosted`` or ``self_hosted``; the operator settings are ignored on hosted."""

    mode: str
    allow_http: bool = False
    private_cidrs: tuple[str, ...] = ()
    private_ports: tuple[int, ...] = (443,)
    allowed_private_hosts: tuple[str, ...] = ()
    deny_hosts: tuple[str, ...] = ()
    own_hosts: tuple[str, ...] = ("futureagi.com",)

    @classmethod
    def from_mapping(cls, m) -> Profile:
        if m["mode"] not in ("hosted", "self_hosted"):
            raise ValueError(f"unknown mode {m['mode']!r}")
        return cls(
            mode=m["mode"],
            allow_http=bool(m.get("allow_http", False)),
            private_cidrs=tuple(m.get("private_cidrs", ())),
            private_ports=tuple(int(p) for p in m.get("private_ports", (443,))),
            allowed_private_hosts=tuple(
                h.lower() for h in m.get("allowed_private_hosts", ())
            ),
            deny_hosts=tuple(h.lower() for h in m.get("deny_hosts", ())),
        )

    @property
    def hosted(self) -> bool:
        return self.mode == "hosted"


def admit(addr: str, port: int, profile: Profile) -> dict:
    """One address: ``{address, class, allow, rule}``. Private addresses pass only off hosted, inside
    ``private_cidrs``, on ``private_ports``; NEVER addresses never pass."""
    cls = classify(addr)
    if cls == NEVER:
        return {"address": addr, "class": cls, "allow": False, "rule": "addr.never"}
    if cls == PRIVATE:
        if not private_admitted(addr, profile):
            return {
                "address": addr,
                "class": cls,
                "allow": False,
                "rule": "addr.private",
            }
        if port not in profile.private_ports:
            return {
                "address": addr,
                "class": cls,
                "allow": False,
                "rule": "addr.private_port",
            }
        return {
            "address": addr,
            "class": cls,
            "allow": True,
            "rule": "addr.private_cidr_admitted",
        }
    return {"address": addr, "class": cls, "allow": True, "rule": "addr.public"}


@dataclass(frozen=True)
class AddressDecision:
    allow: bool
    outcome: str | None
    rule: str | None
    answers: list[dict] = field(default_factory=list)
    connect: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "allow": self.allow,
            "outcome": self.outcome,
            "rule": self.rule,
            "answers": self.answers,
            "connect": self.connect,
        }


MAX_CONNECT_ADDRESSES = 2


def address_decision(
    answers: Sequence[str], port: int, profile: Profile
) -> AddressDecision:
    """Every resolved answer must be allowed, else ``ssrf_blocked`` and nothing connects; no answer is
    ``dns_error``; duplicates drop in order and at most the first 2 are tried."""
    if not answers:
        return AddressDecision(False, "dns_error", "addr.no_answer", [], [])
    per = [admit(a, port, profile) for a in answers]
    blocked = next((d["rule"] for d in per if not d["allow"]), None)
    if blocked:
        return AddressDecision(False, "ssrf_blocked", blocked, per, [])
    seen: list[str] = []
    for a in answers:
        if a not in seen:
            seen.append(a)
    return AddressDecision(True, None, None, per, seen[:MAX_CONNECT_ADDRESSES])
