package server

import (
	"bytes"
	"context"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/metrics"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// On a gateway with ip_acl on, as these tests build it, KeyAuth applies a
// key's allowed_ips, its org's ip_acl and the gateway's global ip_acl on every
// key-gated path (files, videos, responses, async and scheduled jobs, MCP tool
// calls, A2A and every other /v1 route), from the client IP the pipeline
// derives.
//
// These tests build the server with an empty pipeline, so only the gate can
// refuse, and walk every route KeyAuth gates: a route added later is covered
// without editing a list.

const (
	ipGateOrg = "org-ip-gate"

	ipGateKeyListed = "sk-agentcc-ip-gate-listed" // allowed_ips 203.0.113.0/24
	ipGateKeyOrg    = "sk-agentcc-ip-gate-org"    // its org allows 198.51.100.0/24
	ipGateKeyOpen   = "sk-agentcc-ip-gate-open"   // no restriction of its own

	ipInKeyList    = "203.0.113.7"
	ipInOrgList    = "198.51.100.7"
	ipOutside      = "192.0.2.99"
	ipGlobalDenied = "192.0.2.200"

	keyIPRefusal = "API key is not authorized from this IP address"
	aclRefusal   = "Access denied: IP address is not allowed"
)

// newIPGateServer builds the gateway with request auth on, the global ip_acl
// on with one denied address, every optional
// route group enabled, and an org whose ip_acl allows one network.
func newIPGateServer(t *testing.T, authEnabled bool, configure ...func(*config.Config)) *Server {
	t.Helper()
	return newIPGateServerWithEngine(t, authEnabled, pipeline.NewEngine(), configure...)
}

func newIPGateServerWithEngine(t *testing.T, authEnabled bool, engine *pipeline.Engine, configure ...func(*config.Config)) *Server {
	t.Helper()
	mock := startMockOpenAI(t)
	t.Cleanup(mock.Close)

	cfg := config.DefaultConfig()
	cfg.Admin.Token = canonAdminToken
	cfg.Auth.Enabled = authEnabled
	cfg.Auth.Keys = []config.AuthKeyConfig{
		{Name: "listed", Key: ipGateKeyListed, Owner: "a", KeyType: "internal", AllowedIPs: []string{"203.0.113.0/24"}},
		{Name: "org", Key: ipGateKeyOrg, Owner: "b", KeyType: "internal", Metadata: map[string]string{"org_id": ipGateOrg}},
		{Name: "open", Key: ipGateKeyOpen, Owner: "c", KeyType: "internal"},
	}
	cfg.IPACL = config.IPACLConfig{Enabled: true, Deny: []string{ipGlobalDenied}}
	cfg.A2A.Enabled = true
	cfg.A2A.Card = config.A2ACardConfig{Name: "ip-gate", Description: "ip-gate", Version: "1.0.0"}
	cfg.MCP.Enabled = true
	cfg.Routing.Scheduled.Enabled = true
	cfg.Providers["openai"] = config.ProviderConfig{BaseURL: mock.URL, APIFormat: "openai", Models: []string{"gpt-4o"}}

	for _, c := range configure {
		c(cfg)
	}

	tenants := tenant.NewStore()
	tenants.Set(ipGateOrg, &tenant.OrgConfig{IPACL: &tenant.IPACLConfig{Enabled: true, Allow: []string{"198.51.100.0/24"}}})

	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatalf("creating registry: %v", err)
	}
	srv := New(cfg, "", registry, engine, nil, nil, nil, metrics.NewRegistry(), testModelDBPtr(), tenants, nil)
	srv.ready.Store(true)
	return srv
}

// ipGatedRoutes returns every registered route KeyAuth gates: /v1 (the
// /v1beta GenAI routes are left to the pipeline's auth plugin), /mcp and /a2a.
func ipGatedRoutes(t *testing.T, srv *Server) []Route {
	t.Helper()
	var out []Route
	for _, rt := range srv.router.Routes() {
		p := rt.Pattern
		if strings.HasPrefix(p, "/v1/") || p == "/mcp" || strings.HasPrefix(p, "/mcp/") || p == "/a2a" {
			out = append(out, rt)
		}
	}
	requireExemplars(t, out,
		"POST /v1/files", "GET /v1/files/{file_id}/content",
		"POST /v1/videos", "GET /v1/videos/{video_id}",
		"POST /v1/responses", "GET /v1/responses/{id}",
		"GET /v1/async/{job_id}", "POST /v1/scheduled",
		"POST /mcp", "GET /mcp", "POST /a2a", "POST /v1/a2a",
		"POST /v1/chat/completions",
	)
	return out
}

