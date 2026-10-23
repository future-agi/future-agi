package server

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/gorilla/websocket"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	authplugin "github.com/futureagi/agentcc-gateway/internal/plugins/auth"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/providers/speech"
)

// With request auth on, KeyAuth gates every /v1/ route and the MCP and A2A
// endpoints. These tests walk every route outside /v1 and /-/: each one must be
// listed below as public on purpose, be one of the /v1beta and speech relay
// routes the pipeline's auth plugin gates, or need a key on every spelling of
// its path and for every method.

// publicRoutes answer without a credential by design. Adding a route outside
// /v1 and /-/ means deciding here whether it is public.
var publicRoutes = map[string]bool{
	"GET /healthz": true,
	"GET /readyz":  true,
	"GET /livez":   true,
	// A2A discovery. The card carries only the configured gateway name,
	// description and version, and says the API wants a bearer token.
	"GET /.well-known/agent.json": true,
}

// pipelineAuthPrefix holds the native Google GenAI routes. Their handler runs
// the request pipeline, whose auth plugin requires a key whenever auth is on.
// KeyAuth does not gate them: Google clients send the key as ?key= or
// x-goog-api-key, which KeyAuth does not read.
const pipelineAuthPrefix = "/v1beta/"

// speechRelayRoute reports whether pattern is a speech vendor's relayed
// WebSocket API (speech.Endpoints, registered as GET /<vendor><path>). Like
// /v1beta, these are left to the pipeline's auth plugin: speech clients send
// the key as "Authorization: Token", X-API-Key or ?api_key=, and KeyAuth reads
// only a bearer token or x-api-key.
func speechRelayRoute(pattern string) bool {
	for _, ep := range speech.Endpoints() {
		if pattern == "/"+ep.Vendor+ep.Path {
			return true
		}
	}
	return false
}

// keyGatedRoutes returns every registered route outside /v1, /-/ and
// /v1beta, and not a speech relay route, that is not public.
func keyGatedRoutes(t *testing.T, srv *Server) []Route {
	t.Helper()
	var out []Route
	for _, rt := range srv.router.Routes() {
		if strings.HasPrefix(rt.Pattern, "/v1/") || strings.HasPrefix(rt.Pattern, "/-/") ||
			strings.HasPrefix(rt.Pattern, pipelineAuthPrefix) || speechRelayRoute(rt.Pattern) {
			continue
		}
		if publicRoutes[rt.Method+" "+rt.Pattern] {
			continue
		}
		out = append(out, rt)
	}
	return out
}

func serveWithHeaders(t *testing.T, srv *Server, method, target, body string, headers map[string]string, timeout time.Duration) *httptest.ResponseRecorder {
	t.Helper()
	req := httptest.NewRequest(method, target, bytes.NewReader([]byte(body)))
	if body != "" {
		req.Header.Set("Content-Type", "application/json")
	}
	for k, v := range headers {
		req.Header.Set(k, v)
	}
	ctx, cancel := context.WithTimeout(req.Context(), timeout)
	defer cancel()
	w := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(w, req.WithContext(ctx))
	return w
}

func requireKeyChallenge(t *testing.T, w *httptest.ResponseRecorder, what string) {
	t.Helper()
	if w.Code != http.StatusUnauthorized {
		t.Fatalf("%s: status %d, want 401. Body: %s", what, w.Code, w.Body.String())
	}
	if got := w.Header().Get("WWW-Authenticate"); !strings.HasPrefix(got, "Bearer ") {
		t.Fatalf("%s: WWW-Authenticate %q, want a Bearer challenge", what, got)
	}
	if id := w.Header().Get("MCP-Session-Id"); id != "" {
		t.Fatalf("%s: answered with MCP session %q", what, id)
	}
}

func TestPublicRoutesOutsideV1AnswerWithoutAKey(t *testing.T) {
	srv := newCanonicalPathTestServer(t)
	have := map[string]bool{}
	for _, rt := range srv.router.Routes() {
		have[rt.Method+" "+rt.Pattern] = true
	}
	for route := range publicRoutes {
		if !have[route] {
			t.Fatalf("public route %q is not registered; the allowlist is stale", route)
		}
		method, target, _ := strings.Cut(route, " ")
		w := serveCanonical(t, srv, method, target, "")
		if w.Code != http.StatusOK {
			t.Fatalf("%s with no key: status %d, want 200. Body: %s", route, w.Code, w.Body.String())
		}
	}
}

