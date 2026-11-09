package budget

import (
	"bytes"
	"encoding/json"

	"github.com/futureagi/agentcc-gateway/internal/billing"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// QualifyCapChat runs after routing and before any pipeline/provider work. It
// uses the injected endpoint prices and the existing billing estimator. There
// is deliberately no independent token estimator or name-based price fallback.
func QualifyCapChat(rc *models.RequestContext, prices models.PriceLookup, defaultOutput int) (refusal *models.APIError) {
	a := RequestAdmission(rc)
	defer func() { refusal = a.Refusal(refusal, "", "") }()
	if a == nil {
		return nil
	}
	req := rc.Request
	if req == nil || req.Stream || rc.IsStream || (rc.EndpointType != "" && rc.EndpointType != "chat") ||
		(req.N != nil && *req.N != 1) || len(req.Tools) > 0 || len(req.ToolChoice) > 0 || req.Audio != nil || len(req.Modalities) > 0 || req.ServiceTier != "" {
		return CapUnenforceable("strict caps require bounded synchronous text chat with one completion and no tools or media")
	}
	for _, msg := range req.Messages {
		var text string
		if json.Unmarshal(msg.Content, &text) != nil || len(msg.ToolCalls) != 0 {
			return CapUnenforceable("strict caps do not support multimodal or tool-dependent prompt dimensions")
		}
	}
	// Pass-through fields can add chargeable dimensions (provider tools,
	// reasoning, cached context, async, etc.). No unknown field is assumed free.
	if len(req.Extra) != 0 || rc.Routing.Provider != nil || rc.Routing.Plugins != nil || rc.Routing.Transforms != nil || rc.Routing.Route != "" {
		return CapUnenforceable("strict caps do not support provider pass-through parameters")
	}
	out := billing.OutputCap(req, defaultOutput)
	if out <= 0 || out > 1_000_000 {
		return CapUnenforceable("strict caps require a bounded output ceiling")
	}
	// Forward the same cap the estimator uses, including when the caller omitted
	// both fields. Both fields, when present, carry the same upper bound.
	if req.MaxCompletionTokens != nil {
		req.MaxCompletionTokens = &out
	}
	if req.MaxTokens != nil || req.MaxCompletionTokens == nil {
		req.MaxTokens = &out
	}
	a.managed = rc.Metadata["billing_mode"] == models.BillingModeManaged
	markup := 0
	if a.managed {
		markup = 500
	}
	// Inclusive cards are bounded by their own exact dimensional contract,
	// never the scalar estimator below. In particular a generation-only card
	// must not turn into a zero strict quote even if endpoint scalars are free.
	if pinned := rc.Billing.ReservePrice; pinned != nil && pinned.Inclusive != nil {
		return CapUnenforceable("strict caps do not support inclusive price cards")
	}
	set := rc.Billing.EndpointSet
	if len(set) == 0 {
		if prices == nil {
			return CapUnenforceable("strict caps require exact endpoint pricing")
		}
		card, ok := prices(rc.Provider, req.Model, rc.Billing.EndpointID)
		if !ok {
			return CapUnenforceable("strict caps require exact endpoint pricing")
		}
		set = []models.EndpointRef{{Price: card}}
	}
	for _, ep := range set {
		p := ep.Price
		if p.Inclusive != nil {
			return CapUnenforceable("strict caps do not support inclusive price cards")
		}
		if !p.Billable() || p.Source == "" || p.Image != 0 || p.WebSearch != 0 || p.CacheWrite > p.Prompt || p.CacheRead > p.Prompt || p.Reasoning > p.Completion {
			return CapUnenforceable("strict caps require fixed prices whose dimensions are bounded by the chat estimator")
		}
	}
	_, estimate, ok := billing.HighestCard(set, req, markup, out)
	if !ok || estimate > 1_000_000_000_000_000 {
		return CapUnenforceable("strict quote unavailable or out of range")
	}
	forwarded, err := capForwardedDimensions(req)
	if err != nil {
		return CapUnenforceable("strict forwarded request cannot be qualified")
	}
	priceSnapshot, err := json.Marshal(rc.Billing.EndpointSet)
	if err != nil {
		return CapUnenforceable("strict pricing snapshot unavailable")
	}
	a.forwarded, a.priceSnapshot = forwarded, priceSnapshot
	a.quoteMicros, a.quoteReady = estimate, true
	a.provider, a.model, a.endpoint = rc.Provider, req.Model, rc.Billing.EndpointID
	return nil
}

// RecheckCapChat is deliberately conservative: a changed request must get a new
// quote and atomic reservations, which this single-attempt slice does not offer.
// Check both before budget reservation and at the final attempt boundary, after
// every pre-plugin rewrite. Earlier holds are released by the normal finalizers.
func RecheckCapChat(rc *models.RequestContext) *models.APIError {
	return recheckCapChat(rc, false)
}

// RecheckForwardedCapChat is only for the final server-owned routing rewrite,
// after RecheckCapChat has rejected all caller/plugin additions. The managed
// rewrite adds provider pinning and usage reporting; neither adds a chargeable
// dimension, and the complete quoted endpoint/price set must remain identical.
func RecheckForwardedCapChat(rc *models.RequestContext) *models.APIError {
	return recheckCapChat(rc, true)
}

func recheckCapChat(rc *models.RequestContext, rewritten bool) (refusal *models.APIError) {
	a := RequestAdmission(rc)
	defer func() { refusal = a.Refusal(refusal, "", "") }()
	if a == nil {
		return nil
	}
	req := rc.Request
	if rewritten && a.managed && req != nil {
		copy := *req
		copy.Extra = make(map[string]json.RawMessage)
		for key, value := range req.Extra {
			if key != "provider" && key != "usage" {
				copy.Extra[key] = value
			}
		}
		req = &copy
	}
	raw, err := capForwardedDimensions(req)
	prices, priceErr := json.Marshal(rc.Billing.EndpointSet)
	if err != nil || priceErr != nil || !bytes.Equal(prices, a.priceSnapshot) || !a.quoteReady || !bytes.Equal(raw, a.forwarded) || rc.Provider != a.provider || rc.Billing.EndpointID != a.endpoint || rc.IsStream || (rc.Metadata["billing_mode"] == models.BillingModeManaged) != a.managed {
		return CapUnenforceable("forwarded request changed after cap quotation; no upstream attempt admitted")
	}
	return nil
}

// User is a non-chargeable attribution field; the gateway replaces it with
// the hashed org for platform-funded calls. Every other forwarded field stays
// pinned, including provider pass-through fields added during dispatch rewriting.
func capForwardedDimensions(req *models.ChatCompletionRequest) ([]byte, error) {
	if req == nil {
		return json.Marshal(nil)
	}
	copy := *req
	copy.User = ""
	return json.Marshal(&copy)
}

func (a *Admission) MatchesRoute(provider, model string) bool {
	return a.provider == provider && a.model == model
}
