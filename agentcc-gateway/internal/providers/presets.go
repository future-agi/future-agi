package providers

import "github.com/futureagi/agentcc-gateway/internal/config"

// ProviderPreset contains known defaults for a provider type.
type ProviderPreset = config.ProviderPreset

// KnownProviders shares config's preset table. Keep this name for registry and
// capabilities callers; config owns the defaults so they precede validation.
var KnownProviders = config.KnownProviders

// applyProviderPreset also supports callers that build a registry directly,
// without config.Load or Validate. Reapplying defaults is idempotent.
func applyProviderPreset(cfg *config.ProviderConfig) {
	config.ApplyProviderPreset(cfg)
}
