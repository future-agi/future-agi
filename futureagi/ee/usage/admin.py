from django.contrib import admin
from django.template.response import TemplateResponse
from django.urls import path

from .models.usage import (
    APICallLog,
    APICallType,
    CreditBalance,
    Invoice,
    InvoiceLineItem,
    OrganizationBilling,
    OrganizationSubscription,
    PlanEntitlement,
    PlanPricing,
    Pricing,
    RateLimit,
    ResourceLimits,
    ResourceType,
    StripeProductMap,
    SubscriptionTier,
    UsageBudget,
    UsageEventLog,
    UsageSummary,
)


@admin.register(SubscriptionTier)
class SubscriptionTierAdmin(admin.ModelAdmin):
    list_display = ("name", "description", "stripe_price_id", "wallet_refill_amount")
    list_filter = ("name",)
    search_fields = ("name", "description")
    readonly_fields = ("created_at", "updated_at")


@admin.register(OrganizationSubscription)
class OrganizationSubscriptionAdmin(admin.ModelAdmin):
    list_display = (
        "organization",
        "organization_id",
        "subscription_tier",
        "status",
        "wallet_balance",
        "next_renewal_date",
        "subscription_price",
    )
    list_filter = (
        "status",
        "subscription_tier",
        "auto_recharge_enabled",
        "organization_id",
    )
    search_fields = ("organization__name", "organization__display_name")
    readonly_fields = ("created_at", "updated_at")

    def organization_id(self, obj):
        return obj.organization.id if obj.organization else "-"

    organization_id.short_description = "Organization ID"
    organization_id.admin_order_field = "organization__id"

    fieldsets = (
        (
            "Organization Info",
            {"fields": ("organization", "subscription_tier", "status")},
        ),
        (
            "Subscription Details",
            {
                "fields": (
                    "subscription_price",
                    "next_renewal_date",
                    "subscription_future_tier",
                    "subscription_future_start_date",
                    "subscription_future_price",
                )
            },
        ),
        (
            "Wallet Management",
            {
                "fields": (
                    "wallet_balance",
                    "wallet_refill_amount",
                    "auto_recharge_enabled",
                    "auto_recharge_amount",
                    "auto_recharge_threshold",
                )
            },
        ),
        (
            "Stripe Integration",
            {
                "fields": (
                    "stripe_customer_id_test",
                    "stripe_customer_id_live",
                    "payment_method_id",
                    "custom_subscription_id",
                )
            },
        ),
        (
            "Development",
            {"fields": ("last_refill_date", "last_refill_amount")},
        ),
        (
            "Timestamps",
            {"fields": ("created_at", "updated_at"), "classes": ("collapse",)},
        ),
    )


@admin.register(APICallType)
class APICallTypeAdmin(admin.ModelAdmin):
    list_display = ("name", "description", "id")
    search_fields = ("name", "description", "id")
    readonly_fields = ("created_at", "updated_at")


@admin.register(Pricing)
class PricingAdmin(admin.ModelAdmin):
    list_display = (
        "api_call_type",
        "organization",
        "organization_id",
        "price_per_call",
    )
    list_filter = ("api_call_type", "organization")
    search_fields = ("api_call_type__name", "organization__name")
    readonly_fields = ("created_at", "updated_at")

    def organization_id(self, obj):
        return obj.organization.id if obj.organization else "-"

    organization_id.short_description = "Organization ID"
    organization_id.admin_order_field = "organization__id"


@admin.register(ResourceType)
class ResourceTypeAdmin(admin.ModelAdmin):
    list_display = ("name", "description", "id")
    search_fields = ("name", "description", "id")
    readonly_fields = ("created_at", "updated_at")


@admin.register(ResourceLimits)
class ResourceLimitsAdmin(admin.ModelAdmin):
    list_display = (
        "subscription_tier",
        "resource_type",
        "organization",
        "organization_id",
        "has_access",
        "limit",
    )
    list_filter = (
        "subscription_tier",
        "resource_type",
        "has_access",
        "organization",
        "organization_id",
    )
    search_fields = (
        "subscription_tier__name",
        "resource_type__name",
        "organization__name",
    )
    readonly_fields = ("created_at", "updated_at")

    def organization_id(self, obj):
        return obj.organization.id if obj.organization else "-"

    organization_id.short_description = "Organization ID"
    organization_id.admin_order_field = "organization__id"


