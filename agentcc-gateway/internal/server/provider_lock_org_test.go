package server

// x-agentcc-provider-lock pins "one provider the key may use" (README: routing
// table and request headers). An org key may use its organization's own
// providers, so a lock naming one of them must reach it, even when the
// gateway itself has no provider with that id (an org-only id: any custom
// name the org saved with a base_url). Before this, resolveRoute looked the
// id up in the gateway's registry only and answered 404 provider_not_found.
// A lock to a shared id (the gateway's "openai", which the org also holds)
// already reached the org's instance; the controls pin that and every
// refusal that must stay as it is.
//
// One upstream records which credential each call carried. It listens on a
// private, non-loopback address of this machine: an org provider's own
// base_url may point at a private address when the operator allows it
// (org_providers.allow_private_urls), never at loopback.

import (
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	authplugin "github.com/futureagi/agentcc-gateway/internal/plugins/auth"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

const (
	plOrgA        = "org-plock-a"
	plOrgB        = "org-plock-b"
	plKeyA        = "sk-agentcc-plock-org-a-0123456789abcdef"
	plKeyB        = "sk-agentcc-plock-org-b-0123456789abcdef"
	plInternalKey = "sk-agentcc-plock-internal-a-0123456789ab"
)

type plUpstream struct {
	*httptest.Server
	mu    sync.Mutex
	calls []string
}

// plPrivateListener listens on the first private, non-loopback IPv4 address
// of this machine.
func plPrivateListener(t *testing.T) net.Listener {
	t.Helper()
	addrs, err := net.InterfaceAddrs()
	if err != nil {
		t.Fatalf("interface addresses: %v", err)
	}
	for _, a := range addrs {
		ipnet, ok := a.(*net.IPNet)
		if !ok {
			continue
		}
		ip := ipnet.IP.To4()
		if ip == nil || ip.IsLoopback() || !ip.IsPrivate() {
			continue
		}
		if l, err := net.Listen("tcp", net.JoinHostPort(ip.String(), "0")); err == nil {
			return l
		}
	}
	t.Skip("no private non-loopback IPv4 address to serve an org provider from")
	return nil
}

// newPLUpstream answers OpenAI chat and Anthropic messages, and records the
// path and the credential (x-api-key or Authorization) of every POST.
func newPLUpstream(t *testing.T) *plUpstream {
	t.Helper()
	u := &plUpstream{}
	u.Server = httptest.NewUnstartedServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = io.Copy(io.Discard, r.Body)
		w.Header().Set("Content-Type", "application/json")
		if r.Method != http.MethodPost {
			_, _ = io.WriteString(w, `{"object":"list","data":[]}`)
			return
		}
		cred := r.Header.Get("Authorization")
		if k := r.Header.Get("x-api-key"); k != "" {
			cred = "x-api-key " + k
		}
		u.mu.Lock()
		u.calls = append(u.calls, r.URL.Path+" "+cred)
		u.mu.Unlock()
		if strings.HasSuffix(r.URL.Path, "/messages") {
			_, _ = io.WriteString(w, `{"id":"msg_pl","type":"message","role":"assistant","model":"c-msg","content":[{"type":"text","text":"ok"}],"stop_reason":"end_turn","usage":{"input_tokens":1,"output_tokens":1}}`)
			return
		}
		_, _ = io.WriteString(w, `{"id":"x","object":"chat.completion","created":1,"model":"m-chat","choices":[{"index":0,"message":{"role":"assistant","content":"ok"},"finish_reason":"stop"}],"usage":{"prompt_tokens":1,"completion_tokens":1,"total_tokens":2}}`)
	}))
	_ = u.Server.Listener.Close()
	u.Server.Listener = plPrivateListener(t)
	u.Server.Start()
	t.Cleanup(u.Close)
	return u
}

func plMessage(w *httptest.ResponseRecorder) string {
	var e models.ErrorResponse
	_ = json.Unmarshal(w.Body.Bytes(), &e)
	return e.Error.Message
}

