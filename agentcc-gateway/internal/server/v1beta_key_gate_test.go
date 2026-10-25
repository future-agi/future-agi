package server

import (
	"bytes"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	authplugin "github.com/futureagi/agentcc-gateway/internal/plugins/auth"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// /v1beta (the native Google API) is a key-gated root, as /v1 and the MCP
// and A2A roots are. The gate reads the key the handler reads, in its order
// (?key=, then x-goog-api-key, then a Bearer Authorization header; never
// x-api-key), so every way a Google SDK sends its key works, and a request
// is admitted exactly when the pipeline would admit it.

const (
	gateOrg   = "org-v1beta"
	gateKey   = "sk-agentcc-v1beta-gate-0001"
	gateModel = "gemini-gate"
)

type v1betaGateway struct {
	srv   *Server
	calls atomic.Int32
}

func newV1BetaGateway(t *testing.T) *v1betaGateway {
	t.Helper()
	g := &v1betaGateway{}
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		// Count the model calls only, not the provider's connectivity check.
		if strings.HasPrefix(r.URL.Path, "/v1beta/models/") {
			g.calls.Add(1)
		}
		w.Header().Set("Content-Type", "application/json")
		io.WriteString(w, `{"candidates":[{"content":{"role":"model","parts":[{"text":"ok"}]},"finishReason":"STOP","index":0}],`+
			`"usageMetadata":{"promptTokenCount":1,"candidatesTokenCount":1,"totalTokenCount":2}}`)
	}))
	t.Cleanup(upstream.Close)

	cfg := config.DefaultConfig()
	cfg.Auth.Enabled = true
	cfg.Auth.Keys = []config.AuthKeyConfig{{Name: "gate", Key: gateKey, Owner: "o", KeyType: "byok",
		Metadata: map[string]string{"org_id": gateOrg}}}
	cfg.Providers["gemini"] = config.ProviderConfig{BaseURL: upstream.URL, APIFormat: "gemini", Models: []string{gateModel}}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatal(err)
	}
	tenants := tenant.NewStore()
	tenants.Set(gateOrg, &tenant.OrgConfig{Providers: map[string]*tenant.ProviderConfig{
		"gemini": {Enabled: true, APIKey: "org-google-key", Models: []string{gateModel}},
	}})
	ks := auth.NewKeyStore(cfg.Auth)
	g.srv = New(cfg, "", registry, pipeline.NewEngine(authplugin.New(ks, true)), ks, nil, nil, nil, testModelDBPtr(), tenants, nil)
	g.srv.ready.Store(true)
	return g
}

func (g *v1betaGateway) do(t *testing.T, path, body string, headers map[string]string) *httptest.ResponseRecorder {
	t.Helper()
	req := httptest.NewRequest(http.MethodPost, path, bytes.NewReader([]byte(body)))
	req.Header.Set("Content-Type", "application/json")
	for k, v := range headers {
		req.Header.Set(k, v)
	}
	w := httptest.NewRecorder()
	g.srv.httpServer.Handler.ServeHTTP(w, req)
	return w
}

const gateBody = `{"contents":[{"role":"user","parts":[{"text":"hi"}]}]}`

func gatePath(action string) string { return "/v1beta/models/" + gateModel + ":" + action }

