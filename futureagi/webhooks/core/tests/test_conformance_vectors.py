"""WH-0 self-checks of the conformance vectors. Standard library only, no Django (DESIGN.md section 22.1).

These tests check the vector files themselves: that they are exactly what the builder produces, that every
byte-exact value the design documents print is what the files hold, and that an implementation written
independently of ``tools/whconf/ref_*.py`` (the ten-line receiver of DESIGN.md section 5.4, the delay tables of section
7.3) agrees with them. WH-1a's core runs the same files against its own modules.

Run with the standard library only (no Django, no jsonschema); the addresses and payloads checks are in the two
``test_conformance_vectors_*.py`` files next to this one:
    python3 -I -m unittest discover -s futureagi/webhooks/core/tests -p 'test_conformance_vectors*.py'
``test_conformance_schemas*.py`` need jsonschema; FA's environment has it, so CI runs all of them.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import importlib.util
import json
import sys
import unittest
from pathlib import Path

CONF = Path(__file__).resolve().parents[1] / "conformance"
VERSION = "1.4.0"


def load(name: str) -> dict:
    return json.loads((CONF / name).read_text(encoding="utf-8"))


def raw(b64: str) -> bytes:
    return base64.b64decode(b64)


def recipe_verify(
    secret: str, headers: dict, body: bytes, now: int, tolerance_s: int = 300
) -> bool:
    """DESIGN.md section 5.4 recipe 3, with only the clock and header-case handling added."""
    h = {k.lower(): v for k, v in headers.items()}
    key = base64.b64decode(secret.removeprefix("whsec_"))
    msg_id, ts = h["webhook-id"], h["webhook-timestamp"]
    if abs(now - int(ts)) > tolerance_s:
        return False
    mac = hmac.new(key, f"{msg_id}.{ts}.".encode() + body, hashlib.sha256).digest()
    expected = base64.b64encode(mac).decode()
    return any(
        s.startswith("v1,") and hmac.compare_digest(s[3:], expected)
        for s in h["webhook-signature"].split(" ")
    )


def load_builder():
    """tools/vectors.py, loaded so that neither sys.path nor sys.modules keeps ``whconf`` afterwards: nothing else in
    the test process (in CI, all of FA) can import the reference rules by accident."""
    saved_path, saved_modules = list(sys.path), set(sys.modules)
    try:
        spec = importlib.util.spec_from_file_location(
            "wh_vectors", CONF / "tools" / "vectors.py"
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        sys.path[:] = saved_path
        for name in set(sys.modules) - saved_modules:
            if name == "whconf" or name.startswith("whconf."):
                del sys.modules[name]


class BuilderTests(unittest.TestCase):
    def test_files_match_the_builder_byte_for_byte(self):
        mod = load_builder()
        self.assertNotIn(str(CONF / "tools"), sys.path)
        self.assertNotIn("whconf", sys.modules)
        files = mod.build()
        files["manifest.json"] = mod.manifest(files)
        for rel, data in files.items():
            with self.subTest(file=rel):
                self.assertEqual((CONF / rel).read_bytes(), data)

    def test_manifest_covers_every_file_with_its_sha256(self):
        manifest = load("manifest.json")["files"]
        on_disk = {
            p.relative_to(CONF).as_posix()
            for p in CONF.rglob("*")
            if p.is_file()
            and "tools" not in p.relative_to(CONF).parts
            and p.name not in ("manifest.json", "README.md")
            and "__pycache__" not in p.parts
        }
        self.assertEqual(set(manifest), on_disk)
        for rel, entry in manifest.items():
            data = (CONF / rel).read_bytes()
            self.assertEqual(
                entry,
                {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()},
                rel,
            )

    def test_every_vector_file_carries_the_conformance_version(self):
        for name in (
            "signing.json",
            "signing-v1.json",
            "schedule.json",
            "addresses.json",
            "payloads.json",
            "manifest.json",
            "b8/fixtures.json",
            "b8/delivered/run-completed.json",
        ):
            self.assertEqual(load(name)["conformance_version"], VERSION, name)


class DocumentFacts(unittest.TestCase):
    """The values DESIGN.md section 5.6 and B8-EVENT-CONTRACT.md section 12 print, typed in here by hand."""

    def test_design_5_6(self):
        v = load("signing-v1.json")
        body = raw(v["body_bytes_b64"])
        self.assertEqual(len(body), 354)
        self.assertEqual(
            hashlib.sha256(body).hexdigest(),
            "ac7e28475e9235c1b98d946d2a765d7fa2a23cfef772e04595e9be8c80fd11ee",
        )
        self.assertEqual(
            v["signature_a"], "h44L1CBrM2Suy84SL8vIpjMw9eJ7NfkC+Avik5ObsHc="
        )
        self.assertEqual(
            v["signature_b"], "vmoA63RoGzXfAx8e81YfT8hMnQHugHS2+xXSTUN1Vcw="
        )
        self.assertEqual(v["timestamp"], 1792663200)
        self.assertEqual(
            v["secret_a"], "whsec_AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8="
        )
        self.assertEqual(base64.b64decode(v["secret_a"][6:]), bytes(range(0, 32)))
        self.assertEqual(base64.b64decode(v["secret_b"][6:]), bytes(range(32, 64)))
        self.assertEqual(base64.b64decode(v["secret_c"][6:]), bytes(range(64, 96)))
        r1 = v["rev1_reproduction"]
        self.assertEqual(r1["bytes"], 352)
        self.assertTrue(
            r1["sha256"].startswith("e7fb8b71") and r1["sha256"].endswith("eca59")
        )
        self.assertTrue(
            r1["signature_a"].startswith("3FNJPSXj")
            and r1["signature_a"].endswith("UCI=")
        )
        self.assertTrue(
            r1["signature_b"].startswith("iAGgdMsk")
            and r1["signature_b"].endswith("C/Y=")
        )

    def test_b8_notice_fixtures_are_the_contract_bytes(self):
        for name, size, digest in [
            (
                "b8/valid/run-completed-api-whep.json",
                745,
                "040256445075186f045ed16266ef7cc2b4a3678ce8b2be6892060dfd4be0f0f1",
            ),
            (
                "b8/valid/run-cancelled-queued-cron.json",
                586,
                "f4f3c7c27a8fbcea84de3480c52d0be96c9bfaa77666f23c3ddf24609670c469",
            ),
        ]:
            data = (CONF / name).read_bytes()
            self.assertEqual(
                (len(data), hashlib.sha256(data).hexdigest()), (size, digest), name
            )

    def test_b8_delivered_event(self):
        d = load("b8/delivered/run-completed.json")
        body = raw(d["body_bytes_b64"])
        self.assertEqual(len(body), 1415)
        self.assertEqual(
            hashlib.sha256(body).hexdigest(),
            "4c9fa8f5522c936a8308bf01decbd1568966efbb1deacbc03ad6bb9211a1b7ed",
        )
        self.assertEqual(
            d["webhook_signature"], "v1,HYzAQwCNM0KEuW/OEXXlN/vKbkPawb4S6CuIDuyRXho="
        )
        self.assertEqual(d["timestamp"], 1792664415)
        self.assertTrue(recipe_verify(d["secret"], d["headers"], body, now=1792664415))
        built = {c["id"]: c for c in load("payloads.json")["build"]}["b8-12-5"][
            "expected"
        ]
        self.assertEqual(built["body_bytes_b64"], d["body_bytes_b64"])

    def test_raw_fixtures_have_no_trailing_newline(self):
        for p in sorted((CONF / "b8").glob("*valid/*.json")):
            self.assertFalse(p.read_bytes().endswith(b"\n"), p.name)
            self.assertNotIn(b"\r", p.read_bytes(), p.name)


class SigningAgainstTheRecipe(unittest.TestCase):
    def test_sign_cases(self):
        for c in load("signing.json")["sign"]:
            with self.subTest(case=c["id"]):
                body = raw(c["body_bytes_b64"])
                expected = []
                for s in c["secrets"]:
                    key = base64.b64decode(s.removeprefix("whsec_"))
                    mac = hmac.new(
                        key,
                        f"{c['msg_id']}.{c['timestamp']}.".encode() + body,
                        hashlib.sha256,
                    )
                    expected.append(base64.b64encode(mac.digest()).decode())
                self.assertEqual(c["expected"]["signatures"], expected)
                hdr = c["expected"]["headers"]
                self.assertEqual(
                    hdr["webhook-signature"], " ".join("v1," + s for s in expected)
                )
                self.assertEqual(hdr["webhook-id"], c["msg_id"])
                self.assertEqual(hdr["webhook-timestamp"], str(c["timestamp"]))
                self.assertEqual(hdr["content-type"], "application/json; charset=utf-8")
                self.assertEqual(hdr["user-agent"], "FutureAGI-Webhooks/1.0")
                if c["attempt"]["test"]:
                    self.assertEqual(hdr.get("X-FutureAGI-Test"), "1")
                    self.assertNotIn("X-FutureAGI-Sequence", hdr)
                else:
                    self.assertNotIn("X-FutureAGI-Test", hdr)
                    self.assertEqual(
                        hdr["X-FutureAGI-Sequence"], str(c["attempt"]["sequence"])
                    )
                for s in c["secrets"]:
                    self.assertTrue(recipe_verify(s, hdr, body, now=c["timestamp"]))

    def test_verify_cases_agree_with_the_recipe(self):
        for c in load("signing.json")["verify"]:
            with self.subTest(case=c["id"]):
                h = {k.lower(): v for k, v in c["headers"].items()}
                body = raw(c["body_bytes_b64"])
                if "webhook-signature" not in h or not h["webhook-timestamp"].isdigit():
                    self.assertIn(
                        c["expected"], ("missing_headers", "invalid_timestamp")
                    )
                    continue
                self.assertEqual(
                    recipe_verify(c["secret"], c["headers"], body, c["now"]),
                    c["expected"] == "ok",
                )

    def test_negative_design_vectors(self):
        v = load("signing-v1.json")
        body = raw(v["body_bytes_b64"])
        hdr = {
            "webhook-id": v["msg_id"],
            "webhook-timestamp": str(v["timestamp"]),
            "webhook-signature": "v1," + v["signature_a"],
        }
        t = v["timestamp"]
        self.assertTrue(recipe_verify(v["secret_a"], hdr, body, t + 300))
        self.assertFalse(recipe_verify(v["secret_a"], hdr, body, t + 301))
        self.assertFalse(recipe_verify(v["secret_c"], hdr, body, t))
        tampered = bytearray(body)
        tampered[10] ^= 1
        self.assertEqual(raw(v["must_fail"][0]["body_bytes_b64"]), bytes(tampered))
        self.assertFalse(recipe_verify(v["secret_a"], hdr, bytes(tampered), t))
        overlap = dict(hdr, **{"webhook-signature": v["overlap_header"]})
        self.assertTrue(recipe_verify(v["secret_a"], overlap, body, t))
        self.assertTrue(recipe_verify(v["secret_b"], overlap, body, t))

    def test_secret_format(self):
        for c in load("signing.json")["secret_format"]:
            with self.subTest(case=c["id"]):
                if c["valid"]:
                    self.assertEqual(
                        len(base64.b64decode(c["secret"][6:], validate=True)),
                        c["key_bytes"],
                    )
                    self.assertTrue(24 <= c["key_bytes"] <= 64)


class ScheduleAgainstTheDesignTables(unittest.TestCase):
    STANDARD = [
        0,
        5,
        30,
        120,
        600,
        1800,
        3600,
        7200,
        14400,
        28800,
        43200,
        43200,
        43200,
        43200,
    ]  # DESIGN 7.3
    REJECTED = [0, 5, 60, 600, 2700]

    def test_tables(self):
        r = load("schedule.json")["rules"]["classes"]
        self.assertEqual(
            [r["standard_delays_s"][str(n)] for n in range(2, 15)], self.STANDARD[1:]
        )
        self.assertEqual(
            [r["rejected_delays_s"][str(n)] for n in range(2, 6)], self.REJECTED[1:]
        )
        self.assertEqual(r["max_attempts"], {"standard": 14, "rejected": 5})
        # 63 h 42 min 35 s before attempt 14; the DESIGN 7.3 table rounds it to "63 h 43 min".
        self.assertEqual(sum(self.STANDARD), 63 * 3600 + 42 * 60 + 35)
        self.assertEqual(sum(self.REJECTED), 56 * 60 + 5)  # "about 56 min"

    def test_each_retry_is_the_jittered_table_value(self):
        for c in load("schedule.json")["next_action"]:
            e = c["expected"]
            if e["action"] != "retry":
                continue
            with self.subTest(case=c["id"]):
                n = e["next_attempt"]
                table = (
                    self.STANDARD if e["retry_class"] == "standard" else self.REJECTED
                )
                self.assertEqual(e["base_delay_s"], table[n - 1])
                seed = c["delivery"]["jitter_seed"]
                u32 = int.from_bytes(
                    hashlib.sha256(f"{seed}:{n}".encode()).digest()[:4], "big"
                )
                permille = 800 + (u32 * 401) // 2**32
                self.assertEqual(e["jitter_permille"], permille)
                self.assertTrue(800 <= permille <= 1200)
                self.assertGreaterEqual(e["delay_ms"], e["base_delay_s"] * permille)
                self.assertEqual(
                    e["retry_at_ms"], c["attempts"][-1]["settled_at_ms"] + e["delay_ms"]
                )

    def test_wt08_windows_and_terminal_cases(self):
        cases = {c["id"]: c["expected"] for c in load("schedule.json")["next_action"]}
        windows = {
            "standard-500-x1": (4000, 6000),
            "standard-500-x2": (24000, 36000),
            "standard-500-x3": (96000, 144000),
        }
        for cid, (lo, hi) in windows.items():
            self.assertTrue(lo <= cases[cid]["delay_ms"] <= hi, cid)
        self.assertEqual(cases["standard-500-x14"]["final_outcome"], "max_attempts")
        self.assertEqual(cases["rejected-400-x5"]["final_outcome"], "max_attempts")
        self.assertEqual(cases["413"]["final_outcome"], "not_retried_413")
        self.assertEqual(cases["410"]["disabled_reason"], "gone_410")
        self.assertEqual(cases["400-then-503"]["base_delay_s"], 30)
        self.assertEqual(cases["429-retry-after-7"]["delay_ms"], 7000)
        self.assertEqual(cases["429-retry-after-7200"]["delay_ms"], 3_600_000)
        self.assertEqual(
            cases["max-age-71h59m58s"]["final_outcome"], "max_age_exceeded"
        )


if __name__ == "__main__":
    unittest.main()
