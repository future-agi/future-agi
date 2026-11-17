package server

// Internal keys are served only catalogue or configured models: a model a
// "<provider>/<model>" name or the only configured provider would pick is
// served only when that provider lists it (its configured models) or the
// gateway's catalogue has it for that provider. Any other name answers 404
// model_not_found and nothing is called. Explicit routing (router targets
// and model_map) is served as before.
//
// One upstream records every call it receives.

import (
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/catalog"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/modeldb"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	authplugin "github.com/futureagi/agentcc-gateway/internal/plugins/auth"
	creditsplugin "github.com/futureagi/agentcc-gateway/internal/plugins/credits"
	"github.com/futureagi/agentcc-gateway/internal/providers"
)

const ikmInternalKey = "sk-agentcc-ikm-internal-0123456789abcdef"

var ikmNow = time.Date(2026, 11, 12, 0, 0, 0, 0, time.UTC)

type ikmUpstream struct {
	*httptest.Server
	mu    sync.Mutex
	calls []string
}

func newIKMUpstream(t *testing.T) *ikmUpstream {
	t.Helper()
	u := &ikmUpstream{}
	u.Server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = io.Copy(io.Discard, r.Body)
		w.Header().Set("Content-Type", "application/json")
		if r.Method != http.MethodPost {
			// A model listing (a catalogue refresh, if one runs) lists the
			// catalogue's one model, priced, in the OpenRouter shape.
			_, _ = io.WriteString(w, `{"object":"list","data":[{"id":"vendor/known","name":"Vendor: Known","context_length":100000,"pricing":{"prompt":"0.000001","completion":"0.000002"}}]}`)
			return
		}
		u.mu.Lock()
		u.calls = append(u.calls, r.URL.Path+" "+r.Header.Get("Authorization"))
		u.mu.Unlock()
		if strings.HasSuffix(r.URL.Path, "/embeddings") {
			_, _ = io.WriteString(w, `{"object":"list","model":"e","data":[{"object":"embedding","index":0,"embedding":[0.1]}],"usage":{"prompt_tokens":1,"total_tokens":1}}`)
			return
		}
		_, _ = io.WriteString(w, `{"id":"x","object":"chat.completion","created":1,"model":"m","choices":[{"index":0,"message":{"role":"assistant","content":"ok"},"finish_reason":"stop"}],"usage":{"prompt_tokens":1,"completion_tokens":1,"total_tokens":2}}`)
	}))
	t.Cleanup(u.Close)
	return u
}

func (u *ikmUpstream) count() int {
	u.mu.Lock()
	defer u.mu.Unlock()
	return len(u.calls)
}

// ikmGateway: an internal key; "openai" lists m-listed; "router" is a
// managed provider that lists anthropic/claude-literal (a literal prefixed
// id, with no "anthropic" provider) and whose catalogue has vendor/known
// (priced); model_map
// sends mapped-alias to openai, and a router target sends routed-alias to
// openai as m-listed. With solo set, the only provider is "solo", listing
// solo-listed, and there is no catalogue.
func ikmGateway(t *testing.T, up *ikmUpstream, solo bool) *Server {
	t.Helper()
	off := false
	cfg := config.DefaultConfig()
	cfg.Auth.Enabled = true
	cfg.Auth.Keys = []config.AuthKeyConfig{{Name: "ikm-internal", Key: ikmInternalKey, Owner: "futureagi-backend", KeyType: "internal"}}
	if solo {
		cfg.Providers["solo"] = config.ProviderConfig{BaseURL: up.URL, APIKey: "operator-solo-key", APIFormat: "openai", Models: []string{"solo-listed"}, AutoDiscover: &off}
	} else {
		cfg.Providers["openai"] = config.ProviderConfig{BaseURL: up.URL, APIKey: "operator-openai-key", APIFormat: "openai", Models: []string{"m-listed"}, AutoDiscover: &off}
		cfg.Providers["router"] = config.ProviderConfig{BaseURL: up.URL, APIKey: "operator-router-key", APIFormat: "openai", Type: "openrouter", Managed: true,
			Models: []string{"anthropic/claude-literal"}, AutoDiscover: &off}
		cfg.ModelMap = map[string]string{"mapped-alias": "openai"}
		cfg.Routing.Targets = map[string][]config.RoutingTargetConfig{"routed-alias": {{Provider: "openai", Weight: 1, ModelOverride: "m-listed"}}}
	}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatalf("creating registry: %v", err)
	}
	t.Cleanup(func() { registry.Close() })
	keyStore := auth.NewKeyStore(cfg.Auth)
	mdb := testModelDBPtr()
	var store *catalog.Store
	if !solo {
		store = catalog.NewStore()
		opts := catalog.OptionsFromConfig(cfg, ManagedCatalogSources(registry))
		opts.Now = func() time.Time { return ikmNow }
		if err := store.Configure(opts); err != nil {
			t.Fatal(err)
		}
		store.Put(&catalog.Snapshot{ProviderID: "router", Source: catalog.OriginLive, CatalogSource: catalog.SourceOpenRouter,
			FetchedAt: ikmNow, Models: map[string]*catalog.Model{
				"vendor/known": {Name: "Vendor: Known", ContextLength: 100000, Prices: catalog.Prices{Prompt: 1e-6, Completion: 2e-6}},
			}})
		var withCatalogue atomic.Pointer[modeldb.ModelDB]
		withCatalogue.Store(BuildModelDB(nil, store))
		mdb = &withCatalogue
	}
	srv := New(cfg, "", registry, pipeline.NewEngine(authplugin.New(keyStore, true)), keyStore, nil, nil, nil, mdb, nil, nil)
	releaseTestServer(t, srv)
	if store != nil {
		srv.EnableManaged(ManagedSettings{Catalog: store, Ledger: creditsplugin.NewOrgLedger(nil), MarkupPercent: 5})
	}
	srv.ready.Store(true)
	return srv
}

