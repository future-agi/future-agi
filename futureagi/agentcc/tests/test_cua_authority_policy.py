"""H03 FA policy comparison REDs; stdlib-only, no Django setup or network."""

import copy
import unittest
from datetime import timedelta, timezone

from agentcc.services.cua_authority_policy import (
    AuthorityDenied, AuthorityUnavailable, compare_policy, observe_policy,
    validate_expected,
)
from agentcc.tests.cua_authority_fixtures import (
    ACTIVATION, STAMP, disabled, expected, snapshot,
)


class PolicyComparisonTests(unittest.TestCase):
    def observe(self, snap=None):
        snap = snapshot() if snap is None else snap
        return observe_policy(snap)

    def test_daily_key_only_matches_without_an_agent(self):
        observed = self.observe()
        self.assertEqual(compare_policy(expected(), observed), "match")
        self.assertEqual(observed, {k: v for k, v in expected().items()
                                   if k != "central_admission_id"})

    def test_disabled_positive_revision_is_explicit(self):
        snap, exp = disabled()
        self.assertEqual(compare_policy(exp, self.observe(snap)), "match")

    def test_no_bucket_enabled_policy_is_explicit(self):
        snap, exp = snapshot(), expected()
        snap["budgets"]["key_ids"] = {}
        exp["applicability"]["key_daily"] = "not_applicable"
        exp["activations"]["key_daily"] = {"state": "not_applicable"}
        exp["policy_relation"]["key_daily"] = None
        self.assertEqual(compare_policy(exp, self.observe(snap)), "match")

    def test_disabled_vs_enabled_refuses_both_directions(self):
        snap, exp = disabled()
        self.assertEqual(compare_policy(expected(), self.observe(snap)), "mismatch")
        self.assertEqual(compare_policy(exp, self.observe()), "mismatch")

    def test_same_revision_does_not_hide_any_changed_policy_fact(self):
        changes = [
            (("applicability", "key_daily"), "not_applicable"),
            (("policy_relation", "administrative_agent"), "other-agent"),
            (("policy_relation", "key_daily", "limit_micros"), 2500001),
            (("policy_relation", "org_id"), "00000000-0000-4000-8000-000000000009"),
            (("activations", "key_daily", "activation_id"), "2026-11-02T00:00:00Z"),
            (("activations", "key_daily", "level"), "key_id:other-key"),
        ]
        for path, value in changes:
            with self.subTest(path=path):
                exp = expected()
                node = exp
                for part in path[:-1]:
                    node = node[part]
                node[path[-1]] = value
                self.assertNotEqual(compare_policy(exp, self.observe()), "match")

    def test_changed_revision_mismatches(self):
        exp = expected()
        exp["policy_revision"] = 11
        self.assertEqual(compare_policy(exp, self.observe()), "mismatch")

    def test_missing_extra_or_wrong_typed_expected_evidence_refuses(self):
        variants = [None, {}, {**expected(), "extra": True}]
        for field in expected():
            bad = expected()
            del bad[field]
            variants.append(bad)
        for revision in (0, -1, True, 12.0, "12", 2**63):
            variants.append({**expected(), "policy_revision": revision})
        bad = expected()
        bad["policy_relation"]["key_daily"]["limit_micros"] = True
        variants.append(bad)
        bad = expected()
        bad["activations"]["agent_daily"] = {}
        variants.append(bad)
        bad = expected()
        bad["central_admission_id"] = ""
        variants.append(bad)
        for value in variants:
            with self.subTest(value_type=type(value).__name__):
                with self.assertRaises(AuthorityUnavailable):
                    validate_expected(value)

    def test_never_configured_and_inconsistent_revision_refuse(self):
        for durable, configured in ((0, 0), (None, 12), (13, 12), (True, 1), (12, 0)):
            with self.subTest(durable=durable, configured=configured):
                snap = snapshot()
                snap["revision"] = durable
                snap["budgets"]["policy_revision"] = configured
                with self.assertRaises(AuthorityUnavailable):
                    self.observe(snap)

    def test_missing_authoritative_activation_refuses(self):
        snap = snapshot()
        snap["activations"] = {}
        with self.assertRaises(AuthorityUnavailable):
            self.observe(snap)

    def test_authoritative_and_config_activation_must_equal_exactly(self):
        for stored in ("2026-11-01T00:00:00+00:00", "2026-11-01T00:00:00.000000Z", "", None):
            with self.subTest(stored=stored):
                snap = snapshot()
                snap["budgets"]["key_ids"]["h03-key"]["activated_at"] = stored
                with self.assertRaises(AuthorityUnavailable):
                    self.observe(snap)

    def test_activation_utc_conversion_matches_existing_owner(self):
        snap = snapshot()
        snap["activations"]["key_id:h03-key"] = ACTIVATION.astimezone(
            timezone(timedelta(hours=5, minutes=30)))
        self.assertEqual(self.observe(snap)["activations"]["key_daily"]["activation_id"], STAMP)

    def test_naive_activation_is_not_proof(self):
        snap = snapshot()
        snap["activations"]["key_id:h03-key"] = ACTIVATION.replace(tzinfo=None)
        with self.assertRaises(AuthorityUnavailable):
            self.observe(snap)

    def test_agent_caps_without_binding_denied(self):
        snap = snapshot()
        snap["budgets"]["agents"] = {"h03-agent": {"limit": 1, "activated_at": STAMP}}
        with self.assertRaises(AuthorityDenied):
            self.observe(snap)

    def test_key_and_agent_activations_both_compared(self):
        snap = snapshot()
        snap["budgets"]["agents"] = {"h03-agent": {"limit": 1, "activated_at": STAMP}}
        snap["routing"] = {"agents": {"key_bindings": {"h03-key": "h03-agent"}}}
        snap["activations"]["agent:h03-agent"] = ACTIVATION
        observed = self.observe(snap)
        exp = expected()
        exp["applicability"].update(agent_daily="required", administrative_binding="required")
        exp["policy_relation"].update(administrative_agent="h03-agent",
                                    agent_daily={"limit_micros": 1000000, "period": "daily",
                                                 "hard": True, "action": "block"})
        exp["activations"]["agent_daily"] = {"state": "required", "level": "agent:h03-agent",
                                             "activation_id": STAMP}
        self.assertEqual(compare_policy(exp, observed), "match")
        for slot in ("key_daily", "agent_daily"):
            changed = copy.deepcopy(exp)
            changed["activations"][slot]["activation_id"] = "2026-11-02T00:00:00Z"
            self.assertEqual(compare_policy(changed, observed), "mismatch")

    def test_run_limit_without_authoritative_run_bridge_refuses(self):
        for limit in (0, 1):
            snap = snapshot()
            snap["routing"] = {"agents": {"max_run_usd": limit}}
            with self.assertRaises(AuthorityUnavailable):
                self.observe(snap)

    def test_existing_cap_semantics_not_weakened(self):
        for field, value in (("period", "weekly"), ("hard", False), ("action", "warn"),
                             ("limit", True), ("limit", -1), ("limit", float("nan")),
                             ("extra", 1)):
            with self.subTest(field=field):
                snap = snapshot()
                snap["budgets"]["key_ids"]["h03-key"][field] = value
                with self.assertRaises(AuthorityUnavailable):
                    self.observe(snap)

    def test_exact_zero_and_floor_to_micros(self):
        for usd, micros in ((0, 0), (1.2345678, 1234567), (1000000000, 1000000000000000)):
            snap = snapshot()
            snap["budgets"]["key_ids"]["h03-key"]["limit"] = usd
            self.assertEqual(self.observe(snap)["policy_relation"]["key_daily"]["limit_micros"], micros)

    def test_observation_and_expected_are_not_mutated_or_aliased(self):
        snap, exp = snapshot(), expected()
        original = copy.deepcopy(snap)
        observed = self.observe(snap)
        self.assertEqual(compare_policy(exp, observed), "match")
        observed["policy_relation"]["key_daily"]["limit_micros"] = 0
        self.assertEqual(snap, original)
        self.assertEqual(exp, expected())
