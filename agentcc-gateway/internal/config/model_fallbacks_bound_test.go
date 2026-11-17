package config

// MIXED-HOP-GUARD-1 round 3: a model fallback chain names at most 15 models
// (MaxModelFallbacks, one less than the 16 attempts a request records), and
// config load refuses a longer one. This file uses only APIs that exist at
// 4e38b7961b (the round-2 head), where chains had no bound.

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func chainOf(n int) []string {
	chain := make([]string, n)
	for i := range chain {
		chain[i] = fmt.Sprintf("fallback-%d", i)
	}
	return chain
}

func TestValidateBoundsModelFallbackChains(t *testing.T) {
	cfg := DefaultConfig()
	cfg.Routing.ModelFallbacks = map[string][]string{"gpt-4o": chainOf(15), "claude": chainOf(1)}
	if err := cfg.Validate(); err != nil {
		t.Fatalf("a 15-model chain was refused: %v", err)
	}
	cfg.Routing.ModelFallbacks["claude"] = chainOf(16)
	err := cfg.Validate()
	if err == nil || !strings.Contains(err.Error(), `routing.model_fallbacks["claude"]`) || !strings.Contains(err.Error(), "at most 15") {
		t.Fatalf("Validate() = %v; want the 16-model chain refused", err)
	}
}

func TestLoadBoundsModelFallbackChains(t *testing.T) {
	var b strings.Builder
	b.WriteString("routing:\n  model_fallbacks:\n    gpt-4o:\n")
	for _, m := range chainOf(40) {
		fmt.Fprintf(&b, "      - %s\n", m)
	}
	path := filepath.Join(t.TempDir(), "config.yaml")
	if err := os.WriteFile(path, []byte(b.String()), 0o600); err != nil {
		t.Fatal(err)
	}
	if _, err := Load(path); err == nil || !strings.Contains(err.Error(), "names 40 models") {
		t.Fatalf("Load() = %v; want the 40-model chain refused", err)
	}
}
