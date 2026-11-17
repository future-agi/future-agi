package providers

import (
	"context"
	"errors"
	"net"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/netguard"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

func TestProviderValidationMissingEndpoint(t *testing.T) {
	cache := NewOrgProviderCache(nil, false)
	p, err := cache.GetOrCreateWithTenantConfig("org-test", "typo-provider", "synthetic", &tenant.ProviderConfig{APIFormat: "openai"})
	if err == nil || p != nil || !strings.Contains(err.Error(), "typo-provider") || !strings.Contains(err.Error(), "base_url") {
		t.Fatalf("missing endpoint must name provider and base_url, provider=%v err=%v", p, err)
	}
}

func TestProviderValidationOperatorAliasStillInherits(t *testing.T) {
	cache := NewOrgProviderCache(map[string]config.ProviderConfig{
		"operator-openai": {APIFormat: "openai", BaseURL: "https://public.invalid"},
	}, false)
	p, err := cache.GetOrCreateWithTenantConfig("org-test", "operator-openai", "synthetic", &tenant.ProviderConfig{APIFormat: "openai"})
	if err != nil {
		t.Fatal(err)
	}
	defer p.Close()
}

func TestProviderValidationHostedCannotAllowPrivate(t *testing.T) {
	stubLookupIP(t, map[string][]string{"private.invalid": {"10.2.3.4"}, "host.docker.internal": {"93.184.216.34"}, "localhost": {"93.184.216.34"}})
	for _, deployment := range []string{"US", " eu ", "DEV"} {
		t.Run(deployment, func(t *testing.T) {
			t.Setenv("CLOUD_DEPLOYMENT", deployment)
			cache := NewOrgProviderCache(nil, true)
			for _, baseURL := range []string{"http://private.invalid", "http://10.2.3.4", "http://[fd00::1]", "http://host.docker.internal", "http://localhost"} {
				p, err := cache.GetOrCreateWithTenantConfig("org-test", baseURL, "synthetic", &tenant.ProviderConfig{BaseURL: baseURL, APIFormat: "openai"})
				if err == nil {
					p.Close()
					t.Errorf("hosted accepted %s with private opt-in", baseURL)
				}
			}
			if cache.allowPrivateBaseURLs {
				t.Error("hosted dialer would inherit the self-hosted private opt-in")
			}
		})
	}
}

func TestProviderValidationSelfHostedKeepsPrivateOptIn(t *testing.T) {
	t.Setenv("CLOUD_DEPLOYMENT", "false")
	stubLookupIP(t, map[string][]string{"host.docker.internal": {"192.168.65.254"}})
	cache := NewOrgProviderCache(nil, true)
	p, err := cache.GetOrCreateWithTenantConfig("org-test", "local", "synthetic", &tenant.ProviderConfig{BaseURL: "http://host.docker.internal:11434", APIFormat: "openai"})
	if err != nil {
		t.Fatal(err)
	}
	defer p.Close()
}

// The preflight sees a public IP. The actual dial resolves localhost to
// loopback: the guard must check that resolved address before opening a socket.
func TestProviderValidationDialRechecksResolvedAddress(t *testing.T) {
	t.Setenv("CLOUD_DEPLOYMENT", "US")
	stubLookupIP(t, map[string][]string{"rebind.invalid": {"93.184.216.34"}})
	if err := validateBaseURL("http://rebind.invalid", false); err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()
	conn, err := orgDialContext(false)(ctx, "tcp", net.JoinHostPort("localhost", "9"))
	if conn != nil {
		conn.Close()
		t.Fatal("dial opened a loopback socket")
	}
	var blocked *netguard.BlockedError
	if !errors.As(err, &blocked) {
		t.Fatalf("wanted a dial-time address refusal, got %v", err)
	}
}