@admin.register(RateLimit)
class RateLimitAdmin(admin.ModelAdmin):
    list_display = (
        "api_call_type",
        "subscription_tier",
        "organization",
        "organization_id",
        "minute_limit",
        "hour_limit",
        "day_limit",
        "month_limit",
    )
    list_filter = ("api_call_type", "subscription_tier", "organization")
    search_fields = (
        "api_call_type__name",
        "subscription_tier__name",
        "organization__name",
    )
    readonly_fields = ("created_at", "updated_at")

    def organization_id(self, obj):
        return obj.organization.id if obj.organization else "-"

    organization_id.short_description = "Organization ID"
    organization_id.admin_order_field = "organization__id"

    fieldsets = (
        (
            "Rate Limit Configuration",
            {"fields": ("api_call_type", "subscription_tier", "organization")},
        ),
        (
            "Limits",
            {"fields": ("minute_limit", "hour_limit", "day_limit", "month_limit")},
        ),
        (
            "Timestamps",
            {"fields": ("created_at", "updated_at"), "classes": ("collapse",)},
        ),
    )


@admin.register(OrganizationBilling)
class OrganizationBillingAdmin(admin.ModelAdmin):
    list_display = (
        "organization",
        "organization_id",
        "billing_contact_name",
        "billing_contact_email",
        "company",
        "city",
        "country",
    )
    list_filter = ("country", "state")
    search_fields = (
        "organization__name",
        "organization__display_name",
        "billing_contact_name",
        "billing_contact_email",
        "company",
    )
    readonly_fields = ("created_at", "updated_at")

    def organization_id(self, obj):
        return obj.organization.id if obj.organization else "-"

    organization_id.short_description = "Organization ID"
    organization_id.admin_order_field = "organization__id"

    fieldsets = (
        ("Organization", {"fields": ("organization",)}),
        (
            "Contact Information",
            {"fields": ("billing_contact_name", "billing_contact_email", "company")},
        ),
        (
            "Address",
            {
                "fields": (
                    "billing_address1",
                    "billing_address2",
                    "city",
                    "state",
                    "country",
                    "postal_code",
                )
            },
        ),
        ("Tax Information", {"fields": ("tax_id",)}),
        (
            "Timestamps",
            {"fields": ("created_at", "updated_at"), "classes": ("collapse",)},
        ),
    )


@admin.register(APICallLog)
class APICallLogAdmin(admin.ModelAdmin):
    list_display = (
        "log_id",
        "organization",
        "organization_id",
        "user",
        "api_call_type",
        "cost",
        "deducted_cost",
        "status",
        "created_at",
    )
    list_filter = ("status", "api_call_type", "organization", "created_at")
    search_fields = (
        "organization__name",
        "organization__display_name",
        "user__email",
        "api_call_type__name",
        "reference_id",
    )
    readonly_fields = ("log_id", "created_at", "updated_at")

    def organization_id(self, obj):
        return obj.organization.id if obj.organization else "-"

    organization_id.short_description = "Organization ID"
    organization_id.admin_order_field = "organization__id"

    fieldsets = (
        (
            "Basic Information",
            {"fields": ("log_id", "organization", "user", "api_call_type")},
        ),
        ("Cost & Status", {"fields": ("cost", "deducted_cost", "status")}),
        (
            "Reference & Configuration",
            {"fields": ("refund_parent_id", "reference_id", "config")},
        ),
        ("Token Information", {"fields": ("input_token_count",)}),
        ("Source Information", {"fields": ("source", "source_id")}),
        (
            "Timestamps",
            {"fields": ("created_at", "updated_at"), "classes": ("collapse",)},
        ),
    )
    date_hierarchy = "created_at"


# ── NEW BILLING MODELS (Phase 1-6a) ────────────────────────────────────────


