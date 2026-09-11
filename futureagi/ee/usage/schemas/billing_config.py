"""Pydantic schemas for billing.yaml validation.

These models strictly validate the billing configuration file on startup.
Invalid config = fail fast with a clear error message.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Any, Literal, Optional, Union

from pydantic import BaseModel, Field, model_validator

# All valid plan keys — must match PlanChoices enum
VALID_PLANS = frozenset({"free", "payg", "boost", "scale", "enterprise", "custom"})
ALL_PLANS_SHORTHAND = "_all"


class PlanConfig(BaseModel):
    """Configuration for a single billing plan."""

    model_config = {"frozen": True}

    display_name: str = Field(..., min_length=1)
    platform_fee: Annotated[Decimal, Field(ge=0)]
    usage_caps: Literal["hard", "soft"]


class DimensionConfig(BaseModel):
    """Configuration for a single billing dimension."""

    model_config = {"frozen": True}

    display_name: str = Field(..., min_length=1)
    native_unit: str = Field(..., min_length=1)
    display_unit: str = Field(..., min_length=1)
    native_to_display_divisor: Annotated[int, Field(gt=0)]
    aggregation: Literal["sum", "count", "max"]


class PricingTier(BaseModel):
    """A single tier in tiered pricing."""

    model_config = {"frozen": True}

    up_to: Optional[Annotated[Decimal, Field(gt=0)]] = (
        None  # None = unlimited (last tier)
    )
    price_per_unit: Annotated[Decimal, Field(ge=0)]


class ApiCallTypeConfig(BaseModel):
    """Mapping from an API call type to a billing dimension."""

    model_config = {"frozen": True}

    dimension: str = Field(..., min_length=1)
    per_call: Optional[Annotated[int, Field(ge=0)]] = None
    amount_from: Optional[str] = None

    @model_validator(mode="after")
    def validate_has_amount_source(self) -> "ApiCallTypeConfig":
        """At least one of per_call or amount_from must be set."""
        if self.per_call is None and self.amount_from is None:
            raise ValueError(
                "API call type must have at least one of: per_call, amount_from"
            )
        return self


class EEBandLimits(BaseModel):
    """Usage limits for an EE license band."""

    model_config = {"frozen": True}

    max_traces_monthly: int = Field(..., description="-1 = unlimited")
    max_gateway_monthly: int = Field(..., description="-1 = unlimited")
    max_users: int = Field(..., description="-1 = unlimited")


class EEBandConfig(BaseModel):
    """Configuration for a single EE license band."""

    model_config = {"frozen": True}

    display_name: str = Field(..., min_length=1)
    monthly_price: Annotated[Decimal, Field(ge=0)]
    annual_price: Annotated[Decimal, Field(ge=0)]
    limits: EEBandLimits
    features: list[str]


class AICreditModelConfig(BaseModel):
    """Per-model credit calculation for gateway metering."""

    model_config = {"frozen": True}

    credits_per_1k_input_tokens: Annotated[Decimal, Field(ge=0)]
    credits_per_1k_output_tokens: Annotated[Decimal, Field(ge=0)]


# Entitlement value: either int (-1 = unlimited) or bool (feature flag)
EntitlementValue = Union[int, bool]
# Per-plan entitlement map: {plan_key: value} or {_all: value}
EntitlementMap = dict[str, EntitlementValue]


def expand_all_shorthand(raw: dict[str, Any]) -> dict[str, EntitlementValue]:
    """Expand `_all` shorthand into per-plan values."""
    if ALL_PLANS_SHORTHAND in raw:
        value = raw[ALL_PLANS_SHORTHAND]
        return {plan: value for plan in VALID_PLANS}
    return raw


class BillingConfigSchema(BaseModel):
    """Root schema for billing.yaml. Validates the entire config on load."""

    model_config = {"frozen": True}

    plans: dict[str, PlanConfig]
    dimensions: dict[str, DimensionConfig]
    pricing_tiers: dict[str, list[PricingTier]]
    entitlements: dict[str, EntitlementMap]
    api_call_types: dict[str, ApiCallTypeConfig]
    ee_bands: dict[str, EEBandConfig]
    ai_credit_models: dict[str, AICreditModelConfig]
    ai_cost_markup: Annotated[
        Decimal, Field(ge=Decimal("1.0"), default=Decimal("1.0"))
    ] = Decimal("1.0")
    eval_per_run_fee: Annotated[
        Decimal, Field(ge=Decimal("0"), default=Decimal("0.0005"))
    ] = Decimal("0.0005")

    @model_validator(mode="before")
    @classmethod
    def expand_entitlement_all(cls, data: Any) -> Any:
        """Expand `_all` shorthand in entitlements before validation."""
        if isinstance(data, dict) and "entitlements" in data:
            expanded = {}
            for feature, plan_map in data["entitlements"].items():
                if isinstance(plan_map, dict):
                    expanded[feature] = expand_all_shorthand(plan_map)
                else:
                    expanded[feature] = plan_map
            data["entitlements"] = expanded
        return data

    @model_validator(mode="after")
    def validate_cross_references(self) -> "BillingConfigSchema":
        """Validate cross-references between sections."""
        errors: list[str] = []

        # 1. Plan keys must match VALID_PLANS
        for plan_key in self.plans:
            if plan_key not in VALID_PLANS:
                errors.append(f"Plan '{plan_key}' not in valid plans: {VALID_PLANS}")

        # 2. Pricing tier keys must exist in dimensions
        for dim_key in self.pricing_tiers:
            if dim_key not in self.dimensions:
                errors.append(
                    f"Pricing tier dimension '{dim_key}' not found in dimensions"
                )

        # 3. API call type dimensions must exist
        for call_type, config in self.api_call_types.items():
            if config.dimension not in self.dimensions:
                errors.append(
                    f"API call type '{call_type}' references unknown dimension "
                    f"'{config.dimension}'"
                )

        # 4. Entitlement plan keys must be valid
        for feature, plan_map in self.entitlements.items():
            for plan_key in plan_map:
                if plan_key not in VALID_PLANS:
                    errors.append(
                        f"Entitlement '{feature}' has invalid plan key '{plan_key}'"
                    )

        # 5. Pricing tiers must be sorted ascending by up_to
        for dim_key, tiers in self.pricing_tiers.items():
            prev_up_to = Decimal(0)
            for i, tier in enumerate(tiers):
                if tier.up_to is not None:
                    if tier.up_to <= prev_up_to:
                        errors.append(
                            f"Pricing tier '{dim_key}' tier {i}: up_to={tier.up_to} "
                            f"must be > previous up_to={prev_up_to}"
                        )
                    prev_up_to = tier.up_to
                elif i != len(tiers) - 1:
                    errors.append(
                        f"Pricing tier '{dim_key}': up_to=null must be the last tier"
                    )

        if errors:
            raise ValueError(
                f"Billing config validation failed:\n"
                + "\n".join(f"  - {e}" for e in errors)
            )

        return self
