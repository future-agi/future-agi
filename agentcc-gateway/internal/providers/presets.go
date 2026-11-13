package providers

import "github.com/futureagi/agentcc-gateway/internal/config"

// ProviderPreset contains known defaults for a provider type.
type ProviderPreset = config.ProviderPreset

// KnownProviders maps provider type names to their default configurations.
var KnownProviders = config.KnownProviders

// applyProviderPreset fills in default BaseURL and APIFormat from known presets.
// Explicit config always takes precedence.
func applyProviderPreset(cfg *config.ProviderConfig) {
	config.ApplyProviderPreset(cfg)
}