class FeatureCategoryFilter(admin.SimpleListFilter):
    title = "Feature Category"
    parameter_name = "feature_category"

    def lookups(self, request, model_admin):
        return [
            ("resource_limits", "Resource Limits"),
            ("boolean_features", "Boolean Features"),
            ("free_allowances", "Free Allowances"),
            ("rate_limits", "Rate Limits"),
            ("retention", "Retention"),
            ("gateway", "Gateway Limits"),
        ]

    def queryset(self, request, queryset):
        from django.db.models import Q

        val = self.value()
        if val == "boolean_features":
            return queryset.filter(feature__startswith="has_")
        if val == "free_allowances":
            return queryset.filter(feature__startswith="free_")
        if val == "retention":
            return queryset.filter(feature__startswith="retention_")
        if val == "rate_limits":
            return queryset.filter(feature__contains="_rate_")
        if val == "gateway":
            return queryset.filter(
                Q(feature__startswith="gateway_") | Q(feature="max_concurrency")
            )
        if val == "resource_limits":
            return queryset.exclude(
                Q(feature__startswith="has_")
                | Q(feature__startswith="free_")
                | Q(feature__startswith="retention_")
                | Q(feature__contains="_rate_")
                | Q(feature__startswith="gateway_")
                | Q(feature="max_concurrency")
            )
        return queryset


@admin.register(PlanEntitlement)
class PlanEntitlementAdmin(admin.ModelAdmin):
    list_display = (
        "feature",
        "plan",
        "value_int",
        "value_bool",
        "entitlement_type",
        "created_at",
    )
    list_filter = ("plan", FeatureCategoryFilter, "organization")
    search_fields = ("feature", "organization__name", "organization__display_name")
    readonly_fields = ("created_at", "updated_at")
    list_per_page = 50

    fieldsets = (
        (
            "Entitlement",
            {
                "fields": ("feature", "plan"),
            },
        ),
        (
            "Value",
            {
                "fields": ("value_int", "value_bool"),
                "description": (
                    "Set value_int for numeric limits (-1 = unlimited). "
                    "Set value_bool for feature toggles. Only one should be non-null."
                ),
            },
        ),
        (
            "Override",
            {
                "fields": ("organization",),
                "description": "Leave blank for plan default. Select an org to create a per-org override.",
            },
        ),
        (
            "Timestamps",
            {
                "fields": ("created_at", "updated_at"),
                "classes": ("collapse",),
            },
        ),
    )

    @admin.display(description="Type", ordering="organization")
    def entitlement_type(self, obj):
        if obj.organization:
            return f"Override: {obj.organization.name}"
        return "Plan Default"

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        if obj.organization_id:
            try:
                from ee.usage.services.entitlements import Entitlements

                Entitlements.invalidate_cache(str(obj.organization_id), obj.feature)
            except ImportError:
                pass

    def delete_model(self, request, obj):
        org_id = str(obj.organization_id) if obj.organization_id else None
        feature = obj.feature
        super().delete_model(request, obj)
        if org_id:
            try:
                from ee.usage.services.entitlements import Entitlements

                Entitlements.invalidate_cache(org_id, feature)
            except ImportError:
                pass

    def delete_queryset(self, request, queryset):
        for obj in queryset.filter(organization_id__isnull=False):
            try:
                from ee.usage.services.entitlements import Entitlements

                Entitlements.invalidate_cache(str(obj.organization_id), obj.feature)
            except ImportError:
                pass
        super().delete_queryset(request, queryset)


@admin.register(PlanPricing)
class PlanPricingAdmin(admin.ModelAdmin):
    list_display = (
        "dimension",
        "tier_start",
        "tier_end",
        "price_per_unit",
        "display_unit",
        "organization",
    )
    list_filter = ("dimension", "organization")
    search_fields = ("dimension", "organization__name")
    readonly_fields = ("created_at", "updated_at")
    ordering = ("dimension", "tier_start")


@admin.register(CreditBalance)
class CreditBalanceAdmin(admin.ModelAdmin):
    list_display = (
        "organization",
        "credit_type",
        "original_amount",
        "remaining_amount",
        "expires_at",
        "created_at",
    )
    list_filter = ("credit_type", "organization")
    search_fields = ("organization__name", "description")
    readonly_fields = ("created_at", "updated_at")


@admin.register(UsageSummary)
class UsageSummaryAdmin(admin.ModelAdmin):
    list_display = (
        "organization",
        "dimension",
        "period",
        "total_usage",
        "total_usage_raw",
        "last_flushed_at",
    )
    list_filter = ("dimension", "period")
    search_fields = ("organization__name",)
    readonly_fields = ("created_at", "updated_at")


