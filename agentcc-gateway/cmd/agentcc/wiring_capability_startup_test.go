package main

import (
	"errors"
	"path/filepath"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/toolcatalog"
)

// The capability wiring loads the tool catalog once and fails startup on
// what the catalog refuses, whether or not tools are enabled, and the core's
// CardSource is that same catalog.

func capabilityTestConfig() *config.Config {
	cfg := config.DefaultConfig()
	cfg.Tools.Enabled = false
	return cfg
}

func TestCapabilityWiringDanglingPriceCardFailsStartup(t *testing.T) {
	cfg := capabilityTestConfig()
	cfg.MCP.ManagedServers = map[string]config.ManagedMCPServerConfig{
		"web": {Kind: "remote", Billing: "reserve", URL: "https://web.example/mcp",
			PriceCard: config.ToolPriceCardRef{Capability: "search", Vendor: "exa", SKU: "exa.search.nope"}},
	}
	err := initCapability(&wiringDeps{cfg: cfg})
	if !errors.Is(err, toolcatalog.ErrDanglingPriceCard) {
		t.Fatalf("initCapability with a dangling price_card = %v, want ErrDanglingPriceCard", err)
	}
	cfg.MCP.ManagedServers["web"] = config.ManagedMCPServerConfig{Kind: "remote", Billing: "reserve",
		URL: "https://web.example/mcp", PriceCard: config.ToolPriceCardRef{Capability: "search", Vendor: "exa", SKU: "exa.search.auto"}}
	if err := initCapability(&wiringDeps{cfg: cfg}); err != nil {
		t.Fatalf("initCapability with a resolvable price_card = %v, want nil", err)
	}
}

func TestCapabilityWiringManagedByokOnlyVendorFailsStartup(t *testing.T) {
	cfg := capabilityTestConfig()
	cfg.Tools.Enabled = true
	cfg.Providers["brave"] = config.ProviderConfig{BaseURL: "https://api.search.brave.com", APIKey: "k", Managed: true}
	if err := initCapability(&wiringDeps{cfg: cfg}); !errors.Is(err, toolcatalog.ErrManagedNotAllowed) {
		t.Fatalf("initCapability with a managed byok_only vendor = %v, want ErrManagedNotAllowed", err)
	}
	// With tools off, an operator provider is a model provider, even one that
	// shares a byok_only tool vendor's id (replicate is an LLM preset too).
	cfg = capabilityTestConfig()
	cfg.Providers["replicate"] = config.ProviderConfig{BaseURL: "https://api.replicate.com", APIKey: "k", APIFormat: "openai", Managed: true}
	if err := initCapability(&wiringDeps{cfg: cfg}); err != nil {
		t.Fatalf("initCapability with tools off and a managed replicate LLM provider = %v, want nil", err)
	}
	cfg = capabilityTestConfig()
	cfg.MCP.ManagedServers = map[string]config.ManagedMCPServerConfig{
		"search": {Kind: "remote", Billing: "delegated", URL: "https://brave.example/mcp", CredentialEnv: "FAGI_BRAVE",
			PriceCard: config.ToolPriceCardRef{Capability: "search", Vendor: "brave", SKU: "brave.search.web"}},
	}
	if err := initCapability(&wiringDeps{cfg: cfg}); !errors.Is(err, toolcatalog.ErrManagedNotAllowed) {
		t.Fatalf("initCapability with a delegated server on a byok_only card = %v, want ErrManagedNotAllowed", err)
	}
}

// Every seed card survives the conversion to the core's card type, and a
// tools.catalog_dir replaces the seeds.
func TestCapabilityWiringCardSourceIsTheCatalog(t *testing.T) {
	seeds, err := toolcatalog.SeedCatalog()
	if err != nil {
		t.Fatal(err)
	}
	src, err := cardSourceOf(seeds)
	if err != nil {
		t.Fatalf("cardSourceOf(seeds) = %v", err)
	}
	n := 0
	for _, c := range seeds.Cards() {
		found := false
		for _, got := range src.Cards(c.Capability) {
			if got.Vendor == c.Vendor && got.ResaleStatus == c.ResaleStatus && len(got.SKUs) == len(c.SKUs) &&
				got.SKUs[0].SKU == c.SKUs[0].ID && got.SKUs[0].USDPerUnit == c.SKUs[0].USDPerUnit {
				found = true
			}
		}
		if !found {
			t.Errorf("card %s/%s missing or changed in the core's card source", c.Capability, c.Vendor)
		}
		n++
	}
	if n == 0 {
		t.Fatal("the seed catalog has no cards")
	}

	cfg := capabilityTestConfig()
	cfg.Tools.CatalogDir = filepath.Join("..", "..", "scripts", "scenarios", "fixtures", "capability-echo", "tools")
	cat, _, err := toolcatalog.LoadForConfig(cfg)
	if err != nil {
		t.Fatalf("LoadForConfig(capability fixture cards) = %v", err)
	}
	src, err = cardSourceOf(cat)
	if err != nil {
		t.Fatal(err)
	}
	var vendors []string
	for _, c := range src.Cards("echo") {
		vendors = append(vendors, c.Vendor)
	}
	if len(vendors) != 2 || vendors[0] != "echo-a" || vendors[1] != "echo-b" {
		t.Errorf("echo cards = %v, want [echo-a echo-b] from tools.catalog_dir", vendors)
	}
	if len(src.Cards("search")) != 0 {
		t.Error("a tools.catalog_dir must replace the seed cards, not add to them")
	}
}
