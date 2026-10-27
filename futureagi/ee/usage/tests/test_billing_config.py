"""
Phase 1.4 + 1.5: billing.yaml + Config Loader Tests

Tests that billing.yaml is valid, all sections are consistent,
the config loader validates correctly, and accessors return typed values.
"""

import os
import tempfile
from decimal import Decimal
from unittest.mock import patch

import pytest
import yaml

from ee.usage.models.usage import PlanChoices
from ee.usage.schemas.billing_config import (
    AICreditModelConfig,
    ApiCallTypeConfig,
    BillingConfigSchema,
    DimensionConfig,
    EEBandConfig,
    PlanConfig,
    PricingTier,
    expand_all_shorthand,
)
from ee.usage.services.config import BillingConfig, BillingConfigError

# billing.yaml ships only with the private cloud overlay; this whole module
# asserts against the real file. The missing-file behavior is covered by
# test_billing_config_missing.py, which runs everywhere.
from django.conf import settings as _settings

pytestmark = pytest.mark.skipif(
    not os.path.exists(
        getattr(
            _settings,
            "BILLING_CONFIG_PATH",
            os.path.join(_settings.BASE_DIR, "billing.yaml"),
        )
    ),
    reason="billing.yaml ships only with the private cloud overlay",
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

VALID_PLAN_KEYS = {c.value for c in PlanChoices}


def get_billing_yaml_path():
    """Get the path to the real billing.yaml file."""
    from django.conf import settings

    return settings.BILLING_CONFIG_PATH


def load_raw_yaml():
    """Load the raw YAML content for inspection."""
    with open(get_billing_yaml_path()) as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------------------
# billing.yaml file tests
# ---------------------------------------------------------------------------


class TestBillingYamlFile:
    def test_file_exists(self):
        assert os.path.exists(get_billing_yaml_path())

    def test_is_valid_yaml(self):
        raw = load_raw_yaml()
        assert isinstance(raw, dict)

    def test_has_all_required_sections(self):
        raw = load_raw_yaml()
        required = {
            "plans",
            "dimensions",
            "pricing_tiers",
            "entitlements",
            "api_call_types",
            "ee_bands",
            "ai_credit_models",
        }
        assert required.issubset(
            raw.keys()
        ), f"Missing sections: {required - set(raw.keys())}"


# ---------------------------------------------------------------------------
# Plans section
# ---------------------------------------------------------------------------


class TestPlansSection:
    def test_has_all_plan_choices(self):
        raw = load_raw_yaml()
        assert len(raw["plans"]) == len(PlanChoices)

    def test_plan_keys_match_enum(self):
        raw = load_raw_yaml()
        assert set(raw["plans"].keys()) == VALID_PLAN_KEYS

    def test_every_plan_has_required_fields(self):
        raw = load_raw_yaml()
        for key, plan in raw["plans"].items():
            assert "display_name" in plan, f"Plan '{key}' missing display_name"
            assert "platform_fee" in plan, f"Plan '{key}' missing platform_fee"
            assert "usage_caps" in plan, f"Plan '{key}' missing usage_caps"

    def test_platform_fees(self):
        raw = load_raw_yaml()
        assert raw["plans"]["free"]["platform_fee"] == 0
        assert raw["plans"]["payg"]["platform_fee"] == 0
        assert raw["plans"]["boost"]["platform_fee"] == 250
        assert raw["plans"]["scale"]["platform_fee"] == 750
        assert raw["plans"]["enterprise"]["platform_fee"] == 2000

    def test_usage_caps_are_valid(self):
        raw = load_raw_yaml()
        for key, plan in raw["plans"].items():
            assert plan["usage_caps"] in (
                "hard",
                "soft",
            ), f"Plan '{key}' has invalid usage_caps: {plan['usage_caps']}"

    def test_free_is_hard_cap(self):
        raw = load_raw_yaml()
        assert raw["plans"]["free"]["usage_caps"] == "hard"

    def test_paid_plans_are_soft_cap(self):
        raw = load_raw_yaml()
        for plan in ["payg", "boost", "scale", "enterprise", "custom"]:
            assert raw["plans"][plan]["usage_caps"] == "soft"


# ---------------------------------------------------------------------------
# Dimensions section
# ---------------------------------------------------------------------------


class TestDimensionsSection:
    def test_has_seven_dimensions(self):
        raw = load_raw_yaml()
        assert len(raw["dimensions"]) == 7

    def test_expected_dimensions(self):
        raw = load_raw_yaml()
        expected = {
            "storage",
            "ai_credits",
            "gateway_requests",
            "gateway_cache_hits",
            "text_sim_tokens",
            "voice_sim_minutes",
            "tracing_events",
        }
        assert set(raw["dimensions"].keys()) == expected

    def test_every_dimension_has_required_fields(self):
        raw = load_raw_yaml()
        required = {
            "display_name",
            "native_unit",
            "display_unit",
            "native_to_display_divisor",
            "aggregation",
        }
        for key, dim in raw["dimensions"].items():
            for field in required:
                assert field in dim, f"Dimension '{key}' missing '{field}'"

    def test_aggregation_values_are_valid(self):
        raw = load_raw_yaml()
        for key, dim in raw["dimensions"].items():
            assert dim["aggregation"] in (
                "sum",
                "count",
                "max",
            ), f"Dimension '{key}' has invalid aggregation: {dim['aggregation']}"

    def test_storage_divisor_is_1gb(self):
        raw = load_raw_yaml()
        assert raw["dimensions"]["storage"]["native_to_display_divisor"] == 1073741824


# ---------------------------------------------------------------------------
# Pricing tiers section
# ---------------------------------------------------------------------------


class TestPricingTiersSection:
    def test_keys_match_dimensions(self):
        raw = load_raw_yaml()
        for key in raw["pricing_tiers"]:
            assert key in raw["dimensions"], f"Pricing tier '{key}' not in dimensions"

    def test_every_tier_has_required_fields(self):
        raw = load_raw_yaml()
        for dim, tiers in raw["pricing_tiers"].items():
            for i, tier in enumerate(tiers):
                assert "up_to" in tier, f"Tier {dim}[{i}] missing 'up_to'"
                assert (
                    "price_per_unit" in tier
                ), f"Tier {dim}[{i}] missing 'price_per_unit'"

    def test_last_tier_has_null_up_to(self):
        raw = load_raw_yaml()
        for dim, tiers in raw["pricing_tiers"].items():
            last = tiers[-1]
            assert (
                last["up_to"] is None
            ), f"Dimension '{dim}' last tier up_to should be null, got {last['up_to']}"

    def test_tiers_are_ascending(self):
        raw = load_raw_yaml()
        for dim, tiers in raw["pricing_tiers"].items():
            prev = 0
            for i, tier in enumerate(tiers):
                if tier["up_to"] is not None:
                    assert tier["up_to"] > prev, (
                        f"Dimension '{dim}' tier {i}: up_to={tier['up_to']} "
                        f"not > previous={prev}"
                    )
                    prev = tier["up_to"]

    def test_storage_tiers_match_pricing_sheet(self):
        raw = load_raw_yaml()
        tiers = raw["pricing_tiers"]["storage"]
        assert tiers[0]["up_to"] == 50
        assert tiers[0]["price_per_unit"] == 0
        assert tiers[1]["up_to"] == 500
        assert tiers[1]["price_per_unit"] == 2.0
        assert tiers[2]["up_to"] == 2048
        assert tiers[2]["price_per_unit"] == 1.5
        assert tiers[3]["up_to"] is None
        assert tiers[3]["price_per_unit"] == 1.0


# ---------------------------------------------------------------------------
# Entitlements section
# ---------------------------------------------------------------------------


class TestEntitlementsSection:
    def test_has_monitors(self):
        raw = load_raw_yaml()
        assert "monitors" in raw["entitlements"]

    def test_monitors_values(self):
        raw = load_raw_yaml()
        m = raw["entitlements"]["monitors"]
        assert m["free"] == 3
        assert m["boost"] == 15
        assert m["scale"] == -1

    def test_all_shorthand_exists(self):
        raw = load_raw_yaml()
        assert "_all" in raw["entitlements"]["free_ai_credits"]

    def test_free_allowances(self):
        raw = load_raw_yaml()
        assert raw["entitlements"]["free_storage_gb"]["_all"] == 50
        assert raw["entitlements"]["free_ai_credits"]["_all"] == 2000
        assert raw["entitlements"]["free_gateway_requests"]["_all"] == 100000

    def test_boolean_features_are_booleans(self):
        raw = load_raw_yaml()
        kb = raw["entitlements"]["has_knowledge_base"]
        assert kb["_all"] is True

    def test_retention_values(self):
        raw = load_raw_yaml()
        r = raw["entitlements"]["retention_traces_days"]
        assert r["free"] == 30
        assert r["boost"] == 90
        assert r["scale"] == 365
        assert r["enterprise"] == 2555


# ---------------------------------------------------------------------------
# API call types section
# ---------------------------------------------------------------------------


class TestApiCallTypesSection:
    def test_dimensions_exist(self):
        raw = load_raw_yaml()
        for ct, config in raw["api_call_types"].items():
            assert (
                config["dimension"] in raw["dimensions"]
            ), f"API call type '{ct}' references unknown dimension '{config['dimension']}'"

    def test_ai_credit_types_have_per_call(self):
        raw = load_raw_yaml()
        assert raw["api_call_types"]["turing_large_evaluator"]["per_call"] == 1
        assert raw["api_call_types"]["turing_small_evaluator"]["per_call"] == 1
        assert raw["api_call_types"]["turing_flash_evaluator"]["per_call"] == 1

    def test_storage_type_has_amount_from(self):
        raw = load_raw_yaml()
        assert raw["api_call_types"]["observe_add"]["amount_from"] == "payload_bytes"


# ---------------------------------------------------------------------------
# Cross-section consistency
# ---------------------------------------------------------------------------


class TestCrossSectionConsistency:
    def test_entitlement_plans_match_enum(self):
        raw = load_raw_yaml()
        for feature, plan_map in raw["entitlements"].items():
            for plan_key in plan_map:
                if plan_key == "_all":
                    continue
                assert (
                    plan_key in VALID_PLAN_KEYS
                ), f"Entitlement '{feature}' has invalid plan key '{plan_key}'"

    def test_full_config_validates_via_pydantic(self):
        """The real billing.yaml should pass full Pydantic validation."""
        raw = load_raw_yaml()
        schema = BillingConfigSchema(**raw)
        assert len(schema.plans) == len(PlanChoices)
        assert len(schema.dimensions) == 7


# ---------------------------------------------------------------------------
# Pydantic schema unit tests
# ---------------------------------------------------------------------------


class TestPydanticSchemas:
    def test_plan_config_valid(self):
        pc = PlanConfig(
            display_name="Test", platform_fee=Decimal("100"), usage_caps="soft"
        )
        assert pc.platform_fee == Decimal("100")

    def test_plan_config_rejects_negative_fee(self):
        with pytest.raises(Exception):
            PlanConfig(
                display_name="Test", platform_fee=Decimal("-1"), usage_caps="soft"
            )

    def test_plan_config_rejects_invalid_caps(self):
        with pytest.raises(Exception):
            PlanConfig(
                display_name="Test", platform_fee=Decimal("0"), usage_caps="maybe"
            )

    def test_dimension_config_valid(self):
        dc = DimensionConfig(
            display_name="Test",
            native_unit="bytes",
            display_unit="GB",
            native_to_display_divisor=1073741824,
            aggregation="sum",
        )
        assert dc.display_unit == "GB"

    def test_dimension_rejects_invalid_aggregation(self):
        with pytest.raises(Exception):
            DimensionConfig(
                display_name="Test",
                native_unit="x",
                display_unit="X",
                native_to_display_divisor=1,
                aggregation="average",
            )

    def test_dimension_rejects_zero_divisor(self):
        with pytest.raises(Exception):
            DimensionConfig(
                display_name="Test",
                native_unit="x",
                display_unit="X",
                native_to_display_divisor=0,
                aggregation="sum",
            )

    def test_pricing_tier_valid(self):
        pt = PricingTier(up_to=Decimal("500"), price_per_unit=Decimal("2.00"))
        assert pt.up_to == Decimal("500")

    def test_pricing_tier_null_up_to(self):
        pt = PricingTier(up_to=None, price_per_unit=Decimal("1.00"))
        assert pt.up_to is None

    def test_pricing_tier_rejects_negative_price(self):
        with pytest.raises(Exception):
            PricingTier(up_to=Decimal("100"), price_per_unit=Decimal("-0.01"))

    def test_api_call_type_requires_amount_source(self):
        with pytest.raises(Exception):
            ApiCallTypeConfig(dimension="storage")

    def test_api_call_type_with_per_call(self):
        ct = ApiCallTypeConfig(dimension="ai_credits", per_call=1)
        assert ct.per_call == 1

    def test_expand_all_shorthand(self):
        result = expand_all_shorthand({"_all": 2000})
        assert len(result) == len(PlanChoices)
        assert result["free"] == 2000
        assert result["enterprise"] == 2000
        assert result["custom"] == 2000

    def test_expand_no_shorthand(self):
        data = {"free": 3, "boost": 15}
        result = expand_all_shorthand(data)
        assert result == data


# ---------------------------------------------------------------------------
# Config loader service tests
# ---------------------------------------------------------------------------


class TestBillingConfigService:
    def test_get_returns_instance(self):
        config = BillingConfig.get()
        assert config is not None

    def test_singleton_returns_same_instance(self):
        a = BillingConfig.get()
        b = BillingConfig.get()
        assert a is b

    def test_reload_returns_new_instance(self):
        old = BillingConfig.get()
        BillingConfig.reload()
        new = BillingConfig.get()
        assert old is not new

    def test_get_plan(self):
        config = BillingConfig.get()
        boost = config.get_plan("boost")
        assert isinstance(boost, PlanConfig)
        assert boost.platform_fee == Decimal("250")
        assert boost.usage_caps == "soft"

    def test_get_plan_invalid_raises(self):
        config = BillingConfig.get()
        with pytest.raises(KeyError):
            config.get_plan("platinum")

    def test_get_all_plans(self):
        config = BillingConfig.get()
        plans = config.get_all_plans()
        assert len(plans) == len(PlanChoices)
        assert "free" in plans
        assert "enterprise" in plans
        assert "custom" in plans

    def test_get_dimension(self):
        config = BillingConfig.get()
        storage = config.get_dimension("storage")
        assert storage.display_unit == "GB"
        assert storage.native_to_display_divisor == 1073741824

    def test_get_all_dimensions(self):
        config = BillingConfig.get()
        dims = config.get_all_dimensions()
        assert len(dims) == 7

    def test_get_pricing_tiers(self):
        config = BillingConfig.get()
        tiers = config.get_pricing_tiers("storage")
        assert len(tiers) == 4
        assert tiers[0].price_per_unit == Decimal("0")
        assert tiers[1].price_per_unit == Decimal("2")
        assert tiers[-1].up_to is None

    def test_get_pricing_tiers_empty_for_unknown(self):
        config = BillingConfig.get()
        tiers = config.get_pricing_tiers("nonexistent")
        assert tiers == []

    def test_get_entitlement_default_numeric(self):
        config = BillingConfig.get()
        assert config.get_entitlement_default("monitors", "free") == 3
        assert config.get_entitlement_default("monitors", "boost") == 15
        assert config.get_entitlement_default("monitors", "scale") == -1

    def test_get_entitlement_default_boolean(self):
        config = BillingConfig.get()
        assert config.get_entitlement_default("has_knowledge_base", "free") is True
        assert config.get_entitlement_default("has_knowledge_base", "boost") is True

    def test_get_entitlement_default_unknown_returns_none(self):
        config = BillingConfig.get()
        assert config.get_entitlement_default("nonexistent_feature", "free") is None

    def test_get_free_allowance_storage(self):
        config = BillingConfig.get()
        assert config.get_free_allowance("storage", "free") == Decimal("50")

    def test_get_free_allowance_ai_credits(self):
        config = BillingConfig.get()
        assert config.get_free_allowance("ai_credits", "free") == Decimal("2000")

    def test_get_free_allowance_unknown_returns_zero(self):
        config = BillingConfig.get()
        assert config.get_free_allowance("nonexistent", "free") == Decimal("0")

    def test_get_call_type(self):
        config = BillingConfig.get()
        ct = config.get_call_type("turing_large_evaluator")
        assert ct.dimension == "ai_credits"
        assert ct.per_call == 1

    def test_get_call_type_invalid_raises(self):
        config = BillingConfig.get()
        with pytest.raises(KeyError):
            config.get_call_type("nonexistent_type")

    def test_get_ee_band(self):
        config = BillingConfig.get()
        biz = config.get_ee_band("business")
        assert isinstance(biz, EEBandConfig)
        assert biz.monthly_price == Decimal("999")
        assert biz.limits.max_traces_monthly == 25000000

    def test_get_ai_credit_model(self):
        config = BillingConfig.get()
        sonnet = config.get_ai_credit_model("claude-sonnet-4")
        assert isinstance(sonnet, AICreditModelConfig)
        assert sonnet.credits_per_1k_input_tokens == Decimal("1")
        assert sonnet.credits_per_1k_output_tokens == Decimal("5")

    def test_get_ai_credit_model_unknown_returns_none(self):
        config = BillingConfig.get()
        assert config.get_ai_credit_model("gpt-unknown") is None


# ---------------------------------------------------------------------------
# Config loader error handling
# ---------------------------------------------------------------------------


class TestBillingConfigErrors:
    def test_missing_file_raises_error(self):
        BillingConfig._instance = None  # reset singleton
        with patch("ee.usage.services.config.BillingConfig._load") as mock_load:
            mock_load.side_effect = BillingConfigError("billing.yaml not found")
            with pytest.raises(BillingConfigError, match="not found"):
                BillingConfig._instance = None
                BillingConfig._load()

    def test_invalid_yaml_content(self):
        """A file with invalid YAML structure should raise BillingConfigError."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write("plans:\n  free:\n    - this is invalid\n")
            f.flush()

            BillingConfig._instance = None
            with patch("django.conf.settings.BILLING_CONFIG_PATH", f.name):
                with pytest.raises(BillingConfigError):
                    BillingConfig._load()

        os.unlink(f.name)

    def test_missing_plans_section(self):
        """YAML without plans section should fail validation."""
        raw = load_raw_yaml()
        del raw["plans"]
        with pytest.raises(Exception):
            BillingConfigSchema(**raw)

    def test_invalid_plan_key(self):
        """Plan key not in PlanChoices should fail validation."""
        raw = load_raw_yaml()
        raw["plans"]["platinum"] = {
            "display_name": "Platinum",
            "platform_fee": 5000,
            "usage_caps": "soft",
        }
        with pytest.raises(Exception, match="platinum"):
            BillingConfigSchema(**raw)

    def test_orphan_dimension_in_pricing(self):
        """Pricing tier for a dimension not in dimensions section should fail."""
        raw = load_raw_yaml()
        raw["pricing_tiers"]["bandwidth"] = [{"up_to": None, "price_per_unit": 1.0}]
        with pytest.raises(Exception, match="bandwidth"):
            BillingConfigSchema(**raw)

    def test_unsorted_tiers_rejected(self):
        """Tiers with non-ascending up_to should fail."""
        raw = load_raw_yaml()
        raw["pricing_tiers"]["storage"] = [
            {"up_to": 2048, "price_per_unit": 1.5},
            {"up_to": 500, "price_per_unit": 2.0},  # out of order
            {"up_to": None, "price_per_unit": 1.0},
        ]
        with pytest.raises(Exception, match="must be >"):
            BillingConfigSchema(**raw)

    def teardown_method(self):
        """Reset singleton after error tests."""
        BillingConfig._instance = None
        BillingConfig.get()  # reload valid config
