package config

import "log/slog"

// ProviderPreset contains known defaults for a provider type.
type ProviderPreset struct {
	BaseURL   string
	APIFormat string

	// An explicit prefix prevents an omitted row from silently stripping /v1.
	PathPrefix string
}

// KnownProviders maps provider type names to their default configurations.
var KnownProviders = map[string]ProviderPreset{
	"groq":        {BaseURL: "https://api.groq.com/openai", APIFormat: "openai", PathPrefix: "/v1"},
	"mistral":     {BaseURL: "https://api.mistral.ai", APIFormat: "openai", PathPrefix: "/v1"},
	"together":    {BaseURL: "https://api.together.xyz", APIFormat: "openai", PathPrefix: "/v1"},
	"fireworks":   {BaseURL: "https://api.fireworks.ai/inference", APIFormat: "openai", PathPrefix: "/v1"},
	"deepinfra":   {BaseURL: "https://api.deepinfra.com", APIFormat: "openai", PathPrefix: "/v1"},
	"perplexity":  {BaseURL: "https://api.perplexity.ai", APIFormat: "openai", PathPrefix: "/v1"},
	"cerebras":    {BaseURL: "https://api.cerebras.ai", APIFormat: "openai", PathPrefix: "/v1"},
	"xai":         {BaseURL: "https://api.x.ai", APIFormat: "openai", PathPrefix: "/v1"},
	"huggingface": {BaseURL: "https://router.huggingface.co", APIFormat: "openai", PathPrefix: "/v1"},
	"anyscale":    {BaseURL: "https://api.endpoints.anyscale.com", APIFormat: "openai", PathPrefix: "/v1"},
	"replicate":   {BaseURL: "https://api.replicate.com", APIFormat: "openai", PathPrefix: "/v1"},
	"openrouter":  {BaseURL: "https://openrouter.ai/api", APIFormat: "openai", PathPrefix: "/v1"},
	"telnyx":      {BaseURL: "https://api.telnyx.com/v2/ai", APIFormat: "openai", PathPrefix: "/openai"},
	"azure":       {APIFormat: "azure"},
}

// ApplyProviderPreset fills in known defaults without replacing explicit config.
// Both config loading and programmatically constructed registries use it.
func ApplyProviderPreset(cfg *ProviderConfig) {
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

// logPresetDefaults reports which required fields a preset filled in, so a
// blank base_url (for example an unset ${VAR}) that now falls back to the
// vendor's public endpoint is visible at startup. It never logs credentials.
func logPresetDefaults(name string, before, after ProviderConfig) {
	var filled []string
	if before.BaseURL == "" && after.BaseURL != "" {
		filled = append(filled, "base_url")
	}
	if before.APIFormat == "" && after.APIFormat != "" {
		filled = append(filled, "api_format")
	}
	if len(filled) == 0 {
		return
	}
	slog.Info("config: provider preset filled defaults",
		"provider", name, "preset", after.Type, "fields", filled)
}
