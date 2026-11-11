"""Guard the dev-only CDN change against affecting production or other handlers."""

import copy
import importlib.util
import unittest
from pathlib import Path


SOURCE = Path(__file__).parents[2] / "scripts" / "deploy-dev-spa-router.py"
SPEC = importlib.util.spec_from_file_location("dev_spa_router", SOURCE)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
ARN = "arn:aws:cloudfront::123456789012:function/fagi-dev-spa-test"


def config():
    return {
        "Enabled": True,
        "Aliases": {"Quantity": 1, "Items": ["dev.futureagi.com"]},
        "Origins": {"Items": [{"DomainName": "dev-static.s3.amazonaws.com"}]},
        "CustomErrorResponses": {"Quantity": 1, "Items": [{"ErrorCode": 404}]},
        "DefaultCacheBehavior": {
            "CachePolicyId": "unchanged",
            "OriginRequestPolicyId": "unchanged",
            "FunctionAssociations": {"Quantity": 0},
        },
    }


class DevSpaRouterTests(unittest.TestCase):
    def test_changes_only_default_viewer_request_association(self):
        original = config()
        snapshot = copy.deepcopy(original)
        changed = MODULE.attach_router(original, ARN)
        self.assertEqual(original, snapshot)
        expected = copy.deepcopy(original)
        expected["DefaultCacheBehavior"]["FunctionAssociations"] = {
            "Quantity": 1,
            "Items": [{"EventType": "viewer-request", "FunctionARN": ARN}],
        }
        self.assertEqual(changed, expected)
        self.assertEqual(MODULE.attach_router(changed, ARN), changed)

    def test_rejects_production_shared_and_disabled_distributions(self):
        for aliases in (["app.futureagi.com"], ["dev.futureagi.com", "app.futureagi.com"], []):
            with self.subTest(aliases=aliases):
                value = config()
                value["Aliases"] = {"Quantity": len(aliases), "Items": aliases}
                with self.assertRaises(ValueError):
                    MODULE.attach_router(value, ARN)
        value = config()
        value["Enabled"] = False
        with self.assertRaises(ValueError):
            MODULE.attach_router(value, ARN)

    def test_refuses_existing_viewer_request_owners(self):
        for key in ("FunctionAssociations", "LambdaFunctionAssociations"):
            with self.subTest(key=key):
                value = config()
                value["DefaultCacheBehavior"][key] = {
                    "Quantity": 1,
                    "Items": [{"EventType": "viewer-request", "FunctionARN": "different"}],
                }
                with self.assertRaises(ValueError):
                    MODULE.attach_router(value, ARN)

    def test_preserves_other_function_events(self):
        value = config()
        response = {"EventType": "viewer-response", "FunctionARN": "keep-response"}
        value["DefaultCacheBehavior"]["FunctionAssociations"] = {
            "Quantity": 1, "Items": [response]
        }
        updated = MODULE.attach_router(value, ARN)
        self.assertEqual(updated["DefaultCacheBehavior"]["FunctionAssociations"]["Items"][0], response)


if __name__ == "__main__":
    unittest.main()
