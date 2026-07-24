import json
from pathlib import Path

import pytest
from rest_framework import status

# Every test here hits a ``/usage/*`` route or the usage swagger contract,
# both of which are only wired when ``has_ee("ee.usage")`` — skip the whole
# file in OSS mode where those routes don't exist and would 404.
pytest.importorskip("ee.usage.models.usage")


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


USAGE_V2_PAYMENT_METHOD_ID = "pm_api_journey_missing"
USAGE_ADMIN_INT_ID = 100000001


def _request_body_for_method(method):
    return {} if method in {"post", "put", "patch"} else None


def _usage_admin_api_key_guard_cases():
    cases = [
        ("get", "/usage/admin/custom-plan/", None),
        ("post", "/usage/admin/custom-plan/", {}),
        ("put", "/usage/admin/custom-plan/", {}),
        ("get", "/usage/admin/entitlements/", None),
        ("post", "/usage/admin/entitlements/", {}),
        ("delete", "/usage/admin/entitlements/", None),
        ("post", "/usage/admin/invoice/generate/", {}),
        ("post", "/usage/admin/invoice/preview/", {}),
        ("get", "/usage/admin/pricing/", None),
        ("post", "/usage/admin/pricing/", {}),
        ("delete", "/usage/admin/pricing/", None),
        ("get", "/usage/api-call-type/", None),
        ("get", "/usage/organization-filter/", None),
        ("get", "/usage/organizations/", None),
        ("get", "/usage/resource-type/", None),
    ]
    appsmith_resources = [
        (
            "/usage/subscription-tier/",
            f"/usage/subscription-tier/{USAGE_ADMIN_INT_ID}/",
            ("get", "post", "patch", "delete"),
        ),
        (
            "/usage/organization-billing/",
            f"/usage/organization-billing/{USAGE_ADMIN_INT_ID}/",
            ("get", "patch"),
        ),
        (
            "/usage/organization-subscription/",
            f"/usage/organization-subscription/{USAGE_ADMIN_INT_ID}/",
            ("get", "post", "patch", "delete"),
        ),
        (
            "/usage/pricing/",
            f"/usage/pricing/{USAGE_ADMIN_INT_ID}/",
            ("get", "post", "patch", "delete"),
        ),
        (
            "/usage/rate-limits/",
            f"/usage/rate-limits/{USAGE_ADMIN_INT_ID}/",
            ("get", "post", "patch", "delete"),
        ),
        (
            "/usage/resource-limits/",
            f"/usage/resource-limits/{USAGE_ADMIN_INT_ID}/",
            ("get", "post", "patch", "delete"),
        ),
    ]
    for collection_path, detail_path, methods in appsmith_resources:
        for path in (collection_path, detail_path):
            for method in methods:
                cases.append((method, path, _request_body_for_method(method)))
    return cases


