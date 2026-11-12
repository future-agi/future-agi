package server

import (
	"bytes"
	"context"
	"encoding/json"
	"math"
	"net/http"
	"net/http/httptest"
	"strconv"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	costplugin "github.com/futureagi/agentcc-gateway/internal/plugins/cost"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// Usage as the upstreams report it: OpenAI's documented shape (written by
// hand), Microsoft's documented Azure example, and OpenRouter's.
const (
	cachedOpenAIUsage = `{"prompt_tokens":2006,"completion_tokens":300,"total_tokens":2306,"prompt_tokens_details":{"cached_tokens":1920,"audio_tokens":0},"completion_tokens_details":{"reasoning_tokens":0}}`
	cachedAzureUsage  = `{"prompt_tokens":1566,"completion_tokens":1518,"total_tokens":3084,"prompt_tokens_details":{"audio_tokens":null,"cached_tokens":1408,"cache_write_tokens":0},"completion_tokens_details":{"audio_tokens":null,"reasoning_tokens":576}}`
	cachedRouterUsage = `{"prompt_tokens":10339,"completion_tokens":60,"total_tokens":10399,"prompt_tokens_details":{"cached_tokens":10318,"cache_write_tokens":0}}`

	cachedGeminiUsage = `{"promptTokenCount":1000,"cachedContentTokenCount":750,"candidatesTokenCount":200,"totalTokenCount":1200}`
)

func chatBodyWithUsage(usage string) string {
	return `{"id":"chatcmpl-cached","object":"chat.completion","created":1761580000,"model":"gpt-4o","choices":[{"index":0,"message":{"role":"assistant","content":"ok"},"finish_reason":"stop"}],"usage":` + usage + `}`
}

func cachedProvidersServer(t *testing.T) *Server {
	t.Helper()
	cfg := config.DefaultConfig()
	// Model discovery is off, so no base URL is called for a model list.
	off := false
	cfg.Providers["openai"] = config.ProviderConfig{BaseURL: "https://api.openai.com/v1", APIFormat: "openai", AutoDiscover: &off}
	cfg.Providers["proxy"] = config.ProviderConfig{BaseURL: "https://8.8.8.8", APIFormat: "openai", AutoDiscover: &off}
	cfg.Providers["groq"] = config.ProviderConfig{BaseURL: "https://8.8.8.8", APIFormat: "openai", Type: "groq", AutoDiscover: &off}
	cfg.Providers["openrouter"] = config.ProviderConfig{BaseURL: "https://8.8.8.8", APIFormat: "openai", Type: "openrouter", AutoDiscover: &off}
	cfg.Providers["di"] = config.ProviderConfig{BaseURL: "https://8.8.8.8", APIFormat: "openai", Type: "deepinfra", AutoDiscover: &off}
	cfg.Providers["azure"] = config.ProviderConfig{BaseURL: "https://8.8.8.8", APIFormat: "azure", AutoDiscover: &off}
	cfg.Providers["google"] = config.ProviderConfig{BaseURL: "https://generativelanguage.googleapis.com", APIFormat: "gemini", AutoDiscover: &off}
	cfg.Providers["vertex"] = config.ProviderConfig{BaseURL: "https://us-central1-aiplatform.googleapis.com/v1/projects/p/locations/us-central1", APIFormat: "gemini", AutoDiscover: &off}
	cfg.Providers["claude"] = config.ProviderConfig{BaseURL: "https://8.8.8.8", APIFormat: "anthropic", AutoDiscover: &off}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatalf("creating registry: %v", err)
	}
	srv := New(cfg, "", registry, pipeline.NewEngine(), nil, nil, nil, nil, testModelDBPtr(), tenant.NewStore(), nil)

	// The IP literal passes the base URL check without a lookup. Org instances
	// are not probed for models, so nothing is sent to it.
	for _, inst := range []struct{ org, id, format string }{
		{"org-1", "team-azure", "azure"},
		{"org-2", "openai", "openai"},
	} {
		orgCfg := &tenant.ProviderConfig{APIKey: "org-key", Enabled: true, BaseURL: "https://8.8.8.8", APIFormat: inst.format}
		if _, err := srv.OrgProviderCache.GetOrCreateWithTenantConfig(inst.org, inst.id, orgCfg.APIKey, orgCfg); err != nil {
			t.Fatalf("creating org provider %s/%s: %v", inst.org, inst.id, err)
		}
	}
	return srv
}