// ipGateBody is an MCP tool call, which A2A also reads as a JSON-RPC request
// and every other route as a JSON body.
const ipGateBody = `{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"probe","arguments":{}},"model":"gpt-4o","messages":[{"role":"user","content":"hi"}]}`

func serveFrom(t *testing.T, srv *Server, method, target, remoteIP, forwardedFor, key string) *httptest.ResponseRecorder {
	t.Helper()
	return serveWithHeader(t, srv, method, target, remoteIP, "X-Forwarded-For", forwardedFor, key)
}

func requireIPRefusal(t *testing.T, w *httptest.ResponseRecorder, message, what string) {
	t.Helper()
	if w.Code != http.StatusForbidden || !strings.Contains(w.Body.String(), message) {
		t.Fatalf("%s: status %d, want 403 %q. Body: %s", what, w.Code, message, w.Body.String())
	}
}

func requireNoIPRefusal(t *testing.T, w *httptest.ResponseRecorder, what string) {
	t.Helper()
	body := w.Body.String()
	if strings.Contains(body, keyIPRefusal) || strings.Contains(body, aclRefusal) {
		t.Fatalf("%s: refused for its IP address (status %d). Body: %s", what, w.Code, body)
	}
	if w.Code == http.StatusUnauthorized {
		t.Fatalf("%s: status 401, the key was not accepted. Body: %s", what, body)
	}
}

func TestKeyGate_EnforcesIPRestrictionsOnEveryGatedRoute(t *testing.T) {
	srv := newIPGateServer(t, true)

	for _, rt := range ipGatedRoutes(t, srv) {
		target := concretePath(rt.Pattern)
		name := rt.Method + " " + rt.Pattern
		t.Run(name, func(t *testing.T) {
			// A key's allowed_ips.
			requireIPRefusal(t, serveFrom(t, srv, rt.Method, target, ipOutside, "", ipGateKeyListed), keyIPRefusal,
				name+" with the listed key from outside its allowed_ips")
			requireNoIPRefusal(t, serveFrom(t, srv, rt.Method, target, ipInKeyList, "", ipGateKeyListed),
				name+" with the listed key from inside its allowed_ips")

			// The key's org's ip_acl.
			requireIPRefusal(t, serveFrom(t, srv, rt.Method, target, ipOutside, "", ipGateKeyOrg), aclRefusal,
				name+" with the org key from outside its org's ip_acl")
			requireNoIPRefusal(t, serveFrom(t, srv, rt.Method, target, ipInOrgList, "", ipGateKeyOrg),
				name+" with the org key from inside its org's ip_acl")

			// The gateway's global ip_acl.
			requireIPRefusal(t, serveFrom(t, srv, rt.Method, target, ipGlobalDenied, "", ipGateKeyOpen), aclRefusal,
				name+" with an unrestricted key from a globally denied address")
			requireNoIPRefusal(t, serveFrom(t, srv, rt.Method, target, ipOutside, "", ipGateKeyOpen),
				name+" with an unrestricted key from any other address")
		})
	}

	// Positive control: from an allowed address each key reaches the handler.
	for key, ip := range map[string]string{ipGateKeyListed: ipInKeyList, ipGateKeyOrg: ipInOrgList, ipGateKeyOpen: ipOutside} {
		if w := serveFrom(t, srv, http.MethodGet, "/v1/files", ip, "", key); w.Code != http.StatusOK {
			t.Fatalf("GET /v1/files with %s from %s: status %d, want 200. Body: %s", key, ip, w.Code, w.Body.String())
		}
		if w := serveFrom(t, srv, http.MethodPost, "/v1/chat/completions", ip, "", key); w.Code != http.StatusOK {
			t.Fatalf("POST /v1/chat/completions with %s from %s: status %d, want 200. Body: %s", key, ip, w.Code, w.Body.String())
		}
	}
}

