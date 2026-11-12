package reqopts

import (
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/config"
)

// SlugMatches reports whether name, an entry of provider.only, ignore or
// order, names an endpoint served by upstream with the given quantization.
// Names are upstream provider slugs in OpenRouter's naming ("anthropic",
// "amazon-bedrock", "deepinfra/fp8"), matched case-insensitively; an
// optional "/<quantization>" must match the endpoint's quantization too.
// They are never gateway provider ids: a caller picks a gateway provider
// through the model prefix or x-agentcc-provider.
func SlugMatches(name, upstream, quantization string) bool {
	slug, quant, hasQuant := strings.Cut(strings.TrimSpace(name), "/")
	if !strings.EqualFold(slug, upstream) {
		return false
	}
	return !hasQuant || strings.EqualFold(quant, quantization)
}

// UpstreamSlug is the slug a BYOK direct provider's endpoints match on: its
// providers.<id>.upstream_slug, or else the default for its preset.
func UpstreamSlug(cfg config.ProviderConfig) string {
	if cfg.UpstreamSlug != "" {
		return cfg.UpstreamSlug
	}
	return DefaultUpstreamSlug(cfg)
}

// DefaultUpstreamSlug is the OpenRouter-naming slug of a direct provider,
// from its type or API format: openai, anthropic, amazon-bedrock,
// google-vertex, google-ai-studio or azure. Other OpenAI-format providers use
// their type (deepinfra, groq, ...) and, without one, "openai".
func DefaultUpstreamSlug(cfg config.ProviderConfig) string {
	switch cfg.APIFormat {
	case "anthropic":
		return "anthropic"
	case "bedrock":
		return "amazon-bedrock"
	case "azure":
		return "azure"
	case "gemini", "google":
		if isVertexHost(cfg.BaseURL) {
			return "google-vertex"
		}
		return "google-ai-studio"
	}
	if cfg.Type != "" && cfg.Type != "custom" {
		return strings.ToLower(cfg.Type)
	}
	return "openai"
}

// isVertexHost is the gemini provider's own test for a Vertex AI base URL
// (providers/gemini isVertexAI), so the slug agrees with how it is called.
func isVertexHost(baseURL string) bool {
	return strings.Contains(baseURL, "aiplatform.googleapis.com")
}
