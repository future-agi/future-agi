package server

import (
	"bytes"
	"context"
	"net/http/httptest"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"sync"
	"testing"

	authpkg "github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	authplugin "github.com/futureagi/agentcc-gateway/internal/plugins/auth"
	ipaclplugin "github.com/futureagi/agentcc-gateway/internal/plugins/ipacl"
	"github.com/futureagi/agentcc-gateway/internal/providers"
)

// Chat and the other non-video routes record client_ip, which ip_acl and
// per-key allowed_ips judge. These tests cover client_ip selection from the
// TCP peer or the configured X-Forwarded-For hop across gateway routes.

const (
	clientIPTestKey   = "sk-client-ip-test-001"
	clientIPChatBody  = `{"model":"gpt-4o","messages":[{"role":"user","content":"Hello"}]}`
	allowedClient     = "10.0.0.2"
	outsideClient     = "192.0.2.1"
	proxyOutsideAllow = "35.191.0.10"
)

type clientIPRecorder struct {
	mu  sync.Mutex
	got []string
}

func (p *clientIPRecorder) Name() string  { return "client_ip_recorder" }
func (p *clientIPRecorder) Priority() int { return 1 }
func (p *clientIPRecorder) ProcessRequest(_ context.Context, rc *models.RequestContext) pipeline.PluginResult {
	p.mu.Lock()
	p.got = append(p.got, rc.Metadata["client_ip"])
	p.mu.Unlock()
	return pipeline.ResultContinue()
}
func (p *clientIPRecorder) ProcessResponse(context.Context, *models.RequestContext) pipeline.PluginResult {
	return pipeline.ResultContinue()
}

func (p *clientIPRecorder) take() []string {
	p.mu.Lock()
	defer p.mu.Unlock()
	got := p.got
	p.got = nil
	return got
}

// clientIPServer builds a real Server with the ip_acl plugin bound as in
// cmd/agentcc and request auth on, so per-key allowed_ips is enforced.
func clientIPServer(t *testing.T, mockURL string, acl config.IPACLConfig, keyAllowedIPs []string, extra ...pipeline.Plugin) *Server {
	t.Helper()
	cfg := config.DefaultConfig()
	cfg.IPACL = acl
	cfg.Auth.Enabled = true
	cfg.Auth.Keys = []config.AuthKeyConfig{{
		Name:       "client-ip-test",
		Key:        clientIPTestKey,
		Owner:      "test-org",
		KeyType:    "internal",
		AllowedIPs: keyAllowedIPs,
	}}
	cfg.Providers["openai"] = config.ProviderConfig{
		BaseURL:   mockURL,
		APIFormat: "openai",
		Models:    []string{"gpt-4o", "gpt-4o-mini"},
	}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatalf("creating registry: %v", err)
	}
	keyStore := authpkg.NewKeyStore(cfg.Auth)
	plugins := append([]pipeline.Plugin{ipaclplugin.New(cfg.IPACL, nil), authplugin.New(keyStore, true)}, extra...)
	srv := New(cfg, "", registry, pipeline.NewEngine(plugins...), keyStore, nil, nil, nil, testModelDBPtr(), nil, nil)
	srv.ready.Store(true)
	t.Cleanup(func() { srv.Shutdown(context.Background()) })
	return srv
}

type clientIPReq struct {
	peer, xff, xRealIP string
}

func sendFrom(s *Server, path, body string, c clientIPReq) *httptest.ResponseRecorder {
	r := httptest.NewRequest("POST", path, bytes.NewBufferString(body))
	r.RemoteAddr = c.peer + ":40000"
	if c.xff != "" {
		r.Header.Set("X-Forwarded-For", c.xff)
	}
	if c.xRealIP != "" {
		r.Header.Set("X-Real-IP", c.xRealIP)
	}
	r.Header.Set("Content-Type", "application/json")
	r.Header.Set("Authorization", "Bearer "+clientIPTestKey)
	w := httptest.NewRecorder()
	s.httpServer.Handler.ServeHTTP(w, r)
	return w
}

