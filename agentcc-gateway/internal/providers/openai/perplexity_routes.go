package openai

import (
	"fmt"
	"net/http"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// Perplexity serves two APIs from one host. Its Sonar models ("sonar",
// "sonar-pro", ...) answer /v1/chat/completions. Its Agent API models, named
// "<vendor>/<model>" ("openai/gpt-5.5", "perplexity/glm-5.3") or
// "preset/<name>", answer only /v1/responses: on chat completions Perplexity
// returns 404, which reads as a provider outage. The price table agrees: every
// Perplexity entry in Responses mode has a slash in its name, and no chat
// entry does.
//
// So a chat request for an Agent API model is refused before any upstream
// call, with a 400 that names the route to use. /v1/messages and the other
// routes that turn their request into a chat request reach the same check,
// since it runs in ChatCompletion and StreamChatCompletion.

// perplexityAPIHost is the host of Perplexity's API.
const perplexityAPIHost = "api.perplexity.ai"

// isPerplexity reports whether p sends its requests to Perplexity: it is
// configured as type "perplexity", or its base_url is Perplexity's host (a
// dashboard provider records no type).
func (p *Provider) isPerplexity() bool {
	return strings.EqualFold(p.configType, "perplexity") || p.baseURLHost == perplexityAPIHost
}

// perplexityAgentModel reports whether model names one of Perplexity's Agent
// API models. A Sonar model with the "perplexity/" prefix ("perplexity/sonar-pro")
// is sent upstream as written, as it was before this check, and Perplexity
// answers it.
func perplexityAgentModel(model string) bool {
	vendor, name, ok := strings.Cut(model, "/")
	if !ok || vendor == "" || name == "" {
		return false
	}
	return !strings.EqualFold(vendor, "perplexity") || !sonarModel(name)
}

// sonarModel reports whether name is one of Perplexity's chat completions
// models: the Sonar family ("sonar", "sonar-reasoning-pro",
// "llama-3.1-sonar-large-128k-online") and "r1-1776".
func sonarModel(name string) bool {
	return !strings.Contains(name, "/") && (strings.Contains(name, "sonar") || name == "r1-1776")
}

// UnsupportedChatRoute is the error for req when p does not serve its model
// on chat completions, or nil when it may be sent.
func (p *Provider) UnsupportedChatRoute(req *models.ChatCompletionRequest) *models.APIError {
	if req == nil || !p.isPerplexity() || !perplexityAgentModel(req.Model) {
		return nil
	}
	return models.ErrBadRequest("route_not_supported", fmt.Sprintf(
		"Model %q is a Perplexity Agent API model, served only on the Responses API. Call /v1/responses instead.", req.Model))
}

// chatProviderError is parseProviderError for an upstream's answer to a chat
// completions call for model. Perplexity answers 404, with no OpenAI error
// body, for a model it does not serve on chat completions; that is the
// request's route, not an outage, so it is a 400 rather than a 502.
func (p *Provider) chatProviderError(model string, status int, body []byte) *models.APIError {
	apiErr := parseProviderError(status, body)
	if status != http.StatusNotFound || apiErr.Status != http.StatusBadGateway || !p.isPerplexity() {
		return apiErr
	}
	routeErr := models.ErrBadRequest("route_not_supported_upstream", fmt.Sprintf(
		"Perplexity does not serve model %q on /v1/chat/completions (it answered 404). If it is an Agent API model, call /v1/responses.", model))
	routeErr.UpstreamStatus = status
	return routeErr
}
