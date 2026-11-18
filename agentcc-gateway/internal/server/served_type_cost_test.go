package server

import (
	"context"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/modeldb"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	costplugin "github.com/futureagi/agentcc-gateway/internal/plugins/cost"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// The reseller rule judges the instance that served a request. An org with its
// own "di" (an org instance records no type) and the operator's "di" typed
// deepinfra: a Gemini 3.7 Flash request the operator's instance served bills
// DeepInfra's own entry from 2027; one the org's instance served bills Google's
// 2027 row, as every request of that org did when the org's instance was asked.
func TestTheResellerPriceFollowsTheInstanceThatServed(t *testing.T) {
	cfg := config.DefaultConfig()
	// An IP literal passes the base URL check without a lookup; nothing is sent to it.
	cfg.Providers["di"] = config.ProviderConfig{BaseURL: "https://8.8.8.8", APIKey: "k", APIFormat: "openai", Type: "deepinfra", Models: []string{"m"}}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatalf("creating registry: %v", err)
	}
	srv := New(cfg, "", registry, pipeline.NewEngine(), nil, nil, nil, nil, testModelDBPtr(), tenant.NewStore(), nil)
	orgCfg := &tenant.ProviderConfig{APIKey: "org-key", Enabled: true, BaseURL: "https://8.8.8.8", APIFormat: "openai"}
	orgDI, err := srv.OrgProviderCache.GetOrCreateWithTenantConfig("org-2", "di", orgCfg.APIKey, orgCfg)
	if err != nil {
		t.Fatalf("creating org-2's di: %v", err)
	}
	if typed, ok := orgDI.(interface{ ConfiguredType() string }); ok && typed.ConfiguredType() != "" {
		t.Fatalf("an org instance records type %q; the reverse case would exist", typed.ConfiguredType())
	}
	configDI, ok := registry.GetProvider("di")
	if !ok {
		t.Fatal("no config.yaml di")
	}

	db := modeldb.New(modeldb.BundledModels, nil)
	p := costplugin.New(true, func() *modeldb.ModelDB { return db }, nil, nil)
	p.SetPriceTables(srv.ServedPriceTableName)
	p.SetProviderTypes(srv.ProviderType)

	jan27 := time.Date(2027, 1, 15, 12, 0, 0, 0, time.UTC)
	dec26 := time.Date(2026, 12, 15, 12, 0, 0, 0, time.UTC)
	for _, c := range []struct {
		name      string
		servedBy  providers.Provider
		orgServed bool
		at        time.Time
		want      string
	}{
		{"operator's typed instance served, Jan 2027", configDI, false, jan27, "1.125000"},
		{"org's own instance served, Jan 2027", orgDI, true, jan27, "2.250000"},
		{"operator's typed instance served, Dec 2026", configDI, false, dec26, "1.125000"},
		{"org's own instance served, Dec 2026", orgDI, true, dec26, "1.125000"},
	} {
		rc := &models.RequestContext{
			Model:     "gemini-3.7-flash",
			Provider:  "di",
			StartTime: c.at,
			ServedBy:  c.servedBy,
			Metadata:  map[string]string{"org_id": "org-2"},
			Timings:   map[string]time.Duration{},
			Response: &models.ChatCompletionResponse{Usage: &models.Usage{
				PromptTokens: 1_000_000, CompletionTokens: 100_000, TotalTokens: 1_100_000,
			}},
		}
		if c.orgServed {
			rc.Metadata["org_provider"] = "true"
		}
		p.ProcessResponse(context.Background(), rc)
		if got := rc.Metadata["cost"]; got != c.want {
			t.Errorf("%s: cost = %q, want %q", c.name, got, c.want)
		}
	}
}