def _usage_legacy_auth_guard_cases():
    return [
        ("get", "/usage/api-call-count/", None),
        ("post", "/usage/cancel-subscription/", {}),
        ("post", "/usage/create-auto-recharge-session/", {}),
        ("post", "/usage/create-custom-payment-checkout-session/", {}),
        ("post", "/usage/download-invoice/", {}),
        ("get", "/usage/get-auto-reload-settings/", None),
        ("get", "/usage/get-customer-invoices/", None),
        ("get", "/usage/get-last-four-digits/", None),
        ("get", "/usage/get-wallet-balance/", None),
        ("post", "/usage/pricing-card-details/", {}),
        ("get", "/usage/subscription-plans/", None),
        ("post", "/usage/update-auto-reload-settings/", {}),
        ("post", "/usage/update-billing-details/", {}),
        ("get", "/usage/usage-summary/", None),
    ]


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
        ("PUT", "/usage/v2/payment-methods/setup-intent/"): (
            "SetupIntentConfirmRequest"
        ),
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
        ("PUT", "/usage/v2/payment-methods/setup-intent/"): (
            "PaymentMethodConfirmResponse"
        ),
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


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("post", "/usage/v2/add-addon/", {}),
        ("put", "/usage/v2/add-addon/", {}),
        ("delete", "/usage/v2/add-addon/", None),
        ("post", "/usage/v2/addon/", {}),
        ("put", "/usage/v2/addon/", {}),
        ("delete", "/usage/v2/addon/", None),
        ("post", "/usage/v2/downgrade-to-free/", {}),
        ("post", "/usage/v2/payment-methods/", {}),
        ("put", "/usage/v2/payment-methods/", {}),
        ("get", "/usage/v2/payment-methods/setup-intent/", None),
        ("put", "/usage/v2/payment-methods/setup-intent/", {}),
        ("post", f"/usage/v2/payment-methods/{USAGE_V2_PAYMENT_METHOD_ID}/", {}),
        (
            "delete",
            f"/usage/v2/payment-methods/{USAGE_V2_PAYMENT_METHOD_ID}/default/",
            None,
        ),
        ("post", "/usage/v2/reinstate-addon/", {}),
        ("put", "/usage/v2/reinstate-addon/", {}),
        ("delete", "/usage/v2/reinstate-addon/", None),
        ("post", "/usage/v2/remove-addon/", {}),
        ("put", "/usage/v2/remove-addon/", {}),
        ("delete", "/usage/v2/remove-addon/", None),
        ("post", "/usage/v2/upgrade-to-payg/", {}),
        ("put", "/usage/v2/upgrade-to-payg/", {}),
        ("get", "/usage/v2/usage-overview/", None),
        ("get", "/usage/v2/usage-time-series/", None),
        ("get", "/usage/v2/usage-workspace-breakdown/", None),
    ],
)
def test_usage_v2_routes_reject_anonymous_before_work(api_client, method, path, body):
    request = getattr(api_client, method)

    if body is None:
        response = request(path)
    else:
        response = request(path, body, format="json")

    assert response.status_code in {
        status.HTTP_401_UNAUTHORIZED,
        status.HTTP_403_FORBIDDEN,
    }
    assert response.headers["content-type"].startswith("application/json")


@pytest.mark.django_db
def test_usage_v2_stripe_webhook_requires_signature_before_work(api_client):
    response = api_client.post("/usage/v2/stripe-webhook/", {}, format="json")

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.headers["content-type"].startswith("application/json")
    assert "Stripe-Signature" in str(response.json())


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("method", "path", "body"),
    _usage_admin_api_key_guard_cases(),
)
def test_usage_admin_and_appsmith_routes_reject_missing_api_key_before_work(
    api_client, method, path, body
):
    request = getattr(api_client, method)

    if body is None:
        response = request(path)
    else:
        response = request(path, body, format="json")

    assert response.status_code in {
        status.HTTP_401_UNAUTHORIZED,
        status.HTTP_403_FORBIDDEN,
    }
    assert response.headers["content-type"].startswith("application/json")
    payload_text = str(response.json()).lower()
    assert (
        "api key" in payload_text
        or "permission" in payload_text
        or "authentication" in payload_text
        or "credentials" in payload_text
        or "not authenticated" in payload_text
    )


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("method", "path", "body"),
    _usage_legacy_auth_guard_cases(),
)
def test_usage_legacy_billing_routes_reject_anonymous_before_work(
    api_client, method, path, body
):
    request = getattr(api_client, method)

    if body is None:
        response = request(path)
    else:
        response = request(path, body, format="json")

    assert response.status_code in {
        status.HTTP_401_UNAUTHORIZED,
        status.HTTP_403_FORBIDDEN,
    }
    assert response.headers["content-type"].startswith("application/json")


@pytest.mark.django_db
def test_usage_legacy_pricing_read_is_public_json_boundary(api_client):
    response = api_client.get("/usage/get_latest_prices/")

    assert response.status_code in {
        status.HTTP_200_OK,
        status.HTTP_400_BAD_REQUEST,
    }
    assert response.headers["content-type"].startswith("application/json")


@pytest.mark.django_db
def test_usage_legacy_stripe_webhook_requires_signature_before_work(api_client):
    response = api_client.post("/usage/webhook/", {}, format="json")

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.headers["content-type"].startswith("application/json")
    assert "Stripe-Signature" in str(response.json())
