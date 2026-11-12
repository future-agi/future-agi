"""Independent offline completeness regressions for the credential census."""

import runpy
import unittest
from pathlib import Path

TESTS = Path(__file__).resolve().parent
SUPPORT = runpy.run_path(str(TESTS / "test_hard_erase_r2f_flow.py"))
POLICY = runpy.run_path(str(TESTS.parent / "services/hard_erase_sql_policy.py"))


class CredentialExclusionTests(unittest.TestCase):
    def test_independent_source_oracle_and_reviewed_inventory(self):
        scanner = runpy.run_path(str(TESTS / "test_hard_erase_sql_source.py"))
        oracle = runpy.run_path(str(TESTS / "hard_erase_census_oracle.py"))
        sources = list(scanner["tracked_sources"]())
        _, actual, _ = scanner["model_census"](sources)
        _, required, _ = scanner["model_census"](
            sources,
            rule=lambda module, name, table, fields: oracle["requires_protection"](
                module, name, table, fields, POLICY["EXCLUDED_FIELDS"]
            ),
        )
        self.assertTrue(set(required) <= set(actual), set(required) - set(actual))
        for row in oracle["INVENTORY"]:
            self.assertIn(row["table"], actual)
            self.assertTrue(row["review"].strip())
            # The reviewed inventory independently protects even generic fields.
            model = SUPPORT["fake_model"](row["module"], row["model"], [], row["table"])
            self.assertIn(model, POLICY["protected_models"]([model]))
        # Prove the oracle still detects the historical omissions without using
        # production classification as its expected set.
        incomplete = set(actual) - {
            "simulate_hosted_harness_secret",
            "agentcc_email_alert",
        }
        self.assertFalse(set(required) <= incomplete)

    def test_every_exclusion_is_field_specific_and_never_excludes_new_credentials(self):
        self.assertEqual(
            set(POLICY["CREDENTIAL_EXCLUSIONS"]), set(POLICY["EXCLUDED_FIELDS"])
        )
        for table, names in POLICY["EXCLUDED_FIELDS"].items():
            self.assertTrue(POLICY["CREDENTIAL_EXCLUSIONS"][table].strip())
            for name, kind in (
                ("api_key", "CharField"),
                ("password", "CharField"),
                (names[0], "EncryptedTextField"),
            ):
                with self.subTest(table=table, field=name, kind=kind):
                    model = SUPPORT["fake_model"](
                        "new.models",
                        "UnrelatedModel",
                        [SUPPORT["field"](name, kind=kind)],
                        table,
                    )
                    self.assertIn(model, POLICY["protected_models"]([model]))

    def test_new_identity_membership_and_grant_models_are_automatically_included(self):
        for name in (
            "NewIdentity",
            "NewMembership",
            "NewGrant",
            "NewPermission",
            "NewAuthorization",
        ):
            model = SUPPORT["fake_model"](
                "new.models", name, [SUPPORT["field"]("subject")]
            )
            self.assertIn(model, POLICY["protected_models"]([model]))

    def test_identity_and_grant_fields_protect_unrelated_model_names(self):
        for field_name in ("identity", "membership", "grant", "authorization"):
            model = SUPPORT["fake_model"](
                "new.models", "Unrelated", [SUPPORT["field"](field_name)]
            )
            self.assertIn(model, POLICY["protected_models"]([model]))

    def test_worker_capability_digests_are_not_noncredential_homonyms(self):
        for table in (
            "tracer_trace_grouping_attempt",
            "tracer_trace_grouping_feature_job",
            "tracer_trace_grouping_severity_job",
            "tracer_trace_investigation_attempt",
        ):
            self.assertNotIn(table, POLICY["CREDENTIAL_EXCLUSIONS"])
            model = SUPPORT["fake_model"](
                "new.models", "Worker", [SUPPORT["field"]("lease_token_digest")], table
            )
            self.assertIn(model, POLICY["protected_models"]([model]))

    def test_inherited_concrete_fields_and_auto_created_relations_are_included(self):
        model = SUPPORT["fake_model"](
            "new.models", "InheritedCredential", [SUPPORT["field"]("token")]
        )
        through = SUPPORT["fake_model"]("new.models", "Relation", [])
        through._meta.auto_created = model
        self.assertEqual(POLICY["protected_models"]([model, through]), {model, through})
        model._meta.proxy = True
        self.assertEqual(POLICY["protected_models"]([model, through]), set())


if __name__ == "__main__":
    unittest.main()
