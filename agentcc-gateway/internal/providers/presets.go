package providers

import "github.com/futureagi/agentcc-gateway/internal/config"

// ProviderPreset contains known defaults for a provider type.
type ProviderPreset struct {
	BaseURL   string
	APIFormat string
}

// KnownProviders maps provider type names to their default configurations.
var KnownProviders = map[string]ProviderPreset{
	// Core providers with native API formats.
	"openai":    {BaseURL: "https://api.openai.com/v1", APIFormat: "openai"},
	"anthropic": {BaseURL: "https://api.anthropic.com", APIFormat: "anthropic"},
	"gemini":    {BaseURL: "https://generativelanguage.googleapis.com", APIFormat: "gemini"},
	"cohere":    {BaseURL: "https://api.cohere.ai/compatibility/v1", APIFormat: "cohere"},

	// OpenAI-compatible providers.
	// Note: BaseURLs omit the /v1 suffix — the gateway appends it when
	// constructing the OpenAI-compatible endpoint.  The frontend presets
	// include /v1 because the browser calls the provider directly (fetch-models).
	"groq":        {BaseURL: "https://api.groq.com/openai/v1", APIFormat: "openai"},
	"mistral":     {BaseURL: "https://api.mistral.ai/v1", APIFormat: "openai"},
	"together":    {BaseURL: "https://api.together.xyz/v1", APIFormat: "openai"},
	"deepseek":    {BaseURL: "https://api.deepseek.com/v1", APIFormat: "openai"},
	"perplexity":  {BaseURL: "https://api.perplexity.ai", APIFormat: "openai"},
	"fireworks":   {BaseURL: "https://api.fireworks.ai/inference/v1", APIFormat: "openai"},
	"deepinfra":   {BaseURL: "https://api.deepinfra.com/v1", APIFormat: "openai"},
	"cerebras":    {BaseURL: "https://api.cerebras.ai/v1", APIFormat: "openai"},
	"xai":         {BaseURL: "https://api.x.ai/v1", APIFormat: "openai"},
	"huggingface": {BaseURL: "https://api-inference.huggingface.co", APIFormat: "openai"},
	"anyscale":    {BaseURL: "https://api.endpoints.anyscale.com/v1", APIFormat: "openai"},
	"replicate":   {BaseURL: "https://api.replicate.com", APIFormat: "openai"},
	"openrouter":  {BaseURL: "https://openrouter.ai/api/v1", APIFormat: "openai"},

	// Providers requiring user-supplied base URL or credentials.
	"azure": {APIFormat: "azure"},
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
