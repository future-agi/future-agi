"""fe1r: a Console run's run authorization (fi-security 1d8b0ce0; wire record
lane-status gwc2197-CONSOLE-RUN-SIGN-WIRE-1114.md section 2).

fe1c's 20 fields with version "fe1r" and, as field 21, the agent service
deployment the authorization was handed to. Kind console-run-auth, audience
fa-console-run-signer, scope console-run, at most 12 hours, registered. Only
FA's console-run signer verifies it; the agent service may read it to refuse
early, never to authorize.
"""

import base64
import unittest

from tfc.service_envelope import (
    KINDS,
    PAIR_SETTINGS,
    EnvelopeRejected,
    derive_grant,
    issue_grant,
    verify_grant,
)

SECRET = b"test-only-run-authorization-secret-not-real"
KEYS = {("fa", "fa-console-run-signer", "k1"): SECRET}
ORG = "11111111-1111-4111-8111-111111111111"
WORKSPACE = "55555555-5555-4555-8555-555555555555"
USER = "22222222-2222-4222-8222-222222222222"
SUB = "chat:33333333-3333-4333-8333-333333333333"
IAT = 1700000000
ROW_FIELDS = (
    "kind",
    "kid",
    "aud",
    "org",
    "workspace",
    "user",
    "via",
    "sub",
    "scope",
    "iat",
    "exp",
)


def mint(**overrides):
    data = {
        "keys": KEYS,
        "kind": "console-run-auth",
        "org": ORG,
        "workspace": WORKSPACE,
        "sub": SUB,
        "user": USER,
        "ttl": 43200,
        "now": IAT,
        "identifier": "a" * 32,
        "models": ["gpt-4o-mini", "anthropic/claude-sonnet-5.5"],
        "ee": "ee-prod-1",
    }
    data.update(overrides)
    return issue_grant(**data)


def registry_row(claims):
    row = {field: claims[field] for field in ROW_FIELDS}
    row.update(jti=claims["id"], revoked_at=None, models=claims["models"])
    row.update(ee=claims["ee"])
    return row


def verify(token, claims, row=None, now=IAT + 10):
    return verify_grant(
        token,
        keys=KEYS,
        audience="fa-console-run-signer",
        row=registry_row(claims) if row is None else row,
        now=now,
    )


class TestRunAuthorization(unittest.TestCase):
    def test_kind_and_pair(self):
        aud, _, scope, via, registered, ttl = KINDS["console-run-auth"]
        self.assertEqual(
            (aud, scope, via, registered, ttl),
            ("fa-console-run-signer", "console-run", "person", True, 43200),
        )
        self.assertEqual(PAIR_SETTINGS[("fa", "fa-console-run-signer")], "SECRET_KEY")

    def test_the_wire_is_the_21_lines_of_the_record(self):
        token, _ = mint()
        version, raw, mac = token.split(".")
        self.assertEqual(version, "fe1r")
        lines = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)).decode()
        self.assertEqual(
            lines.split("\n"),
            [
                "fe1r",
                "grant",
                "k1",
                "fa",
                "fa-console-run-signer",
                "-",
                "-",
                str(IAT),
                str(IAT + 43200),
                "a" * 32,
                "console-run-auth",
                ORG,
                WORKSPACE,
                USER,
                "person",
                SUB,
                "console-run",
                "-",
                "-",
                "anthropic/claude-sonnet-5.5,gpt-4o-mini",
                "ee-prod-1",
            ],
        )
        self.assertEqual(len(base64.urlsafe_b64decode(mac + "=")), 32)
        self.assertLessEqual(len(token), 4096)

    def test_it_verifies_against_its_row_and_rederives(self):
        token, claims = mint()
        self.assertEqual(verify(token, claims), claims)
        self.assertEqual(derive_grant(registry_row(claims), keys=KEYS), token)

    def test_a_row_for_another_deployment_refuses_it(self):
        token, claims = mint()
        row = registry_row(claims)
        row["ee"] = "ee-other"
        with self.assertRaises(EnvelopeRejected):
            verify(token, claims, row=row)

    def test_at_most_twelve_hours_and_expired_is_refused(self):
        with self.assertRaises(EnvelopeRejected):
            mint(ttl=43201)
        token, claims = mint(ttl=60)
        with self.assertRaises(EnvelopeRejected):
            verify(token, claims, now=IAT + 61)

    def test_refused_deployment_ids(self):
        for ee in ("", "has space", "x" * 65, "a/b"):
            with self.subTest(ee=ee), self.assertRaises(EnvelopeRejected):
                mint(ee=ee)

    def test_it_needs_models_and_a_deployment(self):
        with self.assertRaises(EnvelopeRejected):
            mint(models=None)
        with self.assertRaises(EnvelopeRejected):
            mint(ee=None)

    def test_no_other_kind_carries_a_deployment(self):
        with self.assertRaises(EnvelopeRejected):
            mint(kind="console-run", ttl=3600)

    def test_a_version_that_disagrees_with_the_fields_is_refused(self):
        token, claims = mint()
        for version in ("fe1c", "fe1"):
            relabelled = version + "." + token.split(".", 1)[1]
            with self.subTest(version), self.assertRaises(EnvelopeRejected):
                verify(relabelled, claims)
