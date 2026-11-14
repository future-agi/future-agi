package server

import (
	"context"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/metrics"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
	"github.com/futureagi/agentcc-gateway/internal/video"
)

// Addresses used below. 10.0.0.0/8 is the allowlist; 192.0.2.0/24 is a client
// outside it. 10.200.0.1 is a proxy whose own address is inside the allowlist,
// 35.191.0.10 one outside it, and 34.120.0.1 a load balancer forwarding-rule
// address (GCP's external Application Load Balancer appends client, LB).
const (
	proxyInsideAllowlist  = "10.200.0.1"
	proxyOutsideAllowlist = "35.191.0.10"
	lbForwardingRule      = "34.120.0.1"
)

var videoRoutes = []string{"create", "list", "get", "content", "delete", "cancel"}

// proxiedVideoServer builds a real Server (so ip_acl is bound exactly as in
// production) that shares the fixture's video store and service.
func proxiedVideoServer(t *testing.T, f *videoHTTPFixture, cfg *config.Config) *Server {
	t.Helper()
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatal(err)
	}
	s := New(cfg, "", registry, f.s.handlers.engine, f.s.handlers.keyStore, nil, nil, metrics.NewRegistry(), testModelDBPtr(), f.s.handlers.tenantStore, nil)
	s.handlers.videoStore = f.store
	s.handlers.videoService = f.service
	s.handlers.videoSyncWait = 0
	t.Cleanup(func() { s.Shutdown(context.Background()) })
	return s
}

// videoRouteRequest sends one request on a video route from the given TCP
// peer, with an optional X-Forwarded-For value.
func videoRouteRequest(s *Server, route, id, key, peer, xff string) *httptest.ResponseRecorder {
	method, path, body := "GET", "/v1/videos/"+id, ""
	switch route {
	case "create":
		method, path, body = "POST", "/v1/videos", videoTestBody
	case "list":
		path = "/v1/videos"
	case "content":
		path += "/content"
	case "delete":
		method = "DELETE"
	case "cancel":
		method = "POST"
		path += "/cancel"
	}
	r := httptest.NewRequest(method, path, strings.NewReader(body))
	r.RemoteAddr = peer + ":12345"
	if xff != "" {
		r.Header.Set("X-Forwarded-For", xff)
	}
	r.Header.Set("Authorization", "Bearer "+key)
	r.Header.Set("Idempotency-Key", video.NewID())
	r.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	s.httpServer.Handler.ServeHTTP(w, r)
	return w
}

func videoRouteOK(route string, code int) bool {
	return code == 200 || (route == "create" && code == 202)
}

type proxyCase struct{ name, peer, xff string }

// Every case here must be refused; a trusted chain of two hops is configured.
var deniedBehindProxy = []proxyCase{
	{"denied client behind allowlisted proxy", proxyInsideAllowlist, "192.0.2.1, " + lbForwardingRule},
	{"caller-prepended allowed address ignored", proxyInsideAllowlist, "10.0.0.2, 192.0.2.1, " + lbForwardingRule},
	{"chain shorter than trusted hops judged by peer", "192.0.2.9", "10.0.0.2"},
	{"unparseable client entry refused", proxyInsideAllowlist, "not-an-ip, " + lbForwardingRule},
}

// TestReviewVideoACLBehindTrustedProxies: behind a load balancer the TCP peer
// is the proxy. Org and global allowlists must judge the client the trusted
// hops report, on every video route, and never a caller-supplied entry.
func TestReviewVideoACLBehindTrustedProxies(t *testing.T) {
	for _, scope := range []string{"global", "org"} {
		for _, route := range videoRoutes {
			t.Run(scope+"/"+route, func(t *testing.T) {
				f := newVideoHTTP(t)
				id := completedVideoFixture(t, f)
				cfg := f.s.cfg
				cfg.IPACL.TrustedProxies = 2
				if scope == "global" {
					cfg.IPACL.Enabled = true
					cfg.IPACL.Allow = []string{"10.0.0.0/8"}
				} else {
					f.s.handlers.tenantStore.Set("a", &tenant.OrgConfig{IPACL: &tenant.IPACLConfig{Enabled: true, Allow: []string{"10.0.0.0/8"}}})
				}
				s := proxiedVideoServer(t, f, cfg)
				for _, c := range deniedBehindProxy {
					if w := videoRouteRequest(s, route, id, f.keyA, c.peer, c.xff); w.Code != 403 {
						t.Errorf("%s: got %d %s", c.name, w.Code, w.Body.String())
					}
				}
				if _, err := f.store.GetForOrg(id, "a"); err != nil {
					t.Fatal("refused request changed the job", err)
				}
				// Allowed client through a proxy whose own address is not allowed.
				if w := videoRouteRequest(s, route, id, f.keyA, proxyOutsideAllowlist, "10.0.0.2, "+lbForwardingRule); !videoRouteOK(route, w.Code) {
					t.Errorf("allowed client behind proxy got %d %s", w.Code, w.Body.String())
				}
			})
		}
	}
}

