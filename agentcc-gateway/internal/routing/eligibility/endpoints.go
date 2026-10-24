package eligibility

import (
	"math"
	"sort"

	"github.com/futureagi/agentcc-gateway/internal/catalog"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// MaxEndpointSet is how many endpoints a managed aggregator attempt is
// pinned to: the provider object's order and only.
const MaxEndpointSet = 5

// endpointScore ranks an endpoint by health × 1/price²: health is
// the upstream's uptime over the last 30 minutes (unknown counts as up), and
// price the blended per-token price, 3 prompt to 1 completion as auto
// weighs them. A free endpoint scores as the cheapest priced one could.
func endpointScore(e *catalog.Endpoint) float64 {
	health := 1.0
	if e.UptimeLast30m != nil {
		health = math.Max(*e.UptimeLast30m, 0) / 100
	}
	price := math.Max((3*e.Prices.Prompt+e.Prices.Completion)/4, 1e-12)
	return health / (price * price)
}

// endpointTier orders endpoints before their score: serving and priced
// first, then priced, then the rest. A pinned set leaves out an endpoint
// that is down (negative status) or whose price only the upstream knows,
// unless no other is eligible.
func endpointTier(e *catalog.Endpoint) int {
	switch {
	case e.DynamicPrice:
		return 2
	case e.Status < 0:
		return 1
	}
	return 0
}

// better reports whether a ranks before b; equal endpoints keep catalog
// order, which the caller's iteration gives.
func better(a, b *catalog.Endpoint) bool {
	if ta, tb := endpointTier(a), endpointTier(b); ta != tb {
		return ta < tb
	}
	return endpointScore(a) > endpointScore(b)
}

// bestEndpoint is the best-ranked of the alive endpoints.
func bestEndpoint(eps []catalog.Endpoint, alive endpointMask) *catalog.Endpoint {
	var best *catalog.Endpoint
	for i := range eps {
		if alive.has(i) && (best == nil || better(&eps[i], best)) {
			best = &eps[i]
		}
	}
	return best
}

// EndpointSet is the verdict's eligible endpoints, ranked, at most max: the
// best tier present only (see endpointTier), by score, ties in catalog
// order. Nil when the candidate has no endpoint list.
func (v *Verdict) EndpointSet(max int) []*catalog.Endpoint {
	if v.entry == nil || v.alive == 0 {
		return nil
	}
	eps := v.entry.Endpoints
	var set []*catalog.Endpoint
	for i := range eps {
		if v.alive.has(i) {
			set = append(set, &eps[i])
		}
	}
	sort.SliceStable(set, func(i, j int) bool { return better(set[i], set[j]) })
	top := endpointTier(set[0])
	n := 0
	for n < len(set) && n < max && endpointTier(set[n]) == top {
		n++
	}
	return set[:n]
}

// PriceCard is e's price card, read from the catalog version snapshotID.
func PriceCard(e *catalog.Endpoint, snapshotID string) models.PriceCard {
	src := models.PriceSourceCatalog
	if e.Source == catalog.SourcePriceCard {
		src = models.PriceSourcePriceCard
	}
	p := e.Prices
	return models.PriceCard{
		Prompt: p.Prompt, Completion: p.Completion, CacheRead: p.CacheRead, CacheWrite: p.CacheWrite,
		Request: p.Request, Image: p.Image, WebSearch: p.WebSearch, Reasoning: p.Reasoning,
		Source: src, SnapshotID: snapshotID,
	}
}

// Source is one managed source of a canonical model: the provider that
// serves it, its customer-billed blended per-token price (0 when the source
// has no fixed price: unpriced, dynamic, or no catalog entry), the fee the
// source adds on top (Endpoint.UpstreamFeeBps, never config; FeeKnown is
// false for a source that lists no endpoints), and its per-model health.
type Source struct {
	ProviderID     string
	Price          float64
	UpstreamFeeBps int
	FeeKnown       bool
	// SuccessRate is the model's recent success rate on the provider
	// (ModelHealth); HealthKnown is false when nothing was recorded.
	SuccessRate float64
	HealthKnown bool
}

// sourcePriceTolerance is how close to the best price a source counts as
// equally priced, so its landed cost decides.
const sourcePriceTolerance = 0.01

// landed is what serving one unit through s costs FutureAGI before card
// processing: its price plus the source's fee, or plus unknownFeeBps when
// its fee is unknown.
func (s Source) landed(unknownFeeBps int) float64 {
	return s.Price * float64(10000+s.fee(unknownFeeBps)) / 10000
}

// fee is s's fee in basis points, or unknownFeeBps when its fee is unknown.
func (s Source) fee(unknownFeeBps int) int {
	if !s.FeeKnown {
		return unknownFeeBps
	}
	return s.UpstreamFeeBps
}

// priced reports whether s has a fixed price to compare.
func (s Source) priced() bool { return s.Price > 0 }

func (s Source) health() float64 {
	if !s.HealthKnown {
		return 1
	}
	return s.SuccessRate
}

// ChooseSource picks among the managed sources of one model: the lowest
// customer-billed price; among the sources within 1% of
// it, the lowest landed cost; then the best health; ties keep order. At
// equal list price a direct source (fee 0) therefore beats an aggregator
// (fee 550). A source whose fee is unknown is costed at the highest known
// fee and loses a tie to a known one, so it never wins on a fee nobody
// read. A source with no fixed price is never compared at "price 0": it is
// chosen only when no source has one, and then the lower fee decides. landedRatio is the chosen source's
// landed cost over the cheapest landed cost among the other comparable
// sources (1 with none), which the decision records.
func ChooseSource(sources []Source) (best int, landedRatio float64) {
	if len(sources) == 0 {
		return -1, 0
	}
	anyPriced := false
	for _, s := range sources {
		anyPriced = anyPriced || s.priced()
	}
	comparable := func(s Source) bool { return !anyPriced || s.priced() }
	minPrice := math.Inf(1)
	unknownFee := 0
	for _, s := range sources {
		if comparable(s) {
			minPrice = math.Min(minPrice, s.Price)
		}
		if s.FeeKnown && s.UpstreamFeeBps > unknownFee {
			unknownFee = s.UpstreamFeeBps
		}
	}
	best = -1
	for i, s := range sources {
		if !comparable(s) || s.Price > minPrice*(1+sourcePriceTolerance)+1e-18 {
			continue
		}
		if best < 0 {
			best = i
			continue
		}
		b := sources[best]
		sl, bl := s.landed(unknownFee), b.landed(unknownFee)
		switch {
		case sl != bl:
			if sl < bl {
				best = i
			}
		case s.FeeKnown != b.FeeKnown:
			if s.FeeKnown {
				best = i
			}
		case s.fee(unknownFee) != b.fee(unknownFee): // only with no fixed price
			if s.fee(unknownFee) < b.fee(unknownFee) {
				best = i
			}
		case s.health() > b.health():
			best = i
		}
	}
	other := math.Inf(1)
	for i, s := range sources {
		if i != best && comparable(s) {
			other = math.Min(other, s.landed(unknownFee))
		}
	}
	if math.IsInf(other, 1) || other == 0 {
		return best, 1
	}
	return best, sources[best].landed(unknownFee) / other
}
