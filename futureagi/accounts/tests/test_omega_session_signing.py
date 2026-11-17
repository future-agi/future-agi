import base64
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa

from accounts.omega_session import (
    ASSERTION_PREFIX,
    DelegationUnavailable,
    HostAssertionSigner,
    delegation_role,
)


def decode_segment(value):
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def assertion_parts(response):
    token = response["accessToken"]
    assert token.startswith(ASSERTION_PREFIX)
    return token.removeprefix(ASSERTION_PREFIX).split(".")


class HostAssertionSigningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.key_file = Path(self.directory.name) / "signing.pem"
        self.write_key(self.key)
        self.environ = {
            "OMEGA_SQUAD_DELEGATION_ISSUER": "https://host.example/omega",
            "OMEGA_SQUAD_DELEGATION_KEY_ID": "host-2026-10",
            "OMEGA_SQUAD_DELEGATION_PRIVATE_KEY_FILE": str(self.key_file),
        }

    def write_key(self, key):
        self.key_file.write_bytes(
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
        self.key_file.chmod(0o600)

    def issue(self, **kwargs):
        values = {
            "subject": "user/one",
            "organization_id": "org/one",
            "workspace_id": "workspace/one",
            "role": "member",
            "audience": "omega-squad",
            "now": 1_800_000_000,
            "auth_method": "session",
            "credential_id": "6f0b6f5e-5c1d-4a3e-9d55-0c8a7a2f0001",
        }
        values.update(kwargs)
        return HostAssertionSigner.from_environment(self.environ).issue(**values)

    def test_emergency_authority_does_not_promote_delegated_members(self):
        from accounts.omega_session import emergency_authority

        self.assertEqual(delegation_role(3, 8), "member")
        for org, workspace, expected in [
            (3, 8, "workspace"),
            (3, 3, None),
            (8, None, "organization"),
            (15, None, "organization"),
        ]:
            self.assertEqual(emergency_authority(org, workspace, "session"), expected)
            self.assertIsNone(emergency_authority(org, workspace, "api_key"))
        response = self.issue(emergency_control="workspace")
        self.assertEqual(
            json.loads(decode_segment(assertion_parts(response)[1]))[
                "emergency_control"
            ],
            "workspace",
        )
        with self.assertRaises(ValueError):
            self.issue(emergency_control="organization")
        with self.assertRaises(ValueError):
            self.issue(emergency_control="workspace", auth_method="api_key")

    def test_signed_scope_display_names_are_optional_and_bounded(self):
        response = self.issue(
            organization_name="Customer organization",
            workspace_name="Operations workspace",
        )
        header, payload, signature = assertion_parts(response)
        self.key.public_key().verify(
            decode_segment(signature),
            f"{header}.{payload}".encode(),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
        claims = json.loads(decode_segment(payload))
        self.assertEqual(claims["org_name"], "Customer organization")
        self.assertEqual(claims["workspace_name"], "Operations workspace")
        for value in ("", "  ", "x" * 257, 1, "Name\nspoof"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.issue(workspace_name=value)

    def test_maximum_unicode_names_fit_the_complete_wire_budget(self):
        organization_name = "\U0001f9ea" * 255
        workspace_name = "\U0001f680" * 255
        response = self.issue(
            organization_name=organization_name,
            workspace_name=workspace_name,
            emergency_control="workspace",
            products={"console": "active"},
            rollout_epoch=1,
        )
        self.assertLessEqual(len(response["accessToken"]), 8192)
        header, payload, signature = assertion_parts(response)
        self.key.public_key().verify(
            decode_segment(signature),
            f"{header}.{payload}".encode(),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
        claims = json.loads(decode_segment(payload))
        self.assertEqual(claims["org_name"], organization_name)
        self.assertEqual(claims["workspace_name"], workspace_name)
        self.assertEqual(claims["role"], "member")
        self.assertEqual(claims["emergency_control"], "workspace")

    def test_display_metadata_yields_to_the_encoded_budget_not_identity(self):
        key = rsa.generate_private_key(public_exponent=65537, key_size=4096)
        self.write_key(key)
        self.environ["OMEGA_SQUAD_DELEGATION_ISSUER"] = (
            "https://host.example/" + "a" * (2048 - len("https://host.example/"))
        )
        self.environ["OMEGA_SQUAD_DELEGATION_KEY_ID"] = "k" * 128
        name = "\U0001f9ea" * 255
        response = self.issue(
            subject="s" * 256,
            organization_id="o" * 256,
            workspace_id="w" * 256,
            credential_id="c" * 256,
            organization_name=name,
            workspace_name=name,
            emergency_control="workspace",
            products=dict.fromkeys(
                ("console", "sandboxes", "deployments", "studio_tab", "hub"), "active"
            ),
            rollout_epoch=123456789,
        )
        self.assertLessEqual(len(response["accessToken"]), 8192)
        header, payload, signature = assertion_parts(response)
        key.public_key().verify(
            decode_segment(signature),
            f"{header}.{payload}".encode(),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
        claims = json.loads(decode_segment(payload))
        for claim, value in (
            ("sub", "s" * 256),
            ("org_id", "o" * 256),
            ("workspace_id", "w" * 256),
            ("sid", "c" * 256),
            ("iss", self.environ["OMEGA_SQUAD_DELEGATION_ISSUER"]),
        ):
            self.assertEqual(claims[claim], value)
        for claim in ("org_name", "workspace_name"):
            self.assertTrue(claims[claim].endswith("…"))
            self.assertTrue(name.startswith(claims[claim][:-1]))
            self.assertGreater(len(claims[claim]), 1)
        self.assertEqual(claims["role"], "member")
        self.assertEqual(claims["emergency_control"], "workspace")
        self.assertEqual(len(claims["products"]), 5)

    def test_required_claims_over_budget_fail_without_issuing_a_token(self):
        self.environ["OMEGA_SQUAD_DELEGATION_ISSUER"] = (
            "https://host.example/" + "\U0001f9ea" * 2000
        )
        with self.assertRaisesRegex(
            DelegationUnavailable, "^Omega delegation is unavailable$"
        ):
            self.issue()

    def test_signature_and_exact_wire_claims(self):
        response = self.issue()
        header, payload, signature = assertion_parts(response)
        self.key.public_key().verify(
            decode_segment(signature),
            f"{header}.{payload}".encode(),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
        self.assertEqual(
            json.loads(decode_segment(header)),
            {"alg": "RS256", "typ": "JWT", "kid": "host-2026-10"},
        )
        claims = json.loads(decode_segment(payload))
        self.assertEqual(
            set(claims),
            {
                "iss",
                "aud",
                "sub",
                "org_id",
                "workspace_id",
                "role",
                "iat",
                "exp",
                "nonce",
                "jti",
                "auth_method",
                "sid",
            },
        )
        self.assertEqual(claims["auth_method"], "session")
        self.assertEqual(claims["sid"], "6f0b6f5e-5c1d-4a3e-9d55-0c8a7a2f0001")
        self.assertEqual(claims["exp"] - claims["iat"], 45)
        self.assertEqual(response["expiresIn"], 45)
        self.assertEqual(response["tokenType"], "Bearer")
        self.assertEqual(claims["sub"], "user/one")
        self.assertEqual(claims["aud"], "omega-squad")
        for field in ("nonce", "jti"):
            self.assertRegex(claims[field], r"^[A-Za-z0-9_-]{43}$")
        self.assertNotEqual(claims["jti"], claims["nonce"])

    def test_a_fresh_assertion_is_dated_one_second_back_for_a_verifier_slightly_behind(
        self,
    ):
        # EE refuses an `iat` ahead of its clock by any amount; a host a fraction of a second ahead
        # (the local Colima VM runs ~0.1 s ahead of the host EE runs on) must not mint one.
        with mock.patch(
            "accounts.omega_session.time.time", return_value=1_800_000_000.9
        ):
            response = self.issue(now=None)
        claims = json.loads(decode_segment(assertion_parts(response)[1]))
        self.assertEqual(claims["iat"], 1_799_999_999)
        self.assertEqual(claims["exp"], claims["iat"] + 45)
        self.assertEqual(response["expiresIn"], 45)

    def test_every_assertion_has_fresh_replay_identifiers(self):
        first, second = [
            json.loads(decode_segment(assertion_parts(self.issue())[1]))
            for _ in range(2)
        ]
        self.assertNotEqual(first["nonce"], second["nonce"])
        self.assertNotEqual(first["jti"], second["jti"])

    def test_payload_tampering_invalidates_signature(self):
        header, payload, signature = assertion_parts(self.issue())
        with self.assertRaises(InvalidSignature):
            self.key.public_key().verify(
                decode_segment(signature),
                f"{header}.{payload}A".encode(),
                padding.PKCS1v15(),
                hashes.SHA256(),
            )

    def test_configuration_is_opt_in_and_errors_do_not_expose_key_path(self):
        for missing in self.environ:
            with self.subTest(missing=missing):
                values = dict(self.environ)
                del values[missing]
                with self.assertRaisesRegex(
                    DelegationUnavailable, "^Omega delegation is unavailable$"
                ):
                    HostAssertionSigner.from_environment(values)

    def test_rejects_unsafe_key_files(self):
        for mode in (0o644, 0o640, 0o666):
            with self.subTest(mode=mode):
                self.key_file.chmod(mode)
                with self.assertRaises(DelegationUnavailable):
                    HostAssertionSigner.from_environment(self.environ)
        self.key_file.chmod(0o600)
        target = self.key_file.with_suffix(".target")
        self.key_file.rename(target)
        self.key_file.symlink_to(target)
        with self.assertRaises(DelegationUnavailable):
            HostAssertionSigner.from_environment(self.environ)

    def test_rejects_invalid_oversized_and_non_rsa_keys(self):
        for value in (b"not a key", b"x" * 16385):
            self.key_file.write_bytes(value)
            with self.assertRaises(DelegationUnavailable):
                HostAssertionSigner.from_environment(self.environ)
        for key in (
            ec.generate_private_key(ec.SECP256R1()),
            rsa.generate_private_key(public_exponent=65537, key_size=1024),
        ):
            self.write_key(key)
            with self.assertRaises(DelegationUnavailable):
                HostAssertionSigner.from_environment(self.environ)

    def test_rejects_directory_key_and_relative_path(self):
        for path in (self.directory.name, "relative.pem"):
            with self.assertRaises(DelegationUnavailable):
                HostAssertionSigner.from_environment(
                    {**self.environ, "OMEGA_SQUAD_DELEGATION_PRIVATE_KEY_FILE": path}
                )

    def test_rejects_invalid_issuer_and_kid(self):
        for issuer in (
            "http://host.example",
            "https://user:pass@host.example",
            "https://host.example?a=1",
            "https://host.example/#x",
            "https://host.example/ bad",
            "",
        ):
            with self.assertRaises(DelegationUnavailable):
                HostAssertionSigner.from_environment(
                    {**self.environ, "OMEGA_SQUAD_DELEGATION_ISSUER": issuer}
                )
        for key_id in ("", "bad key", "x" * 129):
            with self.assertRaises(DelegationUnavailable):
                HostAssertionSigner.from_environment(
                    {**self.environ, "OMEGA_SQUAD_DELEGATION_KEY_ID": key_id}
                )

    def test_wrong_audience_identity_role_and_timestamp_fail(self):
        for kwargs in (
            {"audience": "other"},
            {"subject": "bad subject"},
            {"workspace_id": ""},
            {"organization_id": "x" * 257},
            {"role": "viewer"},
            {"now": 1.5},
            {"now": True},
            {"auth_method": "password"},
            {"auth_method": ["session"]},
            {"auth_method": None},
            {"credential_id": ""},
            {"credential_id": "bad id"},
            {"credential_id": None},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.issue(**kwargs)

    def test_an_api_key_assertion_names_its_method_and_key_never_a_session(self):
        # SB W2-KEYS: EE refuses human-session-only verbs unless `auth_method` is "session";
        # the binding claim is the key's id, never the secret.
        claims = json.loads(
            decode_segment(
                assertion_parts(
                    self.issue(auth_method="api_key", credential_id="key-0001")
                )[1]
            )
        )
        self.assertEqual(claims["auth_method"], "api_key")
        self.assertEqual(claims["key_id"], "key-0001")
        self.assertNotIn("sid", claims)

    def test_role_policy_caps_org_authority_and_refuses_viewer_elevation(self):
        for org, workspace, expected in (
            (15, None, "owner"),
            (15, 1, "owner"),
            (8, None, "admin"),
            (8, 1, "admin"),
            (3, 3, "member"),
            (3, 8, "member"),
        ):
            self.assertEqual(delegation_role(org, workspace), expected)
        for org, workspace in ((1, 3), (1, 8), (3, 1), (3, None), (None, 8), (99, 8)):
            with self.assertRaises(ValueError):
                delegation_role(org, workspace)

    def test_private_key_never_appears_in_repr_or_result(self):
        signer = HostAssertionSigner.from_environment(self.environ)
        self.assertNotIn("private_key", repr(signer))
        self.assertNotIn("PRIVATE KEY", json.dumps(self.issue()))
        self.assertNotIn(str(self.key_file), repr(signer))


if __name__ == "__main__":
    unittest.main()
