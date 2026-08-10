from rest_framework import serializers

from ee.usage.serializers.usage import (
    APICallTypeSerializer,
    OrganizationBillingSerializer,
    OrganizationSerializer,
    OrganizationSubscriptionCreateSerializer,
    OrganizationSubscriptionSerializer,
    PricingCreateSerializer,
    PricingSerializer,
    RateLimitCreateSerializer,
    RateLimitSerializer,
    ResourceLimitCreateSerializer,
    ResourceLimitSerializer,
    ResourceTypeSerializer,
    SubscriptionTierSerializer,
)
from tracer.serializers.filters import StrictInputSerializer


class UsageErrorResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    message = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    error = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    detail = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    details = serializers.DictField(
        child=serializers.ListField(child=serializers.CharField()),
        required=False,
        allow_empty=True,
    )


class UsageEmptyRequestSerializer(StrictInputSerializer):
    pass


class UsageMessageResultSerializer(serializers.Serializer):
    message = serializers.CharField()


class UsageMessageResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = UsageMessageResultSerializer()


class UsageJSONResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = serializers.JSONField()


class SubscriptionPlansResultSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=("success", "error"))
    data = serializers.JSONField(required=False)
    current_subscription = serializers.CharField(required=False)
    message = serializers.CharField(required=False, allow_blank=True)


class SubscriptionPlansResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = SubscriptionPlansResultSerializer()


class LastFourDigitsResultSerializer(serializers.Serializer):
    last4 = serializers.CharField(allow_blank=True, allow_null=True)


class LastFourDigitsResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = LastFourDigitsResultSerializer()


class AutoReloadUpdateResponseSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=("success",))
    message = serializers.CharField()


class UpdateBillingDetailsResultSerializer(serializers.Serializer):
    message = serializers.CharField()


class UpdateBillingDetailsResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = UpdateBillingDetailsResultSerializer()


class UsageListResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = serializers.ListField(child=serializers.JSONField())


class UsageStringResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = serializers.CharField()


class SubscriptionTierDetailResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = SubscriptionTierSerializer()


class SubscriptionTierListResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = SubscriptionTierSerializer(many=True)


class OrganizationBillingDetailResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = OrganizationBillingSerializer()


class OrganizationBillingListResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = OrganizationBillingSerializer(many=True)


class OrganizationSubscriptionDetailResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = OrganizationSubscriptionSerializer()


class OrganizationSubscriptionListResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = OrganizationSubscriptionSerializer(many=True)


class OrganizationSubscriptionMutationResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = OrganizationSubscriptionCreateSerializer()


class OrganizationListResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = OrganizationSerializer(many=True)


class PricingDetailResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = PricingCreateSerializer()


class PricingReadResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = PricingSerializer()


class PricingListResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = PricingCreateSerializer(many=True)


class APICallTypeListResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = APICallTypeSerializer(many=True)


class ResourceTypeListResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = ResourceTypeSerializer(many=True)


class ResourceLimitDetailResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = ResourceLimitSerializer()


class ResourceLimitListResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = ResourceLimitSerializer(many=True)


class ResourceLimitMutationResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = ResourceLimitCreateSerializer()


class RateLimitDetailResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = RateLimitSerializer()


class RateLimitListResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = RateLimitSerializer(many=True)


class RateLimitMutationResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = RateLimitCreateSerializer()


class AdminEntitlementOverrideSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    feature = serializers.CharField()
    value_int = serializers.IntegerField(required=False, allow_null=True)
    value_bool = serializers.BooleanField(required=False, allow_null=True)


class AdminEntitlementsQuerySerializer(serializers.Serializer):
    organization_id = serializers.UUIDField()
    feature = serializers.CharField(required=False)


class AdminEntitlementsResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = serializers.DictField()


class AdminEntitlementMutationRequestSerializer(serializers.Serializer):
    organization_id = serializers.UUIDField()
    feature = serializers.CharField()
    value_int = serializers.IntegerField(required=False)
    value_bool = serializers.BooleanField(required=False)


class AdminEntitlementMutationResultSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    feature = serializers.CharField()
    created = serializers.BooleanField()
    value_int = serializers.IntegerField(required=False, allow_null=True)
    value_bool = serializers.BooleanField(required=False, allow_null=True)


class AdminEntitlementMutationResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = AdminEntitlementMutationResultSerializer()


class AdminPricingTierSerializer(serializers.Serializer):
    id = serializers.IntegerField(required=False)
    dimension = serializers.CharField()
    tier_start = serializers.DecimalField(max_digits=20, decimal_places=8)
    tier_end = serializers.DecimalField(
        max_digits=20, decimal_places=8, required=False, allow_null=True
    )
    price_per_unit = serializers.DecimalField(max_digits=20, decimal_places=8)
    display_unit = serializers.CharField(required=False, allow_blank=True)


class AdminPricingQuerySerializer(serializers.Serializer):
    organization_id = serializers.UUIDField()
    dimension = serializers.CharField(required=False)


class AdminPricingListResultSerializer(serializers.Serializer):
    pricing = AdminPricingTierSerializer(many=True)


class AdminPricingListResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = AdminPricingListResultSerializer()


class AdminPricingMutationRequestSerializer(serializers.Serializer):
    organization_id = serializers.UUIDField()
    dimension = serializers.CharField()
    tier_start = serializers.DecimalField(max_digits=20, decimal_places=8)
    tier_end = serializers.DecimalField(
        max_digits=20, decimal_places=8, required=False, allow_null=True
    )
    price_per_unit = serializers.DecimalField(max_digits=20, decimal_places=8)
    display_unit = serializers.CharField(required=False, allow_blank=True)


class AdminPricingMutationResultSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    dimension = serializers.CharField()
    created = serializers.BooleanField()


class AdminPricingMutationResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = AdminPricingMutationResultSerializer()


class AdminCustomPricingTierSerializer(serializers.Serializer):
    tier_start = serializers.DecimalField(max_digits=20, decimal_places=8)
    tier_end = serializers.DecimalField(
        max_digits=20, decimal_places=8, required=False, allow_null=True
    )
    price_per_unit = serializers.DecimalField(max_digits=20, decimal_places=8)
    display_unit = serializers.CharField(required=False, allow_blank=True)


class AdminCustomPlanRequestSerializer(serializers.Serializer):
    organization_id = serializers.UUIDField()
    platform_fee = serializers.DecimalField(
        max_digits=20, decimal_places=8, required=False
    )
    platform_fee_billing_cycle = serializers.IntegerField(required=False, min_value=1)
    contract_end_date = serializers.DateField(required=False, allow_null=True)
    start_date = serializers.DateField(required=False, allow_null=True)
    entitlements = serializers.DictField(child=serializers.JSONField(), required=False)
    pricing = serializers.DictField(
        child=serializers.ListField(child=AdminCustomPricingTierSerializer()),
        required=False,
    )
    create_stripe_subscription = serializers.BooleanField(required=False)


class AdminCustomPlanResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = serializers.DictField()


class AdminInvoiceRequestSerializer(serializers.Serializer):
    organization_id = serializers.UUIDField()
    period = serializers.RegexField(regex=r"^\d{4}-\d{2}$")


class AdminInvoiceLineItemSerializer(serializers.Serializer):
    line_type = serializers.CharField()
    dimension = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    description = serializers.CharField()
    quantity = serializers.CharField()
    unit = serializers.CharField(required=False, allow_blank=True)
    unit_price = serializers.CharField()
    amount = serializers.CharField()
    tier_breakdown = serializers.JSONField(required=False, allow_null=True)


class AdminInvoicePreviewResultSerializer(serializers.Serializer):
    org_id = serializers.UUIDField()
    period = serializers.CharField()
    plan = serializers.CharField()
    backfill_ran = serializers.BooleanField()
    usage_summary_count = serializers.IntegerField()
    invoice_exists = serializers.BooleanField()
    platform_fee = serializers.CharField()
    usage_total = serializers.CharField()
    credits_applied = serializers.CharField()
    subtotal = serializers.CharField()
    tax = serializers.CharField()
    total = serializers.CharField()
    line_items = AdminInvoiceLineItemSerializer(many=True)


class AdminInvoicePreviewResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = AdminInvoicePreviewResultSerializer()


class AdminInvoiceGenerateResultSerializer(serializers.Serializer):
    created = serializers.IntegerField()
    skipped = serializers.IntegerField()
    errors = serializers.IntegerField()
    invoice_id = serializers.UUIDField(required=False)
    total = serializers.CharField(required=False)
    status = serializers.CharField(required=False)
    period_start = serializers.DateField(required=False)
    period_end = serializers.DateField(required=False)
    line_items_count = serializers.IntegerField(required=False)


class AdminInvoiceGenerateResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = AdminInvoiceGenerateResultSerializer()


class CheckoutSessionRequestSerializer(serializers.Serializer):
    subscription_type = serializers.CharField(required=False)


class CheckoutSessionResultSerializer(serializers.Serializer):
    session_id = serializers.CharField(required=False)
    url = serializers.URLField(required=False)
    status = serializers.CharField(required=False)
    message = serializers.CharField(required=False)


class CheckoutSessionResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(required=False)
    result = CheckoutSessionResultSerializer(required=False)
    session_id = serializers.CharField(required=False)
    url = serializers.URLField(required=False)


class CustomPaymentCheckoutRequestSerializer(serializers.Serializer):
    amount = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=0)


class BillingPortalResponseSerializer(serializers.Serializer):
    url = serializers.URLField()


class AutoReloadSettingsRequestSerializer(serializers.Serializer):
    autoreload_enabled = serializers.BooleanField()
    autoreload_walletamount = serializers.DecimalField(max_digits=12, decimal_places=2)
    autoreload_walletthreshold = serializers.DecimalField(
        max_digits=12, decimal_places=2
    )


class AutoReloadSettingsDataSerializer(serializers.Serializer):
    autoreload_enabled = serializers.BooleanField()
    autoreload_wallet_amount = serializers.DecimalField(max_digits=12, decimal_places=2)
    autoreload_wallet_threshold = serializers.DecimalField(
        max_digits=12, decimal_places=2
    )


class AutoReloadSettingsResponseSerializer(serializers.Serializer):
    status = serializers.CharField()
    data = AutoReloadSettingsDataSerializer()


class SubscriptionStatusResultSerializer(serializers.Serializer):
    next_renewal_date = serializers.DateField(required=False, allow_null=True)
    subscription_status = serializers.CharField(required=False, allow_null=True)
    tier = serializers.CharField(required=False, allow_null=True)
    subscription_price = serializers.DecimalField(
        max_digits=12, decimal_places=2, required=False, allow_null=True
    )
    subscription_future_tier = serializers.CharField(required=False, allow_null=True)
    subscription_future_start_date = serializers.DateField(
        required=False, allow_null=True
    )
    subscription_future_price = serializers.DecimalField(
        max_digits=12, decimal_places=2, required=False, allow_null=True
    )


class SubscriptionStatusResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = SubscriptionStatusResultSerializer()


class PricingCardDetailsResultSerializer(serializers.Serializer):
    business_monthly_price = serializers.FloatField()
    business_yearly_price = serializers.FloatField()
    discount_percentage = serializers.IntegerField()
    custom_price = serializers.FloatField(required=False, allow_null=True)


class PricingCardDetailsResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = PricingCardDetailsResultSerializer()


class WalletBalanceResponseSerializer(serializers.Serializer):
    wallet_balance = serializers.DecimalField(max_digits=16, decimal_places=8)


class CustomerInvoiceSerializer(serializers.Serializer):
    date = serializers.CharField()
    id = serializers.CharField()
    is_invoice_available = serializers.BooleanField()
    amount = serializers.CharField()
    receipt_url = serializers.URLField(
        required=False, allow_null=True, allow_blank=True
    )
    payment_type = serializers.CharField()


