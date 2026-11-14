package config

import (
	"os"
	"path/filepath"
	"testing"
)

func TestLoad_TelnyxPreset(t *testing.T) {
	for _, tc := range []struct {
		name       string
		overrides  string
		wantURL    string
		wantFormat string
		wantPrefix string
	}{
		{"defaults", "", "https://api.telnyx.com/v2/ai", "openai", "/openai"},
		{"base override", "    base_url: https://proxy.example.test/v2/ai\n", "https://proxy.example.test/v2/ai", "openai", "/openai"},
		{"empty prefix", "    api_path_prefix: ''\n", "https://api.telnyx.com/v2/ai", "openai", ""},
		{"custom prefix", "    api_path_prefix: /compat\n", "https://api.telnyx.com/v2/ai", "openai", "/compat"},
		{"explicit fields", "    base_url: https://proxy.example.test\n    api_format: anthropic\n    api_path_prefix: /v1\n", "https://proxy.example.test", "anthropic", "/v1"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			path := filepath.Join(t.TempDir(), "config.yaml")
			data := "providers:\n  telnyx:\n    type: telnyx\n    models: [MiniMaxAI/MiniMax-M3-MXFP8]\n" + tc.overrides
			if err := os.WriteFile(path, []byte(data), 0600); err != nil {
				t.Fatal(err)
			}
			cfg, err := Load(path)
			if err != nil {
				t.Fatalf("type-only Telnyx config must load: %v", err)
			}
			p := cfg.Providers["telnyx"]
			if p.BaseURL != tc.wantURL || p.APIFormat != tc.wantFormat {
				t.Errorf("connection defaults = (%s, %s), want (%s, %s)", p.BaseURL, p.APIFormat, tc.wantURL, tc.wantFormat)
			}
			if p.APIPathPrefix == nil || *p.APIPathPrefix != tc.wantPrefix {
				t.Errorf("path prefix = %q, want explicit %q", p.EffectiveAPIPathPrefix(), tc.wantPrefix)
			}
			if len(p.Models) != 1 || p.Models[0] != "MiniMaxAI/MiniMax-M3-MXFP8" {
				t.Errorf("configured model IDs changed: %v", p.Models)
			}
		})
	}
}
