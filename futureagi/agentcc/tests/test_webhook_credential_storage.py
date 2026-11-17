"""Isolated codec tests, NOT database/migration or full application coverage.

Standalone: python -I -B <absolute path to this file>. Minimal Django settings;
NOT pytest and NOT the application's pytest conftests. Keys/material are synthetic.
The standalone reporter emits IDs/outcomes, never traces or credential values.
"""

import copy
import json
import math
import secrets
import sys
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from django.conf import settings

    settings.configure(INTEGRATION_ENCRYPTION_KEY=None, INSTALLED_APPS=[])

from cryptography.fernet import Fernet
from django.test import override_settings

from agentcc.services import webhook_credential_storage as codec
from integrations.services.credentials import CredentialManager

E = codec.ErrorCode
store = codec.encrypt_for_storage
preserve = codec.preserve_for_migration
recover = codec.recover_for_migration
verify = codec.verify_preserved_for_migration


def identical(left, right):
    """Compare JSON trees without bool/int equality or loss of float sign."""
    if type(left) is not type(right):
        return False
    if type(left) is dict:
        return list(left) == list(right) and all(
            identical(left[key], right[key]) for key in left
        )
    if type(left) is list:
        return len(left) == len(right) and all(map(identical, left, right))
    if type(left) is float:
        return left.hex() == right.hex()
    return left == right


class Poison:
    def __str__(self):
        raise AssertionError("ARBITRARY_COERCION")

    __repr__ = __str__
    __bool__ = __str__


