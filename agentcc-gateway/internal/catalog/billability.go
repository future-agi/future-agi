package catalog

import (
	"math"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// ManagedBillable reports whether the exact listing has a supported price
// for both reservation and settlement, including an interrupted stream.
// Endpoint lists must contain at least one supported card; request policy
// narrows that set further before dispatch. It never changes BYOK resolution.
func (m *Model) ManagedBillable(source string) bool {
	if m == nil || m.RouterCreditRefusal() != "" {
		return false
	}
	if m.InclusivePrice(source) != nil {
		return true // endpoint policy still narrows the route before dispatch
	}
	if !m.Priced() || !supportedCard(m.Prices, m.PriceTiers, m.ExplicitlyFree(source)) {
		return false
	}
	if len(m.Endpoints) == 0 {
		return true
	}
	for i := range m.Endpoints {
		if m.Endpoints[i].ManagedBillable(m.ID) {
			return true
		}
	}
	return false
}

// ExplicitlyFree requires a current listing from OpenRouter's authoritative
// catalogue, its provider-defined free offering id, and an all-zero card.
// A caller-supplied suffix, an unlisted id, or a zero card from another
// source is not evidence. Parsers mark absent or malformed prices Unpriced.
func (m *Model) ExplicitlyFree(source string) bool {
	return m != nil && m.Priced() && freeOffering(source, m.ID) && m.Prices == (Prices{}) && len(m.PriceTiers) == 0
}

func freeOffering(source, id string) bool {
	return source == SourceOpenRouter && (strings.HasSuffix(id, ":free") || id == "openrouter/free")
}

// ManagedBillable applies the same representation rule to a selected endpoint.
// A free endpoint must belong to a listed free offering on its own source.
func (e *Endpoint) ManagedBillable(model string) bool {
	return e != nil && routerCreditRefusal(model) == "" && !e.DynamicPrice && !e.Unpriced && supportedCard(e.Prices, e.PriceTiers,
		freeOffering(e.Source, model) && e.Prices == (Prices{}))
}

func supportedCard(p Prices, tiers []PriceTier, free bool) bool {
	// PriceCard does not carry prompt tiers or per-input-image counts through
	// reservation and interrupted-stream settlement. Those offers need a
	// richer representation before they can be sold on Future AGI credits.
	card := p.Card()
	card.ExplicitFree = free
	return len(tiers) == 0 && card.Billable()
}

// Card preserves every supported price dimension for reservation and settlement.
func (p Prices) Card() models.PriceCard {
	return models.PriceCard{Prompt: p.Prompt, Completion: p.Completion,
		CacheRead: p.CacheRead, CacheWrite: p.CacheWrite, Request: p.Request,
		Image: p.Image, WebSearch: p.WebSearch, Reasoning: p.Reasoning}
}

// completePricing distinguishes an explicit zero quote from an omitted,
// malformed, non-finite or unsupported price line. Unknown price dimensions
// fail closed, including when familiar token lines are also present.
func completePricing(raw map[string]any) bool {
	if len(raw) == 0 {
		return false
	}
	_, prompt := number(raw["prompt"])
	_, completion := number(raw["completion"])
	fee, request := number(raw["request"])
	// Token-priced offers must quote both dimensions, including explicit
	// zero. A positive per-request-only card is a separate supported shape;
	// it cannot repair a half-present token pair.
	_, hasPrompt := raw["prompt"]
	_, hasCompletion := raw["completion"]
	requestOnly := !hasPrompt && !hasCompletion && request && fee > 0
	if !(prompt && completion) && !requestOnly {
		return false
	}
	base, _, dynamic := parsePricing(raw)
	if !dynamic && base == (Prices{}) && !(prompt && completion) {
		return false
	}
	for key, value := range raw {
		if key == "discount" {
			continue
		}
		if key == "overrides" {
			tiers, ok := value.([]any)
			if !ok {
				return false
			}
			for _, tier := range tiers {
				obj, ok := tier.(map[string]any)
				if !ok {
					return false
				}
				min, ok := number(obj["min_prompt_tokens"])
				if !ok || min <= 0 || math.Trunc(min) != min {
					return false
				}
				for line, price := range obj {
					if line == "min_prompt_tokens" {
						continue
					}
					if _, known := pricingKeys[line]; !known {
						return false
					}
					if _, ok := number(price); !ok {
						return false
					}
				}
			}
			continue
		}
		if _, known := pricingKeys[key]; !known {
			return false
		}
		v, ok := number(value)
		if !ok || math.IsNaN(v) || math.IsInf(v, 0) {
			return false
		}
	}
	return true
}