// The /v1beta routes are left to the pipeline's auth plugin. Build the
// pipeline the way cmd/agentcc does when auth is on, and prove a caller with no
// key, or with a key that is not one, never reaches the provider on any such
// route, while a key holder does.
func TestPipelineAuthenticatedRoutesNeverReachTheProviderWithoutAKey(t *testing.T) {
	var providerCalls atomic.Int64
	mock := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		// Count completions only: background health probes may hit other paths.
		if r.Method != http.MethodPost || r.URL.Path != "/v1/chat/completions" {
			w.WriteHeader(http.StatusNotFound)
			return
		}
		providerCalls.Add(1)
		w.Header().Set("Content-Type", "application/json")
		w.Write([]byte(`{"id":"c1","object":"chat.completion","created":1,"model":"gpt-4o",` +
			`"choices":[{"index":0,"message":{"role":"assistant","content":"ok"},"finish_reason":"stop"}],` +
			`"usage":{"prompt_tokens":1,"completion_tokens":1,"total_tokens":2}}`))
	}))
	t.Cleanup(mock.Close)

	cfg := config.DefaultConfig()
	cfg.Admin.Token = canonAdminToken
	cfg.Auth.Enabled = true
	cfg.Auth.Keys = []config.AuthKeyConfig{{Name: "genai", Key: canonAPIKey, Owner: "test-org", KeyType: "internal"}}
	cfg.Providers["openai"] = config.ProviderConfig{BaseURL: mock.URL, APIKey: "dummy", APIFormat: "openai", Models: []string{"gpt-4o"}}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatalf("creating registry: %v", err)
	}
	keyStore := auth.NewKeyStore(cfg.Auth)
	srv := New(cfg, "", registry, pipeline.NewEngine(authplugin.New(keyStore, true)), keyStore, nil, nil, nil, testModelDBPtr(), nil, nil)
	srv.ready.Store(true)

	routes := registeredRoutes(t, srv, pipelineAuthPrefix)
	requireExemplars(t, routes, "POST /v1beta/models/{model_action}", "POST /v1beta/models/{provider}/{model_action}")
	body := `{"contents":[{"parts":[{"text":"hi"}]}]}`

	for _, rt := range routes {
		target := strings.Replace(concretePath(rt.Pattern), "canon-model_action", "gpt-4o:generateContent", 1)
		target = strings.Replace(target, "canon-provider", "openai", 1)
		for label, headers := range map[string]map[string]string{
			"no key":               nil,
			"wrong bearer":         {"Authorization": "Bearer sk-not-a-key"},
			"wrong x-goog-api-key": {"x-goog-api-key": "sk-not-a-key"},
			"admin token":          {"Authorization": "Bearer " + canonAdminToken},
		} {
			t.Run(rt.Method+" "+rt.Pattern+"/"+label, func(t *testing.T) {
				before := providerCalls.Load()
				w := serveWithHeaders(t, srv, rt.Method, target, body, headers, 2*time.Second)
				if w.Code < 400 || providerCalls.Load() != before {
					t.Fatalf("%s %s with %s: status %d, provider calls %d -> %d. Body: %s",
						rt.Method, target, label, w.Code, before, providerCalls.Load(), w.Body.String())
				}
			})
		}
	}

	// Positive control: the same request with the key reaches the provider.
	before := providerCalls.Load()
	w := serveWithHeaders(t, srv, http.MethodPost, "/v1beta/models/gpt-4o:generateContent", body,
		map[string]string{"x-goog-api-key": canonAPIKey}, 2*time.Second)
	if w.Code != http.StatusOK || providerCalls.Load() != before+1 {
		t.Fatalf("POST /v1beta/models/gpt-4o:generateContent with the key: status %d, provider calls %d -> %d. Body: %s",
			w.Code, before, providerCalls.Load(), w.Body.String())
	}
}