class CustomerInvoicesResultSerializer(serializers.Serializer):
    invoices = CustomerInvoiceSerializer(many=True)
    total = serializers.IntegerField()


class CustomerInvoicesResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = CustomerInvoicesResultSerializer()


class DownloadInvoiceRequestSerializer(serializers.Serializer):
    invoice_id = serializers.CharField()


class DownloadInvoiceResultSerializer(serializers.Serializer):
    invoice_pdf_url = serializers.URLField()


class DownloadInvoiceResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = DownloadInvoiceResultSerializer()


class BillingInfoSerializer(serializers.Serializer):
    name = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    email = serializers.EmailField(required=False, allow_null=True, allow_blank=True)
    company = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    billing_address1 = serializers.CharField(
        required=False, allow_null=True, allow_blank=True
    )
    billing_address2 = serializers.CharField(
        required=False, allow_null=True, allow_blank=True
    )
    city = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    state = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    country = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    postal_code = serializers.CharField(
        required=False, allow_null=True, allow_blank=True
    )
    tax_id = serializers.CharField(required=False, allow_null=True, allow_blank=True)


class OrganizationBillingLegacyResponseSerializer(serializers.Serializer):
    status = serializers.CharField()
    billing_info = BillingInfoSerializer()


class UpdateOrganizationBillingRequestSerializer(BillingInfoSerializer):
    pass


class APICallCountQuerySerializer(serializers.Serializer):
    year = serializers.IntegerField(required=False)
    month = serializers.IntegerField(required=False, min_value=1, max_value=12)
    api_call_type = serializers.CharField(required=False)


class APICallCountResultSerializer(serializers.Serializer):
    data = serializers.DictField(child=serializers.IntegerField())


class APICallCountResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = APICallCountResultSerializer()


class PricingCalculationResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = serializers.DictField(child=serializers.FloatField())


class MonthYearQuerySerializer(serializers.Serializer):
    month = serializers.IntegerField(required=False, min_value=1, max_value=12)
    year = serializers.IntegerField(required=False)


class WorkspaceUsageEvalQuerySerializer(MonthYearQuerySerializer):
    workspace_id = serializers.UUIDField()


class UsageSummaryResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = serializers.DictField()


class UpgradeToPaygConfirmRequestSerializer(StrictInputSerializer):
    session_id = serializers.CharField()


class UpgradeToPaygPostResultSerializer(serializers.Serializer):
    checkout_url = serializers.URLField()


class PlanResultSerializer(serializers.Serializer):
    plan = serializers.CharField()


class UpgradeToPaygPostResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = UpgradeToPaygPostResultSerializer()


class PlanResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = PlanResultSerializer()


class AddonRequestSerializer(StrictInputSerializer):
    plan = serializers.ChoiceField(
        choices=("boost", "scale", "enterprise"), required=False
    )


class AddonPostResultSerializer(serializers.Serializer):
    subscription_id = serializers.CharField()
    plan = serializers.CharField()


class AddonPostResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = AddonPostResultSerializer()


class PaymentMethodCheckoutResponseSerializer(UpgradeToPaygPostResponseSerializer):
    pass


class SetupIntentConfirmRequestSerializer(StrictInputSerializer):
    session_id = serializers.CharField()


class PaymentMethodConfirmResultSerializer(serializers.Serializer):
    payment_method_id = serializers.CharField()
    set_as_default = serializers.BooleanField()


class PaymentMethodConfirmResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = PaymentMethodConfirmResultSerializer()


class PaymentMethodSerializer(serializers.Serializer):
    id = serializers.CharField()
    brand = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    last4 = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    exp_month = serializers.IntegerField(required=False, allow_null=True)
    exp_year = serializers.IntegerField(required=False, allow_null=True)
    is_default = serializers.BooleanField(required=False)


class PaymentMethodsResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = PaymentMethodSerializer(many=True)


