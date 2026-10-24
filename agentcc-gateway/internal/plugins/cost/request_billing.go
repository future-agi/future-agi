package cost

import (
	"strconv"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// writeRequestBilling sums the attempts' costs into rc.Billing and the
// request's cost metadata.
func (p *Plugin) writeRequestBilling(rc *models.RequestContext, costs []attemptCost, serving int) {
	var upManaged, upBYOK, billed, landed int64
	anyManaged, anyPriced, estimated := false, false, false
	for _, c := range costs {
		estimated = estimated || c.estimated
		if !c.billable {
			continue
		}
		anyPriced = anyPriced || c.priced
		if c.managed {
			anyManaged = true
			upManaged += c.upstream
			billed += c.billed
			landed += c.landed
		} else {
			upBYOK += c.upstream
		}
	}
	precise := requestCostSource(costs)

	b := &rc.Billing
	b.UsageEstimated = b.UsageEstimated || estimated
	b.CostSource = logCostSource(precise, costs, serving)
	if anyManaged {
		b.UpstreamMicros, b.BilledMicros, b.LandedMicros = upManaged, billed, landed
		b.MarkupMicros, b.MarkupBps = billed-upManaged, p.markupBps
		if serving >= 0 && costs[serving].managed {
			b.UpstreamFeeBps, b.ProcessingFeeBps = costs[serving].upstreamFeeBps, costs[serving].processingFeeBps
		}
	} else {
		b.UpstreamMicros = upBYOK
	}

	if precise != "" {
		rc.Metadata["cost_source"] = string(precise)
	}
	if !anyPriced {
		return
	}
	if anyManaged {
		rc.Metadata["cost"] = formatMicros(billed)
		rc.Metadata["cost_base"] = formatMicros(upManaged)
		rc.Metadata["cost_markup_percent"] = strconv.FormatFloat(p.managedMarkupPercent, 'f', -1, 64)
		return
	}
	rc.Metadata["cost"] = formatMicros(upBYOK)
}

// requestCostSource is the request's precise cost source: the one source of
// its billable attempts, mixed when they differ, "" when none was billable.
func requestCostSource(costs []attemptCost) models.CostSource {
	var out models.CostSource
	for _, c := range costs {
		switch {
		case !c.billable:
		case out == "":
			out = c.source
		case out != c.source:
			return models.CostSourceMixed
		}
	}
	return out
}

// logCostSource maps the request's precise cost source to the one
// rc.Billing records, which the log entry's cost_source carries and whose
// enum (log-entry-v2's CostSource) has neither mixed nor cache_hit: a mixed
// request records unpriced when any of its
// billable attempts was unpriced, else the source of the attempt that served,
// else of its first billable attempt; a cache hit records nothing. The
// precise value stays in the "cost_source" metadata key.
func logCostSource(precise models.CostSource, costs []attemptCost, serving int) models.CostSource {
	switch precise {
	case models.CostSourceCacheHit:
		return ""
	case models.CostSourceMixed:
		for _, c := range costs {
			if c.billable && !c.priced {
				return models.CostSourceUnpriced
			}
		}
		if serving >= 0 && costs[serving].billable {
			return costs[serving].source
		}
		for _, c := range costs {
			if c.billable {
				return c.source
			}
		}
		return ""
	default:
		return precise
	}
}
