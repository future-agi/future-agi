package cache_test

import (
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/cache"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/modeldb"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	authplugin "github.com/futureagi/agentcc-gateway/internal/plugins/auth"
	cacheplugin "github.com/futureagi/agentcc-gateway/internal/plugins/cache"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/server"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

const fuzzyKey = "sk-fuzzy-test"

// fuzzyGateway is a real chat handler with the cache plugin, its fuzzy
// layer on, for an org whose cache is enabled, in front of an upstream that
// counts its calls.
type fuzzyGateway struct {
	handlers *server.Handlers
	upstream atomic.Int32
	fuzzy    *cache.SemanticStore
}

func newFuzzyGateway(t *testing.T) *fuzzyGateway {
	t.Helper()
	g := &fuzzyGateway{fuzzy: cache.NewSemanticStore(0.8, 256, 100)}
	up := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if !strings.HasSuffix(r.URL.Path, "/chat/completions") { // the start-up connectivity check
			w.WriteHeader(http.StatusOK)
			return
		}
		n := g.upstream.Add(1)
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]any{
			"id": fmt.Sprintf("chatcmpl-%d", n), "object": "chat.completion", "created": 1700000000, "model": "gpt-4o",
			"choices": []any{map[string]any{"index": 0, "message": map[string]string{"role": "assistant", "content": "Paris"}, "finish_reason": "stop"}},
			"usage":   map[string]int{"prompt_tokens": 9, "completion_tokens": 1, "total_tokens": 10},
		})
	}))
	t.Cleanup(up.Close)

	cfg := config.DefaultConfig()
	cfg.Auth.Enabled = true
	cfg.Auth.Keys = []config.AuthKeyConfig{{Name: "fuzzy", Key: fuzzyKey, Owner: "t", KeyType: "internal",
		Metadata: map[string]string{"org_id": "org-fuzzy"}}}
	cfg.Providers["openai"] = config.ProviderConfig{BaseURL: up.URL, APIKey: "dummy", APIFormat: "openai", Models: []string{"gpt-4o"}}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatalf("creating registry: %v", err)
	}
	keyStore := auth.NewKeyStore(cfg.Auth)
	cp := cacheplugin.NewFromStore(cache.NewStore(100), time.Minute)
	cp.SetSemanticStoreFromConcrete(g.fuzzy)
	tenants := tenant.NewStore()
	tenants.Set("org-fuzzy", &tenant.OrgConfig{Cache: &tenant.CacheConfig{Enabled: true}})
	var db atomic.Pointer[modeldb.ModelDB]
	db.Store(modeldb.New(modeldb.BundledModels, nil))
	srv := server.New(cfg, "", registry, pipeline.NewEngine(authplugin.New(keyStore, true), cp), keyStore, nil, nil, nil, &db, tenants, nil)
	g.handlers = srv.Handlers()
	return g
}

func (g *fuzzyGateway) chat(t *testing.T, prompt string, headers ...string) *httptest.ResponseRecorder {
	t.Helper()
	body, _ := json.Marshal(map[string]any{"model": "gpt-4o", "messages": []any{map[string]string{"role": "user", "content": prompt}}})
	req := httptest.NewRequest(http.MethodPost, "/v1/chat/completions", strings.NewReader(string(body)))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+fuzzyKey)
	for i := 0; i+1 < len(headers); i += 2 {
		req.Header.Set(headers[i], headers[i+1])
	}
	w := httptest.NewRecorder()
	g.handlers.ChatCompletion(w, req)
	if w.Code != http.StatusOK {
		t.Fatalf("%q: status %d, body %s", prompt, w.Code, w.Body)
	}
	return w
}

// TestFuzzyCacheSkippedForAgentTraffic: with the fuzzy layer on, a
// near-duplicate request tagged only through x-claude-code-session-id misses
// and is not stored in the fuzzy layer, while the same untagged request hits.
// It runs the real chat handler, so the tag comes from the gateway's header
// alias mapping, not from the test.
func TestFuzzyCacheSkippedForAgentTraffic(t *testing.T) {
	g := newFuzzyGateway(t)

	w := g.chat(t, "what is the capital of France")
	if got := w.Header().Get(cacheplugin.HeaderCacheStatus); got != "MISS" || g.upstream.Load() != 1 || g.fuzzy.Len() != 1 {
		t.Fatalf("seed: status %q, %d upstream calls, %d fuzzy entries; want a MISS stored in the fuzzy layer", got, g.upstream.Load(), g.fuzzy.Len())
	}

	// Untagged, the near-duplicate is served by the fuzzy layer. A hit
	// stores nothing, so the tagged call below finds no exact entry either.
	near := "what is the capital of france?"
	w = g.chat(t, near)
	if got := w.Header().Get(cacheplugin.HeaderCacheStatus); got != "HIT" || g.upstream.Load() != 1 {
		t.Fatalf("untagged near-duplicate: status %q after %d upstream calls; want a fuzzy HIT", got, g.upstream.Load())
	}
	if got := w.Header().Get("x-agentcc-cache"); got != "hit_semantic" {
		t.Fatalf("untagged near-duplicate: x-agentcc-cache %q, want hit_semantic", got)
	}

	// Tagged only through x-claude-code-session-id, the same request misses,
	// reaches upstream, and is kept out of the fuzzy layer.
	w = g.chat(t, near, "x-claude-code-session-id", "cc-session-1")
	if got := w.Header().Get(cacheplugin.HeaderCacheStatus); got != "MISS" || g.upstream.Load() != 2 {
		t.Fatalf("agent near-duplicate: status %q after %d upstream calls; want a MISS that reaches upstream", got, g.upstream.Load())
	}
	if g.fuzzy.Len() != 1 {
		t.Fatalf("the agent request was stored in the fuzzy layer: %d entries, want 1", g.fuzzy.Len())
	}

	// The exact layer still serves agent traffic.
	w = g.chat(t, near, "x-claude-code-session-id", "cc-session-1")
	if got := w.Header().Get(cacheplugin.HeaderCacheStatus); got != "HIT" || g.upstream.Load() != 2 || w.Header().Get("x-agentcc-cache") != "hit_exact" {
		t.Fatalf("agent exact repeat: status %q (%q) after %d upstream calls; want an exact HIT", got, w.Header().Get("x-agentcc-cache"), g.upstream.Load())
	}
}

// TestCacheStatusHeaderReachesTheResponse: the status the plugin sets is on
// the HTTP response, for a miss, an exact hit and a bypass.
func TestCacheStatusHeaderReachesTheResponse(t *testing.T) {
	g := newFuzzyGateway(t)
	for i, want := range []struct {
		headers  []string
		status   string
		upstream int32
	}{
		{nil, "MISS", 1},
		{nil, "HIT", 1},
		{[]string{cacheplugin.HeaderCacheMode, "bypass"}, "BYPASS", 2},
		{[]string{cacheplugin.HeaderCacheKey, "turn-7"}, "MISS", 3},
		{[]string{cacheplugin.HeaderCacheKey, "turn-7"}, "HIT", 3},
	} {
		w := g.chat(t, "hello there", want.headers...)
		if got := w.Header().Get(cacheplugin.HeaderCacheStatus); got != want.status || g.upstream.Load() != want.upstream {
			t.Errorf("call %d: %s %q after %d upstream calls, want %q after %d", i+1, cacheplugin.HeaderCacheStatus, got, g.upstream.Load(), want.status, want.upstream)
		}
	}
}
