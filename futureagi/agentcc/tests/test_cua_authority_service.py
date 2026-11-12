"""Internal service controls; a mocked primary read is not database proof."""

import copy
import unittest
from datetime import timedelta
from unittest.mock import patch

from agentcc.services import cua_authority
from agentcc.services.cua_authority_policy import AuthorityDenied, AuthorityUnavailable
from agentcc.tests.cua_authority_fixtures import NOW, expected, requirements, snapshot, subject


class AuthorityServiceTests(unittest.TestCase):
    def setUp(self):
        self.read = self.enterContext(patch.object(cua_authority, "read_primary", return_value=snapshot()))
        self.clock = self.enterContext(patch.object(cua_authority, "monotonic", side_effect=[10.0, 10.2]))
        self.wall = self.enterContext(patch.object(cua_authority, "_now", return_value=NOW))

    def check(self, who=None, exp=None, needs=None):
        return cua_authority.check_personal_authority(
            subject() if who is None else who,
            expected() if exp is None else exp,
            requirements() if needs is None else needs,
        )

    def test_positive_independently_observed_policy(self):
        result = self.check()
        self.assertEqual(result["result"], "allow")
        self.assertEqual(result["subject"], subject())
        self.assertEqual(result["admission_comparison"]["result"], "match")
        self.assertEqual(result["admission_comparison"]["central_admission_id"], expected()["central_admission_id"])
        self.assertEqual(result["observed_policy"]["policy_revision"], 12)
        self.assertEqual(result["valid_until"], "2026-11-06T12:00:01Z")
        self.read.assert_called_once_with(subject())

    def test_expected_echo_cannot_replace_primary_policy(self):
        exp = expected()
        exp["policy_revision"] = 11
        self.assertEqual(self.check(exp=exp), {"result": "deny"})

    def test_missing_expected_refuses_before_database(self):
        self.assertEqual(self.check(exp={}), {"result": "unavailable"})
        self.read.assert_not_called()

    def test_missing_authority_subject_refuses(self):
        self.assertEqual(self.check(who={}), {"result": "unavailable"})
        self.read.assert_not_called()

    def test_unregistered_delegation_refuses(self):
        self.assertEqual(self.check(who={**subject(), "kind": "delegated"}), {"result": "unavailable"})
        self.read.assert_not_called()

    def test_all_unimplemented_authority_modes_refuse(self):
        for field in requirements():
            with self.subTest(field=field):
                self.assertEqual(self.check(needs={**requirements(), field: True}), {"result": "unavailable"})
        self.read.assert_not_called()

    def test_missing_or_wrong_typed_requirements_refuse(self):
        for bad in ({}, {**requirements(), "root_run": 0}, {**requirements(), "extra": False}):
            self.assertEqual(self.check(needs=bad), {"result": "unavailable"})
        self.read.assert_not_called()

    def test_caller_claims_cannot_be_authority(self):
        for field in ("is_admin", "membership", "owner", "metadata", "root_run"):
            self.assertEqual(self.check(who={**subject(), field: True}), {"result": "unavailable"})
        self.read.assert_not_called()

    def test_noncanonical_identity_refuses(self):
        self.assertEqual(self.check(who={**subject(), "workspace_id": "-"}), {"result": "unavailable"})
        self.read.assert_not_called()

    def test_known_membership_or_revocation_denial_is_value_free(self):
        self.read.side_effect = AuthorityDenied()
        self.assertEqual(self.check(), {"result": "deny"})

    def test_unavailable_primary_has_no_stale_fallback(self):
        self.read.side_effect = AuthorityUnavailable()
        self.assertEqual(self.check(), {"result": "unavailable"})

    def test_database_error_does_not_echo_exception(self):
        self.read.side_effect = RuntimeError("synthetic private diagnostic")
        self.assertEqual(self.check(), {"result": "unavailable"})

    def test_subject_mismatch_denied(self):
        snap = snapshot()
        snap["subject"]["workspace_id"] = "00000000-0000-4000-8000-000000000009"
        self.read.return_value = snap
        self.assertEqual(self.check(), {"result": "deny"})

    def test_exact_key_expiry_denied(self):
        snap = snapshot()
        snap["expires_at"] = NOW
        self.read.return_value = snap
        self.assertEqual(self.check(), {"result": "deny"})

    def test_credential_expiry_shortens_validity(self):
        snap = snapshot()
        snap["expires_at"] = NOW + timedelta(milliseconds=500)
        self.read.return_value = snap
        self.assertEqual(self.check()["valid_until"], "2026-11-06T12:00:00.500000Z")

    def test_naive_credential_expiry_refuses(self):
        snap = snapshot()
        snap["expires_at"] = NOW.replace(tzinfo=None)
        self.read.return_value = snap
        self.assertEqual(self.check(), {"result": "unavailable"})

    def test_one_second_boundary_refuses(self):
        self.clock.side_effect = [10.0, 11.0]
        self.assertEqual(self.check(), {"result": "unavailable"})

    def test_response_completion_does_not_reset_freshness(self):
        self.clock.side_effect = [10.0, 10.99]
        self.wall.side_effect = [NOW, NOW + timedelta(milliseconds=990)]
        self.assertEqual(self.check()["valid_until"], "2026-11-06T12:00:01Z")

    def test_expiry_during_read_denied(self):
        snap = snapshot()
        snap["expires_at"] = NOW + timedelta(milliseconds=100)
        self.read.return_value = snap
        self.wall.side_effect = [NOW, NOW + timedelta(milliseconds=200)]
        self.assertEqual(self.check(), {"result": "deny"})

    def test_no_positive_cache_on_repeat_checks(self):
        self.clock.side_effect = [10.0, 10.2, 10.3, 10.4]
        self.read.side_effect = [snapshot(), AuthorityDenied()]
        self.assertEqual(self.check()["result"], "allow")
        self.assertEqual(self.check(), {"result": "deny"})
        self.assertEqual(self.read.call_count, 2)

    def test_output_does_not_alias_input_or_contain_credentials(self):
        exp, who = expected(), subject()
        original = copy.deepcopy((exp, who))
        result = self.check(exp=exp, who=who)
        result["admission_comparison"]["expected_activations"]["key_daily"]["activation_id"] = "changed"
        result["subject"]["key_id"] = "changed"
        self.assertEqual((exp, who), original)
        self.assertNotIn("key_hash", repr(result))
