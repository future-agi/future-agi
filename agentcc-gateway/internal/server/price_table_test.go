package server

import (
	"bytes"
	"math"
	"net/http"
	"net/http/httptest"
	"strconv"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	costplugin "github.com/futureagi/agentcc-gateway/internal/plugins/cost"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// The name comes from the provider that serves the request, whatever its ID:
// the org's own enabled provider first, else the config.yaml provider. The
// org's name comes from its config, so it is the same before and after the
// org's first request builds its provider.
func TestPriceTableNameComesFromTheProvider(t *testing.T) {
	cfg := config.DefaultConfig()
	cfg.Providers["vertex_ai_global"] = config.ProviderConfig{BaseURL: "https://aiplatform.googleapis.com/v1/projects/p/locations/global", APIFormat: "gemini"}
	cfg.Providers["google"] = config.ProviderConfig{BaseURL: "https://generativelanguage.googleapis.com", APIFormat: "gemini"}
	cfg.Providers["openai"] = config.ProviderConfig{BaseURL: "https://api.openai.com/v1", APIFormat: "openai"}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatalf("creating registry: %v", err)
	}
	// IP literals pass the base URL check without a lookup; nothing is sent.
	teamGemini := &tenant.ProviderConfig{APIKey: "org-key", Enabled: true, BaseURL: "https://8.8.8.8", APIFormat: "gemini"}
	store := tenant.NewStore()
	store.Set("org-1", &tenant.OrgConfig{Providers: map[string]*tenant.ProviderConfig{
		"team-gemini": teamGemini,
		"team-vertex": {APIKey: "org-key", Enabled: true, BaseURL: "https://us-central1-aiplatform.googleapis.com", APIFormat: "gemini"},
		// Layered onto config.yaml's Vertex upstream: the org states no base_url.
		"vertex_ai_global": {APIKey: "org-key", Enabled: true},
		"team-openai":      {APIKey: "org-key", Enabled: true, BaseURL: "https://8.8.4.4", APIFormat: "openai"},
		// Disabled: requests go to config.yaml's provider of that ID.
		"google": {APIKey: "org-key", Enabled: false, BaseURL: "https://us-central1-aiplatform.googleapis.com", APIFormat: "gemini"},
	}})
	srv := New(cfg, "", registry, pipeline.NewEngine(), nil, nil, nil, nil, testModelDBPtr(), store, nil)

	cases := []struct{ orgID, providerID, want string }{
		{"", "vertex_ai_global", "vertex_ai"},
		{"", "google", "gemini"},
		{"", "openai", ""},
		{"org-1", "team-gemini", "gemini"},
		{"org-1", "team-vertex", "vertex_ai"},
		{"org-1", "vertex_ai_global", "vertex_ai"},
		{"org-1", "team-openai", ""},
		{"org-1", "google", "gemini"},
		{"org-2", "team-gemini", ""},
		{"", "missing", ""},
	}
	check := func(when string) {
		t.Helper()
		for _, tc := range cases {
			if got := srv.PriceTableName(tc.orgID, tc.providerID); got != tc.want {
				t.Errorf("%s: PriceTableName(%q, %q) = %q, want %q", when, tc.orgID, tc.providerID, got, tc.want)
			}
		}
	}
	check("before the org's first request")
	// What the org's first request through team-gemini does.
	if _, err := srv.OrgProviderCache.GetOrCreateWithTenantConfig("org-1", "team-gemini", teamGemini.APIKey, teamGemini); err != nil {
		t.Fatalf("creating org provider: %v", err)
	}
	check("after the org's first request")
}

// The table lists gemini-4-argon only as "gemini/…" and "vertex_ai/…". A
// Gemini provider named "google" prices it, through the whole handler chain.
func TestChatCostOnAGeminiProviderOfAnyName(t *testing.T) {
	db := testModelDBPtr()
	if _, ok := db.Load().Get("gemini-4-argon"); ok {
		t.Fatal("the table now lists gemini-4-argon bare; pick a model it lists only under a provider")
	}
	entry, ok := db.Load().Get("gemini/gemini-4-argon")
	if !ok {
		t.Fatal("the table no longer lists gemini/gemini-4-argon")
	}
	want := 1000*entry.Pricing.InputPerToken + 100*entry.Pricing.OutputPerToken

	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/v1beta/models/gemini-4-argon:generateContent" {
			http.Error(w, "unexpected path "+r.URL.Path, http.StatusNotFound)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"candidates":[{"content":{"role":"model","parts":[{"text":"hi"}]},"finishReason":"STOP"}],"usageMetadata":{"promptTokenCount":1000,"candidatesTokenCount":100,"totalTokenCount":1100}}`))
	}))
	defer upstream.Close()

	cfg := config.DefaultConfig()
	cfg.Auth.Enabled = true
	cfg.Auth.Keys = []config.AuthKeyConfig{{Name: "price-table", Key: "sk-agentcc-price-table", Owner: "test-org", KeyType: "internal"}}
	cfg.Providers["google"] = config.ProviderConfig{BaseURL: upstream.URL, APIKey: "test-key", APIFormat: "gemini", Models: []string{"gemini-4-argon"}}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatalf("creating registry: %v", err)
	}
	cost := costplugin.New(true, db.Load, nil, nil)
	srv := New(cfg, "", registry, pipeline.NewEngine(cost), nil, nil, nil, nil, db, nil, nil)
	cost.SetPriceTables(srv.PriceTableName)
	srv.ready.Store(true)

	req := httptest.NewRequest(http.MethodPost, "/v1/chat/completions", bytes.NewBufferString(`{"model":"gemini-4-argon","messages":[{"role":"user","content":"hi"}]}`))
	req.Header.Set("Authorization", "Bearer sk-agentcc-price-table")
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(w, req)
	if w.Code != http.StatusOK {
		t.Fatalf("status %d: %s", w.Code, w.Body.String())
	}
	got, err := strconv.ParseFloat(strings.TrimSpace(w.Header().Get("x-agentcc-cost")), 64)
	if err != nil || math.Abs(got-want) > 1e-6 {
		t.Fatalf("x-agentcc-cost = %q, want %.6f", w.Header().Get("x-agentcc-cost"), want)
	}
}
