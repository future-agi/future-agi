package server

// Three provider-resolution behaviours the gateway relies on: a key
// limited to its org's providers never reaches the global, operator-keyed
// "openai" provider for Assistants and Vector Stores; a chat request whose
// model has routing targets is sent with the targets' model_override; and a
// conditional route on the Gemini native API sends the route's model.

import (
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"sync/atomic"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/routing"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// countingOpenAI is an upstream that counts every Assistants and Vector
// Stores request, whatever the method, and keeps each one's method, path and
// Authorization header. Other paths (the server's asynchronous start-up
// connectivity probe) are answered but not counted: the probe can land at any
// moment of the test.
type countingOpenAI struct {
	*httptest.Server
	calls atomic.Int32
	mu    sync.Mutex
	seen  []string
}

func startCountingOpenAI(t *testing.T) *countingOpenAI {
	t.Helper()
	u := &countingOpenAI{}
	u.Server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if strings.HasPrefix(r.URL.Path, "/v1/vector_stores") || strings.HasPrefix(r.URL.Path, "/v1/assistants") {
			u.calls.Add(1)
			u.mu.Lock()
			u.seen = append(u.seen, r.Method+" "+r.URL.Path+" "+r.Header.Get("Authorization"))
			u.mu.Unlock()
		}
		w.Header().Set("Content-Type", "application/json")
		io.WriteString(w, `{"id":"obj_1","object":"list","data":[]}`)
	}))
	t.Cleanup(u.Close)
	return u
}

func (u *countingOpenAI) last() string {
	u.mu.Lock()
	defer u.mu.Unlock()
	if len(u.seen) == 0 {
		return ""
	}
	return u.seen[len(u.seen)-1]
}

// Assistants and Vector Stores go to an "openai" provider only
// (resolveOpenAIProvider): the org's own when it has one, else the
// global one, which runs on the operator's credentials and which a key limited
// to its org's providers may not use. These routes store objects, so an
// org key's vector stores and assistants are created only with the org's
// own OpenAI key. The test pins:
//   - an org key with no openai provider of its own gets 501 not_supported on
//     every route, and the global upstream is never called;
//   - an internal key IS served by the global provider, so that 501 comes from
//     the refusal and not from a missing provider;
//   - an org with its own openai key is served with that key, and the response
//     says billing mode byok.
func TestOrgKeysNeverReachTheGlobalOpenAIForAssistantsOrVectorStores(t *testing.T) {
	routes := []struct{ method, path, body string }{
		{"POST", "/v1/vector_stores", `{"name":"kb"}`},
		{"GET", "/v1/vector_stores", ""},
		{"GET", "/v1/vector_stores/vs_1", ""},
		{"POST", "/v1/vector_stores/vs_1/search", `{"query":"q"}`},
		{"POST", "/v1/assistants", `{"model":"gpt-4o"}`},
		{"GET", "/v1/assistants", ""},
		{"GET", "/v1/assistants/asst_1", ""},
	}
	send := func(g *embedGateway, method, path, body, key string) *httptest.ResponseRecorder {
		req := httptest.NewRequest(method, path, strings.NewReader(body))
		req.Header.Set("Content-Type", "application/json")
		req.Header.Set("Authorization", "Bearer "+key)
		w := httptest.NewRecorder()
		g.srv.httpServer.Handler.ServeHTTP(w, req)
		return w
	}

	t.Run("org key, no openai provider of its own", func(t *testing.T) {
		up := startCountingOpenAI(t)
		g := newEmbedGateway(t, nil, func(_ *embedGateway, cfg *config.Config) {
			cfg.Providers["openai"] = config.ProviderConfig{BaseURL: up.URL, APIKey: "operator-openai", APIFormat: "openai", Models: []string{"gpt-4o"}}
		})
		// Control: an internal key reaches the global provider.
		start := up.calls.Load()
		if w := send(g, "GET", "/v1/vector_stores", "", embedInternalKey); w.Code != http.StatusOK {
			t.Fatalf("control: internal key status = %d %s, want 200 from the global openai provider", w.Code, w.Body.String())
		}
		if got := up.calls.Load() - start; got != 1 || up.last() != "GET /v1/vector_stores Bearer operator-openai" {
			t.Fatalf("control: upstream calls = %d, last %q; want 1 GET /v1/vector_stores with the operator's key", got, up.last())
		}
		before := up.calls.Load()
		for _, rt := range routes {
			w := send(g, rt.method, rt.path, rt.body, embedOrgKey)
			if w.Code != http.StatusNotImplemented || !strings.Contains(w.Body.String(), `"not_supported"`) {
				t.Errorf("%s %s with an org key: status = %d %s, want 501 not_supported", rt.method, rt.path, w.Code, w.Body.String())
			}
		}
		if got := up.calls.Load(); got != before {
			t.Errorf("the global openai upstream was called %d times by an org key; want 0", got-before)
		}
	})

	t.Run("org key, its own openai key", func(t *testing.T) {
		up := startCountingOpenAI(t)
		orgCfg := &tenant.OrgConfig{Providers: map[string]*tenant.ProviderConfig{
			"openai": {APIKey: "org-openai-key", Models: []string{"gpt-4o"}, Enabled: true},
		}}
		g := newEmbedGateway(t, orgCfg, func(_ *embedGateway, cfg *config.Config) {
			cfg.Providers["openai"] = config.ProviderConfig{BaseURL: up.URL, APIKey: "operator-openai", APIFormat: "openai", Models: []string{"gpt-4o"}}
		})
		for _, rt := range routes {
			before := up.calls.Load()
			w := send(g, rt.method, rt.path, rt.body, embedOrgKey)
			if w.Code != http.StatusOK {
				t.Errorf("%s %s: status = %d %s, want 200 from the org's own provider", rt.method, rt.path, w.Code, w.Body.String())
				continue
			}
			if up.calls.Load() != before+1 || !strings.HasSuffix(up.last(), "Bearer org-openai-key") {
				t.Errorf("%s %s: upstream saw %q; want one call with the org's own key", rt.method, rt.path, up.last())
			}
			if bm := w.Header().Get("x-agentcc-billing-mode"); bm != "byok" {
				t.Errorf("%s %s: x-agentcc-billing-mode = %q, want byok (the org's own key)", rt.method, rt.path, bm)
			}
		}
	})
}

