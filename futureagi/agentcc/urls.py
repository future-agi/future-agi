from django.urls import path
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
from agentcc.views.org_config import AgentccOrgConfigViewSet
from agentcc.views.org_config_bulk import OrgConfigBulkView
from agentcc.views.provider_credential import AgentccProviderCredentialViewSet
from agentcc.views.request_log import AgentccRequestLogViewSet
from agentcc.views.request_log_retention import RequestLogRetentionView
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
    path(
        "request-log-retention/",
        RequestLogRetentionView.as_view(),
        name="agentcc-request-log-retention",
    ),
    *router.urls,
]
