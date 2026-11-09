"""WH-0 self-checks of payloads.json. Standard library only, no Django (DESIGN.md section 22.1).

Bodies are canonical and bounded, numbers are written as JavaScript writes them, commit messages are scrubbed and then
cut, customer keys are in JavaScript order, the X-Omega-Deployment-Name rule agrees with urllib, and fan-out sequences
are gap-free. Run with: python3 -I -m unittest discover -s futureagi/webhooks/core/tests -p 'test_conformance_vectors*.py'
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import unittest
import urllib.parse
from pathlib import Path

CONF = Path(__file__).resolve().parents[1] / "conformance"


def load(name: str) -> dict:
    return json.loads((CONF / name).read_text(encoding="utf-8"))


def raw(b64: str) -> bytes:
    return base64.b64decode(b64)


class HeaderDecoding(unittest.TestCase):
    def test_deployment_name_header_against_urllib(self):
        # An independent reading of payloads.json rules.deployment_name_header.
        for c in load("payloads.json")["deployment_name_header"]:
            with self.subTest(case=c["id"]):
                v = c["value"]
                try:
                    if not all(" " <= ch <= "~" for ch in v):
                        raise ValueError("not visible ASCII")
                    want = {"name": urllib.parse.unquote_to_bytes(v).decode("utf-8")}
                except ValueError:
                    want = {
                        "status": 400,
                        "code": "invalid_header",
                        "field": "X-Omega-Deployment-Name",
                    }
                self.assertEqual(c["expected"], want)


class Payloads(unittest.TestCase):
    def test_bodies_are_canonical_and_bounded(self):
        for c in load("payloads.json")["build"]:
            with self.subTest(case=c["id"]):
                body = raw(c["expected"]["body_bytes_b64"])
                self.assertEqual(body.decode("utf-8"), c["expected"]["body_text"])
                self.assertEqual(
                    hashlib.sha256(body).hexdigest(), c["expected"]["sha256"]
                )
                self.assertLessEqual(len(body), 64 * 1024)
                doc = json.loads(body)
                if c["id"] != "b8-context-numbers":
                    # No non-integral or huge numbers here, so Python's own compact dump is the canonical form.
                    again = json.dumps(
                        doc, ensure_ascii=False, separators=(",", ":")
                    ).encode()
                    self.assertEqual(again, body)
                head = [
                    "id",
                    "type",
                    "api_version",
                    "timestamp",
                    "organization_id",
                    "workspace_id",
                ]
                self.assertEqual(list(doc)[:6], head)
                self.assertEqual(list(doc)[-1], "data")

    def test_numbers_are_written_as_javascript_writes_them(self):
        # Typed in from Node's JSON.stringify for the same values; Python's repr would give 1e-05, 1.5e-07,
        # 1.152921504606847e+18 and -0.0.
        case = next(
            c for c in load("payloads.json")["build"] if c["id"] == "b8-context-numbers"
        )
        self.assertIn(
            '"context":{"score":1,"ratio":2.5,"n":-3,"ok":true,"tiny":0.00001,"tinier":1.5e-7,"big":1e+21,'
            '"pow60":1152921504606847000,"bigint":12345678901234567000,"negzero":0,"sum":0.30000000000000004}',
            case["expected"]["body_text"],
        )

    def test_commit_messages_are_scrubbed_then_cut(self):
        shape = re.compile(r"sk-[A-Za-z0-9_-]{8,}|whsec_[A-Za-z0-9+/=]{8,}")
        seen_mask = 0
        for c in load("payloads.json")["build"]:
            if not c["builder"].startswith("prompt."):
                continue
            with self.subTest(case=c["id"]):
                body = json.loads(raw(c["expected"]["body_bytes_b64"]))
                key = (
                    "version"
                    if c["builder"] == "prompt.version.created"
                    else "to_version"
                )
                src, out = c["input"][key], body["data"][key]
                if src is None or src.get("commit_message") is None:
                    continue
                limit = 200 if c["variant"]["payload_mode"] == "thin" else 1000
                scrubbed = shape.sub("[redacted]", src["commit_message"])
                self.assertEqual(out["commit_message"], scrubbed[:limit])
                self.assertEqual(
                    out.get("commit_message_truncated", False), len(scrubbed) > limit
                )
                self.assertIsNone(shape.search(out["commit_message"]))
                seen_mask += "[red" in out["commit_message"]
        self.assertGreaterEqual(seen_mask, 2)
        straddle = next(
            c
            for c in load("payloads.json")["build"]
            if c["id"] == "pvc-thin-secret-straddles-cut"
        )
        text = json.loads(raw(straddle["expected"]["body_bytes_b64"]))["data"][
            "version"
        ]["commit_message"]
        self.assertEqual(text, "y" * 196 + "[red")
        self.assertNotIn("sk-", text)

    def test_context_keys_are_in_javascript_order(self):
        case = next(
            c
            for c in load("payloads.json")["build"]
            if c["id"] == "b8-context-key-order"
        )
        self.assertIn(
            '"context":{"2":"two","10":"ten","4294967294":"largest index","b":1,"a":true,"01":"zero-one",'
            '"4294967295":"not an index"}',
            case["expected"]["body_text"],
        )

    def test_fan_out_sequences_are_gap_free_per_endpoint(self):
        f = load("payloads.json")["fan_out"]
        start = {e["id"]: e.get("next_seq", 1) for e in f["input"]["endpoints"]}
        seen: dict[str, list[int]] = {}
        for ev in f["expected"]:
            ids = [d["endpoint_id"] for d in ev["deliveries"]]
            self.assertEqual(
                len(ids), len(set(ids)), "an endpoint never gets the same event twice"
            )
            for d in ev["deliveries"]:
                if ev["event_id"].startswith("evt_test_"):
                    self.assertIsNone(d["seq"])
                else:
                    seen.setdefault(d["endpoint_id"], []).append(d["seq"])
        for ep, seqs in seen.items():
            self.assertEqual(seqs, list(range(start[ep], start[ep] + len(seqs))), ep)


if __name__ == "__main__":
    unittest.main()
