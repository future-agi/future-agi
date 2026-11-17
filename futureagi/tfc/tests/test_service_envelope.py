"""Language-neutral fe1 vectors and request replay behavior."""

import copy
import json
import pathlib
import unittest
from concurrent.futures import ThreadPoolExecutor

from tfc.envelope_nonces import MemoryNonceStore
from tfc.service_envelope import (
    KINDS,
    PAIR_SETTINGS,
    EnvelopeRejected,
    derive_grant,
    issue_grant,
    registry_equal,
    sign_request,
    verify_grant,
    verify_liveness,
    verify_request,
)

VECTORS = json.loads(
    (
        pathlib.Path(__file__).resolve().parents[3]
        / "api_contracts/service_envelope/v1/vectors.json"
    ).read_text()
)
KEYS = {
    (*pair, "k1"): VECTORS["test_only_pair_secret"].encode() for pair in PAIR_SETTINGS
}
NOW = 1800000000
ORG = "11111111-1111-4111-8111-111111111111"


def verify(v, **overrides):
    c = v["claims"]
    data = {"keys": KEYS, "audience": c["aud"], "now": v["now"]}
    if c["mode"] == "grant":
        data.update(public_id=v["public_id"], row=v["row"])
        data.update(overrides)
        return verify_grant(data.pop("token", v["token"]), **data)
    data.update(
        method=c["method"],
        route=c["route"],
        body=v["body"].encode(),
        nonces=MemoryNonceStore(epoch=NOW - 10),
        grant=v["accompanying_grant"],
    )
    data.update(overrides)
    if isinstance(data["body"], str):
        data["body"] = data["body"].encode()
    return verify_request(data.pop("token", v["token"]), **data)


class TestServiceEnvelope(unittest.TestCase):
    def test_all_valid_vectors(self):
        for v in VECTORS["valid_macs"]:
            with self.subTest(v["name"]):
                self.assertEqual(verify(v), v["claims"])
                if v["claims"]["mode"] == "grant":
                    c = v["claims"]
                    token, claims = issue_grant(
                        keys=KEYS,
                        kind=c["kind"],
                        org=c["org"],
                        workspace=c["workspace"],
                        user=c["user"],
                        sub=c["sub"],
                        ttl=int(c["exp"]) - int(c["iat"]),
                        now=int(c["iat"]),
                        identifier=c["id"],
                    )
                    self.assertEqual(token, v["token"])
                    self.assertEqual(claims, c)
                    if v["row"]:
                        self.assertEqual(derive_grant(v["row"], keys=KEYS), token)

    def test_all_rejection_vectors(self):
        valid = {v["name"]: v for v in VECTORS["valid_macs"]}
        for v in VECTORS["rejections"]:
            with self.subTest(v["name"]), self.assertRaises(EnvelopeRejected):
                verify(
                    valid[v["source"]],
                    **{k: x for k, x in v.items() if k not in {"name", "source"}},
                )

    def test_registry_vectors(self):
        for v in VECTORS["registry_cases"]:
            with self.subTest(v["name"]):

                def check(v=v):
                    c = v["claims"]
                    if KINDS[c["kind"]][4]:
                        registry_equal(c, v["row"])
                    verify_liveness(c, v["liveness"])

                if v["expected"]:
                    check()
                else:
                    with self.assertRaises(EnvelopeRejected):
                        check()

    def test_concurrent_replay_accepts_exactly_once(self):
        v = next(v for v in VECTORS["valid_macs"] if v["name"] == "fa-relay-svc")
        store = MemoryNonceStore(epoch=NOW - 10)

        def call(_):
            try:
                verify(v, nonces=store)
                return 1
            except EnvelopeRejected:
                return 0

        with ThreadPoolExecutor(max_workers=8) as pool:
            self.assertEqual(sum(pool.map(call, range(8))), 1)

    def test_future_timestamp_cannot_replay_after_quick_restart(self):
        token = self.sign(NOW + 4)
        self.accept(token, MemoryNonceStore(epoch=NOW - 10), NOW)
        with self.assertRaises(EnvelopeRejected):
            self.accept(token, MemoryNonceStore(epoch=NOW + 1), NOW + 1)

    def test_first_skew_seconds_refused_then_fresh_retry_passes(self):
        store = MemoryNonceStore(epoch=NOW)
        with self.assertRaises(EnvelopeRejected):
            self.accept(self.sign(NOW), store, NOW)
        self.accept(self.sign(NOW + 6), store, NOW + 6)

    def test_nonce_store_full_fails_closed(self):
        store = MemoryNonceStore(epoch=NOW - 10, capacity=1)
        self.accept(self.sign(NOW), store, NOW)
        with self.assertRaises(EnvelopeRejected):
            self.accept(self.sign(NOW), store, NOW)
        self.accept(self.sign(NOW + 61), store, NOW + 61)

    def test_grant_redaction_derivation_survives_revocation_and_expiry(self):
        v = VECTORS["valid_macs"][0]
        row = copy.deepcopy(v["row"])
        row["revoked_at"] = NOW
        self.assertEqual(derive_grant(row, keys=KEYS), v["token"])
        with self.assertRaises(EnvelopeRejected):
            verify(v, row=row)
        with self.assertRaises(EnvelopeRejected):
            verify(v, now=int(v["claims"]["exp"]) + 1)

    def test_grant_verifier_does_not_accept_admin_pair_key(self):
        c = VECTORS["valid_macs"][2]["claims"]
        bad_keys = {
            **KEYS,
            ("fa", "fa-eval-grant", "k1"): b"test_only_admin_secret_wrong_pair_000000",
        }
        token, _ = issue_grant(
            keys=bad_keys,
            kind="playground",
            org=c["org"],
            user=c["user"],
            sub=c["sub"],
            ttl=300,
            now=NOW,
        )
        with self.assertRaises(EnvelopeRejected):
            verify_grant(token, keys=KEYS, audience="fa-eval-grant", now=NOW)

    @staticmethod
    def sign(now):
        return sign_request(
            keys=KEYS,
            issuer="fa",
            audience="fa-relay",
            method="POST",
            route="/call-websocket/",
            body=b"{}",
            org=ORG,
            via="svc",
            scope="relay",
            now=now,
        )

    @staticmethod
    def accept(token, store, now):
        return verify_request(
            token,
            keys=KEYS,
            audience="fa-relay",
            method="POST",
            route="/call-websocket/",
            body=b"{}",
            nonces=store,
            now=now,
        )
