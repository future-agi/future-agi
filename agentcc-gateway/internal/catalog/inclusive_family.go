package catalog

import (
	"encoding/json"
	"slices"
	"strconv"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/routerbilling"
)

const InclusiveFamilyContract = "openrouter-inclusive-r601-image-1"

// Exact listing identities, not prefix permission. Expiration and all normal
// policy checks still apply. No router (especially Fusion) is qualified here.
var imageFamily = map[string]bool{
	"google/gemini-4-argon": true, "google/gemini-4-argon:batch": true,
	"google/gemini-3.8-flash": true, "google/gemini-3.8-flash:batch": true,
	"google/gemini-3.7-flash": true, "google/gemini-3.7-flash:batch": true,
	"google/gemini-3.6-flash": true, "google/gemini-3.6-flash:batch": true,
	"google/gemini-3.5-flash-lite": true, "google/gemini-3.5-flash-lite:batch": true,
	"google/gemini-3-pro-image": true,
	"google/gemini-3.5-flash":   true, "google/gemini-3.5-flash:batch": true,
	"google/gemini-3.1-flash-lite": true, "google/gemini-3.1-flash-lite:batch": true,
	"~google/gemini-pro-latest": true, "~google/gemini-flash-latest": true,
	"google/gemini-3.1-flash-lite-preview":      true,
	"google/gemini-3.1-pro-preview-customtools": true,
	"google/gemini-3.1-pro-preview":             true, "google/gemini-3.1-pro-preview:batch": true,
	"google/gemini-3-flash-preview": true, "google/gemini-3-flash-preview:batch": true,
	"google/gemini-3-pro-image-preview": true, "google/gemini-2.5-flash-image": true,
	"google/gemini-2.5-flash-lite": true, "google/gemini-2.5-flash-lite:batch": true,
	"google/gemini-2.5-flash": true, "google/gemini-2.5-flash:batch": true,
	"google/gemini-2.5-pro": true, "google/gemini-2.5-pro:batch": true,
	"google/gemini-2.5-pro-preview": true,
}

// readInclusivePrice freezes exact decimal strings from the authoritative
// listing. The Lyria amounts are the explicit listing prices ratified by R601,
// not an inference that a zero token quote means a free generation.
func readInclusivePrice(m *Model, raw map[string]any) *models.InclusivePrice {
	q := &models.InclusivePrice{Contract: InclusiveFamilyContract, Model: m.ID,
		ContextLimit: int64(m.ContextLength), OutputLimit: int64(m.MaxOutputTokens)}
	switch m.ID {
	case "google/lyria-3-pro-preview":
		if !strings.Contains(m.Description, "$0.08") {
			return nil
		}
		q.Generation = "0.08"
	case "google/lyria-3-clip-preview":
		if !strings.Contains(m.Description, "$0.04") {
			return nil
		}
		q.Generation = "0.04"
	default:
		if !imageFamily[m.ID] || m.Prices.Image <= 0 {
			return nil
		}
		q.Prompt, q.Completion = exactRate(raw, "prompt"), exactRate(raw, "completion")
		q.CacheRead, q.CacheWrite = exactRate(raw, "input_cache_read"), exactRate(raw, "input_cache_write")
		q.Reasoning, q.Image = exactRate(raw, "internal_reasoning"), exactRate(raw, "image")
		q.TextOutput = slices.Contains(m.OutputModalities, "text")
		q.ImageOutput = slices.Contains(m.OutputModalities, "image")
	}
	return q
}

func exactRate(raw map[string]any, key string) string {
	switch v := raw[key].(type) {
	case string:
		return v
	case json.Number:
		return string(v)
	case float64:
		return strconv.FormatFloat(v, 'f', -1, 64)
	default:
		return "0"
	}
}

// InclusivePrice is non-nil only on parsed, fixed, complete OpenRouter family
// listings. A dynamic, stale, missing or tiered card cannot borrow qualification.
func (m *Model) InclusivePrice(source string) *models.InclusivePrice {
	// Also reject old in-process or persisted qualification, regardless of the
	// startup flag, quote sign, or the contract label on the stale card.
	if m != nil && routerbilling.FamilyMember(m.ID) {
		return nil
	}
	if m == nil || !m.InclusiveEnabled || source != SourceOpenRouter || !m.Priced() || len(m.PriceTiers) != 0 || m.Inclusive == nil || m.Inclusive.Model != m.ID || m.Inclusive.Contract != InclusiveFamilyContract {
		return nil
	}
	q := *m.Inclusive
	return &q
}
