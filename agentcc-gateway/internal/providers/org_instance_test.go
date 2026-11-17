package providers

import (
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// IsOrgInstance tells an instance built from an org's own provider config
// (the org's credentials) from the gateway's own instance of the same
// provider (the gateway's credentials). An async job records which one ran.
func TestIsOrgInstance(t *testing.T) {
	base := config.ProviderConfig{BaseURL: "https://api.openai.com", APIKey: "platform-key", APIFormat: "openai"}
	platform, err := createProvider("openai", base)
	if err != nil {
		t.Fatal(err)
	}
	orgA, err := NewOrgProviderCache(map[string]config.ProviderConfig{"openai": base}, false).
		GetOrCreateWithTenantConfig("org-a", "openai", "org-a-key", &tenant.ProviderConfig{Enabled: true, APIKey: "org-a-key"})
	if err != nil {
		t.Fatal(err)
	}

	cases := []struct {
		name string
		p    Provider
		org  string
		want bool
	}{
		{"the org's own instance", orgA, "org-a", true},
		{"another org's instance", orgA, "org-b", false},
		{"an org instance, no org", orgA, "", false},
		{"the gateway's instance", platform, "org-a", false},
		{"the gateway's instance, no org", platform, "", false},
		{"no provider", nil, "org-a", false},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if got := IsOrgInstance(tc.p, tc.org); got != tc.want {
				t.Errorf("IsOrgInstance(%v, %q) = %v, want %v", tc.p, tc.org, got, tc.want)
			}
		})
	}
}