class StripeWebhookRequestSerializer(serializers.Serializer):
    id = serializers.CharField(required=False)
    type = serializers.CharField(required=False)
    data = serializers.DictField(required=False)


class StripeWebhookResultSerializer(serializers.Serializer):
    event_type = serializers.CharField(required=False, allow_blank=True)
    action = serializers.CharField(required=False, allow_blank=True)
    status = serializers.CharField(required=False, allow_blank=True)
    message = serializers.CharField(required=False, allow_blank=True)


class StripeWebhookLegacyResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = StripeWebhookResultSerializer(required=False)


class StripeWebhookResponseSerializer(serializers.Serializer):
    status = serializers.CharField()
    result = StripeWebhookResultSerializer(required=False)


class UsagePeriodQuerySerializer(StrictInputSerializer):
    period = serializers.RegexField(regex=r"^\d{4}-\d{2}$", required=False)
    period_end = serializers.RegexField(regex=r"^\d{4}-\d{2}$", required=False)
    workspace_id = serializers.UUIDField(required=False)


class UsageDimensionPeriodQuerySerializer(StrictInputSerializer):
    dimension = serializers.CharField()
    period = serializers.RegexField(regex=r"^\d{4}-\d{2}$", required=False)
    period_end = serializers.RegexField(regex=r"^\d{4}-\d{2}$", required=False)


class UsageTierBreakdownSerializer(serializers.Serializer):
    tier_start = serializers.FloatField(required=False)
    tier_end = serializers.FloatField(required=False, allow_null=True)
    quantity = serializers.FloatField(required=False)
    rate = serializers.FloatField(required=False)
    cost = serializers.FloatField(required=False)


class UsageOverviewDimensionSerializer(serializers.Serializer):
    key = serializers.CharField()
    display_name = serializers.CharField()
    display_unit = serializers.CharField()
    current_usage = serializers.FloatField()
    current_usage_raw = serializers.FloatField()
    free_allowance = serializers.FloatField()
    projected_usage = serializers.FloatField()
    estimated_cost = serializers.FloatField()
    tier_breakdown = UsageTierBreakdownSerializer(many=True, required=False)
    usage_pct = serializers.FloatField()


class UsageOverviewResultSerializer(serializers.Serializer):
    plan = serializers.CharField()
    plan_display_name = serializers.CharField()
    platform_fee = serializers.FloatField()
    period = serializers.CharField()
    billing_period_start = serializers.CharField()
    billing_period_end = serializers.CharField()
    total_estimated_cost = serializers.FloatField()
    total_with_platform = serializers.FloatField()
    dimensions = UsageOverviewDimensionSerializer(many=True)
    pending_cancel = serializers.BooleanField()
    cancel_at = serializers.CharField(required=False, allow_null=True)


class UsageOverviewResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = UsageOverviewResultSerializer()


class UsageTimeSeriesPointSerializer(serializers.Serializer):
    date = serializers.CharField()
    usage = serializers.FloatField()


class UsageTimeSeriesResultSerializer(serializers.Serializer):
    dimension = serializers.CharField()
    period = serializers.CharField()
    period_end = serializers.CharField()
    series = UsageTimeSeriesPointSerializer(many=True)


class UsageTimeSeriesResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = UsageTimeSeriesResultSerializer()


class UsageWorkspaceBreakdownItemSerializer(serializers.Serializer):
    workspace_id = serializers.UUIDField(required=False, allow_null=True)
    workspace_name = serializers.CharField()
    usage = serializers.FloatField()


class UsageWorkspaceBreakdownResultSerializer(serializers.Serializer):
    dimension = serializers.CharField()
    period = serializers.CharField()
    period_end = serializers.CharField()
    workspaces = UsageWorkspaceBreakdownItemSerializer(many=True)


class UsageWorkspaceBreakdownResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = UsageWorkspaceBreakdownResultSerializer()


class UsagePlanOptionSerializer(serializers.Serializer):
    key = serializers.CharField()
    display_name = serializers.CharField()
    platform_fee_monthly = serializers.FloatField()
    is_current = serializers.BooleanField()
    features = serializers.DictField(child=serializers.JSONField())


