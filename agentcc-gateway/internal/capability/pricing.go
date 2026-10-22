package capability

import (
	"math"
	"math/big"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// Money below a micro is kept in picodollars (1e-12 USD), so a card priced
// per character or per thousand characters keeps its markup instead of
// rounding it away (the micro rounding of billing.USDToMicros would).
const (
	picoPerUSD   = 1_000_000_000_000
	picoPerMicro = 1_000_000
	bpsDenom     = 10_000
)

// usdToPico is usd in picodollars, rounded half up; 0 for a non-positive or
// non-finite amount.
func usdToPico(usd float64) int64 {
	if !(usd > 0) || math.IsInf(usd, 1) {
		return 0
	}
	p := usd * picoPerUSD
	if p >= math.MaxInt64/2 {
		return math.MaxInt64 / 2
	}
	return int64(math.Floor(p + 0.5))
}

// countedUnits are the tool-price-card-v1 units a vendor counts in whole
// numbers; the others (seconds, minutes, thousands of characters, gigabytes,
// container hours, GB-days) are measured and may be fractional.
var countedUnits = map[string]bool{
	"request": true, "result": true, "page": true, "content_page": true,
	"image": true, "clip": true, "search_unit": true,
}

// callUnits is how many units a call is held for, and priced for when the
// vendor reports none: what it asked for, at least the SKU's min_units, and
// 1 when it named none. A counted unit (a request, a page, an image) is
// rounded up to a whole count, so a caller's fractional units can never
// price a call below one unit. Hold and rate-card settlement both use it, so
// a call is never held under what it settles at. A measured unit takes the
// caller's figure: an adapter for such a SKU reports the units it measured.
func callUnits(sku SKU, requested float64) float64 {
	units := requested
	if !(units > 0) {
		units = 1
	}
	if countedUnits[sku.Unit] {
		units = math.Ceil(units)
	}
	if sku.MinUnits > units {
		units = sku.MinUnits
	}
	return units
}

// upstreamPico is the upstream cost of units of sku, capped at the SKU's
// max_usd_per_call when it has one.
func upstreamPico(sku SKU, units float64) int64 {
	p := usdToPico(sku.USDPerUnit * units)
	if sku.MaxUSDPerCall > 0 {
		p = min(p, usdToPico(sku.MaxUSDPerCall))
	}
	return p
}

// withMarkupPico is upstream × (10000 + bps) / 10000, rounded half up.
func withMarkupPico(upstream int64, bps int) int64 {
	n := new(big.Int).Mul(big.NewInt(upstream), big.NewInt(int64(bpsDenom+bps)))
	n.Add(n, big.NewInt(bpsDenom/2))
	return n.Quo(n, big.NewInt(bpsDenom)).Int64()
}

// reserveMicros is the hold for upstream: upstream × (10000 + bps) / 10000,
// rounded up to a whole micro (the worst case is never under-held).
func reserveMicros(upstream int64, bps int) int64 {
	if upstream <= 0 {
		return 0
	}
	n := new(big.Int).Mul(big.NewInt(upstream), big.NewInt(int64(bpsDenom+bps)))
	d := big.NewInt(bpsDenom * picoPerMicro)
	q, r := new(big.Int).QuoRem(n, d, new(big.Int))
	if r.Sign() > 0 {
		q.Add(q, big.NewInt(1))
	}
	return q.Int64()
}

// picoToMicrosHalfUp is pico in whole micros, rounded half up.
func picoToMicrosHalfUp(pico int64) int64 {
	if pico <= 0 {
		return 0
	}
	return (pico + picoPerMicro/2) / picoPerMicro
}

// landedMicros is the landed cost of a managed capability call: the
// upstream cost (capability vendors add no upstream fee) plus the card
// processing estimate on what was billed.
func landedMicros(upstreamMicros, billedMicros int64, processingFeeBps int) int64 {
	processing := (billedMicros*int64(processingFeeBps) + bpsDenom/2) / bpsDenom
	return upstreamMicros + processing
}

// worstCase is a candidate's hold: the requested units of its SKU at the
// card's price with the tool markup, rounded up. An unpriced managed call is
// held at tools.reserve_default_usd.
func (c *Core) worstCase(cand candidate, req Request) int64 {
	if !cand.priced {
		return reserveMicros(usdToPico(c.opts.ReserveDefaultUSD), 0)
	}
	return reserveMicros(upstreamPico(cand.sku, callUnits(cand.sku, req.Units)), c.opts.MarkupBps)
}

// WorstCaseMicros is the most a call of capability for req can be held at:
// the largest hold among the managed candidates the selector admits for it,
// computed exactly as Do reserves (0 when only BYOK candidates are left,
// because BYOK calls are free). The budget hold takes it in its pre phase.
func (c *Core) WorstCaseMicros(rc *models.RequestContext, capability string, req Request) int64 {
	cands, _, err := c.selectCandidates(rc, capability, req)
	if err != nil {
		return 0
	}
	return c.worstOf(cands, req)
}

// worstOf is the largest managed hold among cands.
func (c *Core) worstOf(cands []candidate, req Request) int64 {
	var worst int64
	for _, cand := range cands {
		if cand.managed() {
			worst = max(worst, c.worstCase(cand, req))
		}
	}
	return worst
}

// WorstCaseMicros is the installed core's WorstCaseMicros, 0 without one.
func WorstCaseMicros(rc *models.RequestContext, capability string, req Request) int64 {
	if c := Installed(); c != nil {
		return c.WorstCaseMicros(rc, capability, req)
	}
	return 0
}

// listPricePico is a candidate's list price for ranking: its SKU's upstream
// price per unit.
func listPricePico(cand candidate) int64 {
	return usdToPico(cand.sku.USDPerUnit)
}
