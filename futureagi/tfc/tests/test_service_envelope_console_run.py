"""fe1c: the console-run grant (gateway console-run contract; fi-security 1d8b0ce0).

The 19 fe1 grant fields with field 1 "fe1c" and, as field 20, the models the
Console run may call: 1 to 8 ids, ascending byte order, unique, each the one id
pattern and never a router id. fe1c is a console-run grant's alone, and a
console-run grant must be fe1c. Request envelopes stay fe1. The fixture token is
the gateway's (agentcc-gateway internal/auth/envelope_console_run_models_test.go),
reproduced byte for byte with its test-only secret.
"""

import unittest

from tfc.envelope_nonces import MemoryNonceStore
from tfc.service_envelope import (
    KINDS,
    REQUEST_SCOPES,
    EnvelopeRejected,
    derive_grant,
    issue_grant,
    sign_request,
    verify_grant,
    verify_request,
)

FA_GRANT_SECRET = b"test-only-fa-secret-unavailable-to-gateway"
DELEGATED_SECRET = b"test-only-delegated-pair-secret-not-real"
KEYS = {
    ("fa", "fa-eval-grant", "k1"): FA_GRANT_SECRET,
    ("fa", "gw-delegated", "k1"): DELEGATED_SECRET,
}
FIXTURE_TOKEN = (
    "fe1c.ZmUxYwpncmFudAprMQpmYQpmYS1ldmFsLWdyYW50Ci0KLQoxNzAwMDAwMDAwCjE3MDAwMDM"
    "2MDAKY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2MKY29uc29sZS1ydW4KMTExMTExMTE"
    "tMTExMS00MTExLTgxMTEtMTExMTExMTExMTExCi0KMjIyMjIyMjItMjIyMi00MjIyLTgyMjItMjI"
    "yMjIyMjIyMjIyCnBlcnNvbgpjaGF0OjMzMzMzMzMzLTMzMzMtNDMzMy04MzMzLTMzMzMzMzMzMzM"
    "zMwpldmFsCi0KLQphbnRocm9waWMvY2xhdWRlLXNvbm5ldC01LjUsZ3B0LTRvLW1pbmk.cgDPJ4B"
    "Z3NreH1Zjq-Bi5m2i8DNvYd0Cs-04CSQE1l4"
)
ORG = "11111111-1111-4111-8111-111111111111"
USER = "22222222-2222-4222-8222-222222222222"
SUB = "chat:33333333-3333-4333-8333-333333333333"
MODELS = ["gpt-4o-mini", "anthropic/claude-sonnet-5.5"]
IAT = 1700000000
ROUTE = "/-/delegated/v1/chat/completions"
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
        "kind": "console-run",
        "org": ORG,
        "sub": SUB,
        "user": USER,
        "ttl": 3600,
        "now": IAT,
        "identifier": "c" * 32,
        "models": MODELS,
    }
    data.update(overrides)
    return issue_grant(**data)


def registry_row(claims):
    row = {field: claims[field] for field in ROW_FIELDS}
    row.update(jti=claims["id"], revoked_at=None, models=claims["models"])
    return row


def sign(token, claims, route=ROUTE, body=b"{}"):
    return sign_request(
        keys=KEYS,
        issuer="fa",
        audience="gw-delegated",
        method="POST",
        route=route,
        body=body,
        org=ORG,
        user=USER,
        via="grant:" + claims["id"],
        scope="console-run",
        grant=token,
        now=IAT + 10,
    )


class TestConsoleRunGrant(unittest.TestCase):
    def test_console_run_is_a_registered_person_grant_of_an_hour(self):
        aud, _, scope, via, registered, ttl = KINDS["console-run"]
        self.assertEqual(
            (aud, scope, via, registered, ttl),
            ("fa-eval-grant", "eval", "person", True, 3600),
        )
        self.assertIn("console-run", REQUEST_SCOPES["gw-delegated"])

    def test_the_gateway_fixture_token_byte_for_byte(self):
        token, claims = mint()
        self.assertEqual(token, FIXTURE_TOKEN)
        self.assertEqual(claims["version"], "fe1c")
        # Sorted in byte order whatever order the caller gave.
        self.assertEqual(claims["models"], "anthropic/claude-sonnet-5.5,gpt-4o-mini")

    def test_a_console_run_grant_must_name_its_models(self):
        with self.assertRaises(EnvelopeRejected):
            mint(models=None)

    def test_no_other_kind_carries_models(self):
        with self.assertRaises(EnvelopeRejected):
            mint(kind="playground", ttl=900)

    def test_refused_model_lists(self):
        refused = {
            "empty": [],
            "nine ids": [f"m{i}" for i in range(9)],
            "duplicate": ["gpt-4o", "gpt-4o"],
            "not the id pattern": ["has space"],
            "leading dash": ["-model"],
            "a URL": ["https://x.invalid/m"],
            "router auto": ["auto"],
            "router agentcc/auto": ["agentcc/auto"],
            "router omega-default": ["omega-default"],
            "router omega/auto": ["omega/auto"],
            "upstream router": ["openrouter/auto"],
            "upstream router in any case": ["vendor/AUTO"],
            "a comma inside an id": ["a,b"],
            "not a string": [7],
        }
        for case, models in refused.items():
            with self.subTest(case), self.assertRaises(EnvelopeRejected):
                mint(models=models)

    def test_at_most_an_hour(self):
        with self.assertRaises(EnvelopeRejected):
            mint(ttl=3601)

    def test_the_grant_verifies_against_its_registered_row(self):
        token, claims = mint()
        got = verify_grant(
            token,
            keys=KEYS,
            audience="fa-eval-grant",
            row=registry_row(claims),
            now=IAT + 10,
        )
        self.assertEqual(got, claims)

    def test_a_row_with_other_models_refuses_the_grant(self):
        token, claims = mint()
        row = registry_row(claims)
        row["models"] = "gpt-4o-mini"
        with self.assertRaises(EnvelopeRejected):
            verify_grant(
                token, keys=KEYS, audience="fa-eval-grant", row=row, now=IAT + 10
            )

    def test_the_row_rederives_the_exact_token(self):
        token, claims = mint()
        self.assertEqual(derive_grant(registry_row(claims), keys=KEYS), token)

    def test_a_version_that_disagrees_with_the_fields_is_refused(self):
        token, claims = mint()
        relabelled = "fe1." + token.split(".", 1)[1]
        with self.assertRaises(EnvelopeRejected):
            verify_grant(
                relabelled,
                keys=KEYS,
                audience="fa-eval-grant",
                row=registry_row(claims),
                now=IAT + 10,
            )

    def test_a_request_envelope_stays_fe1_and_binds_the_grant(self):
        token, claims = mint()
        body = b'{"model":"gpt-4o-mini"}'
        envelope = sign(token, claims, body=body)
        self.assertTrue(envelope.startswith("fe1."))
        got = verify_request(
            envelope,
            keys=KEYS,
            audience="gw-delegated",
            method="POST",
            route=ROUTE,
            body=body,
            nonces=MemoryNonceStore(epoch=IAT),
            grant=token,
            now=IAT + 20,
        )
        self.assertEqual(got["scope"], "console-run")

    def test_a_console_run_envelope_needs_the_chat_route(self):
        token, claims = mint()
        with self.assertRaises(EnvelopeRejected):
            sign(token, claims, route="/-/batches")
