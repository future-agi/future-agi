"""WH-0 self-checks of addresses.json. Standard library only, no Django (DESIGN.md section 22.1).

The URL and address expectations are checked against the WT-05 to WT-07 test rows and against a table typed in by hand
(class and rule per address), so a change to the reference rules in ``tools/whconf`` cannot rewrite them unnoticed.
Run with: python3 -I -m unittest discover -s futureagi/webhooks/core/tests -p 'test_conformance_vectors*.py'
"""

from __future__ import annotations

import ipaddress
import json
import random
import re
import sys
import unittest
from pathlib import Path

CONF = Path(__file__).resolve().parents[1] / "conformance"


def load(name: str) -> dict:
    return json.loads((CONF / name).read_text(encoding="utf-8"))


class AddressesAgainstTheTestPlan(unittest.TestCase):
    def test_wt05_urls_are_refused(self):
        cases = {
            c["url"]: c["expected"]
            for c in load("addresses.json")["url"]
            if c["profile"] == "hosted"
        }
        for url in [
            "https://169.254.169.254/",
            "https://[::ffff:169.254.169.254]/",
            "https://localhost/",
            "https://metadata.google.internal/",
            "https://10.0.0.5/",
            "https://100.64.0.1/",
            "https://[fd00::1]/",
            "https://[fe80::1]/",
            "https://user:pw@hooks.acme.ai/",
            "http://hooks.acme.ai/",
            "https://hooks.acme.ai:6379/",
            "https://hooks.acme.ai:6443/",
            "https://redis.default.svc/",
            "https://kubernetes/",
            "https://api.futureagi.com/hook",
        ]:
            self.assertFalse(cases[url]["allow"], url)
        for url in [
            "https://2852039166/",
            "https://127.1/",
            "https://0x7f.1/",
            "https://0251.0376.0251.0376/",
        ]:
            self.assertEqual(cases[url]["code"], "WEBHOOK_URL_INVALID", url)

    def test_codes_and_rules_are_from_the_declared_sets(self):
        f = load("addresses.json")
        codes = f["rules"]["codes"]
        for c in f["url"]:
            e = c["expected"]
            if not e["allow"]:
                self.assertIn(e["rule"], codes[e["code"]], c["id"])

    def test_redirects_are_never_followed_and_never_show_an_address(self):
        cases = load("addresses.json")["redirect"]
        self.assertGreaterEqual(len(cases), 10)
        for c in cases:
            e = c["expected"]
            self.assertEqual(
                (e["outcome"], e["follow"], e["retry_class"]),
                ("redirect_refused", False, "standard"),
            )
            shown = e["log_location_host"]
            self.assertTrue(
                shown == "another address" or not any(ch in shown for ch in ":[@ /"),
                c["id"],
            )
            self.assertFalse(shown.replace(".", "").isdigit(), c["id"])
        wt07 = next(c for c in cases if c["location"] == "https://10.0.0.5/")
        self.assertEqual(wt07["expected"]["log_location_host"], "another address")

    def test_every_answer_must_be_allowed(self):
        for c in load("addresses.json")["address"]:
            e = c["expected"]
            self.assertEqual(
                e["allow"],
                bool(c["answers"]) and all(a["allow"] for a in e["answers"]),
                c["id"],
            )
            self.assertLessEqual(len(e["connect"]), 2)


