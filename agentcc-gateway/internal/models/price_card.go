package models

import "math"

// PriceSource says where a PriceCard came from.
type PriceSource string

const (
	PriceSourceCatalog   PriceSource = "catalog"
	PriceSourceModelDB   PriceSource = "modeldb"
	PriceSourcePriceCard PriceSource = "price_card"
	PriceSourceOrgCustom PriceSource = "org_custom"
)

// PriceCard is what one endpoint charges, in USD per token (Prompt,
// Completion, CacheRead, CacheWrite, Reasoning) or per unit (Request, Image,
// WebSearch). A zero price means the endpoint does not charge for that line.
// SnapshotID names the catalog snapshot the card was read from, when it came
// from one.
type PriceCard struct {
	Prompt     float64
	Completion float64
	CacheRead  float64
	CacheWrite float64
	Request    float64
	Image      float64
	WebSearch  float64
	Reasoning  float64
	Source     PriceSource
	SnapshotID string
	// ExplicitFree is trusted catalogue evidence, never a client assertion.
	ExplicitFree bool `json:"-"`
	// Inclusive is admission-only; tokenCost must never settle it.
	Inclusive *InclusivePrice `json:"-"`
}

// Billable reports whether this card represents a chat charge without a
// provider cost report. Image counts need a richer reservation and estimate.
func (p PriceCard) Billable() bool {
	if p.Inclusive != nil {
		return true
	} // bounded by inclusive.Bound, never tokenCost
	if p.Image != 0 {
		return false
	}
	for _, v := range []float64{p.Prompt, p.Completion, p.CacheRead, p.CacheWrite, p.Request, p.WebSearch, p.Reasoning} {
		if v < 0 || v > float64(math.MaxInt64/2)/1e6 || math.IsNaN(v) || math.IsInf(v, 0) {
			return false
		}
	}
	return p.Prompt > 0 || p.Completion > 0 || p.Request > 0 ||
		(p.ExplicitFree && p.Prompt == 0 && p.Completion == 0 && p.CacheRead == 0 && p.CacheWrite == 0 && p.Request == 0 && p.WebSearch == 0 && p.Reasoning == 0)
}

// Clone owns the optional exact-decimal quote as well as its scalar card.
func (p PriceCard) Clone() PriceCard {
	if p.Inclusive != nil {
		q := *p.Inclusive
		p.Inclusive = &q
	}
	return p
}

// PriceLookup returns the price card for model served by providerID through
// endpointID ("" when the provider has no endpoint ids), and false when it has
// none. The gateway injects one at startup (plan G0b contract g); consumers
// read it at call time and never import its provider.
type PriceLookup func(providerID, model, endpointID string) (PriceCard, bool)
