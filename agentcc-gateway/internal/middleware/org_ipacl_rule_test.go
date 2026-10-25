package middleware

import (
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
)

// ipRuleConfigs are the configurations that decide where org configs come
// from and whether the node resolves client IPs.
func ipRuleConfigs() map[string]*config.Config {
	cfgs := map[string]*config.Config{}
	for _, src := range []string{"none", "control_plane", "admin_token", "both"} {
		for _, proxy := range []string{"none", "trusted_proxies", "trusted_hops"} {
			c := &config.Config{}
			if src == "control_plane" || src == "both" {
				c.ControlPlane.URL = "http://control-plane.test"
			}
			if src == "admin_token" || src == "both" {
				c.Admin.Token = "test-admin-token"
			}
			switch proxy {
			case "trusted_proxies":
				c.Server.TrustedProxies = []string{"10.0.0.0/8"}
			case "trusted_hops":
				c.Server.TrustedHops = 1
			}
			cfgs[src+"/"+proxy] = c
		}
	}
	return cfgs
}

// One rule decides where an org's ip_acl applies with the node's ip_acl
// switch off: org configs can reach the node (control_plane.url or
// admin.token) and it resolves client IPs (server.trusted_proxies or
// trusted_hops). The start-up warning names the fix exactly where org
// configs arrive and the rule does not hold.
func TestOrgIPACLWithoutNodeSwitch(t *testing.T) {
	for name, cfg := range ipRuleConfigs() {
		arrive := !strings.HasPrefix(name, "none/")
		resolves := !strings.HasSuffix(name, "/none")
		if got := cfg.OrgConfigsArrive(); got != arrive {
			t.Errorf("%s: OrgConfigsArrive = %v, want %v", name, got, arrive)
		}
		if got := OrgIPACLWithoutNodeSwitch(cfg); got != (arrive && resolves) {
			t.Errorf("%s: OrgIPACLWithoutNodeSwitch = %v, want %v", name, got, arrive && resolves)
		}
		warned := false
		for _, w := range TrustedProxyWarnings(cfg) {
			warned = warned || strings.HasPrefix(w, "org ip_acl is not enforced on this gateway")
		}
		if want := cfg.OrgConfigsArrive() && !OrgIPACLWithoutNodeSwitch(cfg); warned != want {
			t.Errorf("%s: org ip_acl warning = %v, want %v", name, warned, want)
		}
		cfg.IPACL.Enabled = true
		for _, w := range TrustedProxyWarnings(cfg) {
			if strings.HasPrefix(w, "org ip_acl is not enforced on this gateway") {
				t.Errorf("%s with ip_acl.enabled: warns %q", name, w)
			}
		}
	}
}
