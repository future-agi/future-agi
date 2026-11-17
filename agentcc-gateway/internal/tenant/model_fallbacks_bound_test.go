package tenant

// MIXED-HOP-GUARD-1 round 3: an org's model fallback chain is held to 15
// models (MaxModelFallbacks) when its config is loaded, from a push, a sync
// or the stored copy, all of which decode it as JSON. A longer chain is cut
// to its first 15 rather than refused, so one long chain never costs the org
// its whole config. This file uses only APIs that exist at 4e38b7961b (the
// round-2 head), where chains had no bound.

import (
	"encoding/json"
	"fmt"
	"slices"
	"testing"
)

func TestOrgModelFallbackChainsAreBoundedAtLoad(t *testing.T) {
	long := make([]string, 40)
	for i := range long {
		long[i] = fmt.Sprintf("fallback-%d", i)
	}
	raw, err := json.Marshal(map[string]any{"routing": map[string]any{
		"fallback_enabled": true,
		"model_fallbacks":  map[string][]string{"gpt-4o": long, "claude": long[:15], "short": long[:2]},
	}})
	if err != nil {
		t.Fatal(err)
	}
	var cfg OrgConfig
	if err := json.Unmarshal(raw, &cfg); err != nil {
		t.Fatalf("the org config was refused: %v", err)
	}
	chains := cfg.Routing.ModelFallbacks
	if !slices.Equal(chains["gpt-4o"], long[:15]) {
		t.Fatalf("the 40-model chain loaded as %d models %v; want its first 15", len(chains["gpt-4o"]), chains["gpt-4o"])
	}
	if !slices.Equal(chains["claude"], long[:15]) || !slices.Equal(chains["short"], long[:2]) {
		t.Fatalf("chains within the bound changed: %v", chains)
	}
	if !cfg.Routing.FallbackOn() {
		t.Fatal("the fallback switch was lost")
	}
	// What the store writes back (GET /-/orgs/{org}/config, the stored copy)
	// is the bounded chain.
	out, err := json.Marshal(cfg.Routing)
	if err != nil {
		t.Fatal(err)
	}
	var back RoutingConfig
	if err := json.Unmarshal(out, &back); err != nil || len(back.ModelFallbacks["gpt-4o"]) != 15 {
		t.Fatalf("round trip: %v, %d models", err, len(back.ModelFallbacks["gpt-4o"]))
	}
}
