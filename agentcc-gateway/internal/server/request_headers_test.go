package server

// Through the whole gateway: what a documented request
// header does on the routes a client calls, and the headers every
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
	reqHdrOrg      = "org-request-headers"
	reqHdrByokKey  = "sk-agentcc-request-headers-byok-0123456789abcdef"
	reqHdrAdminTok = "request-headers-admin-token-0123456789abcdef0123"
)

// metaCapture records what the pipeline saw for the last request.
type metaCapture struct {
	mu        sync.Mutex
	meta      map[string]string
	custom    []string
	requestID string
}

func (c *metaCapture) Name() string  { return "meta-capture" }
func (c *metaCapture) Priority() int { return 999 }
func (c *metaCapture) ProcessRequest(_ context.Context, rc *models.RequestContext) pipeline.PluginResult {
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
func (c *metaCapture) ProcessResponse(context.Context, *models.RequestContext) pipeline.PluginResult {
	return pipeline.ResultContinue()
}

func (c *metaCapture) snapshot() (map[string]string, []string, string) {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.meta, c.custom, c.requestID
}

// startHeadersUpstream answers chat, embeddings and rerank as an OpenAI-compatible
// provider would.
func startHeadersUpstream(t *testing.T) *httptest.Server {
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

// newHeadersGateway: a BYOK key of reqHdrOrg reaches its own "openai" (the org's key
// on the config.yaml provider) for m-chat and m-embed; "node-only" is a
// config.yaml provider the org holds no key for.
func newHeadersGateway(t *testing.T, capture *metaCapture) *Server {
	t.Helper()
	up := startHeadersUpstream(t)
	cfg := config.DefaultConfig()
	cfg.Admin.Token = reqHdrAdminTok
	cfg.Auth.Enabled = true
	cfg.Auth.Keys = []config.AuthKeyConfig{
		{Name: "byok", Key: reqHdrByokKey, Owner: "t", KeyType: "byok", Metadata: map[string]string{"org_id": reqHdrOrg}},
	}
	off := false
	cfg.Providers["openai"] = config.ProviderConfig{BaseURL: up.URL, APIKey: "operator-key", APIFormat: "openai", Models: []string{"m-node"}, AutoDiscover: &off}
	cfg.Providers["node-only"] = config.ProviderConfig{BaseURL: up.URL, APIKey: "operator-key", APIFormat: "openai", Models: []string{"m-chat"}, AutoDiscover: &off}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatalf("creating registry: %v", err)
	}
	tenants := tenant.NewStore()
	tenants.Set(reqHdrOrg, &tenant.OrgConfig{Providers: map[string]*tenant.ProviderConfig{
		"openai": {APIKey: "org-key", Enabled: true, Models: []string{"m-chat", "m-embed"}},
	}})
	keyStore := auth.NewKeyStore(cfg.Auth)
	plugins := []pipeline.Plugin{authplugin.New(keyStore, true)}
	if capture != nil {
		plugins = append(plugins, capture)
	}
	srv := New(cfg, "", registry, pipeline.NewEngine(plugins...), keyStore, nil, nil, nil, testModelDBPtr(), tenants, nil)
	srv.ready.Store(true)
	return srv
}

const headersChatBody = `{"model":"m-chat","messages":[{"role":"user","content":"hi"}]}`

func doRequest(srv *Server, method, path, body, key string, hdr map[string]string) *httptest.ResponseRecorder {
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

func responseErrorCode(w *httptest.ResponseRecorder) string {
	var e models.ErrorResponse
	_ = json.Unmarshal(w.Body.Bytes(), &e)
	return e.Error.Code
}

func TestAgentccConfigHeaderEndToEnd(t *testing.T) {
	capt := &metaCapture{}
	srv := newHeadersGateway(t, capt)

	// The header gives timeout-ms 1234; the config does too.
	for _, hdr := range []map[string]string{
		{"x-agentcc-request-timeout": "1234"},
		{"x-agentcc-config": `{"request-timeout":1234}`},
	} {
		w := doRequest(srv, "POST", "/v1/chat/completions", headersChatBody, reqHdrByokKey, hdr)
		if w.Code != http.StatusOK || w.Header().Get("x-agentcc-timeout-ms") != "1234" {
			t.Errorf("%v -> %d timeout-ms %q, want 200 and 1234; body %s", hdr, w.Code, w.Header().Get("x-agentcc-timeout-ms"), w.Body.String())
		}
	}

	w := doRequest(srv, "POST", "/v1/chat/completions", headersChatBody, reqHdrByokKey, map[string]string{"x-agentcc-config": `{"cache-ttl":300,"cache-namespace":"prod"}`})
	meta, _, _ := capt.snapshot()
	if w.Code != http.StatusOK || meta["cache_ttl"] != "300" || meta["cache_namespace"] != "prod" {
		t.Errorf("cache config -> %d, metadata cache_ttl %q cache_namespace %q; want 200, 300, prod", w.Code, meta["cache_ttl"], meta["cache_namespace"])
	}

	// A config that is not JSON is refused.
	w = doRequest(srv, "POST", "/v1/chat/completions", headersChatBody, reqHdrByokKey, map[string]string{"x-agentcc-config": "this is not json"})
	if w.Code != http.StatusBadRequest || responseErrorCode(w) != "invalid_agentcc_config" {
		t.Errorf("non-JSON config -> %d %s, want 400 invalid_agentcc_config; body %s", w.Code, responseErrorCode(w), w.Body.String())
	}

	// A value is checked as its own header is: the retry header's 400.
	w = doRequest(srv, "POST", "/v1/chat/completions", headersChatBody, reqHdrByokKey, map[string]string{"x-agentcc-config": `{"max-attempts":9}`})
	if w.Code != http.StatusBadRequest || responseErrorCode(w) != "invalid_retry_header" {
		t.Errorf("max-attempts 9 in the config -> %d %s, want the header's 400 invalid_retry_header", w.Code, responseErrorCode(w))
	}

	// The config can pick no provider the header could not: a provider the
	// org holds no key for is refused exactly as x-agentcc-provider-lock is.
	viaHeader := doRequest(srv, "POST", "/v1/chat/completions", headersChatBody, reqHdrByokKey, map[string]string{"x-agentcc-provider-lock": "node-only"})
	viaConfig := doRequest(srv, "POST", "/v1/chat/completions", headersChatBody, reqHdrByokKey, map[string]string{"x-agentcc-config": `{"provider-lock":"node-only"}`})
	if viaHeader.Code == http.StatusOK || viaConfig.Code != viaHeader.Code || responseErrorCode(viaConfig) != responseErrorCode(viaHeader) {
		t.Errorf("provider-lock node-only: header -> %d %s, config -> %d %s; want the same refusal", viaHeader.Code, responseErrorCode(viaHeader), viaConfig.Code, responseErrorCode(viaConfig))
	}

	// Nor set a gateway-owned metadata key through its metadata.
	w = doRequest(srv, "POST", "/v1/chat/completions", headersChatBody, reqHdrByokKey, map[string]string{"x-agentcc-config": `{"metadata":{"org_id":"org-other","key_type":"internal","team":"ml"}}`})
	meta, _, _ = capt.snapshot()
	if w.Code != http.StatusOK || meta["org_id"] != reqHdrOrg || meta["key_type"] == "internal" || meta["team"] != "ml" {
		t.Errorf("config metadata -> %d org_id %q key_type %q team %q; want the key's org, never internal, team ml", w.Code, meta["org_id"], meta["key_type"], meta["team"])
	}
}

func TestGuardrailPolicyAliasEndToEnd(t *testing.T) {
	capt := &metaCapture{}
	srv := newHeadersGateway(t, capt)
	for _, hdr := range []map[string]string{
		{"X-Guardrail-Policy": "log-only"},
		{"x-agentcc-guardrail-policy": "log-only"},
		{"x-agentcc-config": `{"guardrail-policy":"log-only"}`},
	} {
		w := doRequest(srv, "POST", "/v1/chat/completions", headersChatBody, reqHdrByokKey, hdr)
		meta, _, _ := capt.snapshot()
		if meta["x-guardrail-policy"] != "log-only" {
			t.Errorf("%v -> %d, the guardrail plugin's x-guardrail-policy = %q, want log-only", hdr, w.Code, meta["x-guardrail-policy"])
		}
	}
}

func TestPropertyHeadersEndToEnd(t *testing.T) {
	capt := &metaCapture{}
	srv := newHeadersGateway(t, capt)
	for _, tc := range []struct {
		path, body string
	}{
		{"/v1/chat/completions", headersChatBody},
		{"/v1/embeddings", `{"model":"m-embed","input":"hi"}`},
	} {
		w := doRequest(srv, "POST", tc.path, tc.body, reqHdrByokKey, map[string]string{
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
		if meta["org_id"] != reqHdrOrg || meta["key_type"] == "internal" || slices.Contains(custom, "cost") {
			t.Errorf("%s: a property set a gateway-owned key: org_id %q key_type %q custom %v", tc.path, meta["org_id"], meta["key_type"], custom)
		}
	}
}

func TestSessionNamePathEndToEnd(t *testing.T) {
	capt := &metaCapture{}
	srv := newHeadersGateway(t, capt)
	w := doRequest(srv, "POST", "/v1/chat/completions", headersChatBody, reqHdrByokKey, map[string]string{
		"x-agentcc-session-id": "s1", "x-agentcc-session-name": "My session", "x-agentcc-session-path": "/search/rerank",
	})
	meta, _, _ := capt.snapshot()
	if w.Code != http.StatusOK || meta["session_name"] != "My session" || meta["session_path"] != "/search/rerank" {
		t.Errorf("-> %d, metadata session_name %q session_path %q; want them logged", w.Code, meta["session_name"], meta["session_path"])
	}
}

func TestClientRequestIDEndToEnd(t *testing.T) {
	capt := &metaCapture{}
	srv := newHeadersGateway(t, capt)
	w := doRequest(srv, "POST", "/v1/chat/completions", headersChatBody, reqHdrByokKey, map[string]string{"x-agentcc-request-id": "client-req-123"})
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

func TestAlwaysPresentHeadersEndToEnd(t *testing.T) {
	srv := newHeadersGateway(t, nil)
	always := []string{"x-agentcc-request-id", "x-agentcc-trace-id", "x-agentcc-latency-ms"}
	cases := []struct {
		name, method, path, body, key string
		hdr                           map[string]string
	}{
		{"chat ok", "POST", "/v1/chat/completions", headersChatBody, reqHdrByokKey, nil},
		{"chat unknown model", "POST", "/v1/chat/completions", `{"model":"nope","messages":[{"role":"user","content":"hi"}]}`, reqHdrByokKey, nil},
		{"chat no key", "POST", "/v1/chat/completions", headersChatBody, "", nil},
		{"chat bad body", "POST", "/v1/chat/completions", `{`, reqHdrByokKey, nil},
		{"config refused", "POST", "/v1/chat/completions", headersChatBody, reqHdrByokKey, map[string]string{"x-agentcc-config": "nope"}},
		{"embeddings ok", "POST", "/v1/embeddings", `{"model":"m-embed","input":"hi"}`, reqHdrByokKey, nil},
		{"rerank", "POST", "/v1/rerank", `{"model":"m-rerank","query":"q","documents":["a"]}`, reqHdrByokKey, nil},
		{"messages", "POST", "/v1/messages", `{"model":"m-chat","max_tokens":16,"messages":[{"role":"user","content":"hi"}]}`, reqHdrByokKey, nil},
		{"models list", "GET", "/v1/models", "", reqHdrByokKey, nil},
		{"admin unauthorized", "GET", "/-/config", "", "", nil},
		{"unknown route", "GET", "/v1/nothing-here", "", reqHdrByokKey, nil},
	}
	for _, c := range cases {
		w := doRequest(srv, c.method, c.path, c.body, c.key, c.hdr)
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
