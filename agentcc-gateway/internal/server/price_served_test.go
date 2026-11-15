package server

import (
	"bytes"
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

// The cost of a chat request is the price of the provider that served it, as
// that provider states it, whatever the org's config says. The model is listed
// only as "gemini/…" and "vertex_ai/…", so a wrong name leaves it unpriced.
func TestChatCostFollowsTheServingProvider(t *testing.T) {
	// The org's config states another name for provider "google" than the
	// config.yaml provider that serves does ("" for an openai-format provider).
	orgGoogle := func(apiKey string) *tenant.OrgConfig {
		return &tenant.OrgConfig{Providers: map[string]*tenant.ProviderConfig{
			"google": {APIKey: apiKey, Enabled: true, BaseURL: "https://8.8.4.4", APIFormat: "openai"},
		}}
	}
	cases := []struct {
		name    string
		keyType string
		org     *tenant.OrgConfig
		// changeOrg runs while the upstream answers, after serving and before costing.
		changeOrg *tenant.OrgConfig
	}{
		{name: "internal key with a same-ID org provider", keyType: "internal", org: orgGoogle("org-key")},
		{name: "org config changes before costing", keyType: "internal", org: &tenant.OrgConfig{}, changeOrg: orgGoogle("org-key")},
		{name: "enabled org provider without credentials", keyType: "internal", org: orgGoogle("")},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			db := testModelDBPtr()
			entry, ok := db.Load().Get("gemini/gemini-4-argon")
			if !ok {
				t.Fatal("the table no longer lists gemini/gemini-4-argon")
			}
			want := 1000*entry.Pricing.InputPerToken + 100*entry.Pricing.OutputPerToken

			store := tenant.NewStore()
			store.Set("org-1", tc.org)
			upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if tc.changeOrg != nil {
					store.Set("org-1", tc.changeOrg)
				}
				w.Header().Set("Content-Type", "application/json")
				_, _ = w.Write([]byte(`{"candidates":[{"content":{"role":"model","parts":[{"text":"hi"}]},"finishReason":"STOP"}],"usageMetadata":{"promptTokenCount":1000,"candidatesTokenCount":100,"totalTokenCount":1100}}`))
			}))
			defer upstream.Close()

			cfg := config.DefaultConfig()
			cfg.Auth.Enabled = true
			cfg.Auth.Keys = []config.AuthKeyConfig{{Name: "k", Key: "sk-agentcc-served", Owner: "test-org", KeyType: tc.keyType, Metadata: map[string]string{"org_id": "org-1"}}}
			cfg.Providers["google"] = config.ProviderConfig{BaseURL: upstream.URL, APIKey: "test-key", APIFormat: "gemini", Models: []string{"gemini-4-argon"}}
			registry, err := providers.NewRegistry(cfg)
			if err != nil {
				t.Fatalf("creating registry: %v", err)
			}
			cost := costplugin.New(true, db.Load, nil, nil)
			srv := New(cfg, "", registry, pipeline.NewEngine(cost), nil, nil, nil, nil, db, store, nil)
			cost.SetPriceTables(srv.ServedPriceTableName)
			srv.ready.Store(true)

			req := httptest.NewRequest(http.MethodPost, "/v1/chat/completions", bytes.NewBufferString(`{"model":"gemini-4-argon","messages":[{"role":"user","content":"hi"}]}`))
			req.Header.Set("Authorization", "Bearer sk-agentcc-served")
			req.Header.Set("Content-Type", "application/json")
			w := httptest.NewRecorder()
			srv.httpServer.Handler.ServeHTTP(w, req)
			if w.Code != http.StatusOK {
				t.Fatalf("status %d: %s", w.Code, w.Body.String())
			}
			got, err := strconv.ParseFloat(strings.TrimSpace(w.Header().Get("x-agentcc-cost")), 64)
			if err != nil || math.Abs(got-want) > 1e-6 {
				t.Fatalf("x-agentcc-cost = %q, want %.6f (the serving provider's gemini price)", w.Header().Get("x-agentcc-cost"), want)
			}
		})
	}
}

// A caller's metadata cannot choose the price table its request is billed
// from: a non-chat request, which pins nothing, is priced from the table of
// the provider that serves it, whatever the header or body metadata says.
func TestCallerMetadataCannotChooseThePriceTable(t *testing.T) {
	cfg := config.DefaultConfig()
	cfg.Providers["google"] = config.ProviderConfig{BaseURL: "https://generativelanguage.googleapis.com", APIFormat: "gemini"}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatalf("creating registry: %v", err)
	}
	srv := New(cfg, "", registry, pipeline.NewEngine(), nil, nil, nil, nil, testModelDBPtr(), tenant.NewStore(), nil)

	rc := models.AcquireRequestContext()
	defer rc.Release()
	rc.EndpointType = "embedding"
	rc.Provider = "google"
	r := httptest.NewRequest(http.MethodPost, "/v1/embeddings", nil)
	r.Header.Set("x-agentcc-metadata", `{"price_table":"vertex_ai"}`)
	applyCallerMetadata(rc, r, json.RawMessage(`{"price_table":"vertex_ai"}`))

	if got := srv.ServedPriceTableName(rc); got != "gemini" {
		t.Fatalf("ServedPriceTableName = %q, want the serving provider's %q", got, "gemini")
	}
}
