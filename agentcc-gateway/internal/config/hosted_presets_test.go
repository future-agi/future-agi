package config

import (
	"fmt"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
)

// Provider presets resolve before hosted validation (PRESET-ORDER with the
// hosted profile). A type-and-credential shorthand loads under the hosted
// policy, the hosted transport check runs against the resolved URL, and an
// explicit insecure field still wins over the preset and is still refused.
// Load only: no provider client is constructed and nothing is dialed.

const hostedPresetCredential = "synthetic-provider-credential"

func hostedPresetText(t *testing.T, provider string) string {
	t.Helper()
	t.Setenv("HOSTED_PRESET_TEST_CREDENTIAL", hostedPresetCredential)
	return hostedFixture(t) + "providers:\n  fast:\n" + provider
}

func loadHostedConfigAt(t *testing.T, path, text string) (*Config, error) {
	t.Helper()
	if err := os.WriteFile(path, []byte(text), 0600); err != nil {
		t.Fatal(err)
	}
	return Load(path)
}

func hostedPresetShorthand(kind string) string {
	return fmt.Sprintf("    type: %s\n    api_key: ${HOSTED_PRESET_TEST_CREDENTIAL}\n", kind)
}

func TestHostedProfileProviderPresetShorthandLoads(t *testing.T) {
	for kind, preset := range KnownProviders {
		if preset.BaseURL == "" {
			continue // azure: its resource URL cannot be inferred.
		}
		t.Run(kind, func(t *testing.T) {
			text := hostedPresetText(t, hostedPresetShorthand(kind))
			cfg, err := loadHostedConfigAt(t, filepath.Join(t.TempDir(), "gateway.yaml"), text)
			if err != nil {
				t.Fatalf("hosted Load refused a %s type-and-credential shorthand: %v", kind, err)
			}
			if !cfg.IsHosted() {
				t.Fatal("fixture did not select the hosted profile")
			}
			p := cfg.Providers["fast"]
			if p.BaseURL != preset.BaseURL || p.APIFormat != preset.APIFormat {
				t.Errorf("resolved endpoint = (%q, %q), want (%q, %q)", p.BaseURL, p.APIFormat, preset.BaseURL, preset.APIFormat)
			}
			if p.APIPathPrefix == nil || *p.APIPathPrefix != preset.PathPrefix {
				t.Error("hosted Load lost the preset path prefix")
			}
			if p.APIKey != hostedPresetCredential || p.Type != kind {
				t.Error("preset changed the credential or type")
			}
		})
	}
}

func TestHostedProfileProviderPresetReload(t *testing.T) {
	path := filepath.Join(t.TempDir(), "gateway.yaml")
	shorthand := hostedPresetText(t, hostedPresetShorthand("groq"))
	first, err := loadHostedConfigAt(t, path, shorthand)
	if err != nil {
		t.Fatalf("hosted Load refused the shorthand: %v", err)
	}
	// Reload re-reads the same file through Load (server reloadHandler).
	second, err := loadHostedConfigAt(t, path, shorthand)
	if err != nil {
		t.Fatalf("hosted reload refused the shorthand: %v", err)
	}
	if !reflect.DeepEqual(first.Providers, second.Providers) {
		t.Error("reloading the same file resolved a different provider config")
	}
	if err := second.Validate(); err != nil || !reflect.DeepEqual(first.Providers, second.Providers) {
		t.Errorf("revalidating a resolved hosted config must be idempotent: %v", err)
	}
	// A reload that changes the file to an explicit insecure URL is refused,
	// and the operator can return to the shorthand afterwards.
	insecure := hostedPresetText(t, hostedPresetShorthand("groq")+"    base_url: http://api.groq.com/openai\n")
	if _, err := loadHostedConfigAt(t, path, insecure); err == nil {
		t.Fatal("hosted reload accepted an explicit plain-HTTP override of a preset")
	}
	if _, err := loadHostedConfigAt(t, path, shorthand); err != nil {
		t.Fatalf("hosted reload back to the shorthand: %v", err)
	}
}

func TestHostedProfileProviderPresetKeepsExplicitInsecureRefusals(t *testing.T) {
	for _, tc := range []struct{ name, kind, extra string }{
		{"plain http", "groq", "    base_url: http://api.groq.com/openai\n"},
		{"tls bypass", "groq", "    skip_tls: true\n"},
		{"local provider", "groq", "    local: true\n"},
		{"private address", "mistral", "    base_url: https://10.0.0.5\n"},
		{"loopback address", "mistral", "    base_url: https://127.0.0.1\n"},
		{"development host", "together", "    base_url: https://upstream.localhost\n"},
		{"embedded user info", "openrouter", "    base_url: https://operator@openrouter.ai/api\n"},
		{"query string", "xai", "    base_url: https://api.x.ai?route=other\n"},
		{"funded provider", "groq", "    managed: true\n"},
		{"azure without its resource URL", "azure", ""},
		{"unknown type without a URL", "unknown-service", "    api_format: openai\n"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			text := hostedPresetText(t, hostedPresetShorthand(tc.kind)+tc.extra)
			_, err := loadHostedConfigAt(t, filepath.Join(t.TempDir(), "gateway.yaml"), text)
			if err == nil {
				t.Fatal("hosted configuration accepted an explicit insecure or incomplete provider")
			}
			for _, secret := range []string{hostedPresetCredential, os.Getenv("AGENTCC_ADMIN_TOKEN"), os.Getenv("AGENTCC_INTERNAL_API_KEY"), os.Getenv("AGENTCC_WEBHOOK_SECRET")} {
				if secret != "" && strings.Contains(err.Error(), secret) {
					t.Fatal("error exposed a credential")
				}
			}
		})
	}
}
