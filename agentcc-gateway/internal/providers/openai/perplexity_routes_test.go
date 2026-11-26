package openai

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"sync/atomic"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

func TestPerplexityAgentModel(t *testing.T) {
	agent := []string{"perplexity/glm-5.3", "perplexity/glm-5.2", "openai/gpt-5.5", "anthropic/claude-sonnet-5", "google/gemini-3.6-flash", "preset/pro-search", "perplexity/kimi-k3"}
	chat := []string{"sonar", "sonar-pro", "sonar-reasoning-pro", "sonar-deep-research", "r1-1776", "glm-5.3", "perplexity/sonar", "perplexity/sonar-pro", "perplexity/llama-3.1-sonar-large-128k-online", "perplexity/r1-1776", "/x", "x/"}
	for _, m := range agent {
		if !perplexityAgentModel(m) {
			t.Errorf("perplexityAgentModel(%q) = false, want true", m)
		}
	}
	for _, m := range chat {
		if perplexityAgentModel(m) {
			t.Errorf("perplexityAgentModel(%q) = true, want false", m)
		}
	}
}

// perplexityProvider is an OpenAI-format provider pointed at a server that
// counts its calls and answers every one with a bare 404.
func perplexityProvider(t *testing.T, cfg config.ProviderConfig) (*Provider, *atomic.Int32) {
	t.Helper()
	var calls atomic.Int32
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
		io.Copy(io.Discard, r.Body)
		w.WriteHeader(http.StatusNotFound)
		io.WriteString(w, "Not Found")
	}))
	t.Cleanup(ts.Close)
	cfg.BaseURL = ts.URL
	cfg.APIKey = "test-key"
	p, err := New("perplexity", cfg)
	if err != nil {
		t.Fatalf("New: %v", err)
	}
	return p, &calls
}

func chatReq(model string) *models.ChatCompletionRequest {
	return &models.ChatCompletionRequest{Model: model, Messages: []models.Message{{Role: "user", Content: json.RawMessage(`"Hi"`)}}}
}

func wantAPIError(t *testing.T, err error, status int, code string) *models.APIError {
	t.Helper()
	var apiErr *models.APIError
	if !errors.As(err, &apiErr) {
		t.Fatalf("error = %v, want an APIError", err)
	}
	if apiErr.Status != status || apiErr.Code != code {
		t.Fatalf("error = %d %s (%s), want %d %s", apiErr.Status, apiErr.Code, apiErr.Message, status, code)
	}
	return apiErr
}

func TestPerplexityChatRefusesAgentModelWithoutCalling(t *testing.T) {
	p, calls := perplexityProvider(t, config.ProviderConfig{Type: "perplexity"})

	_, err := p.ChatCompletion(context.Background(), chatReq("perplexity/glm-5.3"))
	wantAPIError(t, err, http.StatusBadRequest, "route_not_supported")

	chunks, errs := p.StreamChatCompletion(context.Background(), chatReq("perplexity/glm-5.3"))
	for range chunks {
		t.Error("stream sent a chunk")
	}
	wantAPIError(t, <-errs, http.StatusBadRequest, "route_not_supported")

	if n := calls.Load(); n != 0 {
		t.Errorf("upstream calls = %d, want 0", n)
	}
}

// A provider of no stated type is Perplexity when its base_url is
// Perplexity's host, as a dashboard provider is.
func TestPerplexityRecognisedByHost(t *testing.T) {
	p, err := New("my-pplx", config.ProviderConfig{BaseURL: "https://api.perplexity.ai", APIKey: "k"})
	if err != nil {
		t.Fatalf("New: %v", err)
	}
	if p.UnsupportedChatRoute(chatReq("perplexity/glm-5.3")) == nil {
		t.Error("a provider on api.perplexity.ai should refuse an Agent API model on chat")
	}
}

// Other OpenAI-format upstreams keep vendor-prefixed names on chat completions
// (OpenRouter's "openai/gpt-5.5", Groq's "openai/gpt-oss-120b"), and their
// 404s are unchanged.
func TestNonPerplexityKeepsPrefixedModelsAndItsErrors(t *testing.T) {
	p, calls := perplexityProvider(t, config.ProviderConfig{Type: "openrouter"})
	_, err := p.ChatCompletion(context.Background(), chatReq("perplexity/glm-5.3"))
	wantAPIError(t, err, http.StatusBadGateway, "provider_404")
	if n := calls.Load(); n != 1 {
		t.Errorf("upstream calls = %d, want 1", n)
	}
}

// Perplexity's bare 404 for a model it does not serve on chat completions is
// a 400 naming the route, not a 502.
func TestPerplexityChat404IsRouteNotSupported(t *testing.T) {
	p, calls := perplexityProvider(t, config.ProviderConfig{Type: "perplexity"})

	_, err := p.ChatCompletion(context.Background(), chatReq("glm-5.3"))
	apiErr := wantAPIError(t, err, http.StatusBadRequest, "route_not_supported_upstream")
	if apiErr.UpstreamStatus != http.StatusNotFound {
		t.Errorf("UpstreamStatus = %d, want 404", apiErr.UpstreamStatus)
	}

	chunks, errs := p.StreamChatCompletion(context.Background(), chatReq("glm-5.3"))
	for range chunks {
		t.Error("stream sent a chunk")
	}
	wantAPIError(t, <-errs, http.StatusBadRequest, "route_not_supported_upstream")

	if n := calls.Load(); n != 2 {
		t.Errorf("upstream calls = %d, want 2", n)
	}
}
