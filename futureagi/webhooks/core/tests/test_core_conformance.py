"""The Python core against the conformance vectors (DESIGN.md section 22.2): every case in signing-v1.json,
signing.json, schedule.json, addresses.json and payloads.json, fed to ``webhooks.core``'s own functions.

Standard library only, no Django. It never imports ``conformance/tools`` (the reference rules that built the files).
Run from ``futureagi/``:
    python -m unittest webhooks.core.tests.test_core_conformance
"""

from __future__ import annotations

import base64
import hashlib
import json
import unittest
from pathlib import Path

from webhooks.core import (
    CONFORMANCE_VERSION,
    addresses,
    fanout,
    ipclass,
    payloads,
    runevent,
    schedule,
    signing,
)

CONF = Path(__file__).resolve().parents[1] / "conformance"

# The vectors only use names under these ICANN suffixes; the hosted adapter injects the real Public Suffix List.
TEST_PSL = frozenset(
    {
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
)


def psl_for_tests(host: str) -> str | None:
    labels = host.split(".")
    for i in range(len(labels)):
        cand = ".".join(labels[i:])
        if cand in TEST_PSL:
            return cand
    return None


def load(name: str) -> dict:
    data = json.loads((CONF / name).read_text(encoding="utf-8"))
    assert data["conformance_version"] == CONFORMANCE_VERSION, name
    return data


def raw(b64: str) -> bytes:
    return base64.b64decode(b64)


class SigningVectors(unittest.TestCase):
    def test_signing_v1(self):
        v = load("signing-v1.json")
        body = raw(v["body_bytes_b64"])
        self.assertEqual(hashlib.sha256(body).hexdigest(), v["sha256"])
        self.assertEqual(
            signing.sign(v["secret_a"], v["msg_id"], v["timestamp"], body),
            v["signature_a"],
        )
        self.assertEqual(
            signing.sign(v["secret_b"], v["msg_id"], v["timestamp"], body),
            v["signature_b"],
        )
        hdr = signing.signature_header(
            [v["secret_a"], v["secret_b"]], v["msg_id"], v["timestamp"], body
        )
        self.assertEqual(hdr, v["overlap_header"].removeprefix("webhook-signature: "))
        secrets = {"A": v["secret_a"], "B": v["secret_b"], "C": v["secret_c"]}
        # The header a case's receiver sees: the overlap header when the case says so, else the single signature
        # the file lists for that secret; secret C has none, so its receiver sees what was sent (the overlap).
        single = {"A": "v1," + v["signature_a"], "B": "v1," + v["signature_b"]}
        for case in v["must_pass"] + v["must_fail"]:
            with self.subTest(
                case={k: x for k, x in case.items() if k != "body_bytes_b64"}
            ):
                b = raw(case["body_bytes_b64"]) if "body_bytes_b64" in case else body
                sent = (
                    hdr
                    if case.get("header") == "overlap"
                    else single.get(case["secret"], hdr)
                )
                headers = {
                    "webhook-id": v["msg_id"],
                    "webhook-timestamp": str(v["timestamp"]),
                    "webhook-signature": sent,
                }
                got = signing.verify(
                    secrets[case["secret"]], headers, b, case.get("now", v["timestamp"])
                )
                self.assertEqual(got == "ok", case in v["must_pass"])

    def test_secret_format(self):
        for case in load("signing.json")["secret_format"]:
            with self.subTest(case=case["id"]):
                if case["valid"]:
                    self.assertEqual(
                        signing.secret_key(case["secret"]).hex(), case["key_hex"]
                    )
                else:
                    with self.assertRaises(signing.SecretInvalid) as cm:
                        signing.secret_key(case["secret"])
                    self.assertEqual(cm.exception.args[0], case["reason"])

    def test_sign(self):
        for case in load("signing.json")["sign"]:
            with self.subTest(case=case["id"]):
                body = raw(case["body_bytes_b64"])
                exp = case["expected"]
                sigs = [
                    signing.sign(s, case["msg_id"], case["timestamp"], body)
                    for s in case["secrets"]
                ]
                self.assertEqual(sigs, exp["signatures"])
                hdr = signing.signature_header(
                    case["secrets"], case["msg_id"], case["timestamp"], body
                )
                self.assertEqual(hdr, exp["webhook_signature"])
                a = case["attempt"]
                headers = signing.attempt_headers(
                    msg_id=case["msg_id"],
                    timestamp=case["timestamp"],
                    signature=hdr,
                    event_type=a["event_type"],
                    delivery_id=a["delivery_id"],
                    attempt=a["n"],
                    sequence=a["sequence"],
                    test=a["test"],
                )
                self.assertEqual(list(headers.items()), list(exp["headers"].items()))

    def test_verify(self):
        for case in load("signing.json")["verify"]:
            with self.subTest(case=case["id"]):
                got = signing.verify(
                    case["secret"],
                    case["headers"],
                    raw(case["body_bytes_b64"]),
                    case["now"],
                )
                self.assertEqual(got, case["expected"])


class ScheduleVectors(unittest.TestCase):
    def test_next_action(self):
        for case in load("schedule.json")["next_action"]:
            with self.subTest(case=case["id"]):
                self.assertEqual(
                    schedule.next_action(case["delivery"], case["attempts"]),
                    case["expected"],
                )

    def test_endpoint_health(self):
        for case in load("schedule.json")["endpoint_health"]:
            with self.subTest(case=case["id"]):
                self.assertEqual(
                    schedule.endpoint_health(case["attempts"], case["now_ms"]),
                    case["expected"],
                )


class AddressVectors(unittest.TestCase):
    def setUp(self):
        self.data = load("addresses.json")
        self.profiles = {
            k: ipclass.Profile.from_mapping(v) for k, v in self.data["profiles"].items()
        }

    def test_url(self):
        for case in self.data["url"]:
            with self.subTest(case=case["id"]):
                got = addresses.url_decision(
                    case["url"],
                    self.profiles[case["profile"]],
                    psl_for_tests,
                    case.get("policy"),
                )
                self.assertEqual(got.as_dict(), case["expected"])

    def test_address(self):
        for case in self.data["address"]:
            with self.subTest(case=case["id"]):
                got = ipclass.address_decision(
                    case["answers"], case["port"], self.profiles[case["profile"]]
                )
                self.assertEqual(got.as_dict(), case["expected"])

    def test_redirect(self):
        for case in self.data["redirect"]:
            with self.subTest(case=case["id"]):
                self.assertEqual(
                    addresses.redirect_decision(case.get("location")), case["expected"]
                )

    def test_tables_match_the_rules_block(self):
        r = self.data["rules"]
        self.assertEqual(list(ipclass.NEVER_CIDRS), r["never_cidrs"])
        self.assertEqual(
            list(ipclass.PRIVATE_CIDRS),
            [c for c in r["private_cidrs"] if not c.startswith("every ")],
        )
        self.assertEqual(sorted(addresses.METADATA_HOSTNAMES), r["metadata_names"])


class PayloadVectors(unittest.TestCase):
    def setUp(self):
        self.data = load("payloads.json")

    def test_build(self):
        for case in self.data["build"]:
            with self.subTest(case=case["id"]):
                inp = case["input"]
                if case["builder"] == "deployment.run":
                    notice = json.loads(raw(inp["notice_b64"]))
                    ev = runevent.build_deployment_run(
                        notice,
                        inp["headers"],
                        inp["fa"],
                        inp["dep_id"],
                        test=inp.get("test", False),
                    )
                elif case["builder"] == "prompt.version.created":
                    ev = payloads.build_prompt_version_created(inp, case["variant"])
                else:
                    ev = payloads.build_prompt_label_changed(inp, case["variant"])
                body = payloads.render(ev)
                exp = case["expected"]
                self.assertEqual(body.decode("utf-8"), exp["body_text"])
                self.assertEqual(body, raw(exp["body_bytes_b64"]))
                self.assertEqual(
                    (len(body), hashlib.sha256(body).hexdigest()),
                    (exp["bytes"], exp["sha256"]),
                )

    def test_variant_body_matches_the_builders(self):
        for case in self.data["build"]:
            if case["builder"] == "deployment.run":
                continue
            with self.subTest(case=case["id"]):
                inp = dict(case["input"])
                body = payloads.variant_body(
                    event_id=inp.pop("event_id"),
                    type_=case["builder"],
                    timestamp="unused",
                    organization_id=inp.pop("organization_id"),
                    workspace_id=inp.pop("workspace_id"),
                    data={},
                    builder_input=inp,
                    variant=case["variant"],
                    test=inp.pop("test", False),
                )
                self.assertEqual(body, raw(case["expected"]["body_bytes_b64"]))

    def test_deployment_name_header(self):
        for case in self.data["deployment_name_header"]:
            with self.subTest(case=case["id"]):
                exp = case["expected"]
                if "name" in exp:
                    self.assertEqual(
                        runevent.decode_deployment_name(case["value"]), exp["name"]
                    )
                else:
                    with self.assertRaises(runevent.InvalidHeader) as cm:
                        runevent.decode_deployment_name(case["value"])
                    self.assertEqual(cm.exception.args[0], exp["field"])

    def test_fan_out(self):
        f = self.data["fan_out"]
        self.assertEqual(
            fanout.fan_out(f["input"]["events"], f["input"]["endpoints"]), f["expected"]
        )


if __name__ == "__main__":
    unittest.main()
