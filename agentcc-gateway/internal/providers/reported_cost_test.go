package providers

import (
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// Which providers' own statement of a request's cost is trusted: the vendor
// (type, or provider ID when the config has no type) must be OpenRouter,
// Perplexity or xAI, the provider must speak the OpenAI format, and its
// base_url host must be that vendor's official host.
func TestReportedCostVendor_ConfigYAMLProviders(t *testing.T) {
	t.Parallel()
	for _, tc := range []struct {
		name string
		id   string
		cfg  config.ProviderConfig
		want string
	}{
		// Trusted.
		{"openrouter preset", "openrouter", config.ProviderConfig{Type: "openrouter"}, "openrouter"},
		{"perplexity preset", "perplexity", config.ProviderConfig{Type: "perplexity"}, "perplexity"},
		{"xai preset", "xai", config.ProviderConfig{Type: "xai"}, "xai"},
		{"typed provider of any name", "or-main", config.ProviderConfig{Type: "openrouter", BaseURL: "https://openrouter.ai/api"}, "openrouter"},
		{"http scheme and a port are not compared", "pplx", config.ProviderConfig{Type: "perplexity", BaseURL: "http://api.perplexity.ai:8443"}, "perplexity"},
		{"host case is not compared", "xai", config.ProviderConfig{Type: "xai", BaseURL: "https://API.X.AI/"}, "xai"},
		{"host with a trailing dot", "xai", config.ProviderConfig{Type: "xai", BaseURL: "https://api.x.ai./"}, "xai"},
		{"untyped entry named for the vendor", "perplexity", config.ProviderConfig{BaseURL: "https://api.perplexity.ai", APIFormat: "openai"}, "perplexity"},

		// Not trusted.
		{"openai type at openrouter.ai", "openrouter", config.ProviderConfig{Type: "openai", BaseURL: "https://openrouter.ai/api", APIFormat: "openai"}, ""},
		{"custom type at api.perplexity.ai", "perplexity", config.ProviderConfig{Type: "custom", BaseURL: "https://api.perplexity.ai", APIFormat: "openai"}, ""},
		{"vendor type at a proxy", "openrouter", config.ProviderConfig{Type: "openrouter", BaseURL: "https://llm-proxy.internal/api"}, ""},
		{"vendor type at a look-alike host", "openrouter", config.ProviderConfig{Type: "openrouter", BaseURL: "https://openrouter.ai.evil.example/api"}, ""},
		{"vendor type at a vendor subdomain", "perplexity", config.ProviderConfig{Type: "perplexity", BaseURL: "https://eu.api.perplexity.ai"}, ""},
		{"vendor type at another vendor's host", "xai", config.ProviderConfig{Type: "xai", BaseURL: "https://openrouter.ai/api"}, ""},
		{"untyped entry with another name", "pplx", config.ProviderConfig{BaseURL: "https://api.perplexity.ai", APIFormat: "openai"}, ""},
		{"untyped entry named for the vendor at another host", "perplexity", config.ProviderConfig{BaseURL: "https://pplx.example.com", APIFormat: "openai"}, ""},
		{"ignore_provider_cost", "openrouter", config.ProviderConfig{Type: "openrouter", IgnoreProviderCost: true}, ""},
		{"another vendor type", "deepinfra", config.ProviderConfig{Type: "deepinfra"}, ""},
		{"official openai", "openai", config.ProviderConfig{BaseURL: "https://api.openai.com", APIFormat: "openai"}, ""},
		// The e2e stack names a decision (systemone) provider "perplexity".
		{"perplexity ID in another format", "perplexity", config.ProviderConfig{BaseURL: "https://api.perplexity.ai", APIFormat: APIFormatSystemOne, Models: []string{"jev-latest"}}, ""},
		{"anthropic format at a vendor host", "openrouter", config.ProviderConfig{Type: "openrouter", BaseURL: "https://openrouter.ai/api", APIFormat: "anthropic"}, ""},
	} {
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()
			cfg := tc.cfg
			config.ApplyProviderPreset(&cfg)
			p, err := createProvider(tc.id, cfg)
			if err != nil {
				t.Fatalf("creating provider: %v", err)
			}
			defer func() { _ = p.Close() }()
			if got := ReportedCostVendor(p, tc.id); got != tc.want {
				t.Fatalf("ReportedCostVendor = %q, want %q", got, tc.want)
			}
		})
	}
}

