package config

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// mirrorConfig is a config with a managed provider and a provider key, and
// one traffic mirror rule copying gpt-4o to target.
func mirrorConfig(target string, enabled bool) *Config {
	off := false
	cfg := DefaultConfig()
	cfg.Providers["openrouter"] = ProviderConfig{BaseURL: "http://127.0.0.1:9", APIKey: "synthetic-unused", APIFormat: "openai",
		Type: "openrouter", Managed: true, AutoDiscover: &off}
	cfg.Providers["operator"] = ProviderConfig{BaseURL: "http://127.0.0.1:9", APIKey: "synthetic-unused", APIFormat: "openai",
		Models: []string{"gpt-4o"}, AutoDiscover: &off}
	cfg.Routing.Mirror = MirrorConfig{Enabled: enabled,
		Rules: []MirrorRule{{SourceModel: "gpt-4o", TargetProvider: target, SampleRate: 1}}}
	return cfg
}

// A traffic mirror rule may not target a managed provider: the mirror's copy
// is never held or billed, so it would spend Future AGI credits for nothing
// (MIXED-HOP-GUARD-1). Refused whether the mirror is on or not, so turning it
// on cannot bring one in; a provider key target stays valid.
func TestValidateRefusesManagedMirrorTarget(t *testing.T) {
	for _, enabled := range []bool{true, false} {
		err := mirrorConfig("openrouter", enabled).Validate()
		if err == nil || !strings.Contains(err.Error(), "routing.mirror.rules[0]") || !strings.Contains(err.Error(), "openrouter") {
			t.Fatalf("enabled=%v: Validate() = %v; want the managed mirror target refused", enabled, err)
		}
		if err := mirrorConfig("operator", enabled).Validate(); err != nil {
			t.Fatalf("enabled=%v: a provider key mirror target was refused: %v", enabled, err)
		}
	}
	// A target that is no configured provider is the lookup's business
	// (logged at dispatch), as before.
	if err := mirrorConfig("not-configured", true).Validate(); err != nil {
		t.Fatalf("an unknown mirror target was refused: %v", err)
	}
}

// The refusal holds at config load, from YAML.
func TestLoadRefusesManagedMirrorTarget(t *testing.T) {
	yaml := `
providers:
  openrouter:
    base_url: http://127.0.0.1:9
    api_key: synthetic-unused
    api_format: openai
    type: openrouter
    managed: true
    auto_discover: false
routing:
  mirror:
    enabled: true
    rules:
      - source_model: "*"
        target_provider: openrouter
        sample_rate: 1
`
	path := filepath.Join(t.TempDir(), "config.yaml")
	if err := os.WriteFile(path, []byte(yaml), 0o600); err != nil {
		t.Fatal(err)
	}
	if _, err := Load(path); err == nil || !strings.Contains(err.Error(), "managed provider") {
		t.Fatalf("Load() = %v; want the managed mirror target refused", err)
	}
}