class UsagePricingTierSerializer(serializers.Serializer):
    up_to = serializers.FloatField(required=False, allow_null=True)
    price_per_unit = serializers.FloatField()


class UsagePricingDimensionSerializer(serializers.Serializer):
    display_name = serializers.CharField()
    display_unit = serializers.CharField()
    tiers = UsagePricingTierSerializer(many=True)


class UsageCustomPlanDetailsSerializer(serializers.Serializer):
    platform_fee = serializers.FloatField()
    platform_fee_billing_cycle = serializers.IntegerField()
    per_charge_amount = serializers.FloatField()
    contract_end_date = serializers.CharField(required=False, allow_null=True)
    features = serializers.DictField(child=serializers.JSONField())
    pricing = serializers.DictField(child=serializers.JSONField())


class UsagePlansAndAddonsResultSerializer(serializers.Serializer):
    current_plan = serializers.CharField()
    billing_interval = serializers.CharField()
    tiers = UsagePlanOptionSerializer(many=True)
    addons = UsagePlanOptionSerializer(many=True)
    pricing = serializers.DictField(child=UsagePricingDimensionSerializer())
    isCustomPricing = serializers.BooleanField()
    customDetails = UsageCustomPlanDetailsSerializer(required=False, allow_null=True)
    pending_cancel = serializers.BooleanField()
    cancel_at = serializers.CharField(required=False, allow_null=True)


class UsagePlansAndAddonsResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = UsagePlansAndAddonsResultSerializer()


class UsageInvoiceLineItemSerializer(serializers.Serializer):
    line_type = serializers.CharField()
    dimension = serializers.CharField(required=False, allow_null=True)
    description = serializers.CharField()
    quantity = serializers.DecimalField(max_digits=20, decimal_places=8)
    unit = serializers.CharField(required=False, allow_blank=True)
    unit_price = serializers.DecimalField(max_digits=20, decimal_places=8)
    amount = serializers.DecimalField(max_digits=20, decimal_places=8)
    tier_breakdown = serializers.JSONField(required=False, allow_null=True)
    credit_id = serializers.IntegerField(required=False)


class UsageBillingOverviewResultSerializer(serializers.Serializer):
    org_id = serializers.UUIDField(required=False)
    period = serializers.CharField(required=False)
    plan = serializers.CharField(required=False)
    platform_fee = serializers.DecimalField(
        max_digits=20, decimal_places=8, required=False
    )
    usage_total = serializers.DecimalField(
        max_digits=20, decimal_places=8, required=False
    )
    credits_applied = serializers.DecimalField(
        max_digits=20, decimal_places=8, required=False
    )
    subtotal = serializers.DecimalField(max_digits=20, decimal_places=8, required=False)
    tax = serializers.DecimalField(max_digits=20, decimal_places=8, required=False)
    total = serializers.DecimalField(max_digits=20, decimal_places=8, required=False)
    line_items = UsageInvoiceLineItemSerializer(many=True, required=False)
    error = serializers.CharField(required=False)
    pending_cancel = serializers.BooleanField(required=False)
    cancel_at = serializers.CharField(required=False, allow_null=True)


class UsageBillingOverviewResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = UsageBillingOverviewResultSerializer()


class UsageInvoiceSummarySerializer(serializers.Serializer):
    id = serializers.UUIDField()
    period_start = serializers.DateField()
    period_end = serializers.DateField()
    plan = serializers.CharField()
    platform_fee = serializers.DecimalField(max_digits=20, decimal_places=8)
    usage_total = serializers.DecimalField(max_digits=20, decimal_places=8)
    credits_applied = serializers.DecimalField(max_digits=20, decimal_places=8)
    subtotal = serializers.DecimalField(max_digits=20, decimal_places=8)
    tax = serializers.DecimalField(max_digits=20, decimal_places=8)
    total = serializers.DecimalField(max_digits=20, decimal_places=8)
    status = serializers.CharField()
    stripe_invoice_url = serializers.URLField(
        required=False, allow_null=True, allow_blank=True
    )
    stripe_pdf_url = serializers.URLField(
        required=False, allow_null=True, allow_blank=True
    )
    created_at = serializers.DateTimeField()


