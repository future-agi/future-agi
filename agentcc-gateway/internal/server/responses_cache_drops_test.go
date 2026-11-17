package server

import (
	"bytes"
	"context"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/cache"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	cacheplugin "github.com/futureagi/agentcc-gateway/internal/plugins/cache"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	openaiprov "github.com/futureagi/agentcc-gateway/internal/providers/openai"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

const cachedBridgeKey = "sk-agentcc-cached-bridge"

// newCachedResponsesBridgeServer is the Responses bridge fixture with the
// gateway response cache on and an org that has caching enabled, as in
// production: an exact-match cache hit short-circuits the provider call. Only
// an internal service key may use the gateway's own providers, so the fixture
// uses one; see logInputObserver for the request log.
func newCachedResponsesBridgeServer(t *testing.T, plugins ...pipeline.Plugin) (*Server, *openAIUpstream) {
	t.Helper()
	upstream := &openAIUpstream{hits: map[string]int{}}
	ts := httptest.NewServer(upstream)
	t.Cleanup(ts.Close)

	cfg := config.DefaultConfig()
	cfg.Auth.Enabled = true
	cfg.Auth.Keys = []config.AuthKeyConfig{{Name: "cached", Key: cachedBridgeKey, Owner: "user", KeyType: "internal", Metadata: map[string]string{"org_id": "org-cache"}}}
	tenantStore := tenant.NewStore()
	tenantStore.Set("org-cache", &tenant.OrgConfig{Cache: &tenant.CacheConfig{Enabled: true}})
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatalf("creating registry: %v", err)
	}
	provider, err := openaiprov.New("openai", bridgeProviderConfig(ts, false))
	if err != nil {
		t.Fatalf("creating provider: %v", err)
	}
	registry.RegisterProvider("openai", provider, bridgeModels)

	cachePlugin := cacheplugin.New(cache.NewMemoryBackend(cache.NewStore(100)), time.Minute)
	srv := New(cfg, "", registry, pipeline.NewEngine(append([]pipeline.Plugin{cachePlugin}, plugins...)...), nil, nil, nil, nil, testModelDBPtr(), tenantStore, nil)
	srv.ready.Store(true)
	t.Cleanup(func() {
		if srv.asyncWorker != nil {
			srv.asyncWorker.Stop()
		}
	})
	return srv, upstream
}

// logInputObserver runs where the request-log plugin runs, in the parallel
// post-plugin phase, and records the translation_drops value that plugin
// writes to the log row. Production never request-logs internal service keys,
// which the cached fixture must use, so the real logger would record nothing.
type logInputObserver struct{ rows chan string }

func (o *logInputObserver) Name() string         { return "log-input-observer" }
func (o *logInputObserver) Priority() int        { return 900 }
func (o *logInputObserver) IsPostParallel() bool { return true }
func (o *logInputObserver) ProcessRequest(context.Context, *models.RequestContext) pipeline.PluginResult {
	return pipeline.ResultContinue()
}
func (o *logInputObserver) ProcessResponse(_ context.Context, rc *models.RequestContext) pipeline.PluginResult {
	o.rows <- rc.Metadata["translation_drops"]
	return pipeline.ResultContinue()
}

func observeLogInput(t *testing.T) (pipeline.Plugin, func(string)) {
	t.Helper()
	o := &logInputObserver{rows: make(chan string, 16)}
	return o, func(want string) {
		t.Helper()
		select {
		case got := <-o.rows:
			if got != want {
				t.Errorf("request log translation_drops = %q, want %q", got, want)
			}
		case <-time.After(5 * time.Second):
			t.Fatal("request log input not observed")
		}
	}
}

func postCachedBridge(t *testing.T, srv *Server, path, body string) *httptest.ResponseRecorder {
	t.Helper()
	req := httptest.NewRequest(http.MethodPost, path, bytes.NewBufferString(body))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("anthropic-version", "2023-06-01")
	req.Header.Set("Authorization", "Bearer "+cachedBridgeKey)
	// Caller-supplied notes are never reported, from the cache or otherwise.
	req.Header.Set("x-agentcc-translation-drops", "forged-secret")
	req.Header.Set("x-agentcc-metadata", `{"translation_drops":"forged-secret"}`)
	w := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(w, req)
	if w.Code != http.StatusOK {
		t.Fatalf("%s: status=%d: %s", path, w.Code, w.Body.String())
	}
	return w
}

