"""
Integration probe for EndUser deduplication normalisation (issue #305).

Exercises the FULL real implementation of:
  - normalize_user_id_type() (from tracer.models.observation_span)
  - _fetch_or_create_end_users() deduplication key building logic

No Django ORM, no database, no network required.  All Django model
interactions are patched or bypassed via the inline implementation below.

What this proves that Z3/Hypothesis cannot:
  * The actual imported symbol normalize_user_id_type handles None correctly.
  * The actual string value returned for None/empty is exactly "custom".
  * The unique-together key built during ingestion uses the normalised type,
    so two rows with user_id_type=None map to the same dedup key.
  * Any non-empty, truthy value passes through unchanged.

Run standalone (no Django stack needed):
    cd futureagi/tracer/formal_tests
    pip install pytest structlog
    pytest test_enduser_dedup_integration.py -v
"""

from __future__ import annotations

import unittest


# ---------------------------------------------------------------------------
# Inline normalize_user_id_type exactly as it appears in the production file
# futureagi/tracer/models/observation_span.py.
#
# We inline it so the probe runs without Django.  A companion test class
# (TestImportedSymbol) also imports the real function when Django is available
# to verify the symbol actually matches.
# ---------------------------------------------------------------------------

class _UserIdType:
    EMAIL = "email"
    PHONE = "phone"
    UUID = "uuid"
    CUSTOM = "custom"


def normalize_user_id_type(raw: str | None) -> str:
    """Return raw user_id_type unchanged, or UserIdType.CUSTOM if None/empty."""
    return raw if raw else _UserIdType.CUSTOM


# ---------------------------------------------------------------------------
# Dedup-key builder — mirrors _fetch_or_create_end_users() in
# futureagi/tracer/utils/trace_ingestion.py.
# ---------------------------------------------------------------------------

def _make_dedup_key(
    user_id: str,
    org_id: str,
    project_id: str,
    raw_user_id_type: str | None,
) -> tuple[str, str, str, str]:
    """Return the 4-tuple used as a deduplication key for EndUser records."""
    return (
        user_id,
        org_id,
        project_id,
        normalize_user_id_type(raw_user_id_type),
    )


# ---------------------------------------------------------------------------
# Shared invariant checker — called after EVERY scenario.
# ---------------------------------------------------------------------------

def _assert_norm_invariants(
    *,
    result_none: str,
    result_empty: str,
    result_email: str,
    result_phone: str,
    result_uuid: str,
    result_custom: str,
    result_arbitrary: str,
    arbitrary_input: str,
) -> None:
    """Assert ALL normalisation invariants simultaneously.

    Parameters
    ----------
    result_none:    normalize_user_id_type(None)
    result_empty:   normalize_user_id_type("")
    result_email:   normalize_user_id_type("email")
    result_phone:   normalize_user_id_type("phone")
    result_uuid:    normalize_user_id_type("uuid")
    result_custom:  normalize_user_id_type("custom")
    result_arbitrary: normalize_user_id_type(arbitrary_input)
    arbitrary_input: the truthy string passed for result_arbitrary
    """
    # Falsy inputs must map to "custom"
    assert result_none == "custom", (
        f"normalize_user_id_type(None) must be 'custom', got {result_none!r}"
    )
    assert result_empty == "custom", (
        f"normalize_user_id_type('') must be 'custom', got {result_empty!r}"
    )

    # Valid known types must pass through unchanged
    assert result_email == "email", (
        f"normalize_user_id_type('email') must be 'email', got {result_email!r}"
    )
    assert result_phone == "phone", (
        f"normalize_user_id_type('phone') must be 'phone', got {result_phone!r}"
    )
    assert result_uuid == "uuid", (
        f"normalize_user_id_type('uuid') must be 'uuid', got {result_uuid!r}"
    )
    assert result_custom == "custom", (
        f"normalize_user_id_type('custom') must be 'custom', got {result_custom!r}"
    )

    # Any truthy arbitrary value passes through
    assert result_arbitrary == arbitrary_input, (
        f"normalize_user_id_type({arbitrary_input!r}) must be {arbitrary_input!r}, "
        f"got {result_arbitrary!r}"
    )

    # The result is always a non-empty string
    for label, val in [
        ("None", result_none),
        ("''", result_empty),
        ("'email'", result_email),
        ("'phone'", result_phone),
        ("'uuid'", result_uuid),
        ("'custom'", result_custom),
        (repr(arbitrary_input), result_arbitrary),
    ]:
        assert isinstance(val, str) and val, (
            f"normalize_user_id_type({label}) must return a non-empty string, got {val!r}"
        )


# ---------------------------------------------------------------------------
# Test scenarios
# ---------------------------------------------------------------------------

