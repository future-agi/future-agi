package server

import (
	"encoding/json"
	"math"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/modeldb"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// The last instant of 2026 and the first of 2027, both UTC.
var (
	datedT26 = time.Date(2026, 12, 31, 23, 59, 59, 999999999, time.UTC)
	datedT27 = time.Date(2027, 1, 1, 0, 0, 0, 0, time.UTC)
)

// datedGeminiProvider lists gemini-3.7-flash; nothing is ever sent to it.
func datedGeminiProvider() config.ProviderConfig {
	return config.ProviderConfig{BaseURL: "http://127.0.0.1:1", APIKey: "k", APIFormat: "gemini", Models: []string{"gemini-3.7-flash"}}
}

func datedTestServer(t *testing.T) *Server {
	t.Helper()
	cfg := config.DefaultConfig()
	cfg.Providers["gemini"] = datedGeminiProvider()
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatalf("creating registry: %v", err)
	}
	srv := New(cfg, "", registry, pipeline.NewEngine(), nil, nil, nil, nil, testModelDBPtr(), nil, nil)
	srv.ready.Store(true)
	return srv
}

func TestListModelsQuotesTheDatedPrice(t *testing.T) {
	srv := datedTestServer(t)
	for _, tc := range []struct {
		at                   time.Time
		input, output, cache float64
	}{
		{datedT26, 0.75, 3.75, 0.075},
		{datedT27, 1.5, 7.5, 0.15},
	} {
		instant := tc.at
		srv.handlers.now = func() time.Time { return instant }
		w := httptest.NewRecorder()
		srv.httpServer.Handler.ServeHTTP(w, httptest.NewRequest("GET", "/v1/models", nil))
		if w.Code != 200 {
			t.Fatalf("status %d: %s", w.Code, w.Body.String())
		}
		var resp models.EnrichedModelListResponse
		if err := json.Unmarshal(w.Body.Bytes(), &resp); err != nil {
			t.Fatalf("parsing response: %v", err)
		}
		var pricing *models.ModelPricingAPI
		for _, m := range resp.Data {
			if m.ID == "gemini-3.7-flash" {
				pricing = m.Pricing
			}
		}
		if pricing == nil {
			t.Fatalf("at %s: gemini-3.7-flash listed with no price: %s", instant.Format(time.RFC3339), w.Body.String())
		}
		if math.Abs(pricing.InputPerMTokens-tc.input) > 1e-12 || math.Abs(pricing.OutputPerMTokens-tc.output) > 1e-12 ||
			math.Abs(pricing.CachedInputPerMTokens-tc.cache) > 1e-12 {
			t.Errorf("at %s: quoted %+v, want input %g output %g cached %g", instant.Format(time.RFC3339), *pricing, tc.input, tc.output, tc.cache)
		}
	}
}

func TestReloadKeepsTheDatedPrices(t *testing.T) {
	srv := datedTestServer(t)
	old := srv.handlers.ModelDB()

	noTools := false
	cfg := config.DefaultConfig()
	cfg.Providers["gemini"] = datedGeminiProvider()
	cfg.ModelDatabase.Overrides = map[string]config.ModelOverrideConfig{
		"gemini-3.7-flash": {Capabilities: &config.CapOverrideConfig{FunctionCalling: &noTools}},
	}
	if err := srv.handlers.ReloadRouting(cfg, srv.registry); err != nil {
		t.Fatalf("ReloadRouting: %v", err)
	}

	db := srv.handlers.ModelDB()
	if db == old {
		t.Fatal("the reload kept the old model table")
	}
	if m, ok := db.Get("gemini-3.7-flash"); !ok || m.Capabilities.FunctionCalling {
		t.Fatal("the reloaded table does not carry the new override")
	}
	got, ok := db.CalculateCost("gemini-3.7-flash", 1_000_000, 100_000, modeldb.CostOptions{At: datedT27})
	if !ok || math.Abs(got-2.25) > 1e-12 {
		t.Fatalf("after a reload, 1 Jan cost %v, %v; want 2.25", got, ok)
	}
}

// A reseller configured under another ID is known by the type its config records.
func TestProviderTypeComesFromTheProvidersConfig(t *testing.T) {
	cfg := config.DefaultConfig()
	// IP literals pass the base URL check without a lookup; nothing is sent to them.
	cfg.Providers["di"] = config.ProviderConfig{BaseURL: "https://8.8.8.8", APIKey: "k", APIFormat: "openai", Type: "deepinfra", Models: []string{"m"}}
	cfg.Providers["plain"] = config.ProviderConfig{BaseURL: "https://8.8.8.8", APIKey: "k", APIFormat: "openai", Models: []string{"m3"}}
	cfg.Providers["google"] = config.ProviderConfig{BaseURL: "https://generativelanguage.googleapis.com", APIFormat: "gemini", Models: []string{"m2"}}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatalf("creating registry: %v", err)
	}
	srv := New(cfg, "", registry, pipeline.NewEngine(), nil, nil, nil, nil, testModelDBPtr(), tenant.NewStore(), nil)
	// An org provider with its own base URL records no type.
	orgCfg := &tenant.ProviderConfig{APIKey: "org-key", Enabled: true, BaseURL: "https://8.8.8.8", APIFormat: "openai"}
	if _, err := srv.OrgProviderCache.GetOrCreateWithTenantConfig("org-1", "team-di", orgCfg.APIKey, orgCfg); err != nil {
		t.Fatalf("creating org provider: %v", err)
	}
	// org-2 has its own instance of "di", with its own base URL: the org's
	// instance serves its requests, and it records no type.
	if _, err := srv.OrgProviderCache.GetOrCreateWithTenantConfig("org-2", "di", orgCfg.APIKey, orgCfg); err != nil {
		t.Fatalf("creating org-2's di: %v", err)
	}

	for _, tc := range []struct{ orgID, providerID, want string }{
		{"", "di", "deepinfra"},
		{"", "plain", ""},
		{"", "google", ""},
		{"org-1", "team-di", ""},
		{"org-1", "di", "deepinfra"}, // no org-1 instance: the config.yaml provider
		{"org-2", "di", ""},          // the org's own instance comes first
		{"", "missing", ""},
	} {
		if got := srv.ProviderType(tc.orgID, tc.providerID); got != tc.want {
			t.Errorf("ProviderType(%q, %q) = %q, want %q", tc.orgID, tc.providerID, got, tc.want)
		}
	}
}
