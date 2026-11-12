package server

// Request headers through the whole gateway: what a documented request
// header now does on the routes a client calls, and the headers every
// response carries.

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"slices"
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
	r7Org      = "org-r7-request"
	r7ByokKey  = "sk-agentcc-r7-request-byok-0123456789abcdef"
	r7AdminTok = "r7-request-admin-token-0123456789abcdef0123"
)

// r7Capture records what the pipeline saw for the last request.
type r7Capture struct {
	mu        sync.Mutex
	meta      map[string]string
	custom    []string
	requestID string
}

func (c *r7Capture) Name() string  { return "r7-capture" }
func (c *r7Capture) Priority() int { return 999 }
func (c *r7Capture) ProcessRequest(_ context.Context, rc *models.RequestContext) pipeline.PluginResult {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.meta = make(map[string]string, len(rc.Metadata))
	for k, v := range rc.Metadata {
		c.meta[k] = v
	}
	c.custom = slices.Clone(rc.CustomMetadataKeys)
	c.requestID = rc.RequestID
	return pipeline.ResultContinue()
}
func (c *r7Capture) ProcessResponse(context.Context, *models.RequestContext) pipeline.PluginResult {
	return pipeline.ResultContinue()
}

func (c *r7Capture) snapshot() (map[string]string, []string, string) {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.meta, c.custom, c.requestID
}

// startR7Upstream answers chat, embeddings and rerank as an OpenAI-compatible
// provider would.
func startR7Upstream(t *testing.T) *httptest.Server {
	t.Helper()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = io.Copy(io.Discard, r.Body)
		w.Header().Set("Content-Type", "application/json")
		switch {
		case r.Method == http.MethodGet:
			_, _ = io.WriteString(w, `{"object":"list","data":[]}`)
		case strings.HasSuffix(r.URL.Path, "/embeddings"):
			_, _ = io.WriteString(w, `{"object":"list","model":"m-embed","data":[{"object":"embedding","index":0,"embedding":[0.1,0.2]}],"usage":{"prompt_tokens":1,"total_tokens":1}}`)
		default:
			_, _ = io.WriteString(w, `{"id":"x","object":"chat.completion","created":1,"model":"m-chat","choices":[{"index":0,"message":{"role":"assistant","content":"ok"},"finish_reason":"stop"}],"usage":{"prompt_tokens":1,"completion_tokens":1,"total_tokens":2}}`)
		}
	}))
	t.Cleanup(srv.Close)
	return srv
}

// newR7Gateway: a BYOK key of r7Org reaches its own "openai" (the org's key
// on the config.yaml provider) for m-chat and m-embed; "node-only" is a
// config.yaml provider the org holds no key for.
func newR7Gateway(t *testing.T, capture *r7Capture) *Server {
	t.Helper()
	up := startR7Upstream(t)
	cfg := config.DefaultConfig()
	cfg.Admin.Token = r7AdminTok
	cfg.Auth.Enabled = true
	cfg.Auth.Keys = []config.AuthKeyConfig{
		{Name: "r7-byok", Key: r7ByokKey, Owner: "t", KeyType: "byok", Metadata: map[string]string{"org_id": r7Org}},
	}
	off := false
	cfg.Providers["openai"] = config.ProviderConfig{BaseURL: up.URL, APIKey: "operator-key", APIFormat: "openai", Models: []string{"m-node"}, AutoDiscover: &off}
	cfg.Providers["node-only"] = config.ProviderConfig{BaseURL: up.URL, APIKey: "operator-key", APIFormat: "openai", Models: []string{"m-chat"}, AutoDiscover: &off}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatalf("creating registry: %v", err)
	}
	tenants := tenant.NewStore()
	tenants.Set(r7Org, &tenant.OrgConfig{Providers: map[string]*tenant.ProviderConfig{
		"openai": {APIKey: "org-key", Enabled: true, Models: []string{"m-chat", "m-embed"}},
	}})
	keyStore := auth.NewKeyStore(cfg.Auth)
	plugins := []pipeline.Plugin{authplugin.New(keyStore, true)}
	if capture != nil {
		plugins = append(plugins, capture)
	}
	srv := New(cfg, "", registry, pipeline.NewEngine(plugins...), keyStore, nil, nil, nil, testModelDBPtr(), tenants, nil)
	releaseTestServer(t, srv)
	srv.ready.Store(true)
	return srv
}

const r7ChatBody = `{"model":"m-chat","messages":[{"role":"user","content":"hi"}]}`

func r7Do(srv *Server, method, path, body, key string, hdr map[string]string) *httptest.ResponseRecorder {
	req := httptest.NewRequest(method, path, strings.NewReader(body))
	if body != "" {
		req.Header.Set("Content-Type", "application/json")
	}
	if key != "" {
		req.Header.Set("Authorization", "Bearer "+key)
	}
	for k, v := range hdr {
		req.Header.Set(k, v)
	}
	w := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(w, req)
	return w
}

func r7Code(w *httptest.ResponseRecorder) string {
	var e models.ErrorResponse
	_ = json.Unmarshal(w.Body.Bytes(), &e)
	return e.Error.Code
}

