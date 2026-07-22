"""Unit tests for ``tracer.services.clickhouse.v2.deterministic_id``.

PURE — no DB, no Django. The id formula is a FROZEN byte contract shared by the
historical remap (``ch25_build_id_remap``) and the future ingestion path (P3b
step2); these tests guard the three things that matter:

  1. The namespace seeds + key layout never drift — pinned GOLDEN LITERALS (a
     recompute-with-the-same-code assertion would silently pass a seed edit, so
     the literals, not the formula, are the real guard).
  2. The NULL/``""`` ``user_id_type`` sentinel collapses to ONE id — the
     mechanism behind the box dry-run's 879→544 consolidation.
  3. Identities that genuinely differ get different ids; recompute is stable.

Run (no infra needed)::

    pytest tracer/tests/test_deterministic_id.py -v
"""

from __future__ import annotations

import unittest
import uuid

from tracer.services.clickhouse.v2.deterministic_id import (
    NS_ENDUSER,
    NS_SESSION,
    deterministic_end_user_id,
    deterministic_trace_session_id,
)

# ─── Golden literals — the FROZEN contract ──────────────────────────────────
# These are the exact values the validated box dry-run (879→544 / 1405→1404) and
# the future ingestion path MUST reproduce. Any change here means a breaking
# re-key migration — DO NOT edit to "make the test pass".
_GOLD_NS_ENDUSER = "97daafcc-ae7b-5a44-a76b-c85e63059e1c"
_GOLD_NS_SESSION = "1c4977df-2af9-5330-b34f-7969ffdabf25"
# A known enduser id: the NULL/"" sentinel case (project=p, org=o, user_id=u).
_GOLD_EU_SENTINEL = "a740d3f6-3215-535c-8687-2f3df0decd78"

# Real island rows (pg-test) — precomputed; the in-container build must match.
_ISLAND_EU = {
    # (project_id, organization_id, user_id, user_id_type) -> new_id
    (
        "61840705-018d-415e-b9f0-4120f06e1fcc",
        "42a08887-dba2-4b47-9f12-46ec87b1df9f",
        "test_user",
        None,
    ): "4b98d9c8-56cc-597b-a3e8-ef51f70ea230",
    (
        "86a643c9-791b-4b48-9516-a8da3c058ed7",
        "36ab6a86-28ef-484e-9fa2-0aade2cde52d",
        "sarthak@futureagi.com",
        None,
    ): "65e4ebd8-682d-5788-9bd0-e0d4a69d437b",
}
_ISLAND_SESSION = {
    # (project_id, name) -> new_id
    (
        "61840705-018d-415e-b9f0-4120f06e1fcc",
        "test_session",
    ): "78ec9dff-4299-542d-84e9-a5555e5976f6",
    (
        "86a643c9-791b-4b48-9516-a8da3c058ed7",
        "new-session",
    ): "a720e946-644e-59a3-9fc5-2adbd66e631c",
    (
        "86a643c9-791b-4b48-9516-a8da3c058ed7",
        "new-session2",
    ): "0ebc5453-6a11-56a6-931d-59f278193161",
}


class TestNamespaceConstants(unittest.TestCase):
    """The namespaces equal the formula AND the pinned golden literals."""

    def test_namespaces_match_formula(self):
        self.assertEqual(
            NS_ENDUSER, uuid.uuid5(uuid.NAMESPACE_DNS, "futureagi.enduser.v1")
        )
        self.assertEqual(
            NS_SESSION, uuid.uuid5(uuid.NAMESPACE_DNS, "futureagi.session.v1")
        )

    def test_namespaces_match_golden_literals(self):
        # Catches a seed edit (e.g. v1 -> v2) that the formula assertion misses.
        self.assertEqual(str(NS_ENDUSER), _GOLD_NS_ENDUSER)
        self.assertEqual(str(NS_SESSION), _GOLD_NS_SESSION)


class TestEndUserId(unittest.TestCase):
    def test_known_input_golden(self):
        got = deterministic_end_user_id("p", "o", "u", None)
        self.assertEqual(str(got), _GOLD_EU_SENTINEL)

    def test_null_and_empty_type_collapse_to_same_id(self):
        # THE CONSOLIDATION MECHANISM: NULL user_id_type and "" must give the SAME
        # id (this is what merges the 744 NULL-type box rows — 879→544).
        null_id = deterministic_end_user_id("p", "o", "u", None)
        empty_id = deterministic_end_user_id("p", "o", "u", "")
        self.assertEqual(null_id, empty_id)
        # And NOT the literal "None" — a blanket str(None) would break the contract.
        none_literal_id = deterministic_end_user_id("p", "o", "u", "None")
        self.assertNotEqual(null_id, none_literal_id)

    def test_differ_only_by_user_id_gives_different_id(self):
        a = deterministic_end_user_id("p", "o", "u", None)
        b = deterministic_end_user_id("p", "o", "u2", None)
        self.assertNotEqual(a, b)

    def test_differ_by_project_or_org_gives_different_id(self):
        base = deterministic_end_user_id("p", "o", "u", None)
        self.assertNotEqual(base, deterministic_end_user_id("p2", "o", "u", None))
        self.assertNotEqual(base, deterministic_end_user_id("p", "o2", "u", None))

    def test_distinct_type_stays_split(self):
        # D2: user_id_type stays IN the key, so a typed row is a DIFFERENT id from
        # the NULL-type row (the one cross-type box group stays distinct).
        null_id = deterministic_end_user_id("p", "o", "u", None)
        email_id = deterministic_end_user_id("p", "o", "u", "email")
        self.assertNotEqual(null_id, email_id)

    def test_recompute_is_stable(self):
        first = deterministic_end_user_id("p", "o", "u", "email")
        second = deterministic_end_user_id("p", "o", "u", "email")
        self.assertEqual(first, second)

    def test_uuid_input_equals_str_input(self):
        # A uuid.UUID project_id and its canonical string yield the same id
        # (the build str()-coerces; PG hands either form).
        pid = uuid.UUID("61840705-018d-415e-b9f0-4120f06e1fcc")
        oid = uuid.UUID("42a08887-dba2-4b47-9f12-46ec87b1df9f")
        from_uuid = deterministic_end_user_id(pid, oid, "test_user", None)
        from_str = deterministic_end_user_id(str(pid), str(oid), "test_user", None)
        self.assertEqual(from_uuid, from_str)

    def test_island_rows_match_precomputed(self):
        for (pid, oid, uid, utype), expected in _ISLAND_EU.items():
            self.assertEqual(
                str(deterministic_end_user_id(pid, oid, uid, utype)), expected
            )


class TestTraceSessionId(unittest.TestCase):
    def test_differ_by_name_or_project(self):
        base = deterministic_trace_session_id("p", "s")
        self.assertNotEqual(base, deterministic_trace_session_id("p", "s2"))
        self.assertNotEqual(base, deterministic_trace_session_id("p2", "s"))

    def test_recompute_is_stable(self):
        self.assertEqual(
            deterministic_trace_session_id("p", "s"),
            deterministic_trace_session_id("p", "s"),
        )

    def test_uuid_input_equals_str_input(self):
        pid = uuid.UUID("61840705-018d-415e-b9f0-4120f06e1fcc")
        self.assertEqual(
            deterministic_trace_session_id(pid, "test_session"),
            deterministic_trace_session_id(str(pid), "test_session"),
        )

    def test_island_rows_match_precomputed(self):
        for (pid, name), expected in _ISLAND_SESSION.items():
            self.assertEqual(str(deterministic_trace_session_id(pid, name)), expected)


if __name__ == "__main__":
    unittest.main()
