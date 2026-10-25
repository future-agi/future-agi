package ipacl

import (
	"context"
	"net"
	"net/http"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// CheckClient is what KeyAuth applies on every key-gated path: the gateway's
// ip_acl, then the key's org's ip_acl, then the key's allowed_ips, with the
// 403 each gives in the pipeline.
func TestCheckClient_AppliesEveryRuleThePipelineApplies(t *testing.T) {
	tenants := tenant.NewStore()
	tenants.Set("org-a", &tenant.OrgConfig{IPACL: &tenant.IPACLConfig{Enabled: true, Allow: []string{"198.51.100.0/24"}}})
	p := New(config.IPACLConfig{Enabled: true, Deny: []string{"198.51.100.66"}}, tenants)
	keyList := []string{"198.51.100.0/25"}

	cases := []struct {
		name    string
		ip      string
		orgID   string
		allowed []string
		want    string // "" for allowed
	}{
		{"no key, any address", "192.0.2.1", "", nil, ""},
		{"no key, globally denied", "198.51.100.66", "", nil, "Access denied: IP address is not allowed"},
		{"org key inside its org's list", "198.51.100.7", "org-a", nil, ""},
		{"org key outside its org's list", "192.0.2.1", "org-a", nil, "Access denied: IP address is not allowed"},
		{"org key, globally denied", "198.51.100.66", "org-a", nil, "Access denied: IP address is not allowed"},
		{"key inside its allowed_ips", "198.51.100.7", "org-a", keyList, ""},
		{"key inside its org's list, outside its allowed_ips", "198.51.100.200", "org-a", keyList, "API key is not authorized from this IP address"},
		{"unparsable address", "", "", keyList, "Client IP address could not be determined"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			err := p.CheckClient(net.ParseIP(tc.ip), tc.orgID, tc.allowed, "req-1")
			requireCheck(t, err, tc.want)
		})
	}
}

// A nil plugin at the gate, the pipeline without an IP ACL plugin, holds a
// key to its allowed_ips.
func TestCheckClient_NilPluginAppliesOnlyTheKeysAllowedIPs(t *testing.T) {
	var p *Plugin
	requireCheck(t, p.CheckClient(net.ParseIP("192.0.2.1"), "org-a", nil, ""), "")
	requireCheck(t, p.CheckClient(net.ParseIP("203.0.113.7"), "", []string{"203.0.113.0/24"}, ""), "")
	requireCheck(t, p.CheckClient(net.ParseIP("192.0.2.1"), "", []string{"203.0.113.0/24"}, ""),
		"API key is not authorized from this IP address")
}

// The pipeline's plugin and KeyAuth's each cache parsed org lists. A new org
// ACL from the tenant store (sync, or the admin API) takes effect on the next
// request whether or not anything calls InvalidateOrg.
func TestPlugin_OrgACLFollowsATenantStoreChange(t *testing.T) {
	tenants := tenant.NewStore()
	tenants.Set("org-a", &tenant.OrgConfig{IPACL: &tenant.IPACLConfig{Enabled: true, Allow: []string{"198.51.100.0/24"}}})
	p := New(config.IPACLConfig{Enabled: true}, tenants)

	process := func(ip string) *models.APIError {
		rc := newRC(ip)
		defer rc.Release()
		rc.Metadata[tenant.MetadataKeyOrgID] = "org-a"
		return p.ProcessRequest(context.Background(), rc).Error
	}

	requireCheck(t, process("198.51.100.7"), "")
	tenants.Set("org-a", &tenant.OrgConfig{IPACL: &tenant.IPACLConfig{Enabled: true, Allow: []string{"192.0.2.0/24"}}})
	requireCheck(t, process("198.51.100.7"), "Access denied: IP address is not allowed")
	requireCheck(t, process("192.0.2.1"), "")
}

func requireCheck(t *testing.T, err *models.APIError, want string) {
	t.Helper()
	if want == "" {
		if err != nil {
			t.Fatalf("refused: %d %s", err.Status, err.Message)
		}
		return
	}
	if err == nil || err.Status != http.StatusForbidden || err.Message != want {
		t.Fatalf("got %v, want 403 %q", err, want)
	}
}
