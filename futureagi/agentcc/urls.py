from django.urls import include, path
from rest_framework.routers import DefaultRouter

from agentcc.views.analytics import AgentccAnalyticsViewSet
from agentcc.views.api_key import AgentccAPIKeyViewSet
from agentcc.views.api_key_bulk import APIKeyBulkView
from agentcc.views.blocklist import AgentccBlocklistViewSet
from agentcc.views.custom_property import AgentccCustomPropertySchemaViewSet
from agentcc.views.email_alert import AgentccEmailAlertViewSet
from agentcc.views.gateway import AgentccGatewayViewSet
from agentcc.views.guardrail_config import AgentccGuardrailConfigViewSet
from agentcc.views.guardrail_feedback import AgentccGuardrailFeedbackViewSet
from agentcc.views.guardrail_policy import AgentccGuardrailPolicyViewSet
from agentcc.views.managed_models import AgentccManagedModelsViewSet
from agentcc.views.org_config import AgentccOrgConfigViewSet
from agentcc.views.org_config_bulk import OrgConfigBulkView
from agentcc.views.provider_credential import AgentccProviderCredentialViewSet
from agentcc.views.request_log import AgentccRequestLogViewSet
from agentcc.views.routing_policy import AgentccRoutingPolicyViewSet
from agentcc.views.session import AgentccSessionViewSet
from agentcc.views.shadow_experiments import (
    AgentccShadowExperimentViewSet,
    AgentccShadowResultViewSet,
)
from agentcc.views.shadow_webhook import ShadowResultWebhookView
from agentcc.views.spend_summary import SpendSummaryView
from agentcc.views.webhook import GatewayWebhookView
from agentcc.views.webhook_outbound import (
    AgentccWebhookEventViewSet,
    AgentccWebhookViewSet,
)

router = DefaultRouter()
router.register(r"gateways", AgentccGatewayViewSet, basename="agentcc-gateway")
router.register(r"api-keys", AgentccAPIKeyViewSet, basename="agentcc-api-key")
router.register(
    r"request-logs", AgentccRequestLogViewSet, basename="agentcc-request-log"
)
router.register(r"analytics", AgentccAnalyticsViewSet, basename="agentcc-analytics")
router.register(r"org-configs", AgentccOrgConfigViewSet, basename="agentcc-org-config")
router.register(
    r"guardrail-policies",
    AgentccGuardrailPolicyViewSet,
    basename="agentcc-guardrail-policy",
)
router.register(r"blocklists", AgentccBlocklistViewSet, basename="agentcc-blocklist")
router.register(
    r"guardrail-configs",
    AgentccGuardrailConfigViewSet,
    basename="agentcc-guardrail-config",
)
router.register(
    r"guardrail-feedback",
    AgentccGuardrailFeedbackViewSet,
    basename="agentcc-guardrail-feedback",
)
router.register(r"sessions", AgentccSessionViewSet, basename="agentcc-session")
router.register(r"webhooks", AgentccWebhookViewSet, basename="agentcc-webhook")
router.register(
    r"webhook-events", AgentccWebhookEventViewSet, basename="agentcc-webhook-event"
)
router.register(
    r"custom-properties",
    AgentccCustomPropertySchemaViewSet,
    basename="agentcc-custom-property",
)
router.register(
    r"routing-policies", AgentccRoutingPolicyViewSet, basename="agentcc-routing-policy"
)
router.register(
    r"email-alerts", AgentccEmailAlertViewSet, basename="agentcc-email-alert"
)
router.register(
    r"shadow-experiments",
    AgentccShadowExperimentViewSet,
    basename="agentcc-shadow-experiment",
)
router.register(
    r"shadow-results", AgentccShadowResultViewSet, basename="agentcc-shadow-result"
)
router.register(
    r"provider-credentials",
    AgentccProviderCredentialViewSet,
    basename="agentcc-provider-credential",
)
router.register(
    r"managed-models",
    AgentccManagedModelsViewSet,
    basename="agentcc-managed-models",
)

urlpatterns = [
    path("webhook/logs/", GatewayWebhookView.as_view(), name="agentcc-webhook-logs"),
    path(
        "webhook/shadow-results/",
        ShadowResultWebhookView.as_view(),
        name="agentcc-webhook-shadow-results",
    ),
    path(
        "org-configs/bulk/", OrgConfigBulkView.as_view(), name="agentcc-org-config-bulk"
    ),
    path("api-keys/bulk/", APIKeyBulkView.as_view(), name="agentcc-api-key-bulk"),
    path("spend-summary/", SpendSummaryView.as_view(), name="agentcc-spend-summary"),
    # One module per console concern. They come before the router so that a
    # detail route such as analytics/<pk>/ cannot shadow an explicit path.
    path("", include("agentcc.urls_billing")),
    path("", include("agentcc.urls_catalog")),
    path("", include("agentcc.urls_policy")),
    path("", include("agentcc.urls_analytics_v2")),
    path("", include("agentcc.urls_playground")),
    path("", include("agentcc.urls_capabilities")),
    # MCP URL modules. /.well-known/ lives at the site root, in tfc/urls.py.
    path("", include("agentcc.urls_mcp_credentials")),
    path("", include("agentcc.urls_mcp_toolcalls")),
    path("", include("agentcc.urls_mcp_revisions")),
    path("", include("agentcc.urls_mcp_approvals")),
    path("", include("agentcc.urls_mcp_connect")),
    path("", include("agentcc.urls_mcp_oauth")),
    path("", include("agentcc.urls_mcp_registry")),
    path("", include("agentcc.urls_mcp_doctor")),
    path("", include("agentcc.urls_mcp_review_pack")),
    path("", include("agentcc.urls_mcp_policy")),
    path("", include("agentcc.urls_mcp_vendors")),
    path("", include("agentcc.urls_run_envelope")),
    path("", include("agentcc.urls_identity")),
    path("", include("agentcc.urls_importers")),
    path("", include("agentcc.urls_enterprise_evidence")),
    path("", include("agentcc.urls_budgets")),
    path("", include("agentcc.urls_memory")),
    *router.urls,
]
