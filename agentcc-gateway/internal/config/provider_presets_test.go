package config

import (
	"bytes"
	"log/slog"
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

// A preset that fills base_url or api_format says so at startup, so a blank
// endpoint (for example an unset ${VAR}) that falls back to the vendor's
// public host is visible. The log names the provider, fields and preset only.
func TestLoadLogsPresetDefaults(t *testing.T) {
	for _, tc := range []struct {
		name     string
		provider string
		want     string
	}{
		{name: "type_only", provider: `{"type":"huggingface","api_key":"do-not-log-me"}`, want: "provider=test preset=huggingface fields=\"[base_url api_format]\""},
		{name: "explicit_url", provider: `{"type":"huggingface","base_url":"https://dedicated.example.test","api_key":"do-not-log-me"}`, want: "provider=test preset=huggingface fields=[api_format]"},
		{name: "explicit_both", provider: `{"type":"huggingface","base_url":"https://dedicated.example.test","api_format":"openai","api_key":"do-not-log-me"}`},
		{name: "no_preset", provider: `{"base_url":"https://dedicated.example.test","api_format":"openai","api_key":"do-not-log-me"}`},
	} {
		t.Run(tc.name, func(t *testing.T) {
			var logs bytes.Buffer
			prev := slog.Default()
			slog.SetDefault(slog.New(slog.NewTextHandler(&logs, nil)))
			t.Cleanup(func() { slog.SetDefault(prev) })

			path := filepath.Join(t.TempDir(), "config.json")
			if err := os.WriteFile(path, []byte(`{"providers":{"test":`+tc.provider+`}}`), 0600); err != nil {
				t.Fatal(err)
			}
			if _, err := Load(path); err != nil {
				t.Fatalf("Load: %v", err)
			}
			got := logs.String()
			if strings.Contains(got, "do-not-log-me") {
				t.Fatalf("log contains the provider credential: %s", got)
			}
			if tc.want == "" {
				if strings.Contains(got, "provider preset filled defaults") {
					t.Fatalf("unexpected preset log: %s", got)
				}
				return
			}
			if !strings.Contains(got, `level=INFO msg="config: provider preset filled defaults" `+tc.want) {
				t.Fatalf("preset log missing %q in: %s", tc.want, got)
			}
		})
	}
}