func TestR7AgentccConfigHeaderEndToEnd(t *testing.T) {
	capt := &r7Capture{}
	srv := newR7Gateway(t, capt)

	// The probe's control: the header gives timeout-ms 1234; the config now
	// does too.
	for _, hdr := range []map[string]string{
		{"x-agentcc-request-timeout": "1234"},
		{"x-agentcc-config": `{"request-timeout":1234}`},
	} {
		w := r7Do(srv, "POST", "/v1/chat/completions", r7ChatBody, r7ByokKey, hdr)
		if w.Code != http.StatusOK || w.Header().Get("x-agentcc-timeout-ms") != "1234" {
			t.Errorf("%v -> %d timeout-ms %q, want 200 and 1234; body %s", hdr, w.Code, w.Header().Get("x-agentcc-timeout-ms"), w.Body.String())
		}
	}

	w := r7Do(srv, "POST", "/v1/chat/completions", r7ChatBody, r7ByokKey, map[string]string{"x-agentcc-config": `{"cache-ttl":300,"cache-namespace":"prod"}`})
	meta, _, _ := capt.snapshot()
	if w.Code != http.StatusOK || meta["cache_ttl"] != "300" || meta["cache_namespace"] != "prod" {
		t.Errorf("cache config -> %d, metadata cache_ttl %q cache_namespace %q; want 200, 300, prod", w.Code, meta["cache_ttl"], meta["cache_namespace"])
	}

	// Garbage was a 200 at the base.
	w = r7Do(srv, "POST", "/v1/chat/completions", r7ChatBody, r7ByokKey, map[string]string{"x-agentcc-config": "this is not json"})
	if w.Code != http.StatusBadRequest || r7Code(w) != "invalid_agentcc_config" {
		t.Errorf("non-JSON config -> %d %s, want 400 invalid_agentcc_config; body %s", w.Code, r7Code(w), w.Body.String())
	}

	// A value is checked as its own header is: the retry header's 400.
	w = r7Do(srv, "POST", "/v1/chat/completions", r7ChatBody, r7ByokKey, map[string]string{"x-agentcc-config": `{"max-attempts":9}`})
	if w.Code != http.StatusBadRequest || r7Code(w) != "invalid_retry_header" {
		t.Errorf("max-attempts 9 in the config -> %d %s, want the header's 400 invalid_retry_header", w.Code, r7Code(w))
	}

	// The config can pick no provider the header could not: a provider the
	// org holds no key for is refused exactly as x-agentcc-provider-lock is.
	viaHeader := r7Do(srv, "POST", "/v1/chat/completions", r7ChatBody, r7ByokKey, map[string]string{"x-agentcc-provider-lock": "node-only"})
	viaConfig := r7Do(srv, "POST", "/v1/chat/completions", r7ChatBody, r7ByokKey, map[string]string{"x-agentcc-config": `{"provider-lock":"node-only"}`})
	if viaHeader.Code == http.StatusOK || viaConfig.Code != viaHeader.Code || r7Code(viaConfig) != r7Code(viaHeader) {
		t.Errorf("provider-lock node-only: header -> %d %s, config -> %d %s; want the same refusal", viaHeader.Code, r7Code(viaHeader), viaConfig.Code, r7Code(viaConfig))
	}

	// Nor set a gateway-owned metadata key through its metadata.
	w = r7Do(srv, "POST", "/v1/chat/completions", r7ChatBody, r7ByokKey, map[string]string{"x-agentcc-config": `{"metadata":{"org_id":"org-other","key_type":"internal","team":"ml"}}`})
	meta, _, _ = capt.snapshot()
	if w.Code != http.StatusOK || meta["org_id"] != r7Org || meta["key_type"] == "internal" || meta["team"] != "ml" {
		t.Errorf("config metadata -> %d org_id %q key_type %q team %q; want the key's org, never internal, team ml", w.Code, meta["org_id"], meta["key_type"], meta["team"])
	}
}

func TestR7GuardrailPolicyAliasEndToEnd(t *testing.T) {
	capt := &r7Capture{}
	srv := newR7Gateway(t, capt)
	for _, hdr := range []map[string]string{
		{"X-Guardrail-Policy": "log-only"},
		{"x-agentcc-guardrail-policy": "log-only"},
		{"x-agentcc-config": `{"guardrail-policy":"log-only"}`},
	} {
		w := r7Do(srv, "POST", "/v1/chat/completions", r7ChatBody, r7ByokKey, hdr)
		meta, _, _ := capt.snapshot()
		if meta["x-guardrail-policy"] != "log-only" {
			t.Errorf("%v -> %d, the guardrail plugin's x-guardrail-policy = %q, want log-only", hdr, w.Code, meta["x-guardrail-policy"])
		}
	}
}

