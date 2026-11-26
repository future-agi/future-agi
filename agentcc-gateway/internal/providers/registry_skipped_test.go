package providers

import (
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
)

func TestRegistrySkipsUnsupportedFormat(t *testing.T) {
	cfg := &config.Config{
		Providers: map[string]config.ProviderConfig{
			"active": {APIFormat: "openai", BaseURL: "https://active.example", Models: []string{"chat"}},
			"future": {APIFormat: "future-format", BaseURL: "https://future.example", Models: []string{"future-only"}},
		},
		ModelMap: map[string]string{"alias": "future"},
		Routing: config.RoutingConfig{
			DefaultStrategy: "round-robin",
			Targets: map[string][]config.RoutingTargetConfig{
				"mixed": {{Provider: "future"}, {Provider: "active"}},
			},
		},
	}
	r, err := NewRegistry(cfg)
	if err != nil {
		t.Fatal(err)
	}
	defer r.Close()
	active, ok := r.GetProvider("active")
	if !ok || r.ProviderCount() != 1 {
		t.Fatalf("registered providers: %v", r.ProviderIDs())
	}
	for _, model := range []string{"chat", "mixed"} {
		if p, err := r.Resolve(model); err != nil || p != active {
			t.Errorf("%s did not resolve to active: %v", model, err)
		}
	}
	for _, model := range []string{"future-only", "alias", "future/new"} {
		if _, err := r.Resolve(model); err == nil || len(r.ProvidersFor(model)) != 0 {
			t.Errorf("skipped provider still serves %q", model)
		}
	}
	if err := r.ReloadRouter(cfg.Routing); err != nil {
		t.Fatalf("reload rejected unchanged routing: %v", err)
	}
	cfg.Routing.Targets["typo"] = []config.RoutingTargetConfig{{Provider: "unconfigured"}}
	if err := r.ReloadRouter(cfg.Routing); err == nil {
		t.Error("reload accepted an unconfigured provider")
	}
}
