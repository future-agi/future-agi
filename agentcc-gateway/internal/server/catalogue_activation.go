package server

import "github.com/futureagi/agentcc-gateway/internal/models"

// mixedCatalogueHop refuses the first crossing between an ordinary token card
// and a CAT-2 inclusive card. Examine all earlier managed attempts (including
// nonbillable failures), not just the immediately preceding hop: a BYOK bridge
// cannot launder a mixed chain. Frozen attempt cards survive catalogue refresh.
// The caller invokes this before reserving a fallback, and again at dispatch
// for retry/failover paths. Earlier dispatched work still settles normally.
func (h *Handlers) mixedCatalogueHop(rc *models.RequestContext, next *models.PriceCard) bool {
	if !h.catalog.Load().MixedCatalogueDisabled() {
		return false
	}
	nextInclusive := next != nil && next.Inclusive != nil
	for _, a := range rc.AttemptsSnapshot() {
		if a.CredentialType != models.CredentialManaged || a.Outcome == models.OutcomeSkipped {
			continue
		}
		priorInclusive := a.Pricing != nil && a.Pricing.ReservePrice != nil && a.Pricing.ReservePrice.Inclusive != nil
		if priorInclusive != nextInclusive {
			return true
		}
	}
	return false
}
