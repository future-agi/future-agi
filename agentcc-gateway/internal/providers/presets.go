package providers

import "github.com/futureagi/agentcc-gateway/internal/config"

// ProviderPreset contains known defaults for a provider type.
type ProviderPreset struct {
	BaseURL   string
	APIFormat string
}

// KnownProviders maps provider type names to their default configurations.
var KnownProviders = map[string]ProviderPreset{
	"groq":        {BaseURL: "https://api.groq.com/openai", APIFormat: "openai"},
	"mistral":     {BaseURL: "https://api.mistral.ai", APIFormat: "openai"},
	"together":    {BaseURL: "https://api.together.xyz", APIFormat: "openai"},
	"fireworks":   {BaseURL: "https://api.fireworks.ai/inference", APIFormat: "openai"},
	"deepinfra":   {BaseURL: "https://api.deepinfra.com", APIFormat: "openai"},
	"perplexity":  {BaseURL: "https://api.perplexity.ai", APIFormat: "openai"},
	"cerebras":    {BaseURL: "https://api.cerebras.ai", APIFormat: "openai"},
	"xai":         {BaseURL: "https://api.x.ai", APIFormat: "openai"},
	"huggingface": {BaseURL: "https://api-inference.huggingface.co", APIFormat: "openai"},
	"anyscale":    {BaseURL: "https://api.endpoints.anyscale.com", APIFormat: "openai"},
	"replicate":   {BaseURL: "https://api.replicate.com", APIFormat: "openai"},
	"openrouter":  {BaseURL: "https://openrouter.ai/api", APIFormat: "openai"},
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
}