// A chat request whose model has routing targets with a model_override is sent
// with the target's model. For chat, resolveProviderFor writes that model to
// rc.Request, and the chat caller (route_resolve.go) discards the model it
// returns, so that write is what changes the model sent. Provider failover is
// off so that chat resolves through resolveProviderFor rather than the failover
// candidates, and there are two targets because Router.HasTargets needs two or
// more. Every upstream must receive the targets' model, never the requested
// one.
func TestChatRoutingTargetSendsTheTargetModel(t *testing.T) {
	g := newOrgRouteGateway(t, func(cfg *config.Config, _ map[string]*orgRouteUpstream) {
		// No provider lists "m" itself: a provider that does makes "m" an
		// explicitly served model, and ResolveWithRouting then keeps it
		// (SelectExactModel skips targets that would change it).
		for id, p := range cfg.Providers {
			p.Models = []string{"m-mini"}
			cfg.Providers[id] = p
		}
		cfg.Routing.Targets = map[string][]config.RoutingTargetConfig{"m": {
			{Provider: "cheap", ModelOverride: "m-mini"},
			{Provider: "mid", ModelOverride: "m-mini"},
		}}
		cfg.Routing.Failover = config.FailoverConfig{Enabled: false}
	})
	g.srv.handlers.failover.Store(nil)
	for i := 0; i < 4; i++ {
		g.chat(t, "m")
	}
	var got []string
	for _, id := range []string{"cheap", "mid", "pricey"} {
		u := g.ups[id]
		u.mu.Lock()
		got = append(got, u.models...)
		u.mu.Unlock()
	}
	if len(got) != 4 {
		t.Fatalf("upstreams received %d requests (%v), want 4", len(got), got)
	}
	for _, m := range got {
		if m != "m-mini" {
			t.Fatalf("an upstream received model %q; want m-mini, the routing targets' model_override (all: %v)", m, got)
		}
	}
	if n := g.ups["pricey"].calls.Load(); n != 0 {
		t.Errorf("pricey was called %d times; it is not a target", n)
	}
}

// A conditional route that names another model sends that model upstream on
// the Gemini native API too (release note C10). That API carries the model
// in its path (/v1beta/models/<model>:embedContent), so the upstream path
// names the routed model and never the requested one.
func TestGenAINativeRouteSendsTheRoutedModel(t *testing.T) {
	g := newEmbedGateway(t, nil, func(g *embedGateway, cfg *config.Config) {
		p := cfg.Providers["gemini"]
		p.Models = []string{"gemini-embedding-001", "gemini-embedding-002"}
		cfg.Providers["gemini"] = p
	})
	cr, err := routing.NewConditionalRouter([]config.ConditionalRouteConfig{{
		Name: "embed-newer", Priority: 10,
		Condition: config.ConditionConfig{Field: "model", Op: "$eq", Value: "gemini-embedding-001"},
		Action:    config.RouteActionConfig{Provider: "gemini", ModelOverride: "gemini-embedding-002"},
	}})
	if err != nil {
		t.Fatal(err)
	}
	g.srv.handlers.conditionalRouter.Store(cr)
	req := httptest.NewRequest(http.MethodPost, "/v1beta/models/gemini-embedding-001:embedContent",
		strings.NewReader(`{"content":{"parts":[{"text":"x"}]}}`))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("x-goog-api-key", embedInternalKey)
	w := httptest.NewRecorder()
	g.srv.httpServer.Handler.ServeHTTP(w, req)
	if w.Code != http.StatusOK {
		t.Fatalf("status = %d %s, want 200", w.Code, w.Body.String())
	}
	body, _ := g.upstreams["gemini"].last()
	if !strings.Contains(body, "gemini-embedding-002:embedContent") || strings.Contains(body, "gemini-embedding-001") {
		t.Fatalf("gemini upstream request = %q; want the routed model gemini-embedding-002 and not the requested one", body)
	}
}