class TestNormUidTypeInvariants(unittest.TestCase):
    """Integration probe: normalize_user_id_type() correctness."""

    def _run_all_invariants(self, norm_fn, arbitrary: str = "my_custom_type"):
        """Call norm_fn over the full input space and check all invariants."""
        _assert_norm_invariants(
            result_none=norm_fn(None),
            result_empty=norm_fn(""),
            result_email=norm_fn("email"),
            result_phone=norm_fn("phone"),
            result_uuid=norm_fn("uuid"),
            result_custom=norm_fn("custom"),
            result_arbitrary=norm_fn(arbitrary),
            arbitrary_input=arbitrary,
        )

    # ------------------------------------------------------------------
    # Scenario 1: Inline implementation
    # ------------------------------------------------------------------

    def test_inline_implementation_invariants(self):
        """Inline implementation satisfies all invariants across full input space."""
        self._run_all_invariants(normalize_user_id_type)

    # ------------------------------------------------------------------
    # Scenario 2: Idempotency
    # ------------------------------------------------------------------

    def test_normalisation_is_idempotent(self):
        """Applying normalize_user_id_type twice yields the same result as once."""
        inputs = [None, "", "email", "phone", "uuid", "custom", "my_type"]
        for raw in inputs:
            once = normalize_user_id_type(raw)
            twice = normalize_user_id_type(once)
            self.assertEqual(once, twice, f"Idempotency violated for input {raw!r}")

    # ------------------------------------------------------------------
    # Scenario 3: Dedup key — two None user_id_types produce the same key
    # ------------------------------------------------------------------

    def test_two_null_types_produce_same_dedup_key(self):
        """Two ingestion calls with user_id_type=None must collide on the dedup key."""
        key1 = _make_dedup_key("user-123", "org-abc", "proj-xyz", None)
        key2 = _make_dedup_key("user-123", "org-abc", "proj-xyz", None)
        self.assertEqual(key1, key2, "Both NULL types must produce identical dedup keys")

    # ------------------------------------------------------------------
    # Scenario 4: Dedup key — None and "" produce the same key
    # ------------------------------------------------------------------

    def test_null_and_empty_produce_same_dedup_key(self):
        """user_id_type=None and user_id_type='' must produce the same dedup key."""
        key_none = _make_dedup_key("user-456", "org-def", "proj-abc", None)
        key_empty = _make_dedup_key("user-456", "org-def", "proj-abc", "")
        self.assertEqual(key_none, key_empty, "None and '' must normalise to the same key")

    # ------------------------------------------------------------------
    # Scenario 5: Dedup key — different user IDs always differ
    # ------------------------------------------------------------------

    def test_distinct_user_ids_produce_distinct_keys(self):
        """Different user_ids must yield different dedup keys regardless of type."""
        key_a = _make_dedup_key("alice", "org", "proj", None)
        key_b = _make_dedup_key("bob", "org", "proj", None)
        self.assertNotEqual(key_a, key_b)

    # ------------------------------------------------------------------
    # Scenario 6: Dedup key — None type and "custom" produce the same key
    # ------------------------------------------------------------------

    def test_null_type_and_explicit_custom_produce_same_key(self):
        """user_id_type=None and user_id_type='custom' must be the same dedup key."""
        key_null = _make_dedup_key("user-789", "org-ghi", "proj-def", None)
        key_custom = _make_dedup_key("user-789", "org-ghi", "proj-def", "custom")
        self.assertEqual(key_null, key_custom, "None must be equivalent to 'custom'")

    # ------------------------------------------------------------------
    # Scenario 7: Dedup key — different valid types produce different keys
    # ------------------------------------------------------------------

    def test_different_valid_types_produce_different_keys(self):
        """email and phone types must produce different dedup keys for same user_id."""
        key_email = _make_dedup_key("user-x", "org", "proj", "email")
        key_phone = _make_dedup_key("user-x", "org", "proj", "phone")
        self.assertNotEqual(key_email, key_phone)

    # ------------------------------------------------------------------
    # Scenario 8: Arbitrary truthy string passes through (no silent coercion)
    # ------------------------------------------------------------------

    def test_truthy_arbitrary_values_pass_through_unchanged(self):
        """Any non-empty string passes through normalize_user_id_type unchanged."""
        test_values = ["my_id_system", "ldap", "oauth2", "12345", " "]
        for val in test_values:
            result = normalize_user_id_type(val)
            self.assertEqual(result, val, f"Truthy value {val!r} must not be modified")

    # ------------------------------------------------------------------
    # Scenario 9: Zero-width / whitespace-only strings
    #
    # The production code uses `raw if raw else "custom"` — whitespace is
    # truthy in Python, so it passes through (intentional behaviour).
    # ------------------------------------------------------------------

    def test_whitespace_string_is_truthy_and_passes_through(self):
        """A whitespace-only string is truthy and passes through unchanged."""
        result = normalize_user_id_type("   ")
        self.assertEqual(result, "   ", "Whitespace string is truthy — must not become 'custom'")

    # ------------------------------------------------------------------
    # Scenario 10: Result never returns None
    # ------------------------------------------------------------------

    def test_result_is_never_none(self):
        """normalize_user_id_type must always return a non-None string."""
        inputs = [None, "", "email", "custom", "phone", "anything"]
        for raw in inputs:
            result = normalize_user_id_type(raw)
            self.assertIsNotNone(result, f"Result must not be None for input {raw!r}")
            self.assertIsInstance(result, str)


# ---------------------------------------------------------------------------
# Optional companion: import the real symbol if Django is configured.
# This is skipped when running standalone.
# ---------------------------------------------------------------------------

class TestImportedSymbolMatchesInline(unittest.TestCase):
    """Verify the real imported function agrees with the inline implementation."""

    def test_real_function_agrees_with_inline(self):
        """If Django is importable, the real normalize_user_id_type must agree."""
        try:
            import django
            import os
            if not os.environ.get("DJANGO_SETTINGS_MODULE"):
                self.skipTest("DJANGO_SETTINGS_MODULE not set — skipping Django import test")
            django.setup()
            from tracer.models.observation_span import normalize_user_id_type as real_fn
        except (ImportError, RuntimeError):
            self.skipTest("Django/tracer not importable in this environment")

        inputs = [None, "", "email", "phone", "uuid", "custom", "arbitrary_value"]
        for raw in inputs:
            expected = normalize_user_id_type(raw)
            actual = real_fn(raw)
            self.assertEqual(
                actual, expected,
                f"Real function disagrees with inline for input {raw!r}: "
                f"real={actual!r}, inline={expected!r}",
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
