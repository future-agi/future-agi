package routing

import "github.com/futureagi/agentcc-gateway/internal/models"

// FallbackAttemptKind is the kind of an attempt that moves down a model
// fallback chain: an auto-routed request's chain is its runners-up.
func FallbackAttemptKind(autoRouted bool) models.AttemptKind {
	if autoRouted {
		return models.AttemptAutoFallback
	}
	return models.AttemptModelFallback
}

// ModelFallbacks holds model fallback chains.
type ModelFallbacks struct {
	chains map[string][]string // model → ordered fallback models
}

// NewModelFallbacks creates a model fallback lookup.
func NewModelFallbacks(chains map[string][]string) *ModelFallbacks {
	if chains == nil {
		chains = make(map[string][]string)
	}
	return &ModelFallbacks{chains: chains}
}

// GetChain returns the fallback models for a given model (not including the model itself).
func (mf *ModelFallbacks) GetChain(model string) []string {
	if mf == nil {
		return nil
	}
	return mf.chains[model]
}

// HasFallbacks returns true if the model has any fallbacks configured.
func (mf *ModelFallbacks) HasFallbacks(model string) bool {
	if mf == nil {
		return false
	}
	return len(mf.chains[model]) > 0
}