class UsageInvoiceListResultSerializer(serializers.Serializer):
    invoices = UsageInvoiceSummarySerializer(many=True)


class UsageInvoiceListResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = UsageInvoiceListResultSerializer()


class UsageInvoiceDetailSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    period_start = serializers.DateField()
    period_end = serializers.DateField()
    plan = serializers.CharField()
    platform_fee = serializers.FloatField()
    usage_total = serializers.FloatField()
    credits_applied = serializers.FloatField()
    subtotal = serializers.FloatField()
    tax = serializers.FloatField()
    total = serializers.FloatField()
    status = serializers.CharField()
    stripe_pdf_url = serializers.URLField(
        required=False, allow_null=True, allow_blank=True
    )


class UsageInvoiceDetailResultSerializer(serializers.Serializer):
    invoice = UsageInvoiceDetailSerializer()
    line_items = UsageInvoiceLineItemSerializer(many=True)


class UsageInvoiceDetailResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = UsageInvoiceDetailResultSerializer()


class UsageNotificationActionSerializer(serializers.Serializer):
    label = serializers.CharField()
    url = serializers.CharField()


class UsageNotificationBannerSerializer(serializers.Serializer):
    id = serializers.CharField()
    type = serializers.CharField()
    message = serializers.CharField()
    action = UsageNotificationActionSerializer(required=False)
    dismissible = serializers.BooleanField(required=False)


class UsageNotificationsResultSerializer(serializers.Serializer):
    banners = UsageNotificationBannerSerializer(many=True)


class UsageNotificationsResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = UsageNotificationsResultSerializer()


class UsageBudgetSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()
    scope = serializers.CharField(required=False)
    threshold_value = serializers.DecimalField(max_digits=20, decimal_places=8)
    action = serializers.CharField()
    notify_emails = serializers.ListField(
        child=serializers.EmailField(), required=False
    )
    is_active = serializers.BooleanField(required=False)
    last_triggered_period = serializers.CharField(required=False, allow_null=True)
    last_triggered_at = serializers.DateTimeField(required=False, allow_null=True)
    created_at = serializers.DateTimeField(required=False)


class UsageBudgetListResultSerializer(serializers.Serializer):
    budgets = UsageBudgetSerializer(many=True)


class UsageBudgetListResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = UsageBudgetListResultSerializer()


class UsageBudgetMutationRequestSerializer(StrictInputSerializer):
    name = serializers.CharField(required=False)
    scope = serializers.CharField(required=False)
    threshold_value = serializers.DecimalField(
        max_digits=20, decimal_places=8, required=False
    )
    action = serializers.ChoiceField(
        choices=("notify", "warn", "pause"), required=False
    )
    notify_emails = serializers.ListField(
        child=serializers.EmailField(), required=False
    )
    notify_slack_webhook = serializers.URLField(
        required=False, allow_null=True, allow_blank=True
    )
    is_active = serializers.BooleanField(required=False)


class UsageBudgetMutationResultSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()
    scope = serializers.CharField(required=False)
    threshold_value = serializers.CharField()
    action = serializers.CharField()
    is_active = serializers.BooleanField(required=False)


class UsageBudgetMutationResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = UsageBudgetMutationResultSerializer()


class UsageBudgetDeleteResultSerializer(serializers.Serializer):
    deleted = serializers.BooleanField()


class UsageBudgetDeleteResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = UsageBudgetDeleteResultSerializer()


USAGE_ERROR_RESPONSES = {
    400: UsageErrorResponseSerializer,
    401: UsageErrorResponseSerializer,
    402: UsageErrorResponseSerializer,
    403: UsageErrorResponseSerializer,
    404: UsageErrorResponseSerializer,
    500: UsageErrorResponseSerializer,
}
