package server

import (
	"context"
	"net/http"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/requestview"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
	anthropictrans "github.com/futureagi/agentcc-gateway/internal/translation/anthropic"
)

// resolveAnthropicRoute is the single selection path for Messages and its
// token count. It was extracted from AnthropicMessages: aliases, provider lock,
// auto, complexity, org-first resolution, fallback and policy share one body
// view. It performs no pipeline dispatch, credit hold or generation write.
func (h *Handlers) resolveAnthropicRoute(ctx context.Context, r *http.Request, rc *models.RequestContext, body []byte) (providers.Provider, *tenant.OrgConfig, string, error) {
	// Caller dimensions, from the x-agentcc-metadata header and the body's own
	// non-spec fields. Read from the raw bytes: the native pass-through below
	// never unmarshals this body.
	applyCallerMetadata(rc, r, nil)
	applyCallerExtrasFromBody(rc, body, anthropictrans.KnownRequestFields())

	// Route on the same request view that native guardrails inspect. Keep the
	// native bytes intact until we know whether a cross-provider chain runs.
	view := requestview.Anthropic(body)
	judged, unreadable := view.ChatRequest(ctx)
	if judged == nil {
		// A native pass-through's guardrail policy decides whether an
		// unreadable body is blocked or merely logged. Routing needs only a
		// carrier here; never give that carrier to the native cache/plugins.
		judged = &models.ChatCompletionRequest{Model: rc.Model, Stream: rc.IsStream}
	}
	rc.Request = judged
	// Peek key org_id before resolving org config (same as ChatCompletion).
	h.peekKeyOrgID(rc)

	// Resolve org config.
	orgID, orgCfg := h.resolveOrgConfig(rc)
	if orgID != "" {
		rc.Metadata["org_id"] = orgID
	}
	h.applyOrgRoutingOverrides(orgCfg, rc)
	h.applyOrgCacheOverrides(orgCfg, rc)
	h.applyOrgRateLimitOverrides(orgCfg, rc)
	h.applyOrgBudgetOverrides(orgCfg, rc)
	h.applyOrgCostTrackingOverrides(orgCfg, rc)
	h.applyOrgIPACLOverrides(orgCfg, rc)
	h.applyOrgAlertingOverrides(orgCfg, rc)
	h.applyOrgPrivacyOverrides(orgCfg, rc)
	h.applyOrgToolPolicyOverrides(orgCfg, rc)
	h.applyOrgAuditOverrides(orgCfg, rc)
	h.applyOrgModelDatabaseOverrides(orgCfg, rc)
	h.applyOrgModelMapOverrides(orgCfg, rc)

	// Count asks which model Messages would use, including a credit-funded
	// route. Use that selection surface only during read-only routing, then
	// restore the true endpoint before the caller admits or executes anything.
	// Count refuses a managed result before the pipeline; it never silently
	// picks a different own-key route just because that route can count.
	endpoint := rc.EndpointType
	rc.EndpointType = "anthropic_messages"
	defer func() { rc.EndpointType = endpoint }()
	provider, _, err := h.resolveRoute(ctx, r, rc, orgCfg, "anthropic_messages")
	return provider, orgCfg, unreadable, err
}
