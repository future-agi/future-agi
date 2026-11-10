package providers

import "github.com/futureagi/agentcc-gateway/internal/config"

// ProviderPreset, KnownProviders, and applyProviderPreset moved to the config
// package, so config.Validate() can apply presets before checking that
// BaseURL/APIFormat are set (see config.ApplyProviderPreset). Aliased here so
// registry.go and this package's existing tests are unaffected.
type ProviderPreset = config.ProviderPreset

var KnownProviders = config.KnownProviders

// applyProviderPreset fills in default BaseURL and APIFormat from known presets.
// Explicit config always takes precedence.
func applyProviderPreset(cfg *config.ProviderConfig) {
	config.ApplyProviderPreset(cfg)
}
