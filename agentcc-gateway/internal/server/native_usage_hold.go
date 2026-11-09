package server

import (
	"github.com/futureagi/agentcc-gateway/internal/providers/anthropic"
	"github.com/futureagi/agentcc-gateway/internal/providers/bedrock"
	"github.com/futureagi/agentcc-gateway/internal/providers/cohere"
	"github.com/futureagi/agentcc-gateway/internal/providers/gemini"
)

// Use the built adapter, never a provider name or caller-controlled metadata.
// Managed attempts use this registry instance; org replacements are BYOK and
// cannot spend the managed hold. Called for every primary/retry/fallback hop.
func (h *Handlers) nativeTokenUsage(providerID string) bool {
	if h.registry == nil {
		return false
	}
	p, ok := h.registry.GetProvider(providerID)
	if !ok {
		return false
	}
	switch p.(type) {
	case *anthropic.Provider, *bedrock.Provider, *cohere.Provider, *gemini.Provider:
		return true
	default:
		return false
	}
}
