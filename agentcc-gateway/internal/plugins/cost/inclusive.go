package cost

import "github.com/futureagi/agentcc-gateway/internal/models"

// priceOwned uses the same per-attempt resolution as the durable journal. It
// runs before legacy failed-hop filtering, which otherwise assigns zero to an
// unknown dispatched attempt whenever a later managed hop happens to bill.
func (p *Plugin) priceOwned(rc *models.RequestContext, a *models.Attempt, usage *models.Usage, serving bool) (attemptCost, bool) {
	if a.CredentialType != models.CredentialManaged {
		return attemptCost{}, false
	}
	var card models.PriceCard
	if a.Pricing != nil && a.Pricing.ReservePrice != nil {
		card = *a.Pricing.ReservePrice
	}
	if !rc.Billing.OwnedRequest && card.Inclusive == nil {
		return attemptCost{}, false
	}
	if a.Pricing == nil || a.Pricing.ReservePrice == nil {
		card, _ = p.cardFor(rc, a, serving, true, usage)
	}
	resolved, receipt := OwnedLiability(a, usage, card, p.markupBps)
	c := attemptCost{managed: true, billable: !resolved.Released, priced: true, estimated: resolved.Estimated,
		upstream: resolved.UpstreamMicros, billed: resolved.BilledMicros}
	c.source = models.CostSourceProviderReported
	if resolved.Estimated {
		c.source = models.CostSourceCatalogMax
		rc.Metadata["billing_estimate"] = "estimated, pending reconciliation"
	} else if !usage.HasReportedCost() && receipt == nil && !resolved.Released {
		c.source = costSourceOf(card.Source)
	}
	if receipt != nil && a.Outcome != models.OutcomeSkipped && a.Outcome != models.OutcomeCircuitOpen {
		rc.Billing.InclusiveOutcomes = append(rc.Billing.InclusiveOutcomes, *receipt)
		if receipt.Anomaly != "" {
			rc.Metadata["billing_alert"] = receipt.Anomaly
		}
	}
	fees := p.fees()
	c.upstreamFeeBps, c.processingFeeBps = fees.UpstreamFeeBps[a.Provider], fees.ProcessingFeeBps
	_, c.landed = billedAndLanded(c.upstream, true, p.markupBps, c.upstreamFeeBps, c.processingFeeBps)
	return c, true
}