// Without a valid key every /v1beta request gets the gate's 401, whatever its
// path or body: nothing about the model, the action or the body is answered
// before the key is checked.
func TestV1Beta_NoValidKeyIsRefusedAtTheGate(t *testing.T) {
	g := newV1BetaGateway(t)
	for _, tc := range []struct {
		name, path, body string
		headers          map[string]string
	}{
		{"no key, known model", gatePath("generateContent"), gateBody, nil},
		{"no key, unknown model", "/v1beta/models/no-such-model:generateContent", gateBody, nil},
		{"no key, invalid JSON", gatePath("generateContent"), "{", nil},
		{"no key, unsupported action", gatePath("frobnicate"), gateBody, nil},
		{"no key, provider in the path", "/v1beta/models/gemini/" + gateModel + ":generateContent", gateBody, nil},
		{"wrong x-goog-api-key", gatePath("generateContent"), gateBody, map[string]string{"x-goog-api-key": "sk-wrong"}},
		{"wrong ?key=", gatePath("generateContent") + "?key=sk-wrong", gateBody, nil},
		{"wrong Bearer", gatePath("generateContent"), gateBody, map[string]string{"Authorization": "Bearer sk-wrong"}},
		{"wrong key, unsupported action", gatePath("frobnicate"), gateBody, map[string]string{"x-goog-api-key": "sk-wrong"}},
		// The handler never reads x-api-key on this route, so neither does the gate.
		{"valid key in x-api-key only", gatePath("generateContent"), gateBody, map[string]string{"x-api-key": gateKey}},
		// The auth plugin reads an Authorization header only with the Bearer scheme.
		{"valid key in Authorization without Bearer", gatePath("generateContent"), gateBody,
			map[string]string{"Authorization": gateKey}},
		// The handler reads ?key= first and x-goog-api-key next, so a wrong
		// key there is the request's key even when a Bearer header is valid.
		{"wrong ?key=, valid Bearer", gatePath("generateContent") + "?key=sk-wrong", gateBody,
			map[string]string{"Authorization": "Bearer " + gateKey}},
		{"wrong x-goog-api-key, valid Bearer", gatePath("generateContent"), gateBody,
			map[string]string{"x-goog-api-key": "sk-wrong", "Authorization": "Bearer " + gateKey}},
		{"wrong ?key=, valid x-goog-api-key", gatePath("generateContent") + "?key=sk-wrong", gateBody,
			map[string]string{"x-goog-api-key": gateKey}},
	} {
		t.Run(tc.name, func(t *testing.T) {
			w := g.do(t, tc.path, tc.body, tc.headers)
			if w.Code != http.StatusUnauthorized || !strings.Contains(w.Body.String(), "invalid_api_key") {
				t.Errorf("status %d, want the gate's 401 invalid_api_key; body %s", w.Code, w.Body.String())
			}
		})
	}
	if n := g.calls.Load(); n != 0 {
		t.Errorf("the upstream was called %d times without a valid key", n)
	}
}

// Every way the handler takes a key still works through the gate.
func TestV1Beta_EveryKeyFormTheHandlerReadsStillWorks(t *testing.T) {
	for _, tc := range []struct {
		name, path string
		headers    map[string]string
	}{
		{"x-goog-api-key", gatePath("generateContent"), map[string]string{"x-goog-api-key": gateKey}},
		{"?key=", gatePath("generateContent") + "?key=" + gateKey, nil},
		{"Bearer", gatePath("generateContent"), map[string]string{"Authorization": "Bearer " + gateKey}},
		{"valid ?key=, wrong Bearer", gatePath("generateContent") + "?key=" + gateKey,
			map[string]string{"Authorization": "Bearer sk-wrong"}},
		{"valid x-goog-api-key, wrong Bearer", gatePath("generateContent"),
			map[string]string{"x-goog-api-key": gateKey, "Authorization": "Bearer sk-wrong"}},
		{"valid ?key=, wrong x-goog-api-key", gatePath("generateContent") + "?key=" + gateKey,
			map[string]string{"x-goog-api-key": "sk-wrong"}},
		{"countTokens", gatePath("countTokens"), map[string]string{"x-goog-api-key": gateKey}},
	} {
		t.Run(tc.name, func(t *testing.T) {
			g := newV1BetaGateway(t)
			w := g.do(t, tc.path, gateBody, tc.headers)
			if w.Code != http.StatusOK {
				t.Fatalf("status %d, want 200; body %s", w.Code, w.Body.String())
			}
			if n := g.calls.Load(); n != 1 {
				t.Errorf("the upstream was called %d times, want 1", n)
			}
		})
	}
}
