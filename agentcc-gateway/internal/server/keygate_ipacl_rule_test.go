package server

import (
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/middleware"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// The KeyAuth gate's ip_acl (the key-gated routes) follows the rule the
// pipeline's follows (cmd/agentcc buildIPACLPlugin): the node's rules with
// ip_acl.enabled, otherwise orgs' rules only where
// middleware.OrgIPACLWithoutNodeSwitch holds and a tenant store exists.
func TestKeyGateIPACLFollowsTheSharedRule(t *testing.T) {
	stores := map[string]*tenant.Store{"no store": nil, "store": tenant.NewStore()}
	for _, src := range []string{"none", "control_plane", "admin_token"} {
		for _, proxy := range []string{"none", "trusted_proxies", "trusted_hops"} {
			for _, node := range []bool{false, true} {
				for storeName, store := range stores {
					cfg := &config.Config{}
					switch src {
					case "control_plane":
						cfg.ControlPlane.URL = "http://control-plane.test"
					case "admin_token":
						cfg.Admin.Token = "keygate-admin-token"
					}
					switch proxy {
					case "trusted_proxies":
						cfg.Server.TrustedProxies = []string{"10.0.0.0/8"}
					case "trusted_hops":
						cfg.Server.TrustedHops = 1
					}
					cfg.IPACL.Enabled = node
					want := node || (store != nil && middleware.OrgIPACLWithoutNodeSwitch(cfg))
					if got := keyGateIPACL(cfg, store) != nil; got != want {
						t.Errorf("%s/%s ip_acl.enabled=%v %s: gate ip_acl built = %v, want %v",
							src, proxy, node, storeName, got, want)
					}
				}
			}
		}
	}
}
