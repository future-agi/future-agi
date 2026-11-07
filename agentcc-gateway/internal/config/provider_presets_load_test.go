package config

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// Exercise the startup path, not just preset application: Load must return a
// complete provider before the registry has been constructed.
func TestLoadProviderPresets(t *testing.T) {
	t.Setenv("TEST_PRESET_API_KEY", "test-provider-key")
	for _, tt := range []struct {
		name       string
		provider   string
		baseURL    string
		apiFormat  string
		pathPrefix string
		wantErr    string
	}{
		{
			name:     "documented type-only groq",
			provider: `type: groq`,
			baseURL:  "https://api.groq.com/openai", apiFormat: "openai", pathPrefix: "/v1",
		},
		{
			name: "custom base URL keeps its path",
			provider: `type: groq
    base_url: https://proxy.example.test/custom`,
			baseURL: "https://proxy.example.test/custom", apiFormat: "openai", pathPrefix: "/v1",
		},
		{
			name: "explicit format wins",
			provider: `type: groq
    api_format: anthropic`,
			baseURL: "https://api.groq.com/openai", apiFormat: "anthropic", pathPrefix: "/v1",
		},
		{
			name: "explicit endpoint and format win",
			provider: `type: groq
    base_url: https://proxy.example.test/custom
    api_format: gemini`,
			baseURL: "https://proxy.example.test/custom", apiFormat: "gemini", pathPrefix: "/v1",
		},
		{
			name: "explicit empty prefix wins",
			provider: `type: groq
    api_path_prefix: ""`,
			baseURL: "https://api.groq.com/openai", apiFormat: "openai", pathPrefix: "",
		},
		{
			name: "explicit custom prefix wins",
			provider: `type: groq
    api_path_prefix: /custom/v2`,
			baseURL: "https://api.groq.com/openai", apiFormat: "openai", pathPrefix: "/custom/v2",
		},
		{
			name:     "azure needs an explicit endpoint",
			provider: `type: azure`,
			wantErr:  "base_url is required",
		},
		{
			name: "azure fills only the format",
			provider: `type: azure
    base_url: https://resource.openai.azure.com`,
			baseURL: "https://resource.openai.azure.com", apiFormat: "azure", pathPrefix: "/v1",
		},
		{
			name:     "unknown type without endpoint",
			provider: `type: custom`,
			wantErr:  "base_url is required",
		},
		{
			name: "unknown type without format",
			provider: `type: custom
    base_url: https://proxy.example.test`,
			wantErr: "api_format is required",
		},
		{
			name: "unknown type with explicit config",
			provider: `type: custom
    base_url: https://proxy.example.test
    api_format: openai
    api_path_prefix: ""`,
			baseURL: "https://proxy.example.test", apiFormat: "openai", pathPrefix: "",
		},
	} {
		t.Run(tt.name, func(t *testing.T) {
			content := "providers:\n  primary:\n    " + tt.provider + `
    api_key: ${TEST_PRESET_API_KEY}
    models: [test-model]
`
			path := filepath.Join(t.TempDir(), "config.yaml")
			if err := os.WriteFile(path, []byte(content), 0644); err != nil {
				t.Fatal(err)
			}
			cfg, err := Load(path)
			if tt.wantErr != "" {
				if err == nil || !strings.Contains(err.Error(), tt.wantErr) {
					t.Fatalf("Load() error = %v, want %q", err, tt.wantErr)
				}
				return
			}
			if err != nil {
				t.Fatalf("Load() error: %v", err)
			}
			p := cfg.Providers["primary"]
			if p.BaseURL != tt.baseURL || p.APIFormat != tt.apiFormat {
				t.Errorf("provider endpoint = (%q, %q), want (%q, %q)", p.BaseURL, p.APIFormat, tt.baseURL, tt.apiFormat)
			}
			if got := p.EffectiveAPIPathPrefix(); got != tt.pathPrefix {
				t.Errorf("EffectiveAPIPathPrefix() = %q, want %q", got, tt.pathPrefix)
			}
			if p.APIKey != "test-provider-key" || len(p.Models) != 1 || p.Models[0] != "test-model" {
				t.Error("provider credentials or models changed")
			}
		})
	}
}