func (u *plUpstream) snapshot() []string {
	u.mu.Lock()
	defer u.mu.Unlock()
	return append([]string(nil), u.calls...)
}

// plGateway: org A holds its own "openai" (the gateway's id too), an
// org-only OpenAI-format "acme-llm", a disabled org-only "acme-off" and an
// org-only Anthropic-format "acme-claude". Org B holds only its "openai".
// The gateway's own providers are "openai" and "other"; it lists m-chat, so
// the model is known whichever provider a lock names.
func plGateway(t *testing.T, up *plUpstream) *Server {
	t.Helper()
	cfg := config.DefaultConfig()
	cfg.Auth.Enabled = true
	cfg.Auth.Keys = []config.AuthKeyConfig{
		{Name: "plock-a", Key: plKeyA, Owner: "t", KeyType: "byok", Metadata: map[string]string{"org_id": plOrgA}},
		{Name: "plock-b", Key: plKeyB, Owner: "t", KeyType: "byok", Metadata: map[string]string{"org_id": plOrgB}},
		{Name: "plock-internal", Key: plInternalKey, Owner: "futureagi-backend", KeyType: "internal", Metadata: map[string]string{"org_id": plOrgA}},
	}
	off := false
	cfg.Providers["openai"] = config.ProviderConfig{BaseURL: up.URL, APIKey: "operator-openai-key", APIFormat: "openai", Models: []string{"m-chat"}, AutoDiscover: &off}
	cfg.Providers["other"] = config.ProviderConfig{BaseURL: up.URL, APIKey: "operator-other-key", APIFormat: "openai", Models: []string{"other-model"}, AutoDiscover: &off}
	// The org-only providers point at the test upstream's private address.
	cfg.OrgProviders.AllowPrivateURLs = true
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatalf("creating registry: %v", err)
	}
	t.Cleanup(func() { registry.Close() })
	tenants := tenant.NewStore()
	tenants.Set(plOrgA, &tenant.OrgConfig{Providers: map[string]*tenant.ProviderConfig{
		"openai":      {Enabled: true, APIKey: "org-a-openai-key", Models: []string{"m-chat"}},
		"acme-llm":    {Enabled: true, APIKey: "org-a-acme-key", APIFormat: "openai", BaseURL: up.URL, Models: []string{"m-chat"}},
		"acme-off":    {Enabled: false, APIKey: "org-a-off-key", APIFormat: "openai", BaseURL: up.URL, Models: []string{"m-chat"}},
		"acme-claude": {Enabled: true, APIKey: "org-a-claude-key", APIFormat: "anthropic", BaseURL: up.URL, Models: []string{"c-msg"}},
	}})
	tenants.Set(plOrgB, &tenant.OrgConfig{Providers: map[string]*tenant.ProviderConfig{
		"openai": {Enabled: true, APIKey: "org-b-openai-key", Models: []string{"m-chat"}},
	}})
	keyStore := auth.NewKeyStore(cfg.Auth)
	srv := New(cfg, "", registry, pipeline.NewEngine(authplugin.New(keyStore, true)), keyStore, nil, nil, nil, testModelDBPtr(), tenants, nil)
	releaseTestServer(t, srv)
	srv.ready.Store(true)
	return srv
}

func plDo(srv *Server, path, key, lock, body string) *httptest.ResponseRecorder {
	req := httptest.NewRequest(http.MethodPost, path, strings.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+key)
	if strings.HasPrefix(path, "/v1/messages") {
		req.Header.Set("anthropic-version", "2023-06-01")
	}
	if lock != "" {
		req.Header.Set("x-agentcc-provider-lock", lock)
	}
	w := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(w, req)
	return w
}

const (
	plChatBody     = `{"model":"m-chat","messages":[{"role":"user","content":"hi"}]}`
	plMessagesBody = `{"model":"c-msg","max_tokens":16,"messages":[{"role":"user","content":"hi"}]}`
)

