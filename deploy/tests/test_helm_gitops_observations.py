"""Status-observation contracts; no cluster or product execution."""

import copy
import unittest

from deploy.tests.helm_gitops.acceptance import (
    argo_at_digest,
    flux_condition,
    status_projection,
)


class GitOpsObservationTests(unittest.TestCase):
    def test_status_evidence_excludes_inline_values_and_messages(self):
        status = {
            "sync": {
                "status": "Synced",
                "revision": "digest",
                "comparedTo": {
                    "source": {"helm": {"valuesObject": {"password": "do-not-retain"}}}
                },
            },
            "history": [{"source": {"helm": {"values": "private"}}}],
            "operationState": {
                "phase": "Failed",
                "message": "private",
                "syncResult": {"revision": "digest", "source": {"values": "private"}},
            },
            "conditions": [
                {
                    "type": "Ready",
                    "status": "False",
                    "reason": "Failed",
                    "message": "private",
                }
            ],
        }
        self.assertEqual(
            status_projection(status),
            {
                "sync": {"status": "Synced", "revision": "digest"},
                "operationState": {"phase": "Failed", "revision": "digest"},
                "conditions": [
                    {"type": "Ready", "status": "False", "reason": "Failed"}
                ],
            },
        )

    def test_flux_stale_ready_is_not_current_success(self):
        doc = {
            "metadata": {"generation": 3},
            "status": {
                "observedGeneration": 2,
                "conditions": [
                    {"type": "Ready", "status": "True", "observedGeneration": 2}
                ],
            },
        }
        self.assertFalse(flux_condition(doc, "Ready", "True"))
        doc["status"]["observedGeneration"] = 3
        self.assertFalse(flux_condition(doc, "Ready", "True"))
        doc["status"]["conditions"][0]["observedGeneration"] = 3
        self.assertTrue(flux_condition(doc, "Ready", "True"))

    def test_flux_current_failure_does_not_need_old_global_generation(self):
        doc = {
            "metadata": {"generation": 3},
            "status": {
                "observedGeneration": 2,
                "conditions": [
                    {
                        "type": "SourceVerified",
                        "status": "False",
                        "observedGeneration": 3,
                    }
                ],
            },
        }
        self.assertTrue(flux_condition(doc, "SourceVerified", "False"))
        self.assertFalse(flux_condition(doc, "Ready", "True"))

    def test_flux_missing_generation_or_condition_refuses(self):
        self.assertFalse(flux_condition({}, "Ready", "True"))
        self.assertFalse(
            flux_condition(
                {"metadata": {"generation": 1}, "status": {}}, "Ready", "True"
            )
        )
        doc = {
            "metadata": {"generation": 1},
            "status": {"conditions": [{"type": "Ready", "status": "True"}]},
        }
        self.assertFalse(flux_condition(doc, "Ready", "True"))
        doc["status"]["observedGeneration"] = 1
        self.assertTrue(flux_condition(doc, "Ready", "True"))

    def test_argo_requires_exact_sync_and_operation_digest(self):
        digest = "sha256:" + "a" * 64
        doc = {
            "status": {
                "operationState": {
                    "phase": "Succeeded",
                    "syncResult": {"revision": digest},
                },
                "sync": {"revision": digest, "status": "Synced"},
                "health": {"status": "Healthy"},
            }
        }
        self.assertTrue(argo_at_digest(doc, digest))
        for group, field, value in [
            ("sync", "revision", "sha256:" + "b" * 64),
            ("sync", "status", "OutOfSync"),
            ("health", "status", "Progressing"),
            ("operationState", "phase", "Running"),
        ]:
            changed = copy.deepcopy(doc)
            changed["status"][group][field] = value
            self.assertFalse(argo_at_digest(changed, digest))
        doc["status"]["operationState"]["syncResult"]["revision"] = "old"
        self.assertFalse(argo_at_digest(doc, digest))

    def test_argo_old_failure_cannot_satisfy_target_failure(self):
        doc = {
            "status": {
                "operationState": {"phase": "Failed", "syncResult": {"revision": "old"}}
            }
        }
        self.assertFalse(argo_at_digest(doc, "target", "Failed"))
        doc["status"]["operationState"]["syncResult"]["revision"] = "target"
        self.assertTrue(argo_at_digest(doc, "target", "Failed"))
        doc["status"]["operationState"]["phase"] = "Succeeded"
        self.assertFalse(argo_at_digest(doc, "target", "Failed"))


if __name__ == "__main__":
    unittest.main()