// With no trusted proxy configured (the default) the gate reads neither
// X-Forwarded-For nor X-Real-IP: the client is the TCP peer, so such a header
// never passes a key's allowed_ips, an org's ip_acl or the global ip_acl.
func TestKeyGate_UntrustedForwardedHeadersCannotPassTheIPRules(t *testing.T) {
	srv := newIPGateServer(t, true)
	for _, rt := range ipGatedRoutes(t, srv) {
		target := concretePath(rt.Pattern)
		name := rt.Method + " " + rt.Pattern
		t.Run(name, func(t *testing.T) {
			for _, forge := range []struct{ header, value string }{
				{"X-Forwarded-For", ipInKeyList},
				{"X-Forwarded-For", ipInKeyList + ", " + ipInOrgList},
				{"X-Real-IP", ipInKeyList},
			} {
				w := serveWithHeader(t, srv, rt.Method, target, ipOutside, forge.header, forge.value, ipGateKeyListed)
				requireIPRefusal(t, w, keyIPRefusal, name+" from outside allowed_ips with "+forge.header+": "+forge.value)
			}
			for _, forge := range []struct{ header, value string }{
				{"X-Forwarded-For", ipInOrgList},
				{"X-Real-IP", ipInOrgList},
			} {
				w := serveWithHeader(t, srv, rt.Method, target, ipOutside, forge.header, forge.value, ipGateKeyOrg)
				requireIPRefusal(t, w, aclRefusal, name+" from outside the org's ip_acl with "+forge.header+": "+forge.value)
			}
			// A globally denied peer cannot claim another address either.
			w := serveWithHeader(t, srv, rt.Method, target, ipGlobalDenied, "X-Forwarded-For", ipOutside, ipGateKeyOpen)
			requireIPRefusal(t, w, aclRefusal, name+" from a globally denied address with X-Forwarded-For: "+ipOutside)
		})
	}

	// Positive control: the same keys from their allowed peers, with a
	// header naming a refused address, are not refused: the header is ignored
	// both ways.
	requireNoIPRefusal(t, serveWithHeader(t, srv, http.MethodGet, "/v1/files", ipInKeyList, "X-Forwarded-For", ipOutside, ipGateKeyListed),
		"GET /v1/files from inside allowed_ips with X-Forwarded-For outside it")
	requireNoIPRefusal(t, serveWithHeader(t, srv, http.MethodGet, "/v1/files", ipInOrgList, "X-Real-IP", ipGlobalDenied, ipGateKeyOrg),
		"GET /v1/files from inside the org's ip_acl with X-Real-IP globally denied")
}

// ipGateProxy is the load balancer the trusted-proxy tests put in front of
// the gateway.
const ipGateProxy = "10.20.30.40"

// Behind a trusted proxy the client is the X-Forwarded-For entry the proxy
// appended: the rightmost one that is not a trusted proxy. Entries left of it
// are the caller's own and are ignored, and a peer that is not a trusted proxy
// is still the client whatever header it sends.
func TestKeyGate_TrustedProxyChainResolvesTheClient(t *testing.T) {
	srv := newIPGateServer(t, true, func(cfg *config.Config) {
		cfg.Server.TrustedProxies = []string{"10.0.0.0/8"}
	})
	for _, target := range []string{"/v1/files", "/v1/chat/completions", "/mcp", "/a2a"} {
		// The client is inside allowed_ips; the proxy appended it.
		requireNoIPRefusal(t, serveWithHeader(t, srv, http.MethodPost, target, ipGateProxy, "X-Forwarded-For", ipInKeyList, ipGateKeyListed),
			"POST "+target+" through the trusted proxy from inside allowed_ips")
		// An allowed address left of the real client changes nothing.
		requireIPRefusal(t, serveWithHeader(t, srv, http.MethodPost, target, ipGateProxy, "X-Forwarded-For", ipInKeyList+", "+ipOutside, ipGateKeyListed),
			keyIPRefusal, "POST "+target+" through the trusted proxy from outside allowed_ips, with a forged allowed address")
		// Two trusted hops (another proxy in 10/8 appended itself).
		requireNoIPRefusal(t, serveWithHeader(t, srv, http.MethodPost, target, ipGateProxy, "X-Forwarded-For", ipOutside+", "+ipInOrgList+", 10.9.9.9", ipGateKeyOrg),
			"POST "+target+" through two trusted proxies from inside the org's ip_acl")
		// The caller is not a trusted proxy: its header is ignored.
		requireIPRefusal(t, serveWithHeader(t, srv, http.MethodPost, target, ipOutside, "X-Forwarded-For", ipInKeyList, ipGateKeyListed),
			keyIPRefusal, "POST "+target+" from an untrusted peer claiming an allowed address")
	}
}

// The gate and the pipeline read the same client IP (ClientIP, which reads
// what TrustedClientIP resolved): a request the gate admits reaches the
// pipeline's ipacl and auth plugins with that address in client_ip, never the
// header's.
func TestKeyGate_PipelineSeesTheClientIPTheGateChecked(t *testing.T) {
	for _, tc := range []struct {
		name    string
		proxies []string
		peer    string
		xff     string
		want    string
	}{
		{"default: the peer, not the header", nil, ipInKeyList, ipOutside, ipInKeyList},
		{"trusted proxy: the client it appended", []string{"10.0.0.0/8"}, ipGateProxy, ipOutside + ", " + ipInKeyList, ipInKeyList},
		{"untrusted peer under a trusted-proxy config: the peer", []string{"10.0.0.0/8"}, ipInKeyList, ipOutside, ipInKeyList},
	} {
		t.Run(tc.name, func(t *testing.T) {
			recorder := &clientIPRecorder{}
			srv := newIPGateServerWithEngine(t, true, pipeline.NewEngine(recorder), func(cfg *config.Config) {
				cfg.Server.TrustedProxies = tc.proxies
			})
			w := serveWithHeader(t, srv, http.MethodPost, "/v1/chat/completions", tc.peer, "X-Forwarded-For", tc.xff, ipGateKeyListed)
			if w.Code != http.StatusOK {
				t.Fatalf("status %d, want 200. Body: %s", w.Code, w.Body.String())
			}
			if got := recorder.seen(); got != tc.want {
				t.Fatalf("pipeline client_ip %q, want %q (peer %s, X-Forwarded-For %q)", got, tc.want, tc.peer, tc.xff)
			}
		})
	}
}

