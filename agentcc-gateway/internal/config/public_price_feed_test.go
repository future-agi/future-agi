package config

import (
	"encoding/json"
	"testing"
)

func TestModelPricesConfig(t *testing.T) {
	cfg := DefaultConfig()
	if cfg.PublicPriceFeed.Enabled || len(cfg.PublicPriceFeed.Sources) != 0 {
		t.Fatal("public price publication must require explicit operator opt-in and source allowlisting")
	}
	if err := json.Unmarshal([]byte(`{"public_price_feed":{"enabled":true,"sources":[{"provider_id":"fixture","catalog_source":"openrouter"}]}}`), cfg); err != nil {
		t.Fatal(err)
	}
	if !cfg.PublicPriceFeed.Enabled || len(cfg.PublicPriceFeed.Sources) != 1 || cfg.PublicPriceFeed.Sources[0].ProviderID != "fixture" || cfg.PublicPriceFeed.Sources[0].CatalogSource != "openrouter" {
		t.Fatalf("JSON setting not preserved: %+v", cfg.PublicPriceFeed)
	}
	if cfg.Managed.MarkupPercent != 5 || len(cfg.Providers) != 0 {
		t.Fatal("public feed configuration changed the default markup or enabled providers")
	}
	for _, endpoint := range []string{"/public", "/public/model-prices/v1.json"} {
		if mcpEndpointCollision(endpoint) == "" {
			t.Fatalf("MCP endpoint %s would cover the public price route", endpoint)
		}
	}
}
