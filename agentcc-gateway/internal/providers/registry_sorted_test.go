package providers

import (
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
)

// sortedRegistryConfig is five providers (none local, so no connectivity
// check) that all list "shared-model".
func sortedRegistryConfig() *config.Config {
	cfg := config.DefaultConfig()
	cfg.Providers = map[string]config.ProviderConfig{}
	for _, id := range []string{"echo", "bravo", "delta", "alpha", "charlie"} {
		cfg.Providers[id] = config.ProviderConfig{
			BaseURL:   "https://" + id + ".example.com",
			APIFormat: "openai",
			Models:    []string{"shared-model"},
		}
	}
	return cfg
}

// With every routing target for a model unhealthy, ResolveWithRouting falls
// back to the model's direct lookup: the provider with the smallest id, on
// every start (the registry is built 50 times; before, the provider the
// startup map range visited last won).
func TestResolveWithRouting_AllUnhealthyFallsBackToSmallestID(t *testing.T) {
	for i := 0; i < 50; i++ {
		r, err := NewRegistry(sortedRegistryConfig())
		if err != nil {
			t.Fatal(err)
		}
		if r.Router() == nil {
			t.Fatal("no router for a model five providers list")
		}
		for _, id := range []string{"alpha", "bravo", "charlie", "delta", "echo"} {
			r.Router().SetHealthy(id, false)
		}
		res, err := r.ResolveWithRouting("shared-model")
		r.Close()
		if err != nil {
			t.Fatalf("build %d: %v", i, err)
		}
		if got := res.Provider.ID(); got != "alpha" {
			t.Fatalf("build %d: the fallback is %q, want alpha (the smallest id)", i, got)
		}
	}
}

// With the targets healthy, round-robin still spreads a model over every
// provider that lists it, one call each in turn.
func TestResolveWithRouting_HealthyRoundRobinUnchanged(t *testing.T) {
	r, err := NewRegistry(sortedRegistryConfig())
	if err != nil {
		t.Fatal(err)
	}
	defer r.Close()
	seen := map[string]int{}
	prev := ""
	for i := 0; i < 20; i++ {
		res, err := r.ResolveWithRouting("shared-model")
		if err != nil {
			t.Fatal(err)
		}
		id := res.Provider.ID()
		if id == prev {
			t.Fatalf("call %d went to %q again; round-robin moves on", i, id)
		}
		prev = id
		seen[id]++
	}
	for _, id := range []string{"alpha", "bravo", "charlie", "delta", "echo"} {
		if seen[id] != 4 {
			t.Errorf("%s served %d of 20 calls, want 4: %v", id, seen[id], seen)
		}
	}
}
