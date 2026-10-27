"""BillingConfig — singleton service that loads, validates, and caches billing.yaml.

Usage:
    from ee.usage.services.config import BillingConfig

    config = BillingConfig.get()
    config.get_plan("boost").platform_fee  # Decimal("250")
    config.get_entitlement_default("monitors", "free")  # 3
    config.get_free_allowance("storage", "free")  # Decimal("50")
"""

from __future__ import annotations

import os
from decimal import Decimal
from typing import Optional, Union

import structlog
import yaml

from ee.usage.schemas.billing_config import (
    AICreditModelConfig,
    ApiCallTypeConfig,
    BillingConfigSchema,
    DimensionConfig,
    EEBandConfig,
    PlanConfig,
    PricingTier,
)

logger = structlog.get_logger(__name__)


class BillingConfigError(Exception):
    """Raised when billing config is invalid or missing."""

    pass


class BillingConfig:
    """Singleton service for billing configuration.

    Loads billing.yaml once on first access. Validates via Pydantic.
    Thread-safe (Python GIL handles reference swap on reload).
    """

    _instance: Optional[BillingConfig] = None
    _schema: BillingConfigSchema

    def __init__(self, schema: BillingConfigSchema) -> None:
        self._schema = schema

    @classmethod
    def get(cls) -> BillingConfig:
        """Return the singleton instance. Loads config on first call."""
        if cls._instance is None:
            cls._instance = cls._load()
        return cls._instance

    @classmethod
    def reload(cls) -> BillingConfig:
        """Force reload of billing config from YAML. Returns new instance."""
        cls._instance = cls._load()
        logger.info("billing_config_reloaded")
        return cls._instance

    @classmethod
    def _empty(cls) -> BillingConfig:
        """Config with no plans/types: metering and entitlement lookups all
        take their fail-open paths (unknown call type → allowed)."""
        return cls(
            BillingConfigSchema(
                plans={},
                dimensions={},
                pricing_tiers={},
                entitlements={},
                api_call_types={},
                ee_bands={},
                ai_credit_models={},
            )
        )

    @classmethod
    def _load(cls) -> BillingConfig:
        """Load and validate billing.yaml."""
        from django.conf import settings

        path = getattr(
            settings,
            "BILLING_CONFIG_PATH",
            os.path.join(settings.BASE_DIR, "billing.yaml"),
        )

        if not os.path.exists(path):
            # billing.yaml ships only with the private cloud overlay. On
            # OSS/EE deployments its absence is expected — fall open to an
            # empty config so metering call sites no-op. On cloud, quota
            # enforcement must fail closed: keep raising.
            from ee.usage.deployment import DeploymentMode

            if not DeploymentMode.is_cloud():
                logger.warning(
                    "billing_config_missing_using_empty_defaults",
                    path=path,
                    mode=DeploymentMode.get_mode(),
                )
                return cls._empty()
            raise BillingConfigError(
                f"billing.yaml not found at {path}. "
                f"Set BILLING_CONFIG_PATH env var or create the file."
            )

        try:
            with open(path) as f:
                raw = yaml.safe_load(f)
        except yaml.YAMLError as e:
            raise BillingConfigError(f"Invalid YAML in {path}: {e}") from e

        if not isinstance(raw, dict):
            raise BillingConfigError(
                f"billing.yaml must be a YAML mapping, got {type(raw).__name__}"
            )

        try:
            schema = BillingConfigSchema(**raw)
        except Exception as e:
            raise BillingConfigError(f"Billing config validation failed:\n{e}") from e

        logger.info(
            "billing_config_loaded",
            path=path,
            plans=list(schema.plans.keys()),
            dimensions=list(schema.dimensions.keys()),
            entitlements_count=len(schema.entitlements),
            api_call_types_count=len(schema.api_call_types),
        )

        return cls(schema)

    # ── Plan accessors ──────────────────────────────────────────────────

    def get_plan(self, plan_key: str) -> PlanConfig:
        """Get plan configuration by key. Raises KeyError if not found."""
        if plan_key not in self._schema.plans:
            raise KeyError(
                f"Plan '{plan_key}' not found. "
                f"Valid plans: {list(self._schema.plans.keys())}"
            )
        return self._schema.plans[plan_key]

    def get_all_plans(self) -> dict[str, PlanConfig]:
        """Get all plan configurations."""
        return dict(self._schema.plans)

    # ── Dimension accessors ─────────────────────────────────────────────

    def get_dimension(self, dim_key: str) -> DimensionConfig:
        """Get dimension configuration by key."""
        if dim_key not in self._schema.dimensions:
            raise KeyError(
                f"Dimension '{dim_key}' not found. "
                f"Valid dimensions: {list(self._schema.dimensions.keys())}"
            )
        return self._schema.dimensions[dim_key]

    def get_all_dimensions(self) -> dict[str, DimensionConfig]:
        """Get all dimension configurations."""
        return dict(self._schema.dimensions)

    # ── Pricing accessors ───────────────────────────────────────────────

    def get_pricing_tiers(self, dim_key: str) -> list[PricingTier]:
        """Get pricing tiers for a dimension. Returns empty list if no tiers defined."""
        return list(self._schema.pricing_tiers.get(dim_key, []))

    # ── Entitlement accessors ───────────────────────────────────────────

    def get_entitlement_default(
        self, feature: str, plan: str
    ) -> Union[int, bool, None]:
        """Get default entitlement value for a feature+plan combo.

        Returns None if the feature or plan is not configured.
        """
        feature_map = self._schema.entitlements.get(feature)
        if feature_map is None:
            return None
        return feature_map.get(plan)

    def get_free_allowance(self, dimension: str, plan: str) -> Decimal:
        """Get free allowance for a dimension in display units.

        Looks up `free_{dimension}_{display_unit}` or `free_{dimension}` in entitlements.
        Returns 0 if not configured.
        """
        # Try dimension-specific key: free_storage_gb, free_ai_credits, etc.
        dim_config = self._schema.dimensions.get(dimension)
        if dim_config:
            key = f"free_{dimension}_{dim_config.display_unit.lower()}"
            val = self.get_entitlement_default(key, plan)
            if val is not None:
                return Decimal(str(val))

        # Try generic key: free_{dimension}
        key = f"free_{dimension}"
        val = self.get_entitlement_default(key, plan)
        if val is not None:
            return Decimal(str(val))

        return Decimal("0")

    # ── API call type accessors ─────────────────────────────────────────

    def get_call_type(self, call_type_key: str) -> ApiCallTypeConfig:
        """Get API call type configuration."""
        if call_type_key not in self._schema.api_call_types:
            raise KeyError(
                f"API call type '{call_type_key}' not found. "
                f"Valid types: {list(self._schema.api_call_types.keys())}"
            )
        return self._schema.api_call_types[call_type_key]

    def get_all_call_types(self) -> dict[str, ApiCallTypeConfig]:
        """Get all API call type configurations."""
        return dict(self._schema.api_call_types)

    # ── EE band accessors ───────────────────────────────────────────────

    def get_ee_band(self, band_key: str) -> EEBandConfig:
        """Get EE license band configuration."""
        if band_key not in self._schema.ee_bands:
            raise KeyError(
                f"EE band '{band_key}' not found. "
                f"Valid bands: {list(self._schema.ee_bands.keys())}"
            )
        return self._schema.ee_bands[band_key]

    def get_all_ee_bands(self) -> dict[str, EEBandConfig]:
        """Get all EE band configurations."""
        return dict(self._schema.ee_bands)

    # ── AI credit model accessors ───────────────────────────────────────

    def get_ai_credit_model(self, model_name: str) -> Optional[AICreditModelConfig]:
        """Get AI credit calculation config for a model. Returns None if unknown."""
        return self._schema.ai_credit_models.get(model_name)

    def get_all_ai_credit_models(self) -> dict[str, AICreditModelConfig]:
        """Get all AI credit model configurations."""
        return dict(self._schema.ai_credit_models)

    # ── AI cost markup accessor ─────────────────────────────────────────

    def get_ai_cost_markup(self) -> Decimal:
        """Get the AI cost markup multiplier.

        Returns the flat markup applied to actual LLM cost before converting
        to credits. Default 1.0 (no markup) if not configured.

        Credit formula: credits = (actual_cost * markup) / price_per_credit
        """
        return self._schema.ai_cost_markup

    def get_eval_per_run_fee(self) -> float:
        """Get the per-run platform fee for eval runs ($0.50 per 1000 runs).

        Applied to all eval types: Agent, LLM-as-Judge, Code.
        """
        return float(self._schema.eval_per_run_fee)

    def calculate_ai_credits(
        self, actual_cost_usd: Union[Decimal, float, int]
    ) -> float:
        """Convert actual LLM cost (USD) to AI credits.

        Formula: credits = (actual_cost * markup) / price_per_credit
        Returns fractional credits. Returns 1 credit when cost is unknown or zero.
        """
        cost = Decimal(str(actual_cost_usd))
        if cost <= 0:
            return 1.0
        markup = self._schema.ai_cost_markup
        price_per_credit = Decimal("0.01")
        return float((cost * markup) / price_per_credit)
