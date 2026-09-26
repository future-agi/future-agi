package providers

import "github.com/futureagi/agentcc-gateway/internal/config"

// ProviderPreset contains known defaults for a provider type.
type ProviderPreset struct {
	BaseURL   string
	APIFormat string

	// PathPrefix is stated on every row rather than left to the zero value:
	// an omitted entry would read as "" and silently strip the version segment
	// from a provider that needs one.
	PathPrefix string
}

// KnownProviders maps provider type names to their default configurations.
//
// PathPrefix is stated on every row rather than left to the zero value: an
// omitted entry would read as "" and silently strip the version segment from a
// provider that needs one.
var KnownProviders = map[string]ProviderPreset{
	"groq":        {BaseURL: "https://api.groq.com/openai", APIFormat: "openai", PathPrefix: "/v1"},
	"mistral":     {BaseURL: "https://api.mistral.ai", APIFormat: "openai", PathPrefix: "/v1"},
	"together":    {BaseURL: "https://api.together.xyz", APIFormat: "openai", PathPrefix: "/v1"},
	"fireworks":   {BaseURL: "https://api.fireworks.ai/inference", APIFormat: "openai", PathPrefix: "/v1"},
	"deepinfra":   {BaseURL: "https://api.deepinfra.com", APIFormat: "openai", PathPrefix: "/v1"},
	"perplexity":  {BaseURL: "https://api.perplexity.ai", APIFormat: "openai", PathPrefix: "/v1"},
	"cerebras":    {BaseURL: "https://api.cerebras.ai", APIFormat: "openai", PathPrefix: "/v1"},
	"xai":         {BaseURL: "https://api.x.ai", APIFormat: "openai", PathPrefix: "/v1"},
	"huggingface": {BaseURL: "https://api-inference.huggingface.co", APIFormat: "openai", PathPrefix: "/v1"},
	"anyscale":    {BaseURL: "https://api.endpoints.anyscale.com", APIFormat: "openai", PathPrefix: "/v1"},
	"replicate":   {BaseURL: "https://api.replicate.com", APIFormat: "openai", PathPrefix: "/v1"},
	"openrouter":  {BaseURL: "https://openrouter.ai/api", APIFormat: "openai", PathPrefix: "/v1"},
	"azure":       {APIFormat: "azure"},
}

// applyProviderPreset fills in default BaseURL and APIFormat from known presets.
// Explicit config always takes precedence.
func applyProviderPreset(cfg *config.ProviderConfig) {
	if cfg.Type == "" {
		return
	}
	preset, ok := KnownProviders[cfg.Type]
	if !ok {
		return
	}
	if cfg.BaseURL == "" && preset.BaseURL != "" {
		cfg.BaseURL = preset.BaseURL
	}
	if cfg.APIFormat == "" && preset.APIFormat != "" {
		cfg.APIFormat = preset.APIFormat
	}
	if cfg.APIPathPrefix == nil && preset.APIFormat == "openai" {
		prefix := preset.PathPrefix
		cfg.APIPathPrefix = &prefix
	}
}
