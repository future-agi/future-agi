package server

import (
	"net/http"
	"net/http/httptest"
	"sort"
	"strings"
	"sync/atomic"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/a2a"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/providers/anthropic"
	"github.com/futureagi/agentcc-gateway/internal/providers/azure"
	"github.com/futureagi/agentcc-gateway/internal/providers/bedrock"
	"github.com/futureagi/agentcc-gateway/internal/providers/cohere"
	"github.com/futureagi/agentcc-gateway/internal/providers/gemini"
	"github.com/futureagi/agentcc-gateway/internal/providers/openai"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// GET /v1/realtime answers 501 for every key: no gateway provider implements RealtimeProvider, and these tests pin that.

// TestNoGatewayProviderImplementsRealtime checks every provider type the
// gateway can register: the six api_formats providers.NewRegistry builds
// (also used for org instances) and the A2A provider server.New registers at
// runtime.
func TestNoGatewayProviderImplementsRealtime(t *testing.T) {
	types := map[string]any{
		"openai":    (*openai.Provider)(nil),
		"anthropic": (*anthropic.Provider)(nil),
		"gemini":    (*gemini.Provider)(nil),
		"bedrock":   (*bedrock.Provider)(nil),
		"cohere":    (*cohere.Provider)(nil),
		"azure":     (*azure.Provider)(nil),
		"a2a":       (*a2a.Provider)(nil),
	}
	for name, p := range types {
		if _, ok := p.(providers.RealtimeProvider); ok {
			t.Errorf("%s provider implements RealtimeProvider; GET /v1/realtime must answer 501 for every key", name)
		}
	}

	// The same through the registry, with one provider of every api_format
	// createProvider supports.
	cfg := config.DefaultConfig()
	cfg.Providers = map[string]config.ProviderConfig{
		"p-openai":    {BaseURL: "http://127.0.0.1:1", APIKey: "k", APIFormat: "openai", Models: []string{"m-openai"}},
		"p-anthropic": {BaseURL: "http://127.0.0.1:1", APIKey: "k", APIFormat: "anthropic", Models: []string{"m-anthropic"}},
		"p-gemini":    {BaseURL: "http://127.0.0.1:1", APIKey: "k", APIFormat: "gemini", Models: []string{"m-gemini"}},
		"p-bedrock": {BaseURL: "http://127.0.0.1:1", APIFormat: "bedrock", Models: []string{"m-bedrock"},
			AWSAccessKeyID: "AKIDEXAMPLE", AWSSecretAccessKey: "secret", AWSRegion: "us-east-1"},
		"p-cohere": {BaseURL: "http://127.0.0.1:1", APIKey: "k", APIFormat: "cohere", Models: []string{"m-cohere"}},
		"p-azure":  {BaseURL: "http://127.0.0.1:1", APIKey: "k", APIFormat: "azure", Models: []string{"m-azure"}},
	}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatalf("NewRegistry: %v", err)
	}
	ids := registry.ProviderIDs()
	sort.Strings(ids)
	if len(ids) != len(cfg.Providers) {
		t.Fatalf("registry holds %v, want one provider per api_format", ids)
	}
	for _, id := range ids {
		p, _ := registry.GetProvider(id)
		if _, ok := p.(providers.RealtimeProvider); ok {
			t.Errorf("registry provider %s (%T) implements RealtimeProvider", id, p)
		}
	}
}

// TestRealtimeNeverReachesAGlobalProviderForAnOrgKey drives GET /v1/realtime
// through the whole server with auth on: an org key whose org has its own
// provider for the model, and an internal key. Both end at 501, and the
// upstream behind the global provider is never contacted.
func TestRealtimeNeverReachesAGlobalProviderForAnOrgKey(t *testing.T) {
	// The server's start-up provider check lists models (GET /v1/models);
	// any other upstream request would come from the realtime handler.
	var hits atomic.Int64
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method == http.MethodGet && r.URL.Path == "/v1/models" {
			w.Header().Set("Content-Type", "application/json")
			w.Write([]byte(`{"object":"list","data":[]}`))
			return
		}
		hits.Add(1)
		w.WriteHeader(http.StatusTeapot)
	}))
	t.Cleanup(upstream.Close)

	cfg := config.DefaultConfig()
	cfg.Auth.Enabled = true
	cfg.Auth.Keys = []config.AuthKeyConfig{
		{Name: "org", Key: "sk-rt-org", Owner: "o", KeyType: "byok", Metadata: map[string]string{"org_id": "org-rt"}},
		{Name: "internal", Key: "sk-rt-internal", Owner: "ops", KeyType: "internal"},
	}
	cfg.Providers["openai"] = config.ProviderConfig{BaseURL: upstream.URL, APIKey: "platform-openai-key", APIFormat: "openai", Models: []string{"gpt-4o-realtime-preview"}}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatal(err)
	}
	ts := tenant.NewStore()
	ts.Set("org-rt", &tenant.OrgConfig{Providers: map[string]*tenant.ProviderConfig{
		"openai": {Enabled: true, APIKey: "org-key", Models: []string{"gpt-4o-realtime-preview"}},
	}})
	srv := New(cfg, "", registry, pipeline.NewEngine(), nil, nil, nil, nil, testModelDBPtr(), ts, nil)
	srv.ready.Store(true)
	t.Cleanup(srv.asyncWorker.Stop)

	for _, key := range []string{"sk-rt-org", "sk-rt-internal"} {
		req := httptest.NewRequest("GET", "/v1/realtime?model=gpt-4o-realtime-preview", nil)
		req.Header.Set("Authorization", "Bearer "+key)
		req.Header.Set("Connection", "Upgrade")
		req.Header.Set("Upgrade", "websocket")
		req.Header.Set("Sec-WebSocket-Version", "13")
		req.Header.Set("Sec-WebSocket-Key", "dGhlIHNhbXBsZSBub25jZQ==")
		w := httptest.NewRecorder()
		srv.httpServer.Handler.ServeHTTP(w, req)
		if w.Code != http.StatusNotImplemented || !strings.Contains(w.Body.String(), "does not support realtime") {
			t.Errorf("%s: GET /v1/realtime answered %d %s, want 501 (no realtime provider)", key, w.Code, w.Body.String())
		}
	}
	if n := hits.Load(); n != 0 {
		t.Errorf("the global provider's upstream was contacted %d times", n)
	}
}
