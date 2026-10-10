package providers

import (
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/providers/typesafe"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
	"testing"
)

func TestRegistryTypeSafe(t *testing.T) {
	cfg := config.DefaultConfig()
	cfg.Providers = map[string]config.ProviderConfig{"typesafe": {APIFormat: "typesafe", Models: []string{"jev-1.13.0", "jev-latest"}}}
	registry, err := NewRegistry(cfg)
	if err != nil {
		t.Fatal(err)
	}
	defer registry.Close()
	for _, model := range []string{"jev-1.13.0", "jev-latest"} {
		p, err := registry.Resolve(model)
		if err != nil {
			t.Fatal(err)
		}
		if _, ok := p.(*typesafe.Provider); !ok || p.ID() != "typesafe" {
			t.Fatalf("%s resolved to %T %s", model, p, p.ID())
		}
		if _, ok := p.(SystemOneProvider); !ok {
			t.Fatal("missing SystemOneProvider interface")
		}
	}
}

func TestOrgProviderTypeSafe(t *testing.T) {
	cache := NewOrgProviderCache(nil, false)
	defer cache.EvictAll()
	for _, format := range []string{"typesafe", ""} {
		p, err := cache.GetOrCreateWithTenantConfig("org-"+format, "typesafe", "synthetic-org-key", &tenant.ProviderConfig{APIFormat: format, Models: []string{"jev-latest"}})
		if err != nil {
			t.Fatal(err)
		}
		if _, ok := p.(SystemOneProvider); !ok {
			t.Fatalf("org provider = %T, want SystemOneProvider", p)
		}
	}
}
