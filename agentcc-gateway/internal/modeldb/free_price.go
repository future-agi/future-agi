package modeldb

import (
	"bytes"
	"encoding/json"
	"strings"
)

// Keep source fields separate from PricingInfo: a missing rate is not a
// stated zero, and a provider can define prices the calculator doesn't use.
type priceDeclaration struct {
	raw  json.RawMessage
	free bool
}
type priceDeclarations map[string]priceDeclaration

func declaresFree(raw json.RawMessage, override *PricingOverride) bool {
	fields := make(map[string]any)
	if len(raw) != 0 {
		decoder := json.NewDecoder(bytes.NewReader(raw))
		decoder.UseNumber()
		if decoder.Decode(&fields) != nil || fields == nil {
			return false
		}
	}
	if override != nil {
		// An override replaces only the named rates; retain any other price
		// declared by the catalogue, including prices we don't calculate.
		for key, value := range map[string]*float64{
			"input_cost_per_token":          override.InputPerToken,
			"output_cost_per_token":         override.OutputPerToken,
			"cache_read_input_token_cost":   override.CachedInputPerToken,
			"input_cost_per_token_batches":  override.BatchInputPerToken,
			"output_cost_per_token_batches": override.BatchOutputPerToken,
			"input_cost_per_second":         override.InputPerSecond,
			"input_cost_per_character":      override.InputPerCharacter,
			"input_cost_per_query":          override.InputPerQuery,
			"ocr_cost_per_page":             override.PerPage,
			"output_cost_per_image":         override.OutputPerImage,
			"output_cost_per_second":        override.OutputPerSecond,
		} {
			if value != nil {
				if *value != 0 {
					return false
				}
				fields[key] = json.Number("0")
			}
		}
	}
	for _, key := range []string{"input_cost_per_token", "output_cost_per_token"} {
		if _, ok := fields[key].(json.Number); !ok {
			return false
		}
	}
	return allDefinedCostsZero(fields)
}

// Price fields may be nested in tier lists alongside non-price values such
// as token ranges. Apply the same price-key rule at each level.
func allDefinedCostsZero(node any) bool {
	switch v := node.(type) {
	case map[string]any:
		for key, value := range v {
			if strings.Contains("_"+key+"_", "_cost_") {
				if !zeroCharge(value) {
					return false
				}
			} else if !allDefinedCostsZero(value) {
				return false
			}
		}
	case []any:
		for _, item := range v {
			if !allDefinedCostsZero(item) {
				return false
			}
		}
	}
	return true
}

// Some prices have tiers (e.g. search_context_cost_per_query). Every defined
// tier must state zero. Don't round tiny nonzero rates down to zero.
func zeroCharge(value any) bool {
	switch v := value.(type) {
	case json.Number:
		s := string(v)
		if i := strings.IndexAny(s, "eE"); i >= 0 {
			s = s[:i]
		}
		return strings.Trim(s, "-0.") == ""
	case map[string]any:
		for _, rate := range v {
			if !zeroCharge(rate) {
				return false
			}
		}
		return len(v) > 0
	default:
		return false
	}
}
