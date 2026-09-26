package providers

import (
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// ---------------------------------------------------------------------------
// resolveOrgConfig: the tenant's path prefix has to survive a provider that is
// also present in config.yaml, which is the path that inherits the YAML prefix.
// ---------------------------------------------------------------------------

func TestResolveOrgConfig_PathPrefix(t *testing.T) {
	ptr := func(s string) *string { return &s }

	tests := []struct {
		name      string
		yaml      *string
		tenantCfg *tenant.ProviderConfig
		want      string
	}{
		{
			name:      "no tenant config keeps the yaml prefix",
			yaml:      ptr("/v1"),
			tenantCfg: nil,
			want:      "/v1",
		},
		{
			name:      "tenant that states nothing keeps the yaml prefix",
			yaml:      ptr("/v1"),
			tenantCfg: &tenant.ProviderConfig{},
			want:      "/v1",
		},
		{
			name:      "explicitly empty tenant prefix overrides the yaml one",
			yaml:      ptr("/v1"),
			tenantCfg: &tenant.ProviderConfig{APIPathPrefix: ptr("")},
			want:      "",
		},
		{
			name:      "non-empty tenant prefix overrides the yaml one",
			yaml:      ptr("/v1"),
			tenantCfg: &tenant.ProviderConfig{APIPathPrefix: ptr("/openai/v1")},
			want:      "/openai/v1",
		},
		{
			name:      "tenant prefix applies where yaml states none",
			yaml:      nil,
			tenantCfg: &tenant.ProviderConfig{APIPathPrefix: ptr("")},
			want:      "",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			baseCfg := config.ProviderConfig{
				BaseURL:       "https://api.perplexity.ai",
				APIKey:        "yaml-key",
				APIFormat:     "openai",
				APIPathPrefix: tt.yaml,
			}

			got := resolveOrgConfig(baseCfg, "org-key", tt.tenantCfg)

			if got.EffectiveAPIPathPrefix() != tt.want {
				t.Errorf("EffectiveAPIPathPrefix() = %q, want %q",
					got.EffectiveAPIPathPrefix(), tt.want)
			}
			if got.APIKey != "org-key" {
				t.Errorf("APIKey = %q, want the org's key", got.APIKey)
			}
			if baseCfg.APIPathPrefix != tt.yaml {
				t.Error("resolveOrgConfig mutated the shared base config")
			}
		})
	}
}

// The endpoint the proxy actually builds, so an empty prefix on a YAML-backed
// provider stops sending requests to /v1.
func TestResolveOrgConfig_EmptyPrefixDropsVersionSegment(t *testing.T) {
	empty := ""
	v1 := "/v1"
	baseCfg := config.ProviderConfig{
		BaseURL:       "https://api.perplexity.ai",
		APIFormat:     "openai",
		APIPathPrefix: &v1,
	}

	orgCfg := resolveOrgConfig(baseCfg, "org-key", &tenant.ProviderConfig{APIPathPrefix: &empty})

	const want = "https://api.perplexity.ai/chat/completions"
	if got := orgCfg.EndpointURL("/v1/chat/completions"); got != want {
		t.Errorf("EndpointURL() = %q, want %q", got, want)
	}
}