// TestChatACLUsesPeerAddressByDefault: with trusted_proxies set to 0, the
// global allow list uses the TCP peer address regardless of forwarding
// headers.
func TestChatACLUsesPeerAddressByDefault(t *testing.T) {
	mock := startMockOpenAI(t)
	defer mock.Close()
	s := clientIPServer(t, mock.URL, config.IPACLConfig{Enabled: true, Allow: []string{"10.0.0.0/8"}}, nil)

	for name, c := range map[string]clientIPReq{
		"different X-Forwarded-For address":   {peer: outsideClient, xff: allowedClient},
		"different X-Real-IP address":         {peer: outsideClient, xRealIP: allowedClient},
		"different addresses in both headers": {peer: outsideClient, xff: allowedClient + ", " + allowedClient, xRealIP: allowedClient},
	} {
		if w := sendFrom(s, "/v1/chat/completions", clientIPChatBody, c); w.Code != 403 {
			t.Errorf("%s: outside caller got %d %s", name, w.Code, w.Body.String())
		}
	}

	// Positive controls: an allowed peer receives 200 with each header combination.
	for name, c := range map[string]clientIPReq{
		"no headers":             {peer: allowedClient},
		"denied X-Forwarded-For": {peer: allowedClient, xff: outsideClient},
		"denied X-Real-IP":       {peer: allowedClient, xRealIP: outsideClient},
	} {
		if w := sendFrom(s, "/v1/chat/completions", clientIPChatBody, c); w.Code != 200 {
			t.Errorf("%s: allowed peer got %d %s", name, w.Code, w.Body.String())
		}
	}
}

// TestChatDenyListUsesPeerAddress: the deny list uses the TCP peer when
// trusted_proxies is 0.
func TestChatDenyListUsesPeerAddress(t *testing.T) {
	mock := startMockOpenAI(t)
	defer mock.Close()
	s := clientIPServer(t, mock.URL, config.IPACLConfig{Enabled: true, Deny: []string{"192.0.2.0/24"}}, nil)

	if w := sendFrom(s, "/v1/chat/completions", clientIPChatBody, clientIPReq{peer: outsideClient, xff: "198.51.100.7"}); w.Code != 403 {
		t.Errorf("peer-address deny-list case: got %d %s, want 403", w.Code, w.Body.String())
	}
	if w := sendFrom(s, "/v1/chat/completions", clientIPChatBody, clientIPReq{peer: "198.51.100.7"}); w.Code != 200 {
		t.Errorf("other peer refused: %d %s", w.Code, w.Body.String())
	}
}

// TestChatKeyAllowedIPsUsePeerAddress: per-key allowed_ips uses the TCP peer
// when trusted_proxies is 0.
func TestChatKeyAllowedIPsUsePeerAddress(t *testing.T) {
	mock := startMockOpenAI(t)
	defer mock.Close()
	s := clientIPServer(t, mock.URL, config.IPACLConfig{}, []string{"10.0.0.0/8"})

	if w := sendFrom(s, "/v1/chat/completions", clientIPChatBody, clientIPReq{peer: outsideClient, xff: allowedClient}); w.Code != 403 {
		t.Errorf("peer-address allowed_ips case: got %d %s, want 403", w.Code, w.Body.String())
	}
	if w := sendFrom(s, "/v1/chat/completions", clientIPChatBody, clientIPReq{peer: allowedClient}); w.Code != 200 {
		t.Errorf("key used from inside its range: %d %s", w.Code, w.Body.String())
	}
}

