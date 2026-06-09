"""Bridge registration for agentcc ViewSets — secrets, blocklists, routing
policies, webhook outbound, guardrails, prompt templates, custom properties,
analytics, etc. All are clean ModelViewSets with serializers; the bridge
auto-generates CRUD tool names.
"""

from agentcc.views.api_key import AgentccAPIKeyViewSet
from agentcc.views.blocklist import AgentccBlocklistViewSet
from agentcc.views.custom_property import AgentccCustomPropertySchemaViewSet
from agentcc.views.email_alert import AgentccEmailAlertViewSet
from agentcc.views.guardrail_feedback import AgentccGuardrailFeedbackViewSet
from agentcc.views.guardrail_policy import AgentccGuardrailPolicyViewSet
from agentcc.views.org_config import AgentccOrgConfigViewSet
from agentcc.views.prompt_template import AgentccPromptTemplateViewSet
from agentcc.views.provider_credential import AgentccProviderCredentialViewSet
from agentcc.views.request_log import AgentccRequestLogViewSet
from agentcc.views.routing_policy import AgentccRoutingPolicyViewSet
from agentcc.views.session import AgentccSessionViewSet
from agentcc.views.shadow_experiments import (
    AgentccShadowExperimentViewSet,
    AgentccShadowResultViewSet,
)
from agentcc.views.webhook_outbound import (
    AgentccWebhookEventViewSet,
    AgentccWebhookViewSet,
)
from ai_tools.drf_bridge import expose_to_mcp

# entity stripping removes 'Agentcc' prefix via the regex CamelCase split,
# so AgentccBlocklistViewSet -> "agentcc_blocklist" by default. That's
# fine — agentcc IS its own product area.

# AgentccAnalyticsViewSet has no standard list/retrieve (custom analytics
# actions only) — not a CRUD ViewSet, so not bridged.
expose_to_mcp(category="agentcc")(AgentccAPIKeyViewSet)
expose_to_mcp(category="agentcc")(AgentccBlocklistViewSet)
expose_to_mcp(category="agentcc")(AgentccCustomPropertySchemaViewSet)
expose_to_mcp(category="agentcc")(AgentccEmailAlertViewSet)
expose_to_mcp(category="agentcc")(AgentccGuardrailFeedbackViewSet)
expose_to_mcp(category="agentcc")(AgentccGuardrailPolicyViewSet)
expose_to_mcp(category="agentcc")(AgentccOrgConfigViewSet)
expose_to_mcp(category="agentcc")(AgentccPromptTemplateViewSet)
expose_to_mcp(category="agentcc")(AgentccProviderCredentialViewSet)
expose_to_mcp(category="agentcc")(AgentccRequestLogViewSet)
expose_to_mcp(category="agentcc")(AgentccRoutingPolicyViewSet)
expose_to_mcp(category="agentcc")(AgentccSessionViewSet)
expose_to_mcp(category="agentcc")(AgentccShadowExperimentViewSet)
expose_to_mcp(category="agentcc")(AgentccShadowResultViewSet)
expose_to_mcp(category="agentcc")(AgentccWebhookViewSet)
expose_to_mcp(category="agentcc")(AgentccWebhookEventViewSet)
