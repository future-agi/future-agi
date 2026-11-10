package providers

import "github.com/futureagi/agentcc-gateway/internal/config"

// ProviderPreset contains known defaults for a provider type.
type ProviderPreset = config.ProviderPreset

// KnownProviders shares the defaults applied before config validation.
var KnownProviders = config.KnownProviders

func applyProviderPreset(cfg *config.ProviderConfig) {
	config.ApplyProviderPreset(cfg)
}
