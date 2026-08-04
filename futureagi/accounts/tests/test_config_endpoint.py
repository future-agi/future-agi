from unittest.mock import patch

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from tfc import ee_gating

try:
    from ee.usage.deployment import _detect_mode
except ImportError:
    _detect_mode = None


def _clear_mode_caches():
    if _detect_mode is not None:
        _detect_mode.cache_clear()
    # The config endpoint reads the lru_cached tfc.ee_gating oracles; clear
    # them so override_settings takes effect per test.
    ee_gating.is_cloud.cache_clear()
    ee_gating.is_oss.cache_clear()
    ee_gating.is_ee.cache_clear()


class PublicConfigEndpointTest(TestCase):
    def setUp(self):
        self.client = APIClient()
        _clear_mode_caches()

    def tearDown(self):
        _clear_mode_caches()

    def test_default_self_hosted(self):
        """Without CLOUD_DEPLOYMENT, returns cloud=false."""
        response = self.client.get("/accounts/config/")
        self.assertEqual(response.status_code, 200)
        result = response.json()["result"]
        self.assertFalse(result["cloud"])
        self.assertIsNone(result["region"])
        self.assertEqual(result["available_regions"], [])

    @patch("ee.usage.deployment._validate_cloud_secret", return_value=True)
    @override_settings(
        CLOUD_DEPLOYMENT="US",
        REGION="us",
        AVAILABLE_REGIONS="us:US (Ohio):https://us.futureagi.com,eu:EU (Frankfurt):https://eu.futureagi.com",
    )
    def test_cloud_deployment(self, _mock_secret):
        """With CLOUD_DEPLOYMENT=US + valid secret, returns cloud=true with region info."""
        _detect_mode.cache_clear()
        response = self.client.get("/accounts/config/")
        self.assertEqual(response.status_code, 200)
        result = response.json()["result"]
        self.assertTrue(result["cloud"])
        self.assertEqual(result["region"], "us")
        self.assertEqual(len(result["available_regions"]), 2)
        self.assertEqual(result["available_regions"][0]["code"], "us")
        self.assertEqual(result["available_regions"][1]["code"], "eu")
        self.assertEqual(
            result["available_regions"][1]["app_url"],
            "https://eu.futureagi.com",
        )

    @override_settings(
        CLOUD_DEPLOYMENT="DEV",
        CLOUD_DEPLOYMENT_SECRET="",
        REGION="dev",
        AVAILABLE_REGIONS="",
    )
    def test_cloud_without_secret_returns_not_cloud(self):
        """CLOUD_DEPLOYMENT=DEV without secret → cloud=false (bypass prevented)."""
        _detect_mode.cache_clear()
        response = self.client.get("/accounts/config/")
        self.assertEqual(response.status_code, 200)
        result = response.json()["result"]
        self.assertFalse(result["cloud"])

    @patch("ee.usage.deployment._validate_cloud_secret", return_value=True)
    @override_settings(CLOUD_DEPLOYMENT="DEV", REGION="dev", AVAILABLE_REGIONS="")
    def test_cloud_no_regions(self, _mock_secret):
        """Cloud with no AVAILABLE_REGIONS returns empty list."""
        _detect_mode.cache_clear()
        response = self.client.get("/accounts/config/")
        self.assertEqual(response.status_code, 200)
        result = response.json()["result"]
        self.assertTrue(result["cloud"])
        self.assertEqual(result["region"], "dev")
        self.assertEqual(result["available_regions"], [])

    @patch("ee.usage.deployment._validate_cloud_secret", return_value=True)
    @override_settings(
        CLOUD_DEPLOYMENT="US",
        REGION="us",
        AVAILABLE_REGIONS="bad-entry,also:bad,us:US (Ohio):https://us.futureagi.com",
    )
    def test_malformed_regions_skipped(self, _mock_secret):
        """Entries with < 3 colon-separated parts are silently skipped."""
        _detect_mode.cache_clear()
        response = self.client.get("/accounts/config/")
        self.assertEqual(response.status_code, 200)
        result = response.json()["result"]
        self.assertEqual(len(result["available_regions"]), 1)
        self.assertEqual(result["available_regions"][0]["code"], "us")

    @override_settings(CLOUD_DEPLOYMENT="INVALID", REGION="us", AVAILABLE_REGIONS="")
    def test_unknown_cloud_deployment_is_not_cloud(self):
        """Unrecognized CLOUD_DEPLOYMENT values treated as self-hosted."""
        _detect_mode.cache_clear()
        response = self.client.get("/accounts/config/")
        self.assertEqual(response.status_code, 200)
        result = response.json()["result"]
        self.assertFalse(result["cloud"])
        self.assertIsNone(result["region"])

    @patch("ee.usage.deployment._validate_cloud_secret", return_value=True)
    @override_settings(
        CLOUD_DEPLOYMENT="EU",
        REGION="eu",
        AVAILABLE_REGIONS="us:US (Ohio):https://us.futureagi.com,eu:EU (Frankfurt):https://eu.futureagi.com",
    )
    def test_eu_region(self, _mock_secret):
        """EU deployment returns correct region and both regions."""
        _detect_mode.cache_clear()
        response = self.client.get("/accounts/config/")
        self.assertEqual(response.status_code, 200)
        result = response.json()["result"]
        self.assertTrue(result["cloud"])
        self.assertEqual(result["region"], "eu")
        self.assertEqual(len(result["available_regions"]), 2)

    def test_post_not_allowed(self):
        """Only GET is allowed."""
        response = self.client.post("/accounts/config/")
        self.assertEqual(response.status_code, 405)