@admin.register(UsageEventLog)
class UsageEventLogAdmin(admin.ModelAdmin):
    list_display = (
        "event_id",
        "organization",
        "event_type",
        "dimension",
        "amount_raw",
        "period",
        "timestamp",
        "status",
    )
    list_filter = ("event_type", "dimension", "status", "period")
    search_fields = ("organization__name", "event_type")
    readonly_fields = ("event_id", "created_at")
    date_hierarchy = "timestamp"
    list_per_page = 100


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "organization",
        "period_start",
        "period_end",
        "plan",
        "total",
        "status",
        "created_at",
    )
    list_filter = ("status", "plan")
    search_fields = ("organization__name", "stripe_invoice_id")
    readonly_fields = ("id", "created_at", "updated_at")
    date_hierarchy = "period_start"


@admin.register(InvoiceLineItem)
class InvoiceLineItemAdmin(admin.ModelAdmin):
    list_display = (
        "invoice",
        "line_type",
        "dimension",
        "description",
        "quantity",
        "amount",
    )
    list_filter = ("line_type", "dimension")
    readonly_fields = ("created_at", "updated_at")


@admin.register(UsageBudget)
class UsageBudgetAdmin(admin.ModelAdmin):
    list_display = (
        "organization",
        "name",
        "scope",
        "threshold_value",
        "action",
        "is_active",
        "last_triggered_at",
    )
    list_filter = ("action", "is_active", "scope")
    search_fields = ("organization__name", "name")
    readonly_fields = (
        "created_at",
        "updated_at",
        "last_triggered_at",
        "last_triggered_period",
    )


@admin.register(StripeProductMap)
class StripeProductMapAdmin(admin.ModelAdmin):
    list_display = ("key", "stripe_product_id", "stripe_price_id", "is_active")
    list_filter = ("is_active",)
    list_editable = ("stripe_price_id", "is_active")
    search_fields = ("key", "stripe_product_id", "stripe_price_id")


# ── Custom Pricing Admin Page ──────────────────────────────────────────────


class CustomPricingAdminSite(admin.AdminSite):
    """Extends the default admin site with custom pricing management page."""

    pass


def _build_org_choices(orgs_qs):
    """Build org dropdown choices. Only orgs with a name."""
    from django.db.models import Q

    orgs = list(
        orgs_qs.filter(
            Q(display_name__isnull=False, display_name__gt="")
            | Q(name__isnull=False, name__gt="")
        )
        .order_by("display_name", "name")
        .values("id", "name", "display_name")[:5000]
    )
    return [{"id": str(o["id"]), "label": o["display_name"] or o["name"]} for o in orgs]


def custom_pricing_view(request):
    """Custom pricing admin page — superuser only."""
    if not request.user.is_superuser:
        from django.http import HttpResponseForbidden

        return HttpResponseForbidden("Superuser access required")

    from accounts.models.organization import Organization

    org_choices = _build_org_choices(Organization.objects.all())

    import json

    from ee.usage.services.config import BillingConfig

    config = BillingConfig.get()
    feature_keys = sorted(config._schema.entitlements.keys())

    context = {
        **admin.site.each_context(request),
        "title": "Custom Pricing Manager",
        "subtitle": "",
        "org_choices_json": json.dumps(org_choices),
        "feature_keys_json": json.dumps(feature_keys),
    }
    return TemplateResponse(request, "admin/usage/custom_pricing.html", context)


def generate_invoice_view(request):
    """Generate invoice admin page — superuser only."""
    if not request.user.is_superuser:
        from django.http import HttpResponseForbidden

        return HttpResponseForbidden("Superuser access required")

    import json

    from accounts.models.organization import Organization

    org_choices = _build_org_choices(Organization.objects.all())

    context = {
        **admin.site.each_context(request),
        "title": "Generate Invoice",
        "subtitle": "",
        "org_choices_json": json.dumps(org_choices),
    }
    return TemplateResponse(request, "admin/usage/generate_invoice.html", context)


# Register the custom URLs with the default admin site
_original_get_urls = admin.AdminSite.get_urls


def _patched_get_urls(self):
    custom_urls = [
        path(
            "usage/custom-pricing/",
            self.admin_view(custom_pricing_view),
            name="custom-pricing",
        ),
        path(
            "usage/generate-invoice/",
            self.admin_view(generate_invoice_view),
            name="generate-invoice",
        ),
    ]
    return custom_urls + _original_get_urls(self)


admin.AdminSite.get_urls = _patched_get_urls