// The speech relay routes are left to the pipeline's auth plugin. With a
// provider configured for every speech vendor, a caller with no key, or with
// a credential that is not a key, never reaches the vendor on any relay route,
// in any of the ways a speech client sends its key, while a key holder does.
func TestSpeechRelayRoutesNeverReachTheVendorWithoutAKey(t *testing.T) {
	var vendorDials atomic.Int64
	upgrader := websocket.Upgrader{}
	vendor := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if !websocket.IsWebSocketUpgrade(r) {
			w.WriteHeader(http.StatusNotFound)
			return
		}
		vendorDials.Add(1)
		conn, err := upgrader.Upgrade(w, r, nil)
		if err != nil {
			return
		}
		defer conn.Close()
		for {
			if _, _, err := conn.ReadMessage(); err != nil {
				return
			}
		}
	}))
	t.Cleanup(vendor.Close)

	cfg := config.DefaultConfig()
	cfg.Admin.Token = canonAdminToken
	cfg.Auth.Enabled = true
	cfg.Auth.Keys = []config.AuthKeyConfig{{Name: "speech", Key: canonAPIKey, Owner: "platform", KeyType: "internal"}}
	vendors := map[string]bool{}
	for _, ep := range speech.Endpoints() {
		vendors[ep.Vendor] = true
	}
	for name := range vendors {
		cfg.Providers[name] = config.ProviderConfig{BaseURL: vendor.URL, APIKey: "platform-" + name + "-key", APIFormat: name}
	}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatalf("creating registry: %v", err)
	}
	keyStore := auth.NewKeyStore(cfg.Auth)
	srv := New(cfg, "", registry, pipeline.NewEngine(authplugin.New(keyStore, true)), keyStore, nil, nil, nil, testModelDBPtr(), nil, nil)
	srv.ready.Store(true)
	gateway := httptest.NewServer(srv.httpServer.Handler)
	t.Cleanup(gateway.Close)

	var routes []Route
	for _, rt := range srv.router.Routes() {
		if speechRelayRoute(rt.Pattern) {
			routes = append(routes, rt)
		}
	}
	requireExemplars(t, routes, "GET /deepgram/v1/listen", "GET /cartesia/tts/websocket")

	// Audio parameters every transcription route needs, and a model, so the
	// request reaches the pipeline rather than stopping at the relay's own checks.
	const query = "?encoding=linear16&sample_rate=16000&model=nova-3"
	dial := func(path, rawQuery string, header http.Header) (*websocket.Conn, *http.Response, error) {
		return websocket.DefaultDialer.Dial("ws"+strings.TrimPrefix(gateway.URL, "http")+path+query+rawQuery, header)
	}
	for _, rt := range routes {
		for label, tc := range map[string]struct {
			query  string
			header http.Header
		}{
			"no key":            {},
			"wrong bearer":      {header: http.Header{"Authorization": {"Bearer sk-not-a-key"}}},
			"wrong token":       {header: http.Header{"Authorization": {"Token sk-not-a-key"}}},
			"wrong x-api-key":   {header: http.Header{"X-API-Key": {"sk-not-a-key"}}},
			"wrong api_key":     {query: "&api_key=sk-not-a-key"},
			"admin token":       {header: http.Header{"Authorization": {"Bearer " + canonAdminToken}}},
			"admin token token": {header: http.Header{"Authorization": {"Token " + canonAdminToken}}},
		} {
			t.Run(rt.Method+" "+rt.Pattern+"/"+label, func(t *testing.T) {
				before := vendorDials.Load()
				conn, resp, err := dial(rt.Pattern, tc.query, tc.header)
				if err == nil {
					conn.Close()
					t.Fatalf("%s with %s: the gateway accepted the connection", rt.Pattern, label)
				}
				if resp == nil || resp.StatusCode < 400 || vendorDials.Load() != before {
					status := 0
					if resp != nil {
						status = resp.StatusCode
					}
					t.Fatalf("%s with %s: status %d, vendor dials %d -> %d (%v)",
						rt.Pattern, label, status, before, vendorDials.Load(), err)
				}
			})
		}

		// Positive control: the same route with the key reaches the vendor.
		t.Run(rt.Method+" "+rt.Pattern+"/key", func(t *testing.T) {
			before := vendorDials.Load()
			conn, resp, err := dial(rt.Pattern, "", http.Header{"X-API-Key": {canonAPIKey}})
			if err != nil {
				status := 0
				if resp != nil {
					status = resp.StatusCode
				}
				t.Fatalf("%s with the key: status %d (%v)", rt.Pattern, status, err)
			}
			conn.Close()
			if vendorDials.Load() != before+1 {
				t.Fatalf("%s with the key: vendor dials %d -> %d, want one", rt.Pattern, before, vendorDials.Load())
			}
		})
	}
}

