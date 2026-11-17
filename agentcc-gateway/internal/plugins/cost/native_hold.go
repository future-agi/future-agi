package cost

import "github.com/futureagi/agentcc-gateway/internal/models"

// Incomplete native metering cannot release a credit-funded call's admitted
// estimate for a partial/zero spend. Keep pricing the evidence first, then use
// this attempt's full reservation as a monetary floor. Never cap measured
// evidence at the hold, change a reported charge/explicit zero, or charge a
// failed pre-output attempt merely because its input-only report is partial.
func (p *Plugin) nativeHoldFloor(a *models.Attempt, usage *models.Usage, serving, modality bool, c *attemptCost) {
	if !a.NativeTokenUsage || !c.managed || !c.billable || modality || usage.HasReportedCost() ||
		!(c.estimated || serving && usage.NeedsTokenEstimate()) || a.Pricing == nil || a.Pricing.HoldMicros <= 0 {
		return
	}
	c.estimated = true
	if c.billed >= a.Pricing.HoldMicros {
		return
	}
	c.billed = a.Pricing.HoldMicros
	// The monetary fallback is an estimate, not invented measured tokens.
	// Undo markup at integer-micro precision for auditable upstream/markup
	// fields, then compute landed fees from that estimate and the exact debit.
	denom := int64(bpsDenom + max(p.markupBps, 0))
	upstream := c.billed/denom*bpsDenom + (c.billed%denom*bpsDenom+denom/2)/denom
	c.upstream = max(c.upstream, upstream)
	c.landed = scaleBps(c.upstream, bpsDenom+c.upstreamFeeBps) + scaleBps(c.billed, c.processingFeeBps)
	c.priced = true
	if c.source == "" || c.source == models.CostSourceUnpriced {
		c.source = models.CostSourceCatalogMax
		if a.Pricing.ReservePrice != nil {
			c.source = costSourceOf(a.Pricing.ReservePrice.Source)
		}
	}
}