func TestCachedTokensInPromptComesFromTheProvider(t *testing.T) {
	srv := cachedProvidersServer(t)

	for _, tc := range []struct {
		orgID, providerID string
		want              bool
	}{
		{"", "openai", true},
		{"", "azure", true},
		{"", "google", true},
		{"", "vertex", true},
		{"org-1", "team-azure", true},
		{"org-1", "openai", true},
		{"", "proxy", false},
		{"", "groq", false},
		{"", "openrouter", false},
		{"", "di", false},
		{"", "claude", false},
		// The org's own instance answers before config.yaml's.
		{"org-2", "openai", false},
		{"", "missing", false},
	} {
		if got := srv.CachedTokensInPrompt(tc.orgID, tc.providerID); got != tc.want {
			t.Errorf("CachedTokensInPrompt(%q, %q) = %v, want %v", tc.orgID, tc.providerID, got, tc.want)
		}
	}
}

func TestCachedPriceByServingProvider(t *testing.T) {
	srv := cachedProvidersServer(t)
	db := testModelDBPtr()
	cost := costplugin.New(true, db.Load, nil, nil)
	cost.SetPriceTables(srv.PriceTableName)
	cost.SetProviderTypes(srv.ProviderType)
	cost.SetCachedTokensInPrompt(srv.CachedTokensInPrompt)

	priced := func(t *testing.T, provider, model, usage string) string {
		t.Helper()
		var resp models.ChatCompletionResponse
		if err := json.Unmarshal([]byte(chatBodyWithUsage(usage)), &resp); err != nil {
			t.Fatalf("decoding the response: %v", err)
		}
		rc := &models.RequestContext{
			EndpointType: "chat",
			Model:        model,
			Provider:     provider,
			Response:     &resp,
			Metadata:     map[string]string{},
		}
		if res := cost.ProcessResponse(context.Background(), rc); res.Error != nil {
			t.Fatalf("cost step failed: %v", res.Error)
		}
		return rc.Metadata["cost"]
	}

	for _, tc := range []struct {
		name, provider, model, usage, want string
	}{
		{"openai", "openai", "gpt-4o", cachedOpenAIUsage, "0.005615"},
		{"groq", "groq", "gpt-4o", cachedOpenAIUsage, "0.008015"},
		{"proxy", "proxy", "gpt-4o", cachedOpenAIUsage, "0.008015"},
		{"di", "di", "gpt-4o", cachedOpenAIUsage, "0.008015"},
		// OpenRouter is out: its documented usage does not say that the
		// prompt count includes the cached tokens, so it is billed in full.
		{"openrouter", "openrouter", "gpt-5.6", cachedRouterUsage, "0.042556"},
		{"azure", "azure", "gpt-4o", cachedAzureUsage, "0.017335"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			if got := priced(t, tc.provider, tc.model, tc.usage); got != tc.want {
				t.Errorf("cost = %q, want %q", got, tc.want)
			}
		})
	}
}

// cachedCostServer serves one provider from an upstream the test controls,
// with the cost plugin wired as main wires it.
func cachedCostServer(t *testing.T, id string, pc config.ProviderConfig) *Server {
	t.Helper()
	db := testModelDBPtr()
	cfg := config.DefaultConfig()
	cfg.Auth.Enabled = true
	cfg.Auth.Keys = []config.AuthKeyConfig{{Name: "cached-cost", Key: "sk-agentcc-cached-cost", Owner: "test-org", KeyType: "internal"}}
	pc.APIKey = "test-key"
	cfg.Providers[id] = pc
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatalf("creating registry: %v", err)
	}
	cost := costplugin.New(true, db.Load, nil, nil)
	srv := New(cfg, "", registry, pipeline.NewEngine(cost), nil, nil, nil, nil, db, nil, nil)
	cost.SetPriceTables(srv.PriceTableName)
	cost.SetProviderTypes(srv.ProviderType)
	cost.SetCachedTokensInPrompt(srv.CachedTokensInPrompt)
	srv.ready.Store(true)
	return srv
}

func postCachedChat(srv *Server, body string, headers map[string]string) *httptest.ResponseRecorder {
	req := httptest.NewRequest(http.MethodPost, "/v1/chat/completions", bytes.NewBufferString(body))
	req.Header.Set("Authorization", "Bearer sk-agentcc-cached-cost")
	req.Header.Set("Content-Type", "application/json")
	for k, v := range headers {
		req.Header.Set(k, v)
	}
	w := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(w, req)
	return w
}