// TestReviewVideoDenyListBehindProxy: a deny entry must match the real client
// behind a single trusted proxy, not the proxy's own address.
func TestReviewVideoDenyListBehindProxy(t *testing.T) {
	for _, route := range videoRoutes {
		t.Run(route, func(t *testing.T) {
			f := newVideoHTTP(t)
			id := completedVideoFixture(t, f)
			cfg := f.s.cfg
			cfg.IPACL.TrustedProxies = 1
			f.s.handlers.tenantStore.Set("a", &tenant.OrgConfig{IPACL: &tenant.IPACLConfig{Enabled: true, Deny: []string{"192.0.2.0/24"}}})
			s := proxiedVideoServer(t, f, cfg)
			if w := videoRouteRequest(s, route, id, f.keyA, proxyInsideAllowlist, "192.0.2.1"); w.Code != 403 {
				t.Errorf("denied client behind proxy got %d %s", w.Code, w.Body.String())
			}
			if w := videoRouteRequest(s, route, id, f.keyA, proxyInsideAllowlist, "198.51.100.7"); !videoRouteOK(route, w.Code) {
				t.Errorf("other client behind proxy got %d %s", w.Code, w.Body.String())
			}
		})
	}
}

// TestReviewVideoKeyAllowedIPsBehindProxy: a key restricted to an address
// range is judged by the client the trusted hops report.
func TestReviewVideoKeyAllowedIPsBehindProxy(t *testing.T) {
	for _, route := range videoRoutes {
		t.Run(route, func(t *testing.T) {
			f := newVideoHTTP(t)
			id := completedVideoFixture(t, f)
			bound, raw := f.s.handlers.keyStore.Create("ip-bound", "owner", nil, nil, map[string]string{"org_id": "a"})
			bound.AllowedIPs = []string{"10.0.0.0/8"}
			cfg := f.s.cfg
			cfg.IPACL.TrustedProxies = 2
			s := proxiedVideoServer(t, f, cfg)
			if w := videoRouteRequest(s, route, id, raw, proxyInsideAllowlist, "192.0.2.1, "+lbForwardingRule); w.Code != 403 {
				t.Errorf("key used from outside its range got %d %s", w.Code, w.Body.String())
			}
			if w := videoRouteRequest(s, route, id, raw, proxyOutsideAllowlist, "10.0.0.2, "+lbForwardingRule); !videoRouteOK(route, w.Code) {
				t.Errorf("key used from inside its range got %d %s", w.Code, w.Body.String())
			}
		})
	}
}

// TestReviewVideoIgnoresForwardedForByDefault: with no trusted proxies
// configured, X-Forwarded-For is caller-controlled and must not change the
// decision in either direction.
func TestReviewVideoIgnoresForwardedForByDefault(t *testing.T) {
	for _, route := range []string{"create", "get"} {
		t.Run(route, func(t *testing.T) {
			f := newVideoHTTP(t)
			id := completedVideoFixture(t, f)
			f.s.handlers.tenantStore.Set("a", &tenant.OrgConfig{IPACL: &tenant.IPACLConfig{Enabled: true, Allow: []string{"10.0.0.0/8"}}})
			s := proxiedVideoServer(t, f, f.s.cfg)
			if w := videoRouteRequest(s, route, id, f.keyA, "192.0.2.1", "10.0.0.2"); w.Code != 403 {
				t.Errorf("spoofed X-Forwarded-For accepted: %d %s", w.Code, w.Body.String())
			}
			if w := videoRouteRequest(s, route, id, f.keyA, "10.0.0.2", "192.0.2.1"); !videoRouteOK(route, w.Code) {
				t.Errorf("allowed peer refused because of X-Forwarded-For: %d %s", w.Code, w.Body.String())
			}
		})
	}
}
