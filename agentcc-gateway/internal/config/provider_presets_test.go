package config

import (
	"fmt"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
)

// Pin the existing defaults, not a new endpoint policy. Azure's resource URL
// cannot be inferred from its type and must still be supplied by the operator.
var presetBaseURLs = map[string]string{
	"groq":        "https://api.groq.com/openai",
	"mistral":     "https://api.mistral.ai",
	"together":    "https://api.together.xyz",
	"fireworks":   "https://api.fireworks.ai/inference",
	"deepinfra":   "https://api.deepinfra.com",
	"perplexity":  "https://api.perplexity.ai",
	"cerebras":    "https://api.cerebras.ai",
	"xai":         "https://api.x.ai",
	"huggingface": "https://router.huggingface.co",
	"anyscale":    "https://api.endpoints.anyscale.com",
	"replicate":   "https://api.replicate.com",
	"openrouter":  "https://openrouter.ai/api",
	"azure":       "https://resource.invalid",
}

func loadPresetYAML(t *testing.T, body string) (*Config, error) {
	t.Helper()
	path := filepath.Join(t.TempDir(), "config.yaml")
	if err := os.WriteFile(path, []byte(body), 0600); err != nil {
		t.Fatal(err)
	}
	return Load(path)
}

func TestProviderPresetLoadBeforeValidation(t *testing.T) {
	t.Setenv("PRESET_TEST_CREDENTIAL", "synthetic-provider-credential")
	for kind, wantURL := range presetBaseURLs {
		t.Run(kind, func(t *testing.T) {
			body := fmt.Sprintf("providers:\n  alias:\n    type: %s\n    api_key: ${PRESET_TEST_CREDENTIAL}\n", kind)
			wantFormat := "openai"
			if kind == "azure" {
				body += "    base_url: " + wantURL + "\n"
				wantFormat = "azure"
			}
			cfg, err := loadPresetYAML(t, body)
			if err != nil {
				t.Fatalf("type and credential should load before registry creation: %v", err)
			}
			p := cfg.Providers["alias"]
			if p.BaseURL != wantURL || p.APIFormat != wantFormat {
				t.Errorf("resolved endpoint = (%q, %q), want (%q, %q)", p.BaseURL, p.APIFormat, wantURL, wantFormat)
			}
			if p.APIKey != "synthetic-provider-credential" || p.Type != kind {
				t.Error("preset changed the credential or type")
			}
			if kind == "azure" {
				if p.APIPathPrefix != nil {
					t.Error("azure must not acquire an OpenAI path prefix")
				}
			} else if p.APIPathPrefix == nil || *p.APIPathPrefix != "/v1" {
				t.Error("OpenAI preset must retain its existing /v1 prefix")
			}
			if err := cfg.Validate(); err != nil || !reflect.DeepEqual(p, cfg.Providers["alias"]) {
				t.Errorf("revalidation must be idempotent: %v", err)
			}
		})
	}
}

func TestProviderPresetValidateBeforeRequiredFields(t *testing.T) {
	for kind, wantURL := range presetBaseURLs {
		t.Run(kind, func(t *testing.T) {
			cfg := DefaultConfig()
			p := ProviderConfig{Type: kind, APIKey: "synthetic-provider-credential"}
			if kind == "azure" {
				p.BaseURL = wantURL
			}
			cfg.Providers["alias"] = p
			if err := cfg.Validate(); err != nil {
				t.Fatalf("Validate of an in-memory preset config: %v", err)
			}
			if got := cfg.Providers["alias"]; got.BaseURL != wantURL || got.APIFormat == "" {
				t.Error("Validate did not persist the resolved provider config")
			}
		})
	}
}

func TestProviderPresetExplicitFieldsAlwaysWin(t *testing.T) {
	for kind, defaultURL := range presetBaseURLs {
		for _, prefix := range []string{"", "/custom/version"} {
			for _, explicit := range []string{"url", "format", "both"} {
				t.Run(kind+"/"+explicit+"/prefix="+prefix, func(t *testing.T) {
					body := fmt.Sprintf("providers:\n  alias:\n    type: %s\n    api_path_prefix: %q\n", kind, prefix)
					wantURL, wantFormat := defaultURL, "openai"
					if kind == "azure" {
						wantFormat = "azure"
					}
					if explicit != "format" || kind == "azure" {
						wantURL = "https://custom.invalid/proxy"
						body += "    base_url: " + wantURL + "\n"
					}
					if explicit != "url" {
						wantFormat = "cohere"
						body += "    api_format: cohere\n"
					}
					cfg, err := loadPresetYAML(t, body)
					if err != nil {
						t.Fatalf("Load: %v", err)
					}
					p := cfg.Providers["alias"]
					if p.BaseURL != wantURL || p.APIFormat != wantFormat || p.APIPathPrefix == nil || *p.APIPathPrefix != prefix {
						t.Errorf("explicit fields changed: URL=%q format=%q prefix=%v", p.BaseURL, p.APIFormat, p.APIPathPrefix)
					}
				})
			}
		}
	}
}

func TestProviderPresetMissingFieldsNameType(t *testing.T) {
	for _, tt := range []struct{ kind, fields, missing string }{
		{"unknown-service", "", "base_url"},
		{"unknown-service", "    api_format: openai\n", "base_url"},
		{"unknown-service", "    base_url: https://custom.invalid\n", "api_format"},
		{"Groq", "", "base_url"}, // type names are exact, not case-folded.
		{"azure", "", "base_url"},
		{"", "    api_format: openai\n", "base_url"},
	} {
		t.Run(tt.kind+"/"+tt.missing+"/"+tt.fields, func(t *testing.T) {
			_, err := loadPresetYAML(t, "providers:\n  alias:\n    type: "+fmt.Sprintf("%q", tt.kind)+"\n"+tt.fields)
			if err == nil {
				t.Fatal("missing required field was accepted")
			}
			for _, part := range []string{"alias", tt.kind, tt.missing} {
				if !strings.Contains(err.Error(), part) {
					t.Errorf("error %q must name %q", err, part)
				}
			}
		})
	}
}

func TestProviderPresetUnknownExplicitConfigUnchanged(t *testing.T) {
	for _, kind := range []string{"", "custom", "unknown-service", "Groq"} {
		cfg := DefaultConfig()
		p := ProviderConfig{Type: kind, BaseURL: "https://custom.invalid", APIFormat: "openai"}
		cfg.Providers["groq"] = p // ID alone must not select a preset.
		if err := cfg.Validate(); err != nil {
			t.Fatalf("explicit config type %q: %v", kind, err)
		}
		if !reflect.DeepEqual(p, cfg.Providers["groq"]) {
			t.Errorf("unknown or absent type %q changed", kind)
		}
	}
}

func TestProviderPresetDoesNotBypassManagedFormatValidation(t *testing.T) {
	_, err := loadPresetYAML(t, "providers:\n  alias:\n    type: groq\n    api_format: deepgram\n    managed: true\n")
	if err == nil || !strings.Contains(err.Error(), "managed: true") || !strings.Contains(err.Error(), "deepgram") {
		t.Fatalf("explicit unsupported managed format must still fail after defaults: %v", err)
	}
}