func ikmPost(srv *Server, path, body string) *httptest.ResponseRecorder {
	req := httptest.NewRequest(http.MethodPost, path, strings.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+ikmInternalKey)
	w := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(w, req)
	return w
}

func ikmChat(model, extra string) string {
	return fmt.Sprintf(`{"model":%q,"messages":[{"role":"user","content":"hi"}]%s}`, model, extra)
}

func ikmWantNotFound(t *testing.T, w *httptest.ResponseRecorder, up *ikmUpstream) {
	t.Helper()
	if w.Code != http.StatusNotFound || r7Code(w) != "model_not_found" {
		t.Fatalf("-> %d %s %s, want 404 model_not_found", w.Code, r7Code(w), w.Body.String())
	}
	if n := up.count(); n != 0 {
		t.Fatalf("upstream calls = %d, want none", n)
	}
}

func ikmWantServed(t *testing.T, w *httptest.ResponseRecorder, up *ikmUpstream) {
	t.Helper()
	if w.Code != http.StatusOK {
		t.Fatalf("-> %d %s %s, want 200", w.Code, r7Code(w), w.Body.String())
	}
	if n := up.count(); n != 1 {
		t.Fatalf("upstream calls = %d, want 1", n)
	}
}

func TestInternalKeysServedOnlyCatalogueOrConfiguredModels(t *testing.T) {
	for _, tc := range []struct {
		name, model string
		served      bool
	}{
		{"prefixed name the provider lists", "openai/m-listed", true},
		{"prefixed name the provider does not list, not in the catalogue", "openai/m-not-listed", false},
		{"prefixed name in the provider's catalogue", "router/vendor/known", true},
		{"prefixed name not in the provider's catalogue", "router/vendor/not-known", false},
		{"literal prefixed listing on another provider", "anthropic/claude-literal", true},
		{"model_map entry", "mapped-alias", true},
		{"router target", "routed-alias", true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			up := newIKMUpstream(t)
			srv := ikmGateway(t, up, false)
			w := ikmPost(srv, "/v1/chat/completions", ikmChat(tc.model, ""))
			if tc.served {
				ikmWantServed(t, w, up)
			} else {
				ikmWantNotFound(t, w, up)
			}
		})
	}
}

func TestInternalKeySoleProviderServesOnlyKnownModels(t *testing.T) {
	for _, tc := range []struct {
		name, model string
		served      bool
	}{
		{"a name the only provider lists", "solo-listed", true},
		{"a name the only provider does not list", "not-listed-anywhere", false},
		{"a prefixed name for a provider that is not configured", "elsewhere/not-listed", false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			up := newIKMUpstream(t)
			srv := ikmGateway(t, up, true)
			w := ikmPost(srv, "/v1/chat/completions", ikmChat(tc.model, ""))
			if tc.served {
				ikmWantServed(t, w, up)
			} else {
				ikmWantNotFound(t, w, up)
			}
		})
	}
}

// The request's own fallback models are judged by the same rule.
func TestInternalKeyFallbackModelsServedOnlyWhenKnown(t *testing.T) {
	up := newIKMUpstream(t)
	srv := ikmGateway(t, up, false)
	w := ikmPost(srv, "/v1/chat/completions", ikmChat("nowhere-model", `,"models":["nowhere-model","openai/fallback-not-listed"]`))
	ikmWantNotFound(t, w, up)
}

// Embeddings resolve through the same rule (the other model endpoints share
// resolveProvider with it).
func TestInternalKeyEmbeddingsServedOnlyKnownModels(t *testing.T) {
	up := newIKMUpstream(t)
	srv := ikmGateway(t, up, false)
	w := ikmPost(srv, "/v1/embeddings", `{"model":"openai/embed-not-listed","input":"hi"}`)
	ikmWantNotFound(t, w, up)
}

// Realtime sessions resolve by the same rule: a name no provider knows is
// not found; a known one gets this release's answer for realtime (501).
func TestInternalKeyRealtimeServedOnlyKnownModels(t *testing.T) {
	for _, tc := range []struct {
		model string
		want  int
	}{
		{"openai/m-not-listed", http.StatusNotFound},
		{"openai/m-listed", http.StatusNotImplemented},
	} {
		t.Run(tc.model, func(t *testing.T) {
			up := newIKMUpstream(t)
			srv := ikmGateway(t, up, false)
			req := httptest.NewRequest(http.MethodGet, "/v1/realtime?model="+tc.model, nil)
			req.Header.Set("Authorization", "Bearer "+ikmInternalKey)
			w := httptest.NewRecorder()
			srv.httpServer.Handler.ServeHTTP(w, req)
			if w.Code != tc.want {
				t.Fatalf("GET /v1/realtime?model=%s -> %d %s, want %d", tc.model, w.Code, w.Body.String(), tc.want)
			}
			if n := up.count(); n != 0 {
				t.Fatalf("upstream calls = %d, want none", n)
			}
		})
	}
}