func TestR7PropertyHeadersEndToEnd(t *testing.T) {
	capt := &r7Capture{}
	srv := newR7Gateway(t, capt)
	for _, tc := range []struct {
		path, body string
	}{
		{"/v1/chat/completions", r7ChatBody},
		{"/v1/embeddings", `{"model":"m-embed","input":"hi"}`},
	} {
		w := r7Do(srv, "POST", tc.path, tc.body, r7ByokKey, map[string]string{
			"x-agentcc-property-env":         "prod",
			"x-agentcc-property-cost_center": "cc-7",
			// Gateway-owned keys: the metadata blocklist drops them.
			"x-agentcc-property-org_id":   "org-other",
			"x-agentcc-property-key_type": "internal",
			"x-agentcc-property-cost":     "0",
		})
		meta, custom, _ := capt.snapshot()
		if w.Code != http.StatusOK {
			t.Fatalf("%s -> %d; body %s", tc.path, w.Code, w.Body.String())
		}
		if meta["env"] != "prod" || meta["cost_center"] != "cc-7" {
			t.Errorf("%s: metadata env %q cost_center %q, want prod and cc-7", tc.path, meta["env"], meta["cost_center"])
		}
		if !slices.Contains(custom, "env") {
			t.Errorf("%s: env is not recorded as a caller dimension (%v)", tc.path, custom)
		}
		if meta["org_id"] != r7Org || meta["key_type"] == "internal" || slices.Contains(custom, "cost") {
			t.Errorf("%s: a property set a gateway-owned key: org_id %q key_type %q custom %v", tc.path, meta["org_id"], meta["key_type"], custom)
		}
	}
}

func TestR7SessionNamePathEndToEnd(t *testing.T) {
	capt := &r7Capture{}
	srv := newR7Gateway(t, capt)
	w := r7Do(srv, "POST", "/v1/chat/completions", r7ChatBody, r7ByokKey, map[string]string{
		"x-agentcc-session-id": "s1", "x-agentcc-session-name": "My session", "x-agentcc-session-path": "/search/rerank",
	})
	meta, _, _ := capt.snapshot()
	if w.Code != http.StatusOK || meta["session_name"] != "My session" || meta["session_path"] != "/search/rerank" {
		t.Errorf("-> %d, metadata session_name %q session_path %q; want them logged", w.Code, meta["session_name"], meta["session_path"])
	}
}

func TestR7ClientRequestIDEndToEnd(t *testing.T) {
	capt := &r7Capture{}
	srv := newR7Gateway(t, capt)
	w := r7Do(srv, "POST", "/v1/chat/completions", r7ChatBody, r7ByokKey, map[string]string{"x-agentcc-request-id": "client-req-123"})
	meta, _, rcID := capt.snapshot()
	gw := w.Header().Get("x-agentcc-request-id")
	if w.Code != http.StatusOK || gw == "" || gw == "client-req-123" || rcID != gw {
		t.Errorf("-> %d, response request id %q, request context's %q; want the gateway's own id on both", w.Code, gw, rcID)
	}
	if got := w.Header().Get("x-agentcc-client-request-id"); got != "client-req-123" {
		t.Errorf("x-agentcc-client-request-id = %q, want client-req-123", got)
	}
	if meta["client_request_id"] != "client-req-123" {
		t.Errorf("metadata client_request_id = %q, want client-req-123", meta["client_request_id"])
	}
}

func TestR7AlwaysPresentHeadersEndToEnd(t *testing.T) {
	srv := newR7Gateway(t, nil)
	always := []string{"x-agentcc-request-id", "x-agentcc-trace-id", "x-agentcc-latency-ms"}
	cases := []struct {
		name, method, path, body, key string
		hdr                           map[string]string
	}{
		{"chat ok", "POST", "/v1/chat/completions", r7ChatBody, r7ByokKey, nil},
		{"chat unknown model", "POST", "/v1/chat/completions", `{"model":"nope","messages":[{"role":"user","content":"hi"}]}`, r7ByokKey, nil},
		{"chat no key", "POST", "/v1/chat/completions", r7ChatBody, "", nil},
		{"chat bad body", "POST", "/v1/chat/completions", `{`, r7ByokKey, nil},
		{"config refused", "POST", "/v1/chat/completions", r7ChatBody, r7ByokKey, map[string]string{"x-agentcc-config": "nope"}},
		{"embeddings ok", "POST", "/v1/embeddings", `{"model":"m-embed","input":"hi"}`, r7ByokKey, nil},
		{"rerank", "POST", "/v1/rerank", `{"model":"m-rerank","query":"q","documents":["a"]}`, r7ByokKey, nil},
		{"messages", "POST", "/v1/messages", `{"model":"m-chat","max_tokens":16,"messages":[{"role":"user","content":"hi"}]}`, r7ByokKey, nil},
		{"models list", "GET", "/v1/models", "", r7ByokKey, nil},
		{"admin unauthorized", "GET", "/-/config", "", "", nil},
		{"unknown route", "GET", "/v1/nothing-here", "", r7ByokKey, nil},
	}
	for _, c := range cases {
		w := r7Do(srv, c.method, c.path, c.body, c.key, c.hdr)
		var missing []string
		for _, h := range always {
			if w.Header().Get(h) == "" {
				missing = append(missing, h)
			}
		}
		if len(missing) > 0 {
			t.Errorf("%s (%d): missing %v", c.name, w.Code, missing)
		}
	}
}
