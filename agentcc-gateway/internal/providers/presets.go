package providers

import "github.com/futureagi/agentcc-gateway/internal/config"

// ProviderPreset contains known defaults for a provider type.
// It remains an alias so callers can use the existing providers API.
type ProviderPreset = config.ProviderPreset

// KnownProviders maps provider type names to their default configurations.
var KnownProviders = config.KnownProviders

// applyProviderPreset also supports callers that construct Config directly,
// instead of loading it through config.Load.
func applyProviderPreset(cfg *config.ProviderConfig) {
	config.ApplyProviderPreset(cfg)
}
