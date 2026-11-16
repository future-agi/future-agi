package server

import (
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/cache"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	cacheplugin "github.com/futureagi/agentcc-gateway/internal/plugins/cache"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// semanticCacheServer runs the cache plugin with a semantic store in front of
// an OpenAI-format provider, for an org that has the cache enabled.
func semanticCacheServer(t *testing.T, up *httptest.Server) *Server {
	t.Helper()
	cfg := config.DefaultConfig()
	cfg.Auth.Enabled = true
	cfg.Auth.Keys = []config.AuthKeyConfig{{Name: "cached", Key: "sk-agentcc-cached", Owner: "user", KeyType: "internal", Metadata: map[string]string{"org_id": "org-cache"}}}
	tenantStore := tenant.NewStore()
	tenantStore.Set("org-cache", &tenant.OrgConfig{Cache: &tenant.CacheConfig{Enabled: true}})
	cfg.Providers["openai"] = config.ProviderConfig{BaseURL: up.URL, APIKey: "k", APIFormat: "openai", Models: []string{"gpt-5.5"}}
	providerRegistry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatalf("creating registry: %v", err)
	}
	plugin := cacheplugin.New(cache.NewMemoryBackend(cache.NewStore(100)), time.Minute)
	plugin.SetSemanticStoreFromConcrete(cache.NewSemanticStore(0.8, 256, 1000))
	srv := New(cfg, "", providerRegistry, pipeline.NewEngine(plugin), nil, nil, nil, nil, testModelDBPtr(), tenantStore, nil)
	srv.ready.Store(true)
	return srv
}

// The x-agentcc-cache-namespace header separates cache entries on every route
// that caches, including /v1/messages and GenAI generateContent when they are
// translated for an OpenAI-format provider. A paraphrase in the same namespace
// still hits.
func TestCacheNamespaceHeaderOnTranslatedRoutes(t *testing.T) {
	routes := []struct {
		name, path, body string // body takes the prompt
	}{
		{"chat", "/v1/chat/completions", `{"model":"gpt-5.5","messages":[{"role":"user","content":%q}]}`},
		{"messages translated", "/v1/messages", `{"model":"gpt-5.5","max_tokens":16,"messages":[{"role":"user","content":%q}]}`},
		{"genai translated", "/v1beta/models/gpt-5.5:generateContent", `{"contents":[{"role":"user","parts":[{"text":%q}]}]}`},
	}
	for _, rt := range routes {
		t.Run(rt.name, func(t *testing.T) {
			up := &dialectUpstream{}
			upSrv := httptest.NewServer(up)
			defer upSrv.Close()
			srv := semanticCacheServer(t, upSrv)
			send := func(namespace, prompt, reply string) string {
				up.reply = reply
				req := httptest.NewRequest(http.MethodPost, rt.path, strings.NewReader(fmt.Sprintf(rt.body, prompt)))
				req.Header.Set("Content-Type", "application/json")
				req.Header.Set("anthropic-version", "2023-06-01")
				req.Header.Set("Authorization", "Bearer sk-agentcc-cached")
				req.Header.Set("x-agentcc-cache-namespace", namespace)
				w := httptest.NewRecorder()
				srv.httpServer.Handler.ServeHTTP(w, req)
				if w.Code != http.StatusOK {
					t.Fatalf("status %d: %s", w.Code, w.Body.String())
				}
				return w.Body.String()
			}

			if body := send("team-a", "what is the capital of France", "answer-a"); !strings.Contains(body, "answer-a") || up.served() != 1 {
				t.Fatalf("first request: upstream calls %d; want 1 and answer-a: %s", up.served(), body)
			}
			if body := send("team-b", "What is the capital of France?", "answer-b"); !strings.Contains(body, "answer-b") || up.served() != 2 {
				t.Errorf("other namespace: upstream calls %d; want 2 and answer-b, not the team-a entry: %s", up.served(), body)
			}
			calls := up.served()
			if body := send("team-a", "what is the capital of france?", "answer-c"); !strings.Contains(body, "answer-a") || up.served() != calls {
				t.Errorf("same namespace paraphrase: upstream calls %d (was %d); want a cache hit with answer-a: %s", up.served(), calls, body)
			}
		})
	}
}
