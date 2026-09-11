"""Missing billing.yaml behavior (OSS/EE deployments).

billing.yaml ships only with the private cloud overlay. Absence must fail
open on OSS/EE (empty config, metering no-ops) and fail closed on cloud
(quota enforcement without config is a deployment bug).

Unlike test_billing_config.py, this module runs without the real file.
"""

from decimal import Decimal
from unittest.mock import patch

import pytest
from django.test import override_settings

from ee.usage.services.config import BillingConfig, BillingConfigError


def _missing_path():
    return override_settings(BILLING_CONFIG_PATH="/nonexistent/billing.yaml")


class TestMissingBillingYaml:
    def teardown_method(self):
        """Reset singleton so later tests reload whatever config exists."""
        BillingConfig._instance = None

    def test_missing_file_non_cloud_returns_empty_config(self):
        BillingConfig._instance = None
        with _missing_path(), patch(
            "ee.usage.deployment.DeploymentMode.is_cloud", return_value=False
        ), patch(
            "ee.usage.deployment.DeploymentMode.get_mode", return_value="oss"
        ):
            config = BillingConfig.get()

        assert config.get_all_plans() == {}
        assert config.get_all_call_types() == {}
        assert config.get_all_dimensions() == {}
        assert config.get_entitlement_default("monitors", "free") is None
        assert config.get_free_allowance("storage", "free") == Decimal("0")
        # defaults from the schema still apply
        assert config.get_ai_cost_markup() == Decimal("1.0")

    def test_missing_file_non_cloud_metering_fails_open(self):
        BillingConfig._instance = None
        with _missing_path(), patch(
            "ee.usage.deployment.DeploymentMode.is_cloud", return_value=False
        ), patch(
            "ee.usage.deployment.DeploymentMode.get_mode", return_value="ee"
        ):
            from ee.usage.services.metering import check_usage

            result = check_usage("org-any", "traces_ingested")

        assert result.allowed is True

    def test_missing_file_on_cloud_raises(self):
        BillingConfig._instance = None
        with _missing_path(), patch(
            "ee.usage.deployment.DeploymentMode.is_cloud", return_value=True
        ):
            with pytest.raises(BillingConfigError, match="not found"):
                BillingConfig.get()