func TestReportedCostVendor_NoProvider(t *testing.T) {
	t.Parallel()
	if got := ReportedCostVendor(nil, "openrouter"); got != "" {
		t.Fatalf("ReportedCostVendor(nil) = %q", got)
	}
}

// A dashboard (org) provider has no type: it is trusted when the dashboard
// provider name is the vendor's and its base_url is the vendor's official
// host. An org instance layered on a config.yaml entry keeps that entry's
// type and its ignore_provider_cost.
func TestReportedCostVendor_OrgInstances(t *testing.T) {
	stubLookupIP(t, map[string][]string{
		"openrouter.ai":     {"104.18.2.115"},
		"api.perplexity.ai": {"104.18.26.48"},
		"api.x.ai":          {"104.18.19.75"},
		"llm-proxy.example": {"93.184.215.14"},
	})
	base := map[string]config.ProviderConfig{
		"xai":       {Type: "xai", BaseURL: "https://api.x.ai", APIFormat: "openai"},
		"pplx-ops":  {Type: "perplexity", BaseURL: "https://api.perplexity.ai", APIFormat: "openai", IgnoreProviderCost: true},
		"or-proxy":  {Type: "openrouter", BaseURL: "https://llm-proxy.example/api", APIFormat: "openai"},
		"openai-or": {Type: "openai", BaseURL: "https://openrouter.ai/api", APIFormat: "openai"},
	}
	for _, tc := range []struct {
		name, providerID string
		tenantCfg        *tenant.ProviderConfig
		want             string
	}{
		{"dashboard openrouter at openrouter.ai", "openrouter", &tenant.ProviderConfig{BaseURL: "https://openrouter.ai/api/v1"}, "openrouter"},
		{"dashboard perplexity at api.perplexity.ai", "perplexity", &tenant.ProviderConfig{BaseURL: "https://api.perplexity.ai"}, "perplexity"},
		{"dashboard custom name at openrouter.ai", "my-or", &tenant.ProviderConfig{BaseURL: "https://openrouter.ai/api/v1"}, ""},
		{"dashboard openrouter at a proxy", "openrouter", &tenant.ProviderConfig{BaseURL: "https://llm-proxy.example/v1"}, ""},
		{"dashboard perplexity as a decision provider", "perplexity", &tenant.ProviderConfig{BaseURL: "https://api.perplexity.ai", APIFormat: APIFormatSystemOne, Models: []string{"jev-latest"}}, ""},
		{"layered on a typed config.yaml entry", "xai", &tenant.ProviderConfig{}, "xai"},
		{"layered on an entry that ignores provider cost", "pplx-ops", &tenant.ProviderConfig{}, ""},
		{"own upstream under an ID whose entry ignores provider cost", "pplx-ops", &tenant.ProviderConfig{BaseURL: "https://api.perplexity.ai"}, ""},
		{"layered on a typed entry at a proxy", "or-proxy", &tenant.ProviderConfig{}, ""},
		{"layered on an openai-typed entry at openrouter.ai", "openai-or", &tenant.ProviderConfig{}, ""},
	} {
		t.Run(tc.name, func(t *testing.T) {
			cache := NewOrgProviderCache(base, false)
			defer cache.EvictAll()
			p, err := cache.GetOrCreateWithTenantConfig("org-1", tc.providerID, "org-key", tc.tenantCfg)
			if err != nil {
				t.Fatalf("creating org provider: %v", err)
			}
			if got := ReportedCostVendor(p, tc.providerID); got != tc.want {
				t.Fatalf("ReportedCostVendor = %q, want %q", got, tc.want)
			}
		})
	}
}

// Every trusted vendor has a preset naming its official host.
func TestReportedCostVendors_HaveOfficialHosts(t *testing.T) {
	t.Parallel()
	want := map[string]string{"openrouter": "openrouter.ai", "perplexity": "api.perplexity.ai", "xai": "api.x.ai"}
	vendors := ReportedCostVendors()
	if len(vendors) != len(want) {
		t.Fatalf("trusted vendors = %v, want %v", vendors, want)
	}
	for _, v := range vendors {
		if got := officialHost(v); got != want[v] {
			t.Errorf("officialHost(%q) = %q, want %q", v, got, want[v])
		}
	}
}
