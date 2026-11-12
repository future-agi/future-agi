package eligibility

import (
	"encoding/json"
	"net/http"
	"strings"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/catalog"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// StatusFor is the HTTP status of an explicit route refused with code: 410
// for an expired model, 404 when no provider matches the caller's filters,
// 403 for every other refusal.
func StatusFor(code string) int {
	switch code {
	case CodeModelExpired:
		return http.StatusGone
	case CodeNoProviderMatches:
		return http.StatusNotFound
	case CodeProviderPreferenceUnavailable:
		return http.StatusBadRequest
	}
	return http.StatusForbidden
}

// checkCatalogState is rule 4, on the provider's catalog entry and the model
// shutdown calendar: a blocked model is refused; one past its catalog
// expiration date, or past its shutdown date in the calendar as the serving
// host retires it (catalog/deprecations.json), is refused (410); one
// that is deprecated but not expired is allowed (the route reports the date)
// and left out of auto, and so is a degraded one. A model no catalog lists
// has no state beyond the calendar.
func checkCatalogState(c *checker) (string, string) {
	m := c.in.Entry
	if m != nil && m.State == catalog.StateBlocked {
		return CodeModelBlocked, "model " + m.ID + " is blocked: " + m.StateReason
	}
	if m != nil && m.ExpirationDate != "" {
		if exp, err := time.Parse(time.DateOnly, m.ExpirationDate); err == nil && !c.now.Before(exp) {
			return CodeModelExpired, "model " + m.ID + " expired on " + m.ExpirationDate
		}
	}
	if d, gone := c.in.Shutdowns.ShutDown(c.in.Host, c.in.Model, c.now); gone {
		return CodeModelExpired, d.Reason()
	}
	if m == nil {
		return "", ""
	}
	switch m.State {
	case catalog.StateDeprecated:
		if c.in.Auto {
			return CodeModelDeprecated, "model " + m.ID + " is deprecated (expires " + m.ExpirationDate + ")"
		}
		c.deprecated = m.ExpirationDate
	case catalog.StateDegraded:
		if c.in.Auto {
			return CodeModelDegraded, "model " + m.ID + " is degraded: " + m.StateReason
		}
	}
	return "", ""
}

// ThoughtSignatureFromGeneration is the default thought-signature family
// ("google/gemini-3* and later"): with catalog.thought_signature_models
// unset (nil), every Gemini model of generation 3 or later needs a thought
// signature, so a later generation needs no config change. A configured
// list of globs replaces the default.
const ThoughtSignatureFromGeneration = 3

// signedToolCallMarker separates a tool call id from the thought signature
// restored by the native Gemini adapter. OpenRouter instead restores opaque
// extra_content or reasoning_details, never this native-only ID marker.
const signedToolCallMarker = "::sig::"

// checkToolHistory is rule 10: Gemini refuses a transcript whose earlier
// assistant tool calls lack a thought signature, as calls another model made
// do, so a candidate in the thought-signature family is refused when any
// assistant tool call in the request is unsigned.
func checkToolHistory(c *checker) (string, string) {
	if !needsThoughtSignature(c.in.Model, c.in.ThoughtSignatureModels) || !hasUnsignedToolCall(c.in.Request, c.in.Aggregator) {
		return "", ""
	}
	return CodeToolHistoryIncompatible, "model " + c.in.Model + " needs a thought signature on every earlier tool call, and this transcript's tool calls have none"
}

// needsThoughtSignature reports whether model is in the thought-signature
// family: by generation when globs is nil, else by glob. A model named
// without its vendor ("gemini-3-pro") is matched against each glob's model
// part.
func needsThoughtSignature(model string, globs []string) bool {
	if globs == nil {
		return geminiGeneration(model) >= ThoughtSignatureFromGeneration
	}
	for _, g := range globs {
		if globMatch(g, model) {
			return true
		}
		if vendorOf(model) == "" {
			if _, bare, ok := strings.Cut(g, "/"); ok && globMatch(bare, model) {
				return true
			}
		}
	}
	return false
}

// geminiGeneration is the major version of a Gemini model id, with or
// without its "google/" vendor ("google/gemini-3.1-pro" and "gemini-3-flash"
// are 3), and 0 for any other model. It allocates nothing.
func geminiGeneration(model string) int {
	const prefix = "gemini-"
	name := model
	if vendor := vendorOf(model); vendor != "" {
		if !strings.EqualFold(vendor, "google") {
			return 0
		}
		name = model[len(vendor)+1:]
	}
	if len(name) <= len(prefix) || !strings.EqualFold(name[:len(prefix)], prefix) {
		return 0
	}
	gen := 0
	for _, r := range name[len(prefix):] {
		if r < '0' || r > '9' || gen > 1000 {
			break
		}
		gen = gen*10 + int(r-'0')
	}
	return gen
}

func hasUnsignedToolCall(req *models.ChatCompletionRequest, aggregator bool) bool {
	if req == nil {
		return false
	}
	for i := range req.Messages {
		m := &req.Messages[i]
		if m.Role != "assistant" || len(m.ToolCalls) == 0 {
			continue
		}
		if aggregator {
			// Gemini signs a parallel-call group, not every sibling. Never
			// carry that signature across separate assistant messages.
			signed := hasOpenRouterGeminiReasoning(m.ReasoningDetails)
			for _, tc := range m.ToolCalls {
				signed = signed || hasOpenRouterGeminiSignature(tc.ExtraContent)
			}
			if !signed {
				return true
			}
			continue
		}
		for _, tc := range m.ToolCalls {
			if !strings.Contains(tc.ID, signedToolCallMarker) {
				return true
			}
		}
	}
	return false
}

// These checks establish representation compatibility, not authenticity:
// validation of the opaque signature remains the upstream provider's job.
func hasOpenRouterGeminiSignature(raw json.RawMessage) bool {
	var extra struct {
		Google struct {
			ThoughtSignature string `json:"thought_signature"`
		} `json:"google"`
	}
	return json.Unmarshal(raw, &extra) == nil && strings.TrimSpace(extra.Google.ThoughtSignature) != ""
}

func hasOpenRouterGeminiReasoning(raw json.RawMessage) bool {
	var details []struct {
		Type   string `json:"type"`
		Data   string `json:"data"`
		Format string `json:"format"`
	}
	if json.Unmarshal(raw, &details) != nil {
		return false
	}
	for _, detail := range details {
		if detail.Type == "reasoning.encrypted" && detail.Format == "google-gemini-v1" && strings.TrimSpace(detail.Data) != "" {
			return true
		}
	}
	return false
}
