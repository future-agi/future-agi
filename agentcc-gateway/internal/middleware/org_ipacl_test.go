package middleware

import (
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
)

// An org's ip_acl is enforced with the node's switch off only on a gateway
// that resolves client IPs (ResolvesClientIP); a gateway org configs can
// reach that sets no trusted proxy warns at start-up, naming the fix.
func TestResolvesClientIPAndOrgIPACLWarning(t *testing.T) {
	warnsOrg := func(cfg *config.Config) bool {
		for _, w := range TrustedProxyWarnings(cfg) {
			if strings.Contains(w, "org ip_acl is not enforced on this gateway: "+OrgIPACLClientIPUnknown) &&
				strings.Contains(w, "server.trusted_proxies") {
				return true
			}
		}
		return false
	}
	cfg := config.DefaultConfig()
	if ResolvesClientIP(cfg.Server) || warnsOrg(cfg) {
		t.Fatal("default config: resolves client IPs, or warns about org ip_acl with no org config source")
	}
	cfg.Admin.Token = "t"
	if !warnsOrg(cfg) {
		t.Fatalf("admin.token set, no trusted proxy: warnings %q, want the org ip_acl warning", TrustedProxyWarnings(cfg))
	}
	cfg.Admin.Token, cfg.ControlPlane.URL = "", "http://backend"
	if !warnsOrg(cfg) {
		t.Fatal("control_plane.url set, no trusted proxy: no org ip_acl warning")
	}
	cfg.IPACL.Enabled = true // the node's switch: node and org rules apply
	if warnsOrg(cfg) {
		t.Fatal("ip_acl.enabled: warned that org ip_acl is not enforced")
	}
	cfg.IPACL.Enabled = false
	for name, server := range map[string]config.ServerConfig{
		"trusted_proxies": {TrustedProxies: []string{"10.0.0.0/8"}},
		"trusted_hops":    {TrustedHops: 2},
	} {
		cfg.Server.TrustedProxies, cfg.Server.TrustedHops = server.TrustedProxies, server.TrustedHops
		if !ResolvesClientIP(cfg.Server) || warnsOrg(cfg) {
			t.Errorf("%s set: ResolvesClientIP %v, org warning %v; want true, false", name, ResolvesClientIP(cfg.Server), warnsOrg(cfg))
		}
	}
}