// A lock naming the org's own org-only provider reaches it, with the org's
// credential, on chat and on Anthropic Messages (both route through
// resolveRoute).
func TestProviderLockReachesOrgOnlyProvider(t *testing.T) {
	for _, tc := range []struct {
		name, path, lock, body, wantCall string
	}{
		{"chat", "/v1/chat/completions", "acme-llm", plChatBody, "/v1/chat/completions Bearer org-a-acme-key"},
		{"messages", "/v1/messages", "acme-claude", plMessagesBody, "/v1/messages x-api-key org-a-claude-key"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			up := newPLUpstream(t)
			srv := plGateway(t, up)
			w := plDo(srv, tc.path, plKeyA, tc.lock, tc.body)
			if w.Code != http.StatusOK {
				t.Fatalf("lock %q -> %d %s %s, want 200 from the org's own provider", tc.lock, w.Code, r7Code(w), w.Body.String())
			}
			if calls := up.snapshot(); len(calls) != 1 || calls[0] != tc.wantCall {
				t.Fatalf("upstream calls = %q, want exactly [%q]", calls, tc.wantCall)
			}
			if got := w.Header().Get("x-agentcc-provider-locked"); got != tc.lock {
				t.Errorf("x-agentcc-provider-locked = %q, want %q", got, tc.lock)
			}
		})
	}
}

// A lock naming the org's own provider that is disabled is refused as a lock
// to a provider the key may not use (403), never as an unknown provider, and
// calls nothing.
func TestProviderLockOrgOnlyDisabledIsNotAvailable(t *testing.T) {
	up := newPLUpstream(t)
	srv := plGateway(t, up)
	w := plDo(srv, "/v1/chat/completions", plKeyA, "acme-off", plChatBody)
	want := fmt.Sprintf("provider %q is not available for this API key", "acme-off")
	if w.Code != http.StatusForbidden || r7Code(w) != "permission_denied" || plMessage(w) != want {
		t.Fatalf("lock acme-off -> %d %s %s, want 403 permission_denied %q", w.Code, r7Code(w), w.Body.String(), want)
	}
	if calls := up.snapshot(); len(calls) != 0 {
		t.Fatalf("upstream calls = %q, want none", calls)
	}
}

// Controls: what a lock does today and must keep doing.
func TestProviderLockOrgPathControls(t *testing.T) {
	for _, tc := range []struct {
		name, key, lock string
		wantCode        int
		wantErr         string
		wantCall        string
	}{
		// A shared id the org holds: the org's own instance (providerForKey).
		{"shared id, org key", plKeyA, "openai", http.StatusOK, "", "/v1/chat/completions Bearer org-a-openai-key"},
		// Another org's org-only id is not this key's provider.
		{"other org's id", plKeyB, "acme-llm", http.StatusNotFound, "provider_not_found", ""},
		// Internal keys resolve from the gateway's own providers only.
		{"internal key, org-only id", plInternalKey, "acme-llm", http.StatusNotFound, "provider_not_found", ""},
		// An id nobody has.
		{"unknown id", plKeyA, "nope", http.StatusNotFound, "provider_not_found", ""},
	} {
		t.Run(tc.name, func(t *testing.T) {
			up := newPLUpstream(t)
			srv := plGateway(t, up)
			w := plDo(srv, "/v1/chat/completions", tc.key, tc.lock, plChatBody)
			if w.Code != tc.wantCode || (tc.wantErr != "" && r7Code(w) != tc.wantErr) {
				t.Fatalf("lock %q -> %d %s %s, want %d %s", tc.lock, w.Code, r7Code(w), w.Body.String(), tc.wantCode, tc.wantErr)
			}
			calls := up.snapshot()
			if tc.wantCall == "" && len(calls) != 0 {
				t.Fatalf("upstream calls = %q, want none", calls)
			}
			if tc.wantCall != "" && (len(calls) != 1 || calls[0] != tc.wantCall) {
				t.Fatalf("upstream calls = %q, want exactly [%q]", calls, tc.wantCall)
			}
		})
	}
}
