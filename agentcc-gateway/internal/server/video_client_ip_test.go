package server

import (
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// allowOnlyTenNet turns on an allow list of 10.0.0.0/8: the node's ip_acl
// (scope "global") or org a's (scope "org").
func allowOnlyTenNet(f *videoHTTPFixture, cfg *config.Config, scope string) {
	if scope == "global" {
		cfg.IPACL.Enabled = true
		cfg.IPACL.Allow = []string{"10.0.0.0/8"}
		return
	}
	f.s.handlers.tenantStore.Set("a", &tenant.OrgConfig{IPACL: &tenant.IPACLConfig{Enabled: true, Allow: []string{"10.0.0.0/8"}}})
}

// The video routes take the client address from the gateway's own proxy
// settings (server.trusted_hops or server.trusted_proxies) when either is set,
// like every other route, and ip_acl.trusted_proxies is then not read: here it
// names one hop, which would judge the load balancer's address, and every
// request is still judged by the server setting. Behind a load balancer an
// allowed client is served and a refused one is refused, on every video route,
// under the global and an org's allow list.
//
// With only ip_acl.trusted_proxies set, the video routes read it
// (handlers_video_proxy_review_test.go); with neither, they judge the TCP peer
// (TestVideoRoutesJudgeThePeerWhenNoProxyIsNamed).
func TestVideoRoutesJudgeTheClientTheServerProxySettingsReport(t *testing.T) {
	settings := []struct {
		name string
		set  func(*config.ServerConfig)
	}{
		{"trusted_hops", func(s *config.ServerConfig) { s.TrustedHops = 2 }},
		{"trusted_proxies", func(s *config.ServerConfig) {
			s.TrustedProxies = []string{proxyInsideAllowlist + "/32", proxyOutsideAllowlist + "/32", lbForwardingRule + "/32"}
		}},
	}
	for _, setting := range settings {
		for _, scope := range []string{"global", "org"} {
			for _, route := range videoRoutes {
				t.Run(setting.name+"/"+scope+"/"+route, func(t *testing.T) {
					f := newVideoHTTP(t)
					id := completedVideoFixture(t, f)
					cfg := f.s.cfg
					setting.set(&cfg.Server)
					cfg.IPACL.TrustedProxies = 1
					allowOnlyTenNet(f, cfg, scope)
					s := proxiedVideoServer(t, f, cfg)
					for _, c := range deniedBehindProxy {
						if w := videoRouteRequest(s, route, id, f.keyA, c.peer, c.xff); w.Code != 403 {
							t.Errorf("%s: got %d %s", c.name, w.Code, w.Body.String())
						}
					}
					if w := videoRouteRequest(s, route, id, f.keyA, proxyOutsideAllowlist, "10.0.0.2, "+lbForwardingRule); !videoRouteOK(route, w.Code) {
						t.Errorf("allowed client behind the proxies got %d %s", w.Code, w.Body.String())
					}
				})
			}
		}
	}
}

// With neither the gateway's proxy settings nor ip_acl.trusted_proxies set,
// the video routes judge the TCP peer: an X-Forwarded-For entry changes no
// decision, on every video route, under the global and an org's allow list.
func TestVideoRoutesJudgeThePeerWhenNoProxyIsNamed(t *testing.T) {
	for _, scope := range []string{"global", "org"} {
		for _, route := range videoRoutes {
			t.Run(scope+"/"+route, func(t *testing.T) {
				f := newVideoHTTP(t)
				id := completedVideoFixture(t, f)
				cfg := f.s.cfg
				allowOnlyTenNet(f, cfg, scope)
				s := proxiedVideoServer(t, f, cfg)
				if w := videoRouteRequest(s, route, id, f.keyA, "192.0.2.1", "10.0.0.2"); w.Code != 403 {
					t.Errorf("a refused peer naming an allowed address got %d %s", w.Code, w.Body.String())
				}
				if w := videoRouteRequest(s, route, id, f.keyA, "10.0.0.2", "192.0.2.1"); !videoRouteOK(route, w.Code) {
					t.Errorf("an allowed peer naming a refused address got %d %s", w.Code, w.Body.String())
				}
			})
		}
	}
}
