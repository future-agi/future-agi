package server

import (
	"bytes"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/capability"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/middleware"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	authplugin "github.com/futureagi/agentcc-gateway/internal/plugins/auth"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// A key admitted by KeyAuth must also pass the pipeline's real auth plugin.
// These handlers used to omit client_ip, so even an allowed peer was refused
// as unknown. Exercise both the whole HTTP stack and the handler pipeline:
// a denial at the outer gate alone cannot prove the pipeline checks the IP.
func TestModelRoutesHonorKeyClientIP(t *testing.T) {
	routes := []struct {
		name, path, model string
		body              func(*testing.T, string) (string, []byte)
	}{
		{"embeddings", "/v1/embeddings", rf17Embedding, r5JSON(`{"model":"@MODEL@","input":"hello"}`)},
		{"rerank", "/v1/rerank", rf17Rerank, r5JSON(`{"model":"@MODEL@","query":"hello","documents":["world"]}`)},
		{"images", "/v1/images/generations", rf17Image, r5JSON(`{"model":"@MODEL@","prompt":"hello"}`)},
		{"speech", "/v1/audio/speech", rf17Speech, r5JSON(`{"model":"@MODEL@","input":"hello","voice":"alloy"}`)},
		{"speech_stream", "/v1/audio/speech/stream", rf17Speech, r5JSON(`{"model":"@MODEL@","input":"hello","voice":"alloy"}`)},
		{"transcription", "/v1/audio/transcriptions", rf17Whisper, r5AudioForm},
		{"translation", "/v1/audio/translations", rf17Whisper, r5AudioForm},
		{"ocr", "/v1/ocr", rf17OCR, r5JSON(`{"model":"@MODEL@","document":{"type":"document_url","document_url":"https://document.example.invalid/probe.pdf"}}`)},
		{"search", "/v1/search", rf17Search, r5JSON(`{"search_provider":"@MODEL@","query":"hello"}`)},
		{"echo", "/v1/capabilities/echo", "", r5JSON(`{"input":{"q":"hello"}}`)},
	}
	cases := []struct {
		name, peer, forwarded, realIP, wantIP string
		proxies                               []string
		hops                                  int
		allowed                               bool
	}{
		{name: "allowed_peer", peer: ipInKeyList, wantIP: ipInKeyList, allowed: true},
		{name: "denied_peer", peer: ipOutside, wantIP: ipOutside},
		{name: "default_ignores_forged_forwarded_allow", peer: ipOutside, forwarded: ipInKeyList, wantIP: ipOutside},
		{name: "default_ignores_forwarded_deny", peer: ipInKeyList, forwarded: ipOutside, wantIP: ipInKeyList, allowed: true},
		{name: "default_ignores_forged_real_ip_allow", peer: ipOutside, realIP: ipInKeyList, wantIP: ipOutside},
		{name: "default_ignores_real_ip_deny", peer: ipInKeyList, realIP: ipOutside, wantIP: ipInKeyList, allowed: true},
		{name: "trusted_proxy_allows_forwarded_client", peer: ipGateProxy, forwarded: ipInKeyList, proxies: []string{"10.0.0.0/8"}, wantIP: ipInKeyList, allowed: true},
		{name: "trusted_proxy_denies_forwarded_client", peer: ipGateProxy, forwarded: ipOutside, proxies: []string{"10.0.0.0/8"}, wantIP: ipOutside},
		{name: "trusted_chain_ignores_forged_leftmost", peer: ipGateProxy, forwarded: ipInKeyList + ", " + ipOutside + ", 10.9.9.9", proxies: []string{"10.0.0.0/8"}, wantIP: ipOutside},
		{name: "untrusted_peer_cannot_forward_allow", peer: ipOutside, forwarded: ipInKeyList, proxies: []string{"10.0.0.0/8"}, wantIP: ipOutside},
		{name: "untrusted_peer_cannot_forward_deny", peer: ipInKeyList, forwarded: ipOutside, proxies: []string{"10.0.0.0/8"}, wantIP: ipInKeyList, allowed: true},
		{name: "trusted_hop_uses_rightmost", peer: ipGateProxy, forwarded: ipOutside + ", " + ipInKeyList, hops: 1, wantIP: ipInKeyList, allowed: true},
	}
	for _, mode := range []string{"gateway", "pipeline"} {
		t.Run(mode, func(t *testing.T) {
			for _, tc := range cases {
				t.Run(tc.name, func(t *testing.T) {
					srv, recorder, count := newModelIPServer(t, tc.proxies, tc.hops)
					handler := srv.httpServer.Handler
					if mode == "pipeline" {
						resolver, err := middleware.NewClientIPResolver(config.ServerConfig{TrustedProxies: tc.proxies, TrustedHops: tc.hops})
						if err != nil {
							t.Fatal(err)
						}
						// Retain trusted-proxy resolution, but bypass KeyAuth so the
						// real auth plugin must refuse each denied request itself.
						handler = middleware.TrustedClientIP(resolver)(srv.router)
					}
					for _, route := range routes {
						t.Run(route.name, func(t *testing.T) {
							contentType, body := route.body(t, route.model)
							req := httptest.NewRequest(http.MethodPost, route.path, bytes.NewReader(body))
							req.RemoteAddr = tc.peer + ":41234"
							req.Header.Set("Content-Type", contentType)
							req.Header.Set("Authorization", "Bearer "+ipGateKeyListed)
							req.Header.Set("X-Forwarded-For", tc.forwarded)
							req.Header.Set("X-Real-IP", tc.realIP)
							// Caller metadata is never an authority for client_ip,
							// whether it tries to allow a denied peer or deny an allowed one.
							forged := ipInKeyList
							if tc.allowed {
								forged = ipOutside
							}
							req.Header.Set("X-Agentcc-Metadata", `{"client_ip":"`+forged+`"}`)
							before := count()
							w := httptest.NewRecorder()
							handler.ServeHTTP(w, req)
							wantStatus, wantCalls := http.StatusForbidden, 0
							if tc.allowed {
								wantStatus, wantCalls = http.StatusOK, 1
							}
							if w.Code != wantStatus {
								t.Errorf("status = %d, want %d: %s", w.Code, wantStatus, w.Body.String())
							}
							if got := count() - before; got != wantCalls {
								t.Errorf("provider calls = %d, want %d", got, wantCalls)
							}
							if tc.allowed || mode == "pipeline" {
								if got := recorder.seen(); got != tc.wantIP {
									t.Errorf("pipeline client_ip = %q, want %q", got, tc.wantIP)
								}
							}
							if !tc.allowed {
								var resp models.ErrorResponse
								if err := json.Unmarshal(w.Body.Bytes(), &resp); err != nil {
									t.Fatalf("decode refusal: %v: %s", err, w.Body.String())
								}
								if resp.Error.Code != "permission_denied" || resp.Error.Type != models.ErrTypePermission || resp.Error.Message != keyIPRefusal {
									t.Errorf("refusal = %+v, want the existing key-IP permission_denied error", resp.Error)
								}
							}
						})
					}
				})
			}
		})
	}
}

// All provider requests end at loopback mocks (search is an in-memory fake).
// The real key store is shared by the HTTP gate and the pipeline auth plugin.
func newModelIPServer(t *testing.T, proxies []string, hops int) (*Server, *clientIPRecorder, func() int) {
	t.Helper()
	calls := &upstreamCalls{}
	modelUpstream := otherAPIsUpstream(calls)
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if strings.HasPrefix(r.URL.Path, "/echo-a/") {
			calls.record(w, r)
			_, _ = io.Copy(w, r.Body)
			return
		}
		modelUpstream(w, r)
	}))
	t.Cleanup(upstream.Close)
	cfg := config.DefaultConfig()
	cfg.Server.TrustedProxies, cfg.Server.TrustedHops = proxies, hops
	cfg.Auth.Enabled = true
	cfg.Auth.Keys = []config.AuthKeyConfig{{Name: "listed", Key: ipGateKeyListed, Owner: "test", KeyType: "internal",
		AllowedIPs: []string{"203.0.113.0/24"}, Metadata: map[string]string{"org_id": ipGateOrg}}}
	cfg.Providers = map[string]config.ProviderConfig{
		"openai": {BaseURL: upstream.URL, APIFormat: "openai", Models: []string{rf17Embedding, rf17Image, rf17Speech, rf17Whisper, rf17OCR}},
		"cohere": {BaseURL: upstream.URL, APIFormat: "cohere", Models: []string{rf17Rerank}},
	}
	cfg.Tools.Enabled = true
	cfg.Capabilities.PerCapability = map[string]config.CapabilityConfig{"echo": {}}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatal(err)
	}
	search := &fakeSearchProvider{}
	registry.RegisterProvider(rf17Search, search, []string{rf17Search})
	keys := auth.NewKeyStore(cfg.Auth)
	recorder := &clientIPRecorder{}
	engine := pipeline.NewEngine(recorder, authplugin.New(keys, true))
	tenants := tenant.NewStore()
	org := capcoreOrgConfig([]string{"echo-a"}, false)
	org.Providers["echo-a"] = &tenant.ProviderConfig{Enabled: true, APIKey: "synthetic-echo-credential"}
	tenants.Set(ipGateOrg, org)
	srv := New(cfg, "", registry, engine, keys, nil, nil, nil, testModelDBPtr(), tenants, nil)
	releaseTestServer(t, srv)
	capRegistry := capability.NewRegistry()
	if err := capability.RegisterConfigured(capRegistry, cfg); err != nil {
		t.Fatal(err)
	}
	srv.SetCapabilityCore(capability.NewCore(capability.Options{Registry: capRegistry, Cards: capability.NewStaticCards(capcoreCards(t)...),
		Credentials: capability.ConfigCredentials{Operator: map[string]config.ProviderConfig{"echo-a": {BaseURL: upstream.URL + "/echo-a"}}}}))
	t.Cleanup(func() { srv.SetCapabilityCore(nil) })
	srv.ready.Store(true)
	return srv, recorder, func() int { return calls.count() + search.count() }
}
