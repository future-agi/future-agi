package catalog

import "github.com/futureagi/agentcc-gateway/internal/routerbilling"

// RouterCreditsUnavailable is the value-free customer guidance for the six
// deferred CAT-2 router listings. Reuse it in admission and availability views.
const RouterCreditsUnavailable = "Router models (automatic model selection) aren't available with Future AGI credits yet. Choose a specific model."

// RouterCreditRefusal explains why this listing has no qualified credit-billing
// contract. A negative quote is not a free card, and even a positive scalar
// quote cannot establish a finite target set, selector fee or invocation cap.
// Keep these identities refused across price refreshes and price-card overrides
// until an adapter enforces the actual router contract. BYOK is unchanged.
//
// A qualified contract must freeze all target model/endpoint cards, reserve the
// maximum over enforced bounds before dispatch, and bind served-model evidence
// to that snapshot for settlement (or a nonzero, explicitly marked estimate).
// For compound routers it must cover every child and tool charge, not merely
// the model producing the final response. Provider-only pinning is insufficient.
func (m *Model) RouterCreditRefusal() string {
	if m == nil {
		return ""
	}
	if routerbilling.FamilyMember(m.ID) {
		return RouterCreditsUnavailable
	}
	if m.InclusivePrice(SourceOpenRouter) != nil {
		return ""
	}
	if reason := routerCreditRefusal(m.ID); reason != "" {
		return reason
	}
	if m.DynamicPrice {
		return "no gateway-enforced finite target-model set and target price snapshot; a reported cost after dispatch cannot establish an admission hold"
	}
	return ""
}

func routerCreditRefusal(id string) string {
	if routerbilling.FamilyMember(id) {
		return RouterCreditsUnavailable
	}
	if id == "openrouter/fusion" {
		return "no gateway-enforced finite target-model set and target price snapshot; child invocations and search/fetch charges lack enforced fan-out bounds and complete evidence"
	}
	return ""
}
