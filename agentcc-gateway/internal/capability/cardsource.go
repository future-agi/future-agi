package capability

import (
	"encoding/json"
	"fmt"
	"sync"
)

// Resale statuses of a tool price card.
const (
	ResaleNative         = "native"
	ResaleManagedPartner = "managed_partner"
	ResaleUnconfirmed    = "unconfirmed"
	ResaleBYOKOnly       = "byok_only"
)

// Card terms values the selector and retention read.
const (
	TrainsNo             = "no"
	TrainsAIFeaturesOnly = "ai_features_only"
	CacheNone            = "none"
	EvalExportForbidden  = "forbidden"
)

// Card is one tool-price-card-v1 card: a vendor's SKUs for one capability
// and its terms profile. It is the shape of the golden
// examples/tool-price-card-v1.json, field for field.
type Card struct {
	Vendor               string   `json:"vendor"`
	Capability           string   `json:"capability"`
	SKUs                 []SKU    `json:"skus"`
	ResaleStatus         string   `json:"resale_status"`
	OwnerPricePending    bool     `json:"owner_price_pending,omitempty"`
	ResaleTermsRef       string   `json:"resale_terms_ref"`
	TermsURL             string   `json:"terms_url"`
	TermsCheckedAt       string   `json:"terms_checked_at"`
	ZDRCapable           bool     `json:"zdr_capable"`
	TrainsOnInput        string   `json:"trains_on_input"`
	ImprovementLicense   bool     `json:"improvement_license"`
	ImprovementOptOutRef string   `json:"improvement_opt_out_ref,omitempty"`
	Regions              []string `json:"regions"`
	ResultsRetention     string   `json:"results_retention"`
	Cacheable            string   `json:"cacheable"`
	EvalExport           string   `json:"eval_export"`
	NoDivert             bool     `json:"no_divert"`
	NoPerfDisclosure     bool     `json:"no_perf_disclosure"`
	NoBenchmark          bool     `json:"no_benchmark"`
	Attribution          string   `json:"attribution,omitempty"`
	PriceEffectiveUntil  string   `json:"price_effective_until,omitempty"`
}

// SKU is one priced unit of a card. USDPerUnit is the upstream cost, before
// the markup.
type SKU struct {
	SKU                string  `json:"sku"`
	Unit               string  `json:"unit"`
	USDPerUnit         float64 `json:"usd_per_unit"`
	Tier               string  `json:"tier,omitempty"`
	FreeOnFailure      bool    `json:"free_on_failure"`
	MinUnits           float64 `json:"min_units,omitempty"`
	MaxUSDPerCall      float64 `json:"max_usd_per_call,omitempty"`
	CostFromResultMeta bool    `json:"cost_from_result_meta,omitempty"`
}

// SKUFor is the card's SKU for a depth tier: the one whose tier matches, or,
// for no tier, the first untiered SKU (else the first).
func (c *Card) SKUFor(tier string) (SKU, bool) {
	if c == nil || len(c.SKUs) == 0 {
		return SKU{}, false
	}
	for _, s := range c.SKUs {
		if s.Tier == tier {
			return s, true
		}
	}
	if tier == "" {
		return c.SKUs[0], true
	}
	return SKU{}, false
}

// managedRefusal is why the card's terms refuse a managed call, "" when they
// allow one: never for byok_only, and not for a native card whose owner has
// not set the price yet (a price gate).
func (c *Card) managedRefusal() string {
	switch {
	case c.ResaleStatus == ResaleBYOKOnly:
		return ReasonBYOKOnly
	case c.OwnerPricePending:
		return ReasonOwnerPricePending
	}
	return ""
}

// CardSource is how the capability core reaches tool price cards. The core
// never imports the tool catalog: the gateway's wiring binds the catalog to
// this interface, and the core's own tests serve fixture cards.
type CardSource interface {
	// Cards are the capability's cards, one per vendor, in the catalog's
	// default order.
	Cards(capability string) []*Card
}

// EmptyCards is the card source of a gateway with no tool catalog bound:
// every candidate is unpriced, so no managed call is admitted.
type EmptyCards struct{}

// Cards returns nothing.
func (EmptyCards) Cards(string) []*Card { return nil }

// StaticCards serves a fixed set of cards, in the order given.
type StaticCards struct {
	mu    sync.RWMutex
	byCap map[string][]*Card
}

// NewStaticCards returns a source over cards.
func NewStaticCards(cards ...*Card) *StaticCards {
	s := &StaticCards{byCap: make(map[string][]*Card)}
	for _, c := range cards {
		s.byCap[c.Capability] = append(s.byCap[c.Capability], c)
	}
	return s
}

// Cards returns capability's cards.
func (s *StaticCards) Cards(capability string) []*Card {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return s.byCap[capability]
}

// CardsFromJSON parses tool-price-card-v1 cards: a document {"cards": [...]}
// as the golden holds them, or one card. Each card needs a vendor, a
// capability and at least one SKU.
func CardsFromJSON(raw []byte) ([]*Card, error) {
	var doc struct {
		Cards []*Card `json:"cards"`
	}
	if err := json.Unmarshal(raw, &doc); err != nil {
		return nil, fmt.Errorf("tool price cards: %w", err)
	}
	cards := doc.Cards
	if cards == nil {
		var one Card
		if err := json.Unmarshal(raw, &one); err != nil {
			return nil, fmt.Errorf("tool price card: %w", err)
		}
		cards = []*Card{&one}
	}
	for i, c := range cards {
		if c == nil || c.Vendor == "" || c.Capability == "" || len(c.SKUs) == 0 {
			return nil, fmt.Errorf("tool price card %d: vendor, capability and skus are required", i)
		}
	}
	return cards, nil
}

// cardOf is vendor's card for capability, or nil.
func cardOf(src CardSource, capability, vendor string) *Card {
	for _, c := range src.Cards(capability) {
		if c != nil && c.Vendor == vendor {
			return c
		}
	}
	return nil
}
