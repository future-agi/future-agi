package models

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
}

// PriceLookup returns the price card for model served by providerID through
// endpointID ("" when the provider has no endpoint ids), and false when it has
// none. The gateway injects one at startup; consumers
// read it at call time and never import its provider.
type PriceLookup func(providerID, model, endpointID string) (PriceCard, bool)