func TestCachedPriceThroughAzureChat(t *testing.T) {
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/openai/deployments/gpt-4o/chat/completions" {
			http.Error(w, "unexpected path "+r.URL.Path, http.StatusNotFound)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(chatBodyWithUsage(cachedAzureUsage)))
	}))
	defer upstream.Close()

	srv := cachedCostServer(t, "azure", config.ProviderConfig{BaseURL: upstream.URL, APIFormat: "azure", Models: []string{"gpt-4o"}})
	w := postCachedChat(srv, `{"model":"gpt-4o","messages":[{"role":"user","content":"hi"}]}`, nil)
	if w.Code != http.StatusOK {
		t.Fatalf("status %d: %s", w.Code, w.Body.String())
	}
	if got := strings.TrimSpace(w.Header().Get("x-agentcc-cost")); got != "0.017335" {
		t.Fatalf("x-agentcc-cost = %q, want 0.017335", got)
	}
}

func geminiCachedUpstream(t *testing.T) *httptest.Server {
	t.Helper()
	return httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/v1beta/models/gemini-4-argon:generateContent":
			w.Header().Set("Content-Type", "application/json")
			_, _ = w.Write([]byte(`{"candidates":[{"content":{"role":"model","parts":[{"text":"hi"}]},"finishReason":"STOP"}],"usageMetadata":` + cachedGeminiUsage + `}`))
		case "/v1beta/models/gemini-4-argon:streamGenerateContent":
			w.Header().Set("Content-Type", "text/event-stream")
			_, _ = w.Write([]byte("data: {\"candidates\":[{\"content\":{\"role\":\"model\",\"parts\":[{\"text\":\"hi\"}]}}]}\n\n"))
			_, _ = w.Write([]byte("data: {\"candidates\":[{\"content\":{\"role\":\"model\",\"parts\":[{\"text\":\"!\"}]},\"finishReason\":\"STOP\"}],\"usageMetadata\":" + cachedGeminiUsage + "}\n\n"))
		default:
			http.Error(w, "unexpected path "+r.URL.Path, http.StatusNotFound)
		}
	}))
}

func TestCachedPriceThroughGeminiChat(t *testing.T) {
	upstream := geminiCachedUpstream(t)
	defer upstream.Close()

	srv := cachedCostServer(t, "google", config.ProviderConfig{BaseURL: upstream.URL, APIFormat: "gemini", Models: []string{"gemini-4-argon"}})
	w := postCachedChat(srv, `{"model":"gemini-4-argon","messages":[{"role":"user","content":"hi"}]}`, nil)
	if w.Code != http.StatusOK {
		t.Fatalf("status %d: %s", w.Code, w.Body.String())
	}
	got, err := strconv.ParseFloat(strings.TrimSpace(w.Header().Get("x-agentcc-cost")), 64)
	if err != nil || math.Abs(got-0.002575) > 1e-6 {
		t.Fatalf("x-agentcc-cost = %q, want 0.002575", w.Header().Get("x-agentcc-cost"))
	}
}

func TestCachedPriceThroughGeminiStream(t *testing.T) {
	upstream := geminiCachedUpstream(t)
	defer upstream.Close()

	srv := cachedCostServer(t, "google", config.ProviderConfig{BaseURL: upstream.URL, APIFormat: "gemini", Models: []string{"gemini-4-argon"}})
	w := postCachedChat(srv, `{"model":"gemini-4-argon","messages":[{"role":"user","content":"hi"}],"stream":true}`,
		map[string]string{"x-agentcc-include-metadata": "1"})
	if w.Code != http.StatusOK {
		t.Fatalf("status %d: %s", w.Code, w.Body.String())
	}

	var dataLines []string
	for _, line := range strings.Split(w.Body.String(), "\n") {
		if strings.HasPrefix(line, "data: ") {
			dataLines = append(dataLines, strings.TrimPrefix(line, "data: "))
		}
	}
	if len(dataLines) < 3 || dataLines[len(dataLines)-1] != "[DONE]" {
		t.Fatalf("unexpected stream: %s", w.Body.String())
	}
	var final models.StreamChunk
	if err := json.Unmarshal([]byte(dataLines[len(dataLines)-2]), &final); err != nil {
		t.Fatalf("parsing the final metadata chunk: %v", err)
	}
	if final.AgentccMetadata == nil {
		t.Fatalf("no agentcc metadata in the final chunk: %s", dataLines[len(dataLines)-2])
	}
	if got := final.AgentccMetadata.Cost; math.Abs(got-0.002575) > 5e-7 || math.Abs(got-0.004) <= 5e-7 {
		t.Fatalf("streamed cost = %v, want 0.002575 (not the uncached 0.004)", got)
	}
}