// TestChatACLBehindTrustedProxies: behind N trusted hops the client is the
// Nth X-Forwarded-For entry from the right. Entries the caller prepended are
// ignored and a chain shorter than N is judged by the TCP peer.
func TestChatACLBehindTrustedProxies(t *testing.T) {
	mock := startMockOpenAI(t)
	defer mock.Close()

	t.Run("one hop", func(t *testing.T) {
		s := clientIPServer(t, mock.URL, config.IPACLConfig{Enabled: true, TrustedProxies: 1, Allow: []string{"10.0.0.0/8"}}, nil)
		// The proxy appends the address it received the request from.
		if w := sendFrom(s, "/v1/chat/completions", clientIPChatBody, clientIPReq{peer: proxyOutsideAllow, xff: allowedClient}); w.Code != 200 {
			t.Errorf("allowed client behind proxy got %d %s", w.Code, w.Body.String())
		}
		if w := sendFrom(s, "/v1/chat/completions", clientIPChatBody, clientIPReq{peer: proxyOutsideAllow, xff: allowedClient + ", " + outsideClient}); w.Code != 403 {
			t.Errorf("rightmost forwarded-address case: got %d %s, want 403", w.Code, w.Body.String())
		}
		if w := sendFrom(s, "/v1/chat/completions", clientIPChatBody, clientIPReq{peer: proxyOutsideAllow, xRealIP: allowedClient}); w.Code != 403 {
			t.Errorf("X-Real-IP trusted behind proxy: %d %s", w.Code, w.Body.String())
		}
	})

	t.Run("two hops", func(t *testing.T) {
		s := clientIPServer(t, mock.URL, config.IPACLConfig{Enabled: true, TrustedProxies: 2, Allow: []string{"10.0.0.0/8"}}, nil)
		if w := sendFrom(s, "/v1/chat/completions", clientIPChatBody, clientIPReq{peer: proxyOutsideAllow, xff: allowedClient + ", 34.120.0.1"}); w.Code != 200 {
			t.Errorf("allowed client behind two hops got %d %s", w.Code, w.Body.String())
		}
		if w := sendFrom(s, "/v1/chat/completions", clientIPChatBody, clientIPReq{peer: outsideClient, xff: allowedClient}); w.Code != 403 {
			t.Errorf("short chain judged by forwarded entry: %d %s", w.Code, w.Body.String())
		}
	})
}

// TestRoutesRecordTrustedClientIP: every route that records client_ip must
// record the TCP peer when the caller sends X-Forwarded-For / X-Real-IP and
// no proxies are trusted.
func TestRoutesRecordTrustedClientIP(t *testing.T) {
	mock := startMockOpenAI(t)
	defer mock.Close()
	rec := &clientIPRecorder{}
	s := clientIPServer(t, mock.URL, config.IPACLConfig{}, nil, rec)

	routes := map[string]string{
		"/v1/chat/completions":                  clientIPChatBody,
		"/v1/completions":                       `{"model":"gpt-4o","prompt":"Hello"}`,
		"/v1/responses":                         `{"model":"gpt-4o","input":"Hello"}`,
		"/v1/messages":                          `{"model":"gpt-4o","max_tokens":16,"messages":[{"role":"user","content":"Hello"}]}`,
		"/v1beta/models/gpt-4o:generateContent": `{"contents":[{"role":"user","parts":[{"text":"Hello"}]}]}`,
	}
	for path, body := range routes {
		rec.take()
		w := sendFrom(s, path, body, clientIPReq{peer: outsideClient, xff: allowedClient, xRealIP: allowedClient})
		got := rec.take()
		if len(got) == 0 {
			t.Errorf("%s: pipeline never ran (%d %s)", path, w.Code, w.Body.String())
			continue
		}
		for _, ip := range got {
			if ip != outsideClient {
				t.Errorf("%s: recorded client_ip %q, want TCP peer %q", path, ip, outsideClient)
			}
		}
	}
}

// TestClientIPAssignmentsUseTrustedExtractor: routes that are awkward to drive
// over HTTP (speech relay, assistants, decisions, and count_tokens, which needs an
// anthropic-format provider) are pinned at the source:
// every client_ip assignment in a handler must go through the trusted
// extractor.
func TestClientIPAssignmentsUseTrustedExtractor(t *testing.T) {
	files, err := filepath.Glob("handlers*.go")
	if err != nil {
		t.Fatal(err)
	}
	assign := regexp.MustCompile(`Metadata\["client_ip"\]\s*=\s*(.+)`)
	seen := 0
	for _, f := range files {
		if strings.HasSuffix(f, "_test.go") {
			continue
		}
		src, err := os.ReadFile(f)
		if err != nil {
			t.Fatal(err)
		}
		for _, m := range assign.FindAllStringSubmatch(string(src), -1) {
			seen++
			rhs := strings.TrimSpace(m[1])
			if rhs != "h.clientIP(r)" && !strings.HasPrefix(rhs, "trustedClientIP(") {
				t.Errorf("%s: client_ip assigned from %q; use h.clientIP(r)", f, rhs)
			}
		}
	}
	if seen < 10 {
		t.Fatalf("found only %d client_ip assignments; the pattern no longer matches the handlers", seen)
	}
}
