package middleware

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
)

// ClientIP reads X-Forwarded-For only from a proxy the gateway trusts and never
// reads X-Real-IP. These pin the trusted-proxy rules.

func clientIPRequest(remoteAddr string, headers map[string][]string) *http.Request {
	r := httptest.NewRequest(http.MethodPost, "/v1/files", nil)
	r.RemoteAddr = remoteAddr
	for name, values := range headers {
		for _, v := range values {
			r.Header.Add(name, v)
		}
	}
	return r
}

func resolveThroughMiddleware(t *testing.T, resolver *ClientIPResolver, r *http.Request) string {
	t.Helper()
	var got string
	TrustedClientIP(resolver)(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		got = ClientIP(r)
	})).ServeHTTP(httptest.NewRecorder(), r)
	return got
}

func mustResolver(t *testing.T, server config.ServerConfig) *ClientIPResolver {
	t.Helper()
	resolver, err := NewClientIPResolver(server)
	if err != nil {
		t.Fatalf("NewClientIPResolver(%+v): %v", server, err)
	}
	return resolver
}

func TestClientIP_DefaultIgnoresForwardedHeaders(t *testing.T) {
	forged := map[string][]string{
		"X-Forwarded-For": {"203.0.113.7"},
		"X-Real-IP":       {"203.0.113.8"},
	}
	for _, tc := range []struct{ remoteAddr, want string }{
		{"192.0.2.99:41234", "192.0.2.99"},
		{"[2001:db8::1]:41234", "2001:db8::1"},
		{"192.0.2.99", "192.0.2.99"}, // no port
	} {
		r := clientIPRequest(tc.remoteAddr, forged)
		// No resolver configured (the default), a nil resolver, a resolver
		// from an empty server config, and a request that skipped the
		// middleware altogether: all use the TCP peer.
		for name, got := range map[string]string{
			"default config":      resolveThroughMiddleware(t, mustResolver(t, config.DefaultConfig().Server), r),
			"nil resolver":        resolveThroughMiddleware(t, nil, r),
			"without middleware":  ClientIP(r),
			"zero-value resolver": (&ClientIPResolver{}).Resolve(r),
		} {
			if got != tc.want {
				t.Errorf("%s, peer %s with forged X-Forwarded-For and X-Real-IP: client %q, want the peer %q", name, tc.remoteAddr, got, tc.want)
			}
		}
	}
}

func TestClientIP_TrustedProxiesWalkTheChainRightToLeft(t *testing.T) {
	resolver := mustResolver(t, config.ServerConfig{TrustedProxies: []string{"10.0.0.0/8", "35.191.0.0/16", "192.0.2.10", "2001:db8:ffff::/48"}})
	for _, tc := range []struct {
		name, remoteAddr string
		xff              []string
		want             string
	}{
		{"one trusted proxy appended the client", "10.1.2.3:5555", []string{"203.0.113.7"}, "203.0.113.7"},
		{"a forged entry left of the appended client is ignored", "10.1.2.3:5555", []string{"198.51.100.1, 203.0.113.7"}, "203.0.113.7"},
		{"two trusted hops: load balancer then ingress", "10.1.2.3:5555", []string{"198.51.100.1, 203.0.113.7, 35.191.4.5"}, "203.0.113.7"},
		{"a caller forging a trusted address is still placed at its own", "10.1.2.3:5555", []string{"10.9.9.9, 203.0.113.7"}, "203.0.113.7"},
		{"repeated headers are one list", "10.1.2.3:5555", []string{"198.51.100.1", "203.0.113.7"}, "203.0.113.7"},
		{"every entry trusted: the leftmost", "10.1.2.3:5555", []string{"10.4.4.4, 10.5.5.5"}, "10.4.4.4"},
		{"no header from a trusted peer: the peer", "10.1.2.3:5555", nil, "10.1.2.3"},
		{"an untrusted peer's header is ignored", "192.0.2.99:5555", []string{"203.0.113.7"}, "192.0.2.99"},
		{"IPv6 proxy and client", "[2001:db8:ffff::1]:5555", []string{"2001:db8:1::7"}, "2001:db8:1::7"},
		// A Google Cloud external Application Load Balancer connects from a
		// GFE range and appends "<client>,<forwarding-rule IP>".
		{"GCP load balancer: the forwarding-rule IP is skipped", "35.191.7.8:5555", []string{"198.51.100.1, 203.0.113.7,192.0.2.10"}, "203.0.113.7"},
		{"an entry that is not an address stays unparseable", "10.1.2.3:5555", []string{"unknown"}, "unknown"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			got := resolveThroughMiddleware(t, resolver, clientIPRequest(tc.remoteAddr, map[string][]string{"X-Forwarded-For": tc.xff}))
			if got != tc.want {
				t.Fatalf("peer %s, X-Forwarded-For %q: client %q, want %q", tc.remoteAddr, tc.xff, got, tc.want)
			}
		})
	}
}