class CodecTests(unittest.TestCase):
    def setUp(self):
        key = Fernet.generate_key()
        self.settings = override_settings(INTEGRATION_ENCRYPTION_KEY=key)
        self.settings.enable()
        self.addCleanup(self.settings.disable)
        self.context = codec.StorageContext(
            organization_id=secrets.token_hex(16),
            webhook_id=secrets.token_hex(16),
            field="headers",
            destination="https://fixture.invalid/" + secrets.token_hex(8),
        )
        self.value = {"X-Fixture": secrets.token_urlsafe(24)}

    def encrypt(self, value=None, context=None):
        return store(context or self.context, self.value if value is None else value)

    def read(self, blob, context=None, version=1):
        return codec.decrypt_for_delivery(
            context or self.context, storage_version=version, ciphertext=blob
        )

    def refusal(self, code, function, *args, **kwargs):
        with self.assertRaises(codec.WebhookCredentialStorageError) as caught:
            function(*args, **kwargs)
        error = caught.exception
        self.assertIs(error.code, code)
        self.assertTrue(error.args == (code.value,))
        self.assertTrue(str(error) == code.value)
        self.assertTrue(repr(error) == f"WebhookCredentialStorageError({code.value!r})")
        self.assertIsNone(error.__cause__)
        self.assertIsNone(error.__context__)

    def forge(self, blob, **changes):
        envelope = CredentialManager.decrypt(blob)
        envelope.update(changes)
        return CredentialManager.encrypt(envelope)

    def test_actual_primitive_roundtrip_and_nonmutation(self):
        before = copy.deepcopy(self.value)
        blob = self.encrypt()
        self.assertIs(type(blob), bytes)
        self.assertTrue(identical(self.read(blob), self.value))
        self.assertTrue(identical(before, self.value))
        self.assertTrue(type(CredentialManager.decrypt(blob)) is dict)
        self.assertFalse(self.value["X-Fixture"].encode() in blob)

    def test_explicit_encrypted_empty_and_no_null_sentinel(self):
        for field, empty in [("headers", {}), ("secret", "")]:
            ctx = replace(self.context, field=field)
            blob = self.encrypt(empty, ctx)
            self.assertTrue(identical(self.read(blob, ctx), empty))
            self.refusal(E.INVALID_CIPHERTEXT, self.read, None, ctx)
            self.refusal(E.INVALID_CIPHERTEXT, self.read, b"", ctx)
            with override_settings(INTEGRATION_ENCRYPTION_KEY=None):
                self.refusal(E.CRYPTO_FAILURE, self.read, blob, ctx)
                self.refusal(E.CRYPTO_FAILURE, self.encrypt, empty, ctx)

    def test_secret_preserves_whitespace_case_and_unicode(self):
        ctx = replace(self.context, field="secret")
        value = " \t" + secrets.token_urlsafe(24) + "\n☃"
        self.assertTrue(identical(self.read(self.encrypt(value, ctx), ctx), value))

    def test_normalized_headers_do_not_tighten_existing_value_semantics(self):
        value = {"A": None, "a": 0, "B": False, "C": [1, "x"], "D": {"": 1.0}}
        self.assertTrue(identical(self.read(self.encrypt(value)), value))

    def test_all_independent_context_swaps_refused(self):
        blob = self.encrypt()
        swaps = [
            ("organization_id", secrets.token_hex(16)),
            ("webhook_id", secrets.token_hex(16)),
            ("field", "secret"),
            ("destination", self.context.destination + "/"),
            ("destination", self.context.destination.upper()),
        ]
        for field, value in swaps:
            ctx = replace(self.context, **{field: value})
            self.refusal(E.CONTEXT_MISMATCH, self.read, blob, ctx)

    def test_untrusted_context_shape_not_coerced(self):
        for ctx in [None, {}, Poison(), replace(self.context, field="unknown")]:
            self.refusal(E.INVALID_CONTEXT, store, ctx, {})
        for field in ["organization_id", "webhook_id", "field", "destination"]:
            for value in ["", None, Poison(), 1]:
                ctx = replace(self.context, **{field: value})
                self.refusal(E.INVALID_CONTEXT, store, ctx, {})

    def test_storage_version_is_required_and_exact(self):
        blob = self.encrypt()
        for version in [None, 0, 2, -1, True, False, "1", 1.0, Poison()]:
            self.refusal(E.UNSUPPORTED_VERSION, self.read, blob, version=version)
        # Missing required arguments also cross the closed error boundary.
        self.refusal(
            E.CRYPTO_FAILURE, codec.decrypt_for_delivery, self.context, ciphertext=blob
        )

    def test_no_plaintext_or_legacy_prefix_fallback(self):
        for value in [self.value, "", "plain", "encrypted:plain", Poison(), 1]:
            self.refusal(E.INVALID_CIPHERTEXT, self.read, value)
        for value in [b"plain", b"{}", b"null", b"encrypted:plain"]:
            self.refusal(E.CRYPTO_FAILURE, self.read, value)
        self.refusal(E.UNSUPPORTED_VERSION, self.read, None, version=0)

    def test_corrupt_ciphertext_missing_wrong_and_malformed_key(self):
        blob = self.encrypt()
        corrupt = blob[:30] + bytes([blob[30] ^ 1]) + blob[31:]
        self.refusal(E.CRYPTO_FAILURE, self.read, corrupt)
        for key in [None, b"", b"invalid", Fernet.generate_key()]:
            with override_settings(INTEGRATION_ENCRYPTION_KEY=key):
                self.refusal(E.CRYPTO_FAILURE, self.read, blob)
        for key in [None, b"", b"invalid"]:
            with override_settings(INTEGRATION_ENCRYPTION_KEY=key):
                self.refusal(E.CRYPTO_FAILURE, self.encrypt)

    def test_authenticated_non_json_refused(self):
        blob = CredentialManager._get_fernet().encrypt(b"not-json")
        self.refusal(E.CRYPTO_FAILURE, self.read, blob)

    def test_authenticated_envelope_shape_and_types(self):
        blob = self.encrypt()
        envelope = CredentialManager.decrypt(blob)
        for value in [None, [], 0, "text"]:
            token = CredentialManager.encrypt(value)
            self.refusal(E.INVALID_ENVELOPE, self.read, token)
        for key in envelope:
            missing = dict(envelope)
            del missing[key]
            self.refusal(
                E.INVALID_ENVELOPE, self.read, CredentialManager.encrypt(missing)
            )
        self.refusal(E.INVALID_ENVELOPE, self.read, self.forge(blob, extra=True))
        for key in (
            "purpose",
            "state",
            "organization_id",
            "webhook_id",
            "field",
            "destination",
        ):
            for value in [None, [], 1, True]:
                self.refusal(
                    E.INVALID_ENVELOPE, self.read, self.forge(blob, **{key: value})
                )
        for version in [None, True, "1", 1.0, 0, 2]:
            self.refusal(
                E.UNSUPPORTED_VERSION, self.read, self.forge(blob, version=version)
            )

    def test_authenticated_wrong_purpose_state_and_active_shape(self):
        blob = self.encrypt()
        for change in [
            {"purpose": "other"},
            {"state": "other"},
            {"state": "needs_reentry"},
        ]:
            self.refusal(E.INVALID_ENVELOPE, self.read, self.forge(blob, **change))
        for value in [None, [], "text", 1, {"": "value"}]:
            self.refusal(E.INVALID_ENVELOPE, self.read, self.forge(blob, value=value))
        ctx = replace(self.context, field="secret")
        blob = self.encrypt("secret", ctx)
        for value in [None, {}, [], 0, False]:
            self.refusal(
                E.INVALID_ENVELOPE, self.read, self.forge(blob, value=value), ctx
            )

    def refuse_hooked_list(self, function, *, nested):
        touches = []

        class Comparable(type):
            def __eq__(cls, other):
                touches.append("equality")
                return other is str

        class HookedList(list, metaclass=Comparable):
            def __iter__(self):
                touches.append("iterator")
                return super().__iter__()

        value = HookedList([1])
        with self.subTest(check="refusal"):
            self.refusal(
                E.INVALID_VALUE,
                function,
                self.context,
                {"X-Fixture": value} if nested else value,
            )
        self.assertEqual(touches, [])

    def test_preserved_root_rejects_metaclass_equality_without_hooks(self):
        self.refuse_hooked_list(preserve, nested=False)

    def test_active_nested_rejects_metaclass_equality_without_hooks(self):
        self.refuse_hooked_list(store, nested=True)

    def test_only_explicit_normalized_inputs_no_arbitrary_coercion(self):
        values = [None, False, 0, "", [], [["x", "y"]], {"": 1}, {1: "x"}]
        values.append({"x": Poison()})
        for value in values:
            self.refusal(E.INVALID_VALUE, store, self.context, value)
        ctx = replace(self.context, field="secret")
        for value in [None, False, 0, [], {}, Poison()]:
            self.refusal(E.INVALID_VALUE, store, ctx, value)

        class StringSubclass(str):
            pass

        self.refusal(E.INVALID_VALUE, self.encrypt, {StringSubclass("x"): "y"})

    def test_historical_originals_lossless_and_delivery_blocked(self):
        originals = [
            None,
            False,
            0,
            "",
            [],
            ["bad", {"": [1, 1.0, -0.0, True]}],
            [["X", "first"], ["X", "last"], ["x", None]],
            {"": "bad"},
            {},
        ]
        for original in originals:
            before = copy.deepcopy(original)
            blob = preserve(self.context, original)
            value = recover(self.context, storage_version=1, ciphertext=blob)
            self.assertTrue(identical(value, original))
            self.assertTrue(identical(before, original))
            verify(self.context, original, storage_version=1, ciphertext=blob)
            self.refusal(E.NEEDS_REENTRY, self.read, blob)

    def test_historical_secret_recovery_and_reentry_are_distinct(self):
        ctx = replace(self.context, field="secret")
        original = " \t" + secrets.token_urlsafe(24) + "\n"
        blob = preserve(ctx, original)
        value = recover(ctx, storage_version=1, ciphertext=blob)
        self.assertTrue(identical(value, original))
        self.refusal(E.NEEDS_REENTRY, self.read, blob, ctx)
        replacement = self.encrypt(original, ctx)
        self.assertTrue(identical(self.read(replacement, ctx), original))
        self.refusal(E.NEEDS_REENTRY, self.read, blob, ctx)

    def test_opaque_domain_and_context_cannot_be_activated(self):
        blob = preserve(self.context, self.value)
        active = self.encrypt()
        purpose = CredentialManager.decrypt(active)["purpose"]
        for changes in [{"state": "active"}, {"purpose": purpose}]:
            self.refusal(E.INVALID_ENVELOPE, self.read, self.forge(blob, **changes))
        self.refusal(E.UNSUPPORTED_VERSION, self.read, blob, version=0)
        for field, value in [
            ("organization_id", "other"),
            ("webhook_id", "other"),
            ("destination", self.context.destination + "/"),
            ("field", "secret"),
        ]:
            ctx = replace(self.context, **{field: value})
            self.refusal(E.CONTEXT_MISMATCH, self.read, blob, ctx)
            self.refusal(
                E.CONTEXT_MISMATCH, recover, ctx, storage_version=1, ciphertext=blob
            )
        self.refusal(
            E.NOT_PRESERVED, recover, self.context, storage_version=1, ciphertext=active
        )

    def test_verification_compares_types_order_and_float_sign(self):
        pairs = [
            (True, 1),
            (1, 1.0),
            (-0.0, 0.0),
            ([1, 2], [2, 1]),
            ({"X": 1}, {"x": 1}),
            ({"A": 1, "B": 2}, {"B": 2, "A": 1}),
        ]
        for original, different in pairs:
            blob = preserve(self.context, original)
            self.refusal(
                E.VERIFICATION_FAILED,
                verify,
                self.context,
                different,
                storage_version=1,
                ciphertext=blob,
            )

    def test_non_json_historical_objects_refused_without_coercion(self):
        cyclic = []
        cyclic.append(cyclic)
        values = [Poison(), (1, 2), b"bytes", {1: "x"}, {"x": Poison()}]
        values += [cyclic, math.nan, math.inf, -math.inf]
        for value in values:
            self.refusal(E.INVALID_VALUE, preserve, self.context, value)
        self.refusal(
            E.INVALID_VALUE, preserve, replace(self.context, field="secret"), None
        )

    def test_failure_injection_is_value_free_no_logs_or_error_chain(self):
        blob = self.encrypt()
        for operation, function, args in [
            ("encrypt", self.encrypt, ()),
            ("decrypt", self.read, (blob,)),
        ]:
            failure = ValueError(secrets.token_urlsafe(32))
            with patch.object(CredentialManager, operation, side_effect=failure):
                with self.assertNoLogs():
                    self.refusal(E.CRYPTO_FAILURE, function, *args)
        with patch.object(CredentialManager, "encrypt", return_value=None):
            self.refusal(E.CRYPTO_FAILURE, self.encrypt)
        with patch.object(CredentialManager, "decrypt", return_value=Poison()):
            self.refusal(E.INVALID_ENVELOPE, self.read, blob)


if __name__ == "__main__":

    class ClosedResult(unittest.TestResult):
        def addFailure(self, test, err):
            self.failures.append((test, "ASSERTION_FAILED"))

        def addError(self, test, err):
            self.errors.append((test, "TEST_ERROR"))

        def addSubTest(self, test, subtest, err):
            if err is not None:
                self.addFailure(test, err)

    result = ClosedResult()
    unittest.defaultTestLoader.loadTestsFromTestCase(CodecTests).run(result)
    print(
        json.dumps(
            {
                "suite": "isolatedcodec",
                "certifying": False,
                "testsRun": result.testsRun,
                "failures": [test.id() for test, _ in result.failures],
                "errors": [test.id() for test, _ in result.errors],
                "successful": result.wasSuccessful(),
            }
        )
    )
    raise SystemExit(0 if result.wasSuccessful() else 1)
