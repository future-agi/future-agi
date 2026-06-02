import json
from pathlib import Path


def _repo_root():
    return Path(__file__).resolve().parents[3]


def _swagger():
    with (_repo_root() / "api_contracts" / "openapi" / "swagger.json").open() as f:
        return json.load(f)


def _debt_report():
    with (
        _repo_root()
        / "api_contracts"
        / "openapi"
        / "management-api-contract-debt.generated.json"
    ).open() as f:
        return json.load(f)


def _operation(path, method):
    return _swagger()["paths"][path][method.lower()]


def _body_ref(operation):
    body = next(
        parameter
        for parameter in operation.get("parameters", [])
        if parameter.get("in") == "body"
    )
    return body["schema"]["$ref"].rsplit("/", 1)[-1]


def _response_ref(operation, status_code="200"):
    return operation["responses"][status_code]["schema"]["$ref"].rsplit("/", 1)[-1]


def test_usage_contract_debt_is_fully_burned_down():
    report = _debt_report()

    assert report["by_group"]["usage"]["mutation_endpoints_without_body_schema"] == 0
    assert report["by_group"]["usage"]["operations_without_response_schema"] == 0
    assert report["by_group"]["usage"]["operations_without_error_response_schema"] == 0
    assert report["by_group"]["usage"]["broad_error_response_schemas"] == 0


def test_usage_mutations_have_request_contracts():
    expected = {
        ("POST", "/usage/admin/custom-plan/"): "AdminCustomPlanRequest",
        ("PUT", "/usage/admin/custom-plan/"): "AdminCustomPlanRequest",
        ("POST", "/usage/admin/entitlements/"): ("AdminEntitlementMutationRequest"),
        ("POST", "/usage/admin/invoice/generate/"): "AdminInvoiceRequest",
        ("POST", "/usage/admin/invoice/preview/"): "AdminInvoiceRequest",
        ("POST", "/usage/admin/pricing/"): "AdminPricingMutationRequest",
        ("POST", "/usage/create-checkout-session/"): "CheckoutSessionRequest",
        ("POST", "/usage/download-invoice/"): "DownloadInvoiceRequest",
        ("PATCH", "/usage/organization-billing/{billing_id}/"): (
            "UsageOrganizationBilling"
        ),
        ("POST", "/usage/organization-subscription/"): (
            "UsageOrganizationSubscriptionCreate"
        ),
        ("POST", "/usage/pricing/"): "UsagePricingCreate",
        ("POST", "/usage/rate-limits/"): "UsageRateLimitCreate",
        ("POST", "/usage/resource-limits/"): "UsageResourceLimitCreate",
        ("POST", "/usage/subscription-tier/"): "UsageSubscriptionTier",
        ("POST", "/usage/v2/addon/"): "AddonRequest",
        ("POST", "/usage/v2/budgets/"): "UsageBudgetMutationRequest",
        ("PUT", "/usage/v2/budgets/{budget_id}/"): "UsageBudgetMutationRequest",
        ("DELETE", "/usage/v2/budgets/{budget_id}/"): "UsageEmptyRequest",
        ("POST", "/usage/v2/payment-methods/"): "UsageEmptyRequest",
        ("PUT", "/usage/v2/payment-methods/"): "SetupIntentConfirmRequest",
        ("POST", "/usage/v2/stripe-webhook/"): "StripeWebhookRequest",
        ("PUT", "/usage/v2/upgrade-to-payg/"): "UpgradeToPaygConfirmRequest",
    }

    for (method, path), definition_name in expected.items():
        assert _body_ref(_operation(path, method)) == definition_name


def test_usage_endpoints_have_response_contracts():
    expected = {
        ("GET", "/usage/admin/custom-plan/"): "AdminCustomPlanResponse",
        ("POST", "/usage/admin/pricing/"): "AdminPricingMutationResponse",
        ("GET", "/usage/api-call-count/"): "APICallCountResponse",
        ("POST", "/usage/create-checkout-session/"): "CheckoutSessionResponse",
        ("GET", "/usage/get-customer-invoices/"): "CustomerInvoicesResponse",
        ("GET", "/usage/get_latest_prices/"): "PricingCalculationResponse",
        ("GET", "/usage/organization-subscription/"): (
            "OrganizationSubscriptionListResponse"
        ),
        ("GET", "/usage/pricing/"): "PricingListResponse",
        ("GET", "/usage/rate-limits/"): "RateLimitListResponse",
        ("GET", "/usage/resource-limits/"): "ResourceLimitListResponse",
        ("GET", "/usage/subscription-tier/"): "SubscriptionTierListResponse",
        ("GET", "/usage/usage-summary/"): "UsageSummaryResponse",
        ("GET", "/usage/v2/billing-overview/"): "UsageBillingOverviewResponse",
        ("GET", "/usage/v2/budgets/"): "UsageBudgetListResponse",
        ("POST", "/usage/v2/budgets/"): "UsageBudgetMutationResponse",
        ("PUT", "/usage/v2/budgets/{budget_id}/"): "UsageBudgetMutationResponse",
        ("DELETE", "/usage/v2/budgets/{budget_id}/"): "UsageBudgetDeleteResponse",
        ("GET", "/usage/v2/invoices/"): "UsageInvoiceListResponse",
        ("GET", "/usage/v2/invoices/{invoice_id}/"): ("UsageInvoiceDetailResponse"),
        ("GET", "/usage/v2/notifications/"): "UsageNotificationsResponse",
        ("GET", "/usage/v2/payment-methods/"): "PaymentMethodsResponse",
        ("PUT", "/usage/v2/payment-methods/"): "PaymentMethodConfirmResponse",
        ("GET", "/usage/v2/plans-and-addons/"): "UsagePlansAndAddonsResponse",
        ("POST", "/usage/v2/upgrade-to-payg/"): "UpgradeToPaygPostResponse",
        ("PUT", "/usage/v2/upgrade-to-payg/"): "PlanResponse",
        ("GET", "/usage/v2/usage-overview/"): "UsageOverviewResponse",
        ("GET", "/usage/v2/usage-time-series/"): "UsageTimeSeriesResponse",
        ("GET", "/usage/v2/usage-workspace-breakdown/"): (
            "UsageWorkspaceBreakdownResponse"
        ),
        ("GET", "/usage/workspace-usage-summary/"): "UsageSummaryResponse",
    }

    for (method, path), definition_name in expected.items():
        assert _response_ref(_operation(path, method)) == definition_name