class AddressClassesTypedByHand(unittest.TestCase):
    """The class and rule of every security-critical address, typed in from DESIGN.md section 6.2 and the RFCs, so a
    change to the reference tables cannot rewrite these vectors unnoticed (``--write`` then test would pass)."""

    NEVER, PRIVATE, PUBLIC = "never", "private", "public"

    # address case id -> (class of the first answer, the case's rule)
    HOSTED = {
        "loopback": (NEVER, "addr.never"),
        "loopback-v6": (NEVER, "addr.never"),
        "unspecified-v6": (NEVER, "addr.never"),
        "this-network": (NEVER, "addr.never"),
        "this-network-2": (NEVER, "addr.never"),
        "broadcast": (NEVER, "addr.never"),
        "multicast-v4": (NEVER, "addr.never"),
        "multicast-v6": (NEVER, "addr.never"),
        "link-local-v4": (NEVER, "addr.never"),
        "link-local-v6": (NEVER, "addr.never"),
        "metadata": (NEVER, "addr.never"),
        "alibaba-metadata": (NEVER, "addr.never"),
        "azure-wireserver": (NEVER, "addr.never"),
        "aws-imds-v6": (NEVER, "addr.never"),
        "mapped-metadata": (NEVER, "addr.never"),
        "nat64-metadata": (NEVER, "addr.never"),
        "nat64-local-use": (NEVER, "addr.never"),
        "nat64-local-use-96-loopback": (NEVER, "addr.never"),
        "nat64-local-use-64-metadata": (NEVER, "addr.never"),
        "nat64-local-use-public": (NEVER, "addr.never"),
        "6to4-loopback": (NEVER, "addr.never"),
        "v4-compatible-loopback": (NEVER, "addr.never"),
        "teredo-loopback": (NEVER, "addr.never"),
        "teredo-test-net": (PRIVATE, "addr.private"),
        "6to4-public": (PRIVATE, "addr.private"),
        "nat64-wkp-public": (PRIVATE, "addr.private"),
        # 1.2.0, EE review 10-22: IPv4-translated (::ffff:0:0:0/96) is unwrapped; ::7f00:1 is ::127.0.0.1.
        "translated-loopback": (NEVER, "addr.never"),
        "v4-compatible-loopback-hex": (NEVER, "addr.never"),
        "translated-rfc1918": (PRIVATE, "addr.private"),
        "translated-public": (PRIVATE, "addr.private"),
        "cgnat": (PRIVATE, "addr.private"),
        "ula": (PRIVATE, "addr.private"),
        "rfc1918-172": (PRIVATE, "addr.private"),
        "class-e": (PRIVATE, "addr.private"),
        "doc-v6": (PRIVATE, "addr.private"),
        "doc-v6-3fff": (PRIVATE, "addr.private"),
        "test-net-1": (PRIVATE, "addr.private"),
        "benchmark": (PRIVATE, "addr.private"),
        "ietf-192-0-0": (PRIVATE, "addr.private"),
        "srv6-sid": (PRIVATE, "addr.private"),
        "outside-2000-3": (PRIVATE, "addr.private"),
        "rfc1918-edge-public": (PUBLIC, None),
        "public-v4": (PUBLIC, None),
    }

    # A self-hosted install whose CIDRs cover everything: NEVER stays refused, private is admitted.
    WIDE = {
        "wide-loopback": (NEVER, "addr.never"),
        "wide-this-network": (NEVER, "addr.never"),
        "wide-metadata": (NEVER, "addr.never"),
        "wide-link-local-v4": (NEVER, "addr.never"),
        "wide-alibaba-metadata": (NEVER, "addr.never"),
        "wide-azure-wireserver": (NEVER, "addr.never"),
        "wide-aws-imds-v6": (NEVER, "addr.never"),
        "wide-broadcast": (NEVER, "addr.never"),
        "wide-multicast-v4": (NEVER, "addr.never"),
        "wide-loopback-v6": (NEVER, "addr.never"),
        "wide-unspecified-v6": (NEVER, "addr.never"),
        "wide-link-local-v6": (NEVER, "addr.never"),
        "wide-multicast-v6": (NEVER, "addr.never"),
        "wide-mapped-loopback": (NEVER, "addr.never"),
        "wide-compatible-loopback": (NEVER, "addr.never"),
        "wide-nat64-metadata": (NEVER, "addr.never"),
        "wide-nat64-local-use": (NEVER, "addr.never"),
        "wide-6to4-metadata": (NEVER, "addr.never"),
        "wide-teredo-server-loopback": (NEVER, "addr.never"),
        "wide-teredo-client-loopback": (NEVER, "addr.never"),
        "wide-mixed": (PRIVATE, "addr.never"),
        "wide-rfc1918": (PRIVATE, None),
        "wide-cgnat": (PRIVATE, None),
        "wide-ula": (PRIVATE, None),
        "wide-6to4-public": (PRIVATE, None),
        "wide-teredo-test-net": (PRIVATE, None),
        "wide-translated-loopback": (NEVER, "addr.never"),
        "wide-compatible-loopback-hex": (NEVER, "addr.never"),
        "wide-translated-rfc1918": (PRIVATE, None),
        "wide-translated-public": (PRIVATE, None),
        "self-loopback-in-cidr": (NEVER, "addr.never"),
        "self-nat64-in-cidr": (NEVER, "addr.never"),
        "self-cidr-admitted": (PRIVATE, None),
    }

    # url case id -> (code, rule); None for allowed
    URLS = {
        "metadata-ip": ("WEBHOOK_URL_UNSAFE_ADDRESS", "addr.never"),
        "mapped-metadata": ("WEBHOOK_URL_UNSAFE_ADDRESS", "addr.never"),
        "nat64-metadata": ("WEBHOOK_URL_UNSAFE_ADDRESS", "addr.never"),
        "nat64-local-use-metadata": ("WEBHOOK_URL_UNSAFE_ADDRESS", "addr.never"),
        "nat64-local-use-96-loopback": ("WEBHOOK_URL_UNSAFE_ADDRESS", "addr.never"),
        "6to4-loopback": ("WEBHOOK_URL_UNSAFE_ADDRESS", "addr.never"),
        "ipv4-compatible-loopback": ("WEBHOOK_URL_UNSAFE_ADDRESS", "addr.never"),
        "teredo-loopback-client": ("WEBHOOK_URL_UNSAFE_ADDRESS", "addr.never"),
        "ipv4-compatible-loopback-hex": ("WEBHOOK_URL_UNSAFE_ADDRESS", "addr.never"),
        "ipv4-translated-loopback": ("WEBHOOK_URL_UNSAFE_ADDRESS", "addr.never"),
        "link-local-v6": ("WEBHOOK_URL_UNSAFE_ADDRESS", "addr.never"),
        "6to4-public": ("WEBHOOK_URL_UNSAFE_ADDRESS", "addr.private"),
        "rfc1918": ("WEBHOOK_URL_UNSAFE_ADDRESS", "addr.private"),
        "cgnat": ("WEBHOOK_URL_UNSAFE_ADDRESS", "addr.private"),
        "ula": ("WEBHOOK_URL_UNSAFE_ADDRESS", "addr.private"),
        "hosted-ignores-private-cidrs": ("WEBHOOK_URL_UNSAFE_ADDRESS", "addr.private"),
        "self-loopback-never": ("WEBHOOK_URL_UNSAFE_ADDRESS", "addr.never"),
        "self-nat64-never": ("WEBHOOK_URL_UNSAFE_ADDRESS", "addr.never"),
        "wide-metadata-ip": ("WEBHOOK_URL_UNSAFE_ADDRESS", "addr.never"),
        "wide-loopback": ("WEBHOOK_URL_UNSAFE_ADDRESS", "addr.never"),
        "wide-link-local-v6": ("WEBHOOK_URL_UNSAFE_ADDRESS", "addr.never"),
        "wide-mapped-metadata": ("WEBHOOK_URL_UNSAFE_ADDRESS", "addr.never"),
        "wide-nat64-local-use": ("WEBHOOK_URL_UNSAFE_ADDRESS", "addr.never"),
        "wide-6to4-loopback": ("WEBHOOK_URL_UNSAFE_ADDRESS", "addr.never"),
        "wide-ipv4-compatible-loopback-hex": (
            "WEBHOOK_URL_UNSAFE_ADDRESS",
            "addr.never",
        ),
        "wide-ipv4-translated-loopback": ("WEBHOOK_URL_UNSAFE_ADDRESS", "addr.never"),
        "wide-private-port-refused": (
            "WEBHOOK_URL_UNSAFE_ADDRESS",
            "addr.private_port",
        ),
        "wide-rfc1918-admitted": None,
        "wide-6to4-public-admitted": None,
        "arpa-in-addr": ("WEBHOOK_URL_UNSAFE_ADDRESS", "url.host_internal_name"),
        "arpa-reverse-ipv4": ("WEBHOOK_URL_UNSAFE_ADDRESS", "url.host_internal_name"),
        "arpa-ip6": ("WEBHOOK_URL_UNSAFE_ADDRESS", "url.host_internal_name"),
        "home-arpa": ("WEBHOOK_URL_UNSAFE_ADDRESS", "url.host_internal_name"),
        "crlf-in-path": ("WEBHOOK_URL_INVALID", "url.characters"),
        "lf-in-query": ("WEBHOOK_URL_INVALID", "url.characters"),
        "space-in-path": ("WEBHOOK_URL_INVALID", "url.characters"),
        "space-in-query": ("WEBHOOK_URL_INVALID", "url.characters"),
        "tab-in-path": ("WEBHOOK_URL_INVALID", "url.characters"),
        "nul-in-path": ("WEBHOOK_URL_INVALID", "url.characters"),
        "del-in-path": ("WEBHOOK_URL_INVALID", "url.characters"),
        "non-ascii-path": ("WEBHOOK_URL_INVALID", "url.characters"),
        "non-ascii-query": ("WEBHOOK_URL_INVALID", "url.characters"),
        "bidi-in-path": ("WEBHOOK_URL_INVALID", "url.characters"),
        "ok-percent-encoded": None,
        "ok-idna-host-ascii-path": None,
        "ok-idna": None,
        "ok-public-ipv6": None,
    }

    # EE review 10-22: these addresses, as typed, must be pinned under both hosted and self_hosted_wide, whatever id
    # carries them. Both spellings of the IPv4-compatible loopback, and the IPv4-translated (RFC 6145) prefix.
    EE_REVIEW = {
        "::ffff:0:7f00:1": NEVER,
        "::7f00:1": NEVER,
        "::127.0.0.1": NEVER,
        "::ffff:0:a00:1": PRIVATE,
        "::ffff:0:808:808": PRIVATE,
    }
    EE_REVIEW_URLS = ("https://[::7f00:1]/", "https://[::ffff:0:7f00:1]/")

    def test_ee_review_addresses_are_pinned_in_both_profiles(self):
        f = load("addresses.json")
        seen: dict[str, set] = {a: set() for a in self.EE_REVIEW}
        for c in f["address"]:
            if len(c["answers"]) == 1 and c["answers"][0] in self.EE_REVIEW:
                addr = c["answers"][0]
                with self.subTest(case=c["id"]):
                    self.assertEqual(
                        c["expected"]["answers"][0]["class"], self.EE_REVIEW[addr]
                    )
                    if self.EE_REVIEW[addr] == self.NEVER:
                        self.assertEqual(
                            (c["expected"]["outcome"], c["expected"]["rule"]),
                            ("ssrf_blocked", "addr.never"),
                        )
                seen[addr].add(c["profile"])
        for addr, profiles in seen.items():
            self.assertLessEqual({"hosted", "self_hosted_wide"}, profiles, addr)
        for url in self.EE_REVIEW_URLS:
            got = {c["profile"]: c["expected"] for c in f["url"] if c["url"] == url}
            for profile in ("hosted", "self_hosted_wide"):
                with self.subTest(url=url, profile=profile):
                    e = got[profile]
                    self.assertEqual(
                        (e["allow"], e["code"], e["rule"]),
                        (False, "WEBHOOK_URL_UNSAFE_ADDRESS", "addr.never"),
                    )

    def test_resolved_addresses(self):
        cases = {c["id"]: c for c in load("addresses.json")["address"]}
        for table, profiles in (
            (self.HOSTED, {"hosted"}),
            (self.WIDE, {"self_hosted", "self_hosted_wide"}),
        ):
            for cid, (cls, rule) in table.items():
                with self.subTest(case=cid):
                    c = cases[cid]
                    self.assertIn(c["profile"], profiles)
                    e = c["expected"]
                    self.assertEqual(e["answers"][0]["class"], cls)
                    self.assertEqual(e["rule"], rule)
                    self.assertEqual(e["allow"], rule is None)

    def test_urls(self):
        cases = {c["id"]: c["expected"] for c in load("addresses.json")["url"]}
        for cid, want in self.URLS.items():
            with self.subTest(case=cid):
                e = cases[cid]
                if want is None:
                    self.assertTrue(e["allow"])
                else:
                    self.assertEqual((e["allow"], e["code"], e["rule"]), (False, *want))

    def test_url_characters_rule_matches_its_statement(self):
        # Outside the authority, only U+0021-U+007E; checked right after syntax, so it is the reported failure.
        for c in load("addresses.json")["url"]:
            rest = re.sub(r"^[A-Za-z][A-Za-z0-9+.-]*://[^/?#]*", "", c["url"]).split(
                "#"
            )[0]
            visible = all("!" <= ch <= "~" for ch in rest)
            with self.subTest(case=c["id"]):
                if c["expected"]["rule"] == "url.characters":
                    self.assertFalse(visible)
                elif c["expected"]["rule"] not in ("url.length", "url.syntax"):
                    self.assertTrue(visible)

    def test_tables_are_at_least_as_strict_as_is_global(self):
        # DESIGN.md section 6.2 applies is_global on hosted; the tables alone must never call public what this Python
        # calls non-global, or a core using is_global and a port using only the tables would disagree.
        rules = load("addresses.json")["rules"]

        def nets(items):
            out = []
            for n in items:
                try:
                    out.append(ipaddress.ip_network(n))
                except ValueError:
                    pass
            return out

        never, private = nets(rules["never_cidrs"]), nets(rules["private_cidrs"])
        unicast = ipaddress.ip_network("2000::/3")

        def table_public(ip) -> bool:
            if any(ip in n for n in never + private if n.version == ip.version):
                return False
            return ip.version == 4 or ip in unicast

        special = []
        for consts in (ipaddress._IPv4Constants, ipaddress._IPv6Constants):
            for value in vars(consts).values():
                for n in value if isinstance(value, list) else [value]:
                    if isinstance(n, ipaddress.IPv4Network | ipaddress.IPv6Network):
                        special.append(n)
                    elif isinstance(n, ipaddress.IPv4Address | ipaddress.IPv6Address):
                        special.append(ipaddress.ip_network(n))
        self.assertGreater(len(special), 20)
        rng = random.Random(7526)
        samples = []
        for n in special:
            samples += [n.network_address, n.broadcast_address]
            samples += [n[rng.randrange(n.num_addresses)] for _ in range(50)]
        samples += [ipaddress.IPv4Address(rng.getrandbits(32)) for _ in range(20000)]
        samples += [ipaddress.IPv6Address(rng.getrandbits(128)) for _ in range(5000)]
        samples += [
            ipaddress.IPv6Address((1 << 125) | rng.getrandbits(125))  # 2000::/3
            for _ in range(20000)
        ]
        gaps = sorted(
            {str(ip) for ip in samples if not ip.is_global and table_public(ip)}
        )
        self.assertEqual(
            gaps, [], f"{sys.version.split()[0]}: is_global False, table public"
        )


if __name__ == "__main__":
    unittest.main()
