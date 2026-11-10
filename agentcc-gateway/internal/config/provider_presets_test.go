package config

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestLoadProviderPresetsBeforeValidation(t *testing.T) {
	for _, tc := range []struct {
		name       string
		provider   string
		wantURL    string
		wantFormat string
		wantPrefix string
		wantError  string
	}{
		{name: "huggingface", provider: `{"type":"huggingface"}`, wantURL: "https://router.huggingface.co", wantFormat: "openai", wantPrefix: "/v1"},
		{name: "other_preset", provider: `{"type":"groq"}`, wantURL: "https://api.groq.com/openai", wantFormat: "openai", wantPrefix: "/v1"},
		{name: "explicit_url", provider: `{"type":"huggingface","base_url":"https://dedicated.example.test"}`, wantURL: "https://dedicated.example.test", wantFormat: "openai", wantPrefix: "/v1"},
		{name: "explicit_format", provider: `{"type":"huggingface","api_format":"anthropic"}`, wantURL: "https://router.huggingface.co", wantFormat: "anthropic", wantPrefix: "/v1"},
		{name: "explicit_prefix", provider: `{"type":"huggingface","api_path_prefix":"/custom"}`, wantURL: "https://router.huggingface.co", wantFormat: "openai", wantPrefix: "/custom"},
		{name: "empty_prefix", provider: `{"type":"huggingface","api_path_prefix":""}`, wantURL: "https://router.huggingface.co", wantFormat: "openai"},
		{name: "unknown_type", provider: `{"type":"unknown"}`, wantError: "base_url is required"},
		{name: "no_type", provider: `{}`, wantError: "base_url is required"},
		{name: "azure_needs_endpoint", provider: `{"type":"azure"}`, wantError: "base_url is required"},
		{name: "custom_needs_format", provider: `{"base_url":"https://dedicated.example.test"}`, wantError: "api_format is required"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			path := filepath.Join(t.TempDir(), "config.json")
			if err := os.WriteFile(path, []byte(`{"providers":{"test":`+tc.provider+`}}`), 0600); err != nil {
				t.Fatal(err)
			}
			cfg, err := Load(path)
			if tc.wantError != "" {
				if err == nil || !strings.Contains(err.Error(), tc.wantError) {
					t.Fatalf("Load error = %v, want %q", err, tc.wantError)
				}
				return
			}
			if err != nil {
				t.Fatalf("Load: %v", err)
			}
			provider := cfg.Providers["test"]
			if provider.BaseURL != tc.wantURL || provider.APIFormat != tc.wantFormat {
				t.Errorf("provider endpoint/format = %q/%q, want %q/%q", provider.BaseURL, provider.APIFormat, tc.wantURL, tc.wantFormat)
			}
			if provider.APIPathPrefix == nil || *provider.APIPathPrefix != tc.wantPrefix {
				t.Errorf("provider path prefix = %v, want %q", provider.APIPathPrefix, tc.wantPrefix)
			}
		})
	}
}