// clientIPRecorder is a pipeline plugin that records the client_ip the
// handler put on the request.
type clientIPRecorder struct {
	mu sync.Mutex
	ip string
}

func (p *clientIPRecorder) Name() string  { return "client-ip-recorder" }
func (p *clientIPRecorder) Priority() int { return 1 }
func (p *clientIPRecorder) ProcessRequest(_ context.Context, rc *models.RequestContext) pipeline.PluginResult {
	p.mu.Lock()
	p.ip = rc.Metadata["client_ip"]
	p.mu.Unlock()
	return pipeline.ResultContinue()
}
func (p *clientIPRecorder) ProcessResponse(context.Context, *models.RequestContext) pipeline.PluginResult {
	return pipeline.ResultContinue()
}
func (p *clientIPRecorder) seen() string {
	p.mu.Lock()
	defer p.mu.Unlock()
	return p.ip
}

func serveWithHeader(t *testing.T, srv *Server, method, target, remoteIP, header, value, key string) *httptest.ResponseRecorder {
	t.Helper()
	req := httptest.NewRequest(method, target, bytes.NewReader([]byte(ipGateBody)))
	req.Header.Set("Content-Type", "application/json")
	req.RemoteAddr = remoteIP + ":41234"
	if value != "" {
		req.Header.Set(header, value)
	}
	if key != "" {
		req.Header.Set("Authorization", "Bearer "+key)
	}
	ctx, cancel := context.WithTimeout(req.Context(), 2*time.Second)
	defer cancel()
	w := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(w, req.WithContext(ctx))
	return w
}

// The pipeline runs ipacl before auth, so a globally denied address is refused
// before its key is looked at; the gate does the same, and answers a wrong key
// from such an address with the same 403 as a valid one.
func TestKeyGate_GlobalIPACLComesBeforeTheKey(t *testing.T) {
	srv := newIPGateServer(t, true)
	for _, key := range []string{"", "sk-agentcc-not-a-key", ipGateKeyOpen} {
		requireIPRefusal(t, serveFrom(t, srv, http.MethodGet, "/v1/files", ipGlobalDenied, "", key), aclRefusal,
			"GET /v1/files from a globally denied address with key "+key)
	}
}

// With request auth off there is no key, but the global ip_acl still holds on
// every gated route, as it does in the pipeline.
func TestKeyGate_GlobalIPACLHoldsWithRequestAuthOff(t *testing.T) {
	srv := newIPGateServer(t, false)
	for _, rt := range ipGatedRoutes(t, srv) {
		target := concretePath(rt.Pattern)
		name := rt.Method + " " + rt.Pattern
		requireIPRefusal(t, serveFrom(t, srv, rt.Method, target, ipGlobalDenied, "", ""), aclRefusal,
			name+" with auth off from a globally denied address")
	}
	requireNoIPRefusal(t, serveFrom(t, srv, http.MethodGet, "/v1/files", ipOutside, "", ""),
		"GET /v1/files with auth off from any other address")
}

// An org's ip_acl changed through the tenant store (sync or the admin API)
// applies at the gate on the next request, with no cache to invalidate.
func TestKeyGate_FollowsAnOrgIPACLChange(t *testing.T) {
	srv := newIPGateServer(t, true)
	requireNoIPRefusal(t, serveFrom(t, srv, http.MethodGet, "/v1/files", ipInOrgList, "", ipGateKeyOrg),
		"GET /v1/files from inside the org's ip_acl")

	srv.TenantStore.Set(ipGateOrg, &tenant.OrgConfig{IPACL: &tenant.IPACLConfig{Enabled: true, Allow: []string{ipOutside}}})

	requireIPRefusal(t, serveFrom(t, srv, http.MethodGet, "/v1/files", ipInOrgList, "", ipGateKeyOrg), aclRefusal,
		"GET /v1/files from an address outside the org's new ip_acl")
	requireNoIPRefusal(t, serveFrom(t, srv, http.MethodGet, "/v1/files", ipOutside, "", ipGateKeyOrg),
		"GET /v1/files from the address the org's new ip_acl allows")
}
