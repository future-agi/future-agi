package main

import (
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/middleware"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// The pipeline's ip_acl and GET /-/config's org_sections_not_enforced follow
// the rule the KeyAuth gate follows (server keyGateIPACL): the node's rules
// with ip_acl.enabled, otherwise orgs' rules only where
// middleware.OrgIPACLWithoutNodeSwitch holds; where org configs arrive and it
// does not, ip_acl is reported not enforced.
func TestPipelineIPACLFollowsTheSharedRule(t *testing.T) {
	store := tenant.NewStore()
	for _, src := range []string{"none", "control_plane", "admin_token"} {
		for _, proxy := range []string{"none", "trusted_proxies", "trusted_hops"} {
			for _, node := range []bool{false, true} {
				cfg := &config.Config{}
				switch src {
				case "control_plane":
					cfg.ControlPlane.URL = "http://control-plane.test"
				case "admin_token":
					cfg.Admin.Token = "rule-admin-token"
				}
				switch proxy {
				case "trusted_proxies":
					cfg.Server.TrustedProxies = []string{"10.0.0.0/8"}
				case "trusted_hops":
					cfg.Server.TrustedHops = 1
				}
				cfg.IPACL.Enabled = node
				rule := middleware.OrgIPACLWithoutNodeSwitch(cfg)
				if got, want := buildIPACLPlugin(cfg, store) != nil, node || rule; got != want {
					t.Errorf("%s/%s ip_acl.enabled=%v: pipeline ip_acl built = %v, want %v", src, proxy, node, got, want)
				}
				_, reported := orgSectionsNotEnforced(cfg)["ip_acl"]
				if want := !node && cfg.OrgConfigsArrive() && !rule; reported != want {
					t.Errorf("%s/%s ip_acl.enabled=%v: ip_acl reported not enforced = %v, want %v", src, proxy, node, reported, want)
				}
			}
		}
	}
}
