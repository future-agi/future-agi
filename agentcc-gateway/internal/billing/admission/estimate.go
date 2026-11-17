// Package admission computes a hop's worst-case hold without mutating the
// request or ledger. Credits and the spend guard must use the same estimate.
package admission

import (
	"encoding/json"

	"github.com/futureagi/agentcc-gateway/internal/billing"
	"github.com/futureagi/agentcc-gateway/internal/billing/inclusive"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// Estimate carries the estimate and the bookkeeping only credits may apply.
// Card is the selected reserve card, not a write to rc.Billing.ReservePrice.
// Unpriced distinguishes a missing model price from a refused admission shape.
type Estimate struct {
	Micros    int64
	Priced    bool
	Card      *models.PriceCard
	Inclusive bool
	Unpriced  bool
}

// WorstCase estimates the exact hop using its pinned inclusive card, its
// endpoint set, its pinned token card, or its model price, in that order.
// Token-only endpoint selection, arithmetic and rounding remain billing's
// existing HighestCard/EstimateWorstCaseMicros path.
func WorstCase(rc *models.RequestContext, providerID, model string, prices models.PriceLookup, markupBps, defaultMaxOut int) Estimate {
	if card := rc.Billing.ReservePrice; card != nil && card.Inclusive != nil {
		return inclusiveEstimate(rc, model, card)
	}
	if set := rc.Billing.EndpointSet; len(set) > 0 {
		hasInclusive := false
		for _, ref := range set {
			hasInclusive = hasInclusive || ref.Price.Inclusive != nil
		}
		if !hasInclusive {
			card, micros, ok := billing.HighestCard(set, rc.Request, markupBps, defaultMaxOut)
			if !ok {
				return Estimate{}
			}
			return Estimate{Micros: micros, Priced: true, Card: &card}
		}
		var best Estimate
		for i, ref := range set {
			e := cardEstimate(rc, model, &ref.Price, markupBps, defaultMaxOut)
			if !e.Priced {
				return Estimate{}
			}
			if i == 0 || e.Micros > best.Micros {
				best = e
			}
		}
		return best
	}
	if card := rc.Billing.ReservePrice; card != nil {
		return cardEstimate(rc, model, card, markupBps, defaultMaxOut)
	}
	if prices != nil {
		if card, ok := prices(providerID, model, rc.Billing.EndpointID); ok && card.Billable() {
			e := cardEstimate(rc, model, &card, markupBps, defaultMaxOut)
			if card.Inclusive == nil {
				// The old model-lookup token path did not pin a reserve card.
				e.Card = nil
			}
			return e
		}
	}
	return Estimate{Unpriced: true}
}

func cardEstimate(rc *models.RequestContext, model string, card *models.PriceCard, markupBps, defaultMaxOut int) Estimate {
	if card.Inclusive != nil {
		return inclusiveEstimate(rc, model, card)
	}
	return Estimate{Micros: billing.EstimateWorstCaseMicros(rc.Request, *card, markupBps, defaultMaxOut), Priced: card.Billable(), Card: card}
}

func inclusiveEstimate(rc *models.RequestContext, model string, card *models.PriceCard) Estimate {
	e := Estimate{Card: card, Inclusive: true}
	if rc.Request == nil {
		return e
	}
	hop := *rc.Request
	hop.Model = model
	hop.Extra = map[string]json.RawMessage{}
	for k, v := range rc.Request.Extra {
		hop.Extra[k] = append(json.RawMessage(nil), v...)
	}
	if injected := rc.Metadata["upstream_provider_object"]; injected != "" && rc.Billing.UpstreamUser != "" {
		if string(hop.Extra["provider"]) == injected {
			delete(hop.Extra, "provider")
		}
		if string(hop.Extra["usage"]) == `{"include":true}` {
			delete(hop.Extra, "usage")
		}
	}
	// Before credits captures the caller extensions, the guard must treat the
	// current request as that capture. Later hops retain the original capture,
	// even when a token hop stripped or overwrote those caller extensions.
	original := rc.Billing.AdmissionExtra
	if !rc.Billing.AdmissionCaptured {
		original = rc.Request.Extra
	}
	for k, v := range original {
		hop.Extra[k] = append(json.RawMessage(nil), v...)
	}
	micros, err := inclusive.Bound(&hop, card.Inclusive)
	e.Micros, e.Priced = micros, err == nil
	return e
}
