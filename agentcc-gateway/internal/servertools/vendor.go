package servertools

import (
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// Vendors whose server-tool fees the table holds.
const (
	VendorAnthropic = "anthropic"
	VendorOpenAI    = "openai"
	VendorGoogle    = "google"
)

// Vendor is the vendor whose fee schedule prices model's server tools: the
// model id's own vendor ("anthropic/claude-sonnet-5", "google/gemini-3-pro"),
// else its family by name (claude, gpt and the o-series, gemini), else the
// provider named after one. "" when none says. It allocates nothing.
func Vendor(provider, model string) string {
	if v, _, ok := strings.Cut(model, "/"); ok {
		if vendor := vendorNamed(v); vendor != "" {
			return vendor
		}
	}
	bare := model[strings.LastIndexByte(model, '/')+1:]
	switch {
	case hasPrefixFold(bare, "claude"):
		return VendorAnthropic
	case hasPrefixFold(bare, "gemini"):
		return VendorGoogle
	case hasPrefixFold(bare, "gpt-"), hasPrefixFold(bare, "chatgpt"), hasPrefixFold(bare, "codex"), isOSeries(bare):
		return VendorOpenAI
	}
	return vendorNamed(provider)
}

func vendorNamed(s string) string {
	switch strings.ToLower(s) {
	case "anthropic":
		return VendorAnthropic
	case "openai":
		return VendorOpenAI
	case "google", "gemini", "vertex", "vertex_ai":
		return VendorGoogle
	}
	return ""
}

// isOSeries reports an OpenAI o-series id: "o" and a digit ("o3", "o4-mini").
func isOSeries(s string) bool {
	return len(s) >= 2 && (s[0] == 'o' || s[0] == 'O') && s[1] >= '0' && s[1] <= '9'
}

func hasPrefixFold(s, prefix string) bool {
	return len(s) >= len(prefix) && strings.EqualFold(s[:len(prefix)], prefix)
}

// GeminiGrounded reports whether u counts Gemini grounding: Google Search
// queries, or a prompt grounded with Google Search or Maps. The Gemini API
// terms forbid analysing or learning from Grounded Results, so callers mark
// such a request's log entry eval_export forbidden, and the cache never
// stores its response.
func GeminiGrounded(u *models.ServerToolUsage) bool {
	return u != nil && (u.WebSearchQueries > 0 || u.GroundedPrompts > 0 || u.MapsGroundedPrompts > 0)
}