// A repeated non-streaming function-tool request served from an entry the
// Responses route produced reports the settings that route omits from it, in
// x-agentcc-translation-drops and in the request log's translation_drops.
// Expected red before cache hits reported notes.
func TestResponsesDropNotesSurviveCacheHit(t *testing.T) {
	for _, tc := range []struct {
		name  string
		extra string
		want  string
	}{
		{"seed", `"seed":7,`, "seed_unsupported"},
		{"all settings", allResponsesOnlySettings, allResponsesDropNotes},
		{"no settings", "", ""},
	} {
		t.Run(tc.name, func(t *testing.T) {
			logs, wantLog := observeLogInput(t)
			srv, upstream := newCachedResponsesBridgeServer(t, logs)
			body := chatToolBody("gpt-5.6-terra", false, tc.extra)

			for i, wantCache := range []string{"miss", "hit_exact", "hit_exact"} {
				label := fmt.Sprintf("request %d", i+1)
				w := postCachedBridge(t, srv, "/v1/chat/completions", body)
				if got := w.Result().Header.Get("x-agentcc-cache"); got != wantCache {
					t.Fatalf("%s: x-agentcc-cache=%q, want %q", label, got, wantCache)
				}
				checkToolCompletion(t, w.Body.Bytes())
				if strings.Contains(w.Body.String(), "x_agentcc_responses") {
					t.Errorf("%s: body exposes the cache's stored notes: %s", label, w.Body.String())
				}
				if got := w.Result().Header.Get("x-agentcc-translation-drops"); got != tc.want {
					t.Errorf("%s: x-agentcc-translation-drops=%q, want %q", label, got, tc.want)
				}
				wantLog(tc.want)
				// Only the first request reached the provider.
				checkHits(t, upstream, 0, 1)
			}
		})
	}
}

// On the translated /v1/messages route a hit reports the current request's
// translator notes together with what the Responses route omits from it.
func TestResponsesDropNotesSurviveCacheHitTranslated(t *testing.T) {
	logs, wantLog := observeLogInput(t)
	srv, upstream := newCachedResponsesBridgeServer(t, logs)
	body := `{"model":"gpt-5.6-terra","max_tokens":100,"top_k":7,"stop_sequences":["secret"],"tools":[{"name":"get_weather","input_schema":{"type":"object"}}],"messages":[{"role":"user","content":"Hi"}]}`
	const want = "top_k_unsupported,stop"

	for i, wantCache := range []string{"miss", "hit_exact"} {
		label := fmt.Sprintf("request %d", i+1)
		w := postCachedBridge(t, srv, "/v1/messages", body)
		if got := w.Result().Header.Get("x-agentcc-cache"); got != wantCache {
			t.Fatalf("%s: x-agentcc-cache=%q, want %q", label, got, wantCache)
		}
		if strings.Contains(w.Body.String(), "x_agentcc_responses") {
			t.Errorf("%s: body exposes the cache's stored notes: %s", label, w.Body.String())
		}
		if got := w.Result().Header.Get("x-agentcc-translation-drops"); got != want {
			t.Errorf("%s: x-agentcc-translation-drops=%q, want %q", label, got, want)
		}
		wantLog(want)
		checkHits(t, upstream, 0, 1)
	}
}

// stop is not part of the cache key, so requests that differ only in stop
// share a cached answer. The notes describe the request being answered: a
// caller whose stop the Responses route omits is told so even when the entry
// came from a request without stop, and a caller without stop is not told
// about another caller's.
func TestResponsesDropNotesFollowCachedRequest(t *testing.T) {
	const without = `"seed":7,`
	const with = `"seed":7,"stop":["END"],`
	for _, tc := range []struct {
		name          string
		first, second string
		wantFirst     string
		wantSecond    string
	}{
		{"A without stop then B with stop", without, with, "seed_unsupported", "stop_unsupported,seed_unsupported"},
		{"A with stop then B without stop", with, without, "stop_unsupported,seed_unsupported", "seed_unsupported"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			logs, wantLog := observeLogInput(t)
			srv, upstream := newCachedResponsesBridgeServer(t, logs)
			for i, step := range []struct{ extra, cache, want string }{
				{tc.first, "miss", tc.wantFirst},
				{tc.second, "hit_exact", tc.wantSecond},
			} {
				label := fmt.Sprintf("caller %c", 'A'+i)
				w := postCachedBridge(t, srv, "/v1/chat/completions", chatToolBody("gpt-5.6-terra", false, step.extra))
				if got := w.Result().Header.Get("x-agentcc-cache"); got != step.cache {
					t.Fatalf("%s: x-agentcc-cache=%q, want %q", label, got, step.cache)
				}
				checkToolCompletion(t, w.Body.Bytes())
				if got := w.Result().Header.Get("x-agentcc-translation-drops"); got != step.want {
					t.Errorf("%s: x-agentcc-translation-drops=%q, want %q", label, got, step.want)
				}
				wantLog(step.want)
				checkHits(t, upstream, 0, 1)
			}
		})
	}
}