func TestClientIP_TrustedProxiesNeverReadXRealIP(t *testing.T) {
	resolver := mustResolver(t, config.ServerConfig{TrustedProxies: []string{"10.0.0.0/8"}})
	r := clientIPRequest("10.1.2.3:5555", map[string][]string{"X-Real-IP": {"203.0.113.7"}})
	if got := resolveThroughMiddleware(t, resolver, r); got != "10.1.2.3" {
		t.Fatalf("X-Real-IP from a trusted peer, no X-Forwarded-For: client %q, want the peer", got)
	}
}

func TestClientIP_TrustedHopsTakeTheNthEntryFromTheRight(t *testing.T) {
	resolver := mustResolver(t, config.ServerConfig{TrustedHops: 2})
	for _, tc := range []struct {
		xff  []string
		want string
	}{
		{[]string{"203.0.113.7, 35.191.4.5"}, "203.0.113.7"},
		{[]string{"198.51.100.1, 203.0.113.7, 35.191.4.5"}, "203.0.113.7"},
		{[]string{"203.0.113.7"}, "192.0.2.99"}, // fewer entries than hops: not through the proxies
		{nil, "192.0.2.99"},
	} {
		got := resolveThroughMiddleware(t, resolver, clientIPRequest("192.0.2.99:5555", map[string][]string{"X-Forwarded-For": tc.xff}))
		if got != tc.want {
			t.Errorf("trusted_hops 2, X-Forwarded-For %q: client %q, want %q", tc.xff, got, tc.want)
		}
	}
}

func TestNewClientIPResolver_RefusesWhatValidateRefuses(t *testing.T) {
	for _, server := range []config.ServerConfig{
		{TrustedProxies: []string{"10.0.0.0/33"}},
		{TrustedProxies: []string{"load-balancer"}},
		{TrustedHops: -1},
		{TrustedProxies: []string{"10.0.0.0/8"}, TrustedHops: 1},
	} {
		if _, err := NewClientIPResolver(server); err == nil {
			t.Errorf("NewClientIPResolver(%+v): no error", server)
		}
	}
}

func TestTrustedProxyWarnings(t *testing.T) {
	warnsAboutPeer := func(cfg *config.Config) bool {
		for _, w := range TrustedProxyWarnings(cfg) {
			if strings.Contains(w, "server.trusted_proxies is empty") {
				return true
			}
		}
		return false
	}

	cfg := config.DefaultConfig()
	if got := TrustedProxyWarnings(cfg); len(got) != 0 {
		t.Fatalf("default config: warnings %q, want none", got)
	}

	cfg.IPACL.Enabled = true
	if !warnsAboutPeer(cfg) {
		t.Fatal("ip_acl enabled with no trusted proxy: no warning")
	}
	cfg.Server.TrustedProxies = []string{"10.0.0.0/8"}
	if warnsAboutPeer(cfg) {
		t.Fatal("ip_acl enabled with trusted proxies: warned")
	}

	cfg = config.DefaultConfig()
	cfg.Auth.Keys = []config.AuthKeyConfig{{Name: "k", Key: "sk-x", AllowedIPs: []string{"203.0.113.0/24"}}}
	if !warnsAboutPeer(cfg) {
		t.Fatal("a configured key with allowed_ips and no trusted proxy: no warning")
	}
	cfg.Server.TrustedHops = 1
	if warnsAboutPeer(cfg) {
		t.Fatal("allowed_ips with trusted_hops: warned")
	}

	cfg = config.DefaultConfig()
	cfg.IPACL.TrustedProxies = 1
	got := TrustedProxyWarnings(cfg)
	if len(got) != 1 || !strings.Contains(got[0], "ip_acl.trusted_proxies is not read") {
		t.Fatalf("ip_acl.trusted_proxies set: warnings %q, want the not-read warning", got)
	}
}
