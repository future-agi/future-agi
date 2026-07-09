package providers

import "github.com/futureagi/agentcc-gateway/internal/config"

// ProviderPreset contains known defaults for a provider type.
type ProviderPreset struct {
	BaseURL   string
	APIFormat string
}

// KnownProviders maps provider type names to their default configurations.
//
// BaseURLs for openai-format providers MUST NOT include the /v1 suffix.
// The gateway's OpenAI provider (internal/providers/openai/openai.go)
// hardcodes /v1/chat/completions, /v1/models, etc. onto the configured
// BaseURL — including /v1 in the preset would produce double-prefixed URLs
// like /v1/v1/chat/completions.
//
// Frontend presets include /v1 because the backend's fetch_models endpoint
// (provider_credential.py) concatenates "{base_url}/models" without adding a
// version prefix.
var KnownProviders = map[string]ProviderPreset{
	// Core providers with native API formats.
	"openai":    {BaseURL: "https://api.openai.com", APIFormat: "openai"},
	"anthropic": {BaseURL: "https://api.anthropic.com", APIFormat: "anthropic"},
	"gemini":    {BaseURL: "https://generativelanguage.googleapis.com", APIFormat: "gemini"},
	"cohere":    {BaseURL: "https://api.cohere.ai/compatibility/v1", APIFormat: "cohere"},

	// OpenAI-compatible providers.
	"groq":        {BaseURL: "https://api.groq.com/openai", APIFormat: "openai"},
	"mistral":     {BaseURL: "https://api.mistral.ai", APIFormat: "openai"},
	"together":    {BaseURL: "https://api.together.xyz", APIFormat: "openai"},
	"deepseek":    {BaseURL: "https://api.deepseek.com", APIFormat: "openai"},
	"perplexity":  {BaseURL: "https://api.perplexity.ai", APIFormat: "openai"},
	"fireworks":   {BaseURL: "https://api.fireworks.ai/inference", APIFormat: "openai"},
	"deepinfra":   {BaseURL: "https://api.deepinfra.com", APIFormat: "openai"},
	"cerebras":    {BaseURL: "https://api.cerebras.ai", APIFormat: "openai"},
	"xai":         {BaseURL: "https://api.x.ai", APIFormat: "openai"},
	"huggingface": {BaseURL: "https://api-inference.huggingface.co", APIFormat: "openai"},
	"anyscale":    {BaseURL: "https://api.endpoints.anyscale.com", APIFormat: "openai"},
	"replicate":   {BaseURL: "https://api.replicate.com", APIFormat: "openai"},
	"openrouter":  {BaseURL: "https://openrouter.ai/api", APIFormat: "openai"},

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
