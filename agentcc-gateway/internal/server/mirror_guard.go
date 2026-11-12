package server

import (
	"context"
	"log/slog"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/routing"
)

// Traffic mirror guard (MIXED-HOP-GUARD-1). The mirror (config.yaml
// routing.mirror) sends a copy of a served chat request to another provider,
// fire and forget, once the request has been answered. The copy is not an
// attempt: it never passes startAttempt, hop admission or a credit hold, and
// nothing bills it. It goes out on the target's global registry instance,
// whose credentials are the node operator's. So the mirror is the
// operator's own, self-hosted feature:
//
//   - It never reaches a managed provider, whose calls Future AGI pays for
//     and recovers only through credits: config load refuses a rule that
//     targets one (config.Validate), the mirror's provider lookup never
//     returns one (mirrorLookup), and dispatch refuses it before the lookup.
//   - It never copies a customer's request: a key limited to its org's
//     providers (orgProvidersOnly, every key but an internal one or a
//     license-authorized request) never resolves to a global provider, so
//     its request is never sent on the operator's credentials as a copy
//     either.
//   - A copy to any other provider is held to the request's billing mode as
//     every hop is (mixed_billing.go): a request billed to credits is not
//     copied onto a provider key unless the node allows mixed billing chains.
//     Credits are only ever billed to customer keys today, so the first two
//     rules already refuse every such copy; this one stays as a backstop.
//
// dispatchMirror applies the three, in that order, before the lookup.

// Why the guard refused a mirror copy, as its log says.
const (
	mirrorRefusedManagedTarget = "managed_target"
	mirrorRefusedCustomerKey   = "customer_key"
	mirrorRefusedCrossMode     = "cross_billing_mode"
)

// mirrorRefusal returns why rc's served request may not be copied to
// targetProvider, or "": never to a managed provider; never for a key
// limited to its org's providers; to another provider (whose calls are
// billed as byok) only when the request is byok too, or the node allows
// mixed billing chains.
func (h *Handlers) mirrorRefusal(ctx context.Context, rc *models.RequestContext, targetProvider string) string {
	if h.registry == nil || h.registry.IsManaged(targetProvider) {
		return mirrorRefusedManagedTarget
	}
	if h.orgProvidersOnly(ctx, rc) {
		return mirrorRefusedCustomerKey
	}
	if !h.mixedBillingChains && requestBillingMode(rc) != models.CredentialBYOK {
		return mirrorRefusedCrossMode
	}
	return ""
}

// requestBillingMode is the billing mode rc's attempts were held to: its
// chain mode, or, for a request that recorded none, the mode it was billed
// in.
func requestBillingMode(rc *models.RequestContext) models.CredentialType {
	if mode := rc.Billing.ChainMode; mode != "" {
		return mode
	}
	return credentialType(rc)
}

// dispatchMirror copies rc's served non-stream chat request to the target of
// the mirror rule m picks for it, if any, unless mirrorRefusal refuses that
// target. A refusal is logged without the request's content or credentials:
// at debug level for a customer's request, which a node that mirrors its
// own traffic refuses on every sampled request by design, else at info.
func (h *Handlers) dispatchMirror(ctx context.Context, m *routing.Mirror, rc *models.RequestContext) {
	rule, ok := m.ShouldMirror(rc.Request.Model)
	if !ok {
		return
	}
	if reason := h.mirrorRefusal(ctx, rc, rule.TargetProvider); reason != "" {
		level := slog.LevelInfo
		if reason == mirrorRefusedCustomerKey {
			level = slog.LevelDebug
		}
		slog.Log(ctx, level, "traffic mirror refused",
			"reason", reason, "request_id", rc.RequestID,
			"target_provider", rule.TargetProvider, "target_model", rule.TargetModel,
			"source_model", rc.Model, "billing_mode", string(requestBillingMode(rc)))
		return
	}

	// Copy request fields before rc is released to the pool.
	reqCopy := *rc.Request

	// Build production info for shadow result capture.
	var prod *routing.ProductionInfo
	if m.Store != nil {
		prodTokens := 0
		prodResponse := ""
		if rc.Response != nil {
			if rc.Response.Usage != nil {
				prodTokens = rc.Response.Usage.TotalTokens
			}
			prodResponse = routing.ExtractResponseText(rc.Response)
		}
		prod = &routing.ProductionInfo{
			RequestID:  rc.RequestID,
			Model:      rc.Model,
			Response:   prodResponse,
			LatencyMs:  time.Since(rc.StartTime).Milliseconds(),
			Tokens:     prodTokens,
			StatusCode: 200,
		}
	}
	m.ExecuteRuleAsync(rule, &reqCopy, rc.Provider, rc.Model, prod)
}

// mirrorLookup is the traffic mirror's provider lookup: registry's
// providers, never a managed one, whoever asks.
func mirrorLookup(registry *providers.Registry) routing.MirrorProviderLookup {
	return func(providerID string) (routing.MirrorProvider, bool) {
		if registry.IsManaged(providerID) {
			slog.Error("traffic mirror refused a managed target provider", "target_provider", providerID)
			return nil, false
		}
		return registry.GetProvider(providerID)
	}
}
