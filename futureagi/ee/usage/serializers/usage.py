from rest_framework import serializers

from accounts.models.organization import Organization
from ee.usage.models.usage import (
    APICallLog,
    APICallType,
    OrganizationBilling,
    OrganizationSubscription,
    Pricing,
    RateLimit,
    ResourceLimits,
    ResourceType,
    SubscriptionTier,
)


class SubscriptionTierSerializer(serializers.ModelSerializer):
    class Meta:
        model = SubscriptionTier
        ref_name = "UsageSubscriptionTier"
        fields = [
            "id",
            "name",
            "description",
            "stripe_price_id",
            # "stripe_price_ids_all",
            "wallet_refill_amount",
        ]
        read_only_fields = ["name"]


class OrganizationBillingSerializer(serializers.ModelSerializer):
    class Meta:
        model = OrganizationBilling
        ref_name = "UsageOrganizationBilling"
        fields = [
            "id",
            "organization",
            "billing_contact_name",
            "billing_contact_email",
            "company",
            "billing_address1",
            "billing_address2",
            "city",
            "state",
            "country",
            "postal_code",
            "tax_id",
        ]
        read_only_fields = ["organization"]


class OrganizationSubscriptionCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = OrganizationSubscription
        ref_name = "UsageOrganizationSubscriptionCreate"
        fields = [
            "next_renewal_date",
            "subscription_price",
            "subscription_future_tier",
            "subscription_future_start_date",
            "subscription_future_price",
            "status",
            "wallet_refill_amount",
            "wallet_balance",
            "stripe_customer_id_test",
            "stripe_customer_id_live",
            "auto_recharge_enabled",
            "auto_recharge_amount",
            "auto_recharge_threshold",
            "payment_method_id",
            "custom_subscription_id",
            "organization",
            "subscription_tier",
        ]


class OrganizationSubscriptionSerializer(serializers.ModelSerializer):
    subscription_tier = serializers.SerializerMethodField()

    class Meta:
        model = OrganizationSubscription
        ref_name = "UsageOrganizationSubscription"
        fields = [
            "id",
            "organization",
            "subscription_tier",
            "custom_subscription_id",
            "status",
            "subscription_price",
            "wallet_balance",
            "wallet_refill_amount",
            "next_renewal_date",
            "subscription_future_tier",
            "subscription_future_start_date",
            "subscription_future_price",
            "stripe_customer_id_test",
            "stripe_customer_id_live",
            "auto_recharge_enabled",
            "auto_recharge_amount",
            "auto_recharge_threshold",
            "payment_method_id",
            "last_refill_date",
            "last_refill_amount",
        ]

    def get_subscription_tier(self, obj):
        return obj.subscription_tier.name


class OrganizationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Organization
        ref_name = "UsageOrganization"
        fields = ["id", "name"]


class PricingCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Pricing
        ref_name = "UsagePricingCreate"
        fields = ["id", "api_call_type", "price_per_call", "organization"]


class PricingSerializer(serializers.ModelSerializer):
    api_call_type = serializers.SerializerMethodField()

    class Meta:
        model = Pricing
        ref_name = "UsagePricing"
        fields = [
            "id",
            "api_call_type",
            "price_per_call",
            "organization",
        ]

    def get_api_call_type(self, obj):
        return obj.api_call_type.name


class APICallTypeSerializer(serializers.ModelSerializer):
    class Meta:
        model = APICallType
        ref_name = "UsageAPICallType"
        fields = ["id", "name", "description"]


class ResourceTypeSerializer(serializers.ModelSerializer):
    class Meta:
        model = ResourceType
        ref_name = "UsageResourceType"
        fields = ["id", "name", "description"]


class ResourceLimitCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = ResourceLimits
        ref_name = "UsageResourceLimitCreate"
        fields = ["id", "resource_type", "subscription_tier", "limit", "organization"]


class ResourceLimitSerializer(serializers.ModelSerializer):
    resource_type = serializers.SerializerMethodField()
    subscription_tier = serializers.SerializerMethodField()

    class Meta:
        model = ResourceLimits
        ref_name = "UsageResourceLimit"
        fields = ["id", "resource_type", "subscription_tier", "limit", "organization"]

    def get_resource_type(self, obj):
        return obj.resource_type.name

    def get_subscription_tier(self, obj):
        return obj.subscription_tier.name


class RateLimitCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = RateLimit
        ref_name = "UsageRateLimitCreate"
        fields = [
            "id",
            "api_call_type",
            "organization",
            "minute_limit",
            "hour_limit",
            "day_limit",
            "month_limit",
            "subscription_tier",
        ]


class RateLimitSerializer(serializers.ModelSerializer):
    subscription_tier = serializers.SerializerMethodField()
    api_call_type = serializers.SerializerMethodField()

    class Meta:
        model = RateLimit
        ref_name = "UsageRateLimit"
        fields = [
            "id",
            "api_call_type",
            "organization",
            "minute_limit",
            "hour_limit",
            "day_limit",
            "month_limit",
            "subscription_tier",
        ]

    def get_subscription_tier(self, obj):
        return obj.subscription_tier.name

    def get_api_call_type(self, obj):
        return obj.api_call_type.name


class APICallLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = APICallLog
        ref_name = "UsageAPICallLog"
        fields = "__all__"
