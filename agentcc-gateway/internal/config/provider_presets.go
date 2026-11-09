package config

// ProviderPreset contains known defaults for a config.yaml provider type.
// It lives in config so validation and registry construction use the same table
// without making config depend on providers.
type ProviderPreset struct {
	BaseURL   string
	APIFormat string
	// PathPrefix is explicit on every OpenAI row: an omitted value would
	// silently strip the version segment from that provider's requests.
	PathPrefix string
}

// KnownProviders maps exact type names to their existing endpoint defaults.
// Azure's resource-specific base_url cannot be inferred and remains required.
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
	"azure":       {APIFormat: "azure"},
}

// applyProviderPresets resolves every provider's preset defaults in place, the
// first step of Validate. The result persists for all config consumers
// (including the per-org provider cache). Explicit fields always win, so an
// operator's own base_url, skip_tls or local setting is what the hosted and
// required-field checks see.
func (c *Config) applyProviderPresets() {
	for name, p := range c.Providers {
		ApplyProviderPreset(&p)
		c.Providers[name] = p
	}
}

// ApplyProviderPreset fills empty base_url and api_format and an unset
// api_path_prefix. Explicit values (including an empty path prefix) always win.
// Unknown or empty types are left alone; no provider is selected by its map ID.
func ApplyProviderPreset(cfg *ProviderConfig) {
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