func TestEveryNonPublicRouteOutsideV1RequiresAKeyOnEverySpelling(t *testing.T) {
	srv := newCanonicalPathTestServer(t)
	routes := keyGatedRoutes(t, srv)
	requireExemplars(t, routes, "POST /mcp", "GET /mcp", "POST /a2a")

	for _, rt := range routes {
		canonical := concretePath(rt.Pattern)
		targets := nonCanonicalVariants(canonical)
		targets["canonical"] = canonical
		for name, target := range targets {
			for label, headers := range map[string]map[string]string{
				"no key":            nil,
				"wrong bearer":      {"Authorization": "Bearer sk-not-a-key"},
				"wrong x-api-key":   {"x-api-key": "sk-not-a-key"},
				"admin token":       {"Authorization": "Bearer " + canonAdminToken},
				"non-bearer scheme": {"Authorization": "Basic " + canonAPIKey},
			} {
				t.Run(rt.Method+" "+rt.Pattern+"/"+name+"/"+label, func(t *testing.T) {
					w := serveWithHeaders(t, srv, rt.Method, target, `{"jsonrpc":"2.0","id":1,"method":"ping"}`, headers, 2*time.Second)
					requireKeyChallenge(t, w, rt.Method+" "+target+" with "+label)
				})
			}
		}
	}
}

// The gate covers the whole MCP and A2A path subtree and every method, not
// only the registered routes: DELETE /mcp (session teardown in the MCP
// transport), and legacy-transport paths such as /mcp/sse and /mcp/messages
// must not answer differently to a caller without a key.
func TestKeyGatedRootsRefuseEveryMethodAndSubpathWithoutAKey(t *testing.T) {
	srv := newCanonicalPathTestServer(t)
	for _, root := range []string{"/mcp", "/a2a"} {
		for _, p := range []string{root, root + "/sse", root + "/messages", root + "/x/y", "/" + root, root + "/"} {
			for _, method := range []string{http.MethodGet, http.MethodPost, http.MethodPut, http.MethodPatch, http.MethodDelete, http.MethodHead} {
				t.Run(method+" "+p, func(t *testing.T) {
					w := serveWithHeaders(t, srv, method, p, "", nil, 2*time.Second)
					requireKeyChallenge(t, w, method+" "+p+" with no key")
				})
			}
		}
	}
}

// A key holder gets the full MCP and A2A service, with the key sent
// either way the gateway accepts it.
func TestKeyGatedRoutesServeAKeyHolder(t *testing.T) {
	srv := newCanonicalPathTestServer(t)
	initialize := `{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","clientInfo":{"name":"t","version":"1"}}}`

	for label, key := range map[string]map[string]string{
		"bearer":    {"Authorization": "Bearer " + canonAPIKey},
		"x-api-key": {"x-api-key": canonAPIKey},
	} {
		t.Run(label, func(t *testing.T) {
			w := serveWithHeaders(t, srv, http.MethodPost, "/mcp", initialize, key, 2*time.Second)
			sessionID := w.Header().Get("MCP-Session-Id")
			if w.Code != http.StatusOK || sessionID == "" {
				t.Fatalf("POST /mcp initialize with a %s key: status %d, session %q. Body: %s", label, w.Code, sessionID, w.Body.String())
			}

			withSession := map[string]string{"MCP-Session-Id": sessionID}
			for k, v := range key {
				withSession[k] = v
			}
			w = serveWithHeaders(t, srv, http.MethodPost, "/mcp", `{"jsonrpc":"2.0","id":2,"method":"tools/list"}`, withSession, 2*time.Second)
			var list struct {
				Result *json.RawMessage `json:"result"`
				Error  *struct {
					Message string `json:"message"`
				} `json:"error"`
			}
			if err := json.Unmarshal(w.Body.Bytes(), &list); err != nil || w.Code != http.StatusOK || list.Result == nil {
				t.Fatalf("POST /mcp tools/list with a %s key: status %d, body %s", label, w.Code, w.Body.String())
			}

			w = serveWithHeaders(t, srv, http.MethodGet, "/mcp", "", withSession, 200*time.Millisecond)
			if w.Code != http.StatusOK || w.Header().Get("Content-Type") != "text/event-stream" {
				t.Fatalf("GET /mcp with a %s key: status %d, content type %q. Body: %s", label, w.Code, w.Header().Get("Content-Type"), w.Body.String())
			}

			w = serveWithHeaders(t, srv, http.MethodPost, "/a2a", `{"jsonrpc":"2.0","id":3,"method":"tasks/get","params":{"taskId":"task-none"}}`, key, 2*time.Second)
			if w.Code != http.StatusOK || !strings.Contains(w.Body.String(), "task not found") {
				t.Fatalf("POST /a2a tasks/get with a %s key: status %d, body %s", label, w.Code, w.Body.String())
			}
		})
	}
}
