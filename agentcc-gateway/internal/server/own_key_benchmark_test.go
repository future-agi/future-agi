package server

import (
	"bytes"
	"net/http/httptest"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	authplugin "github.com/futureagi/agentcc-gateway/internal/plugins/auth"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// BenchmarkChatCompletionOwnKey is BenchmarkChatCompletionNonStreaming for an
// org key whose org's own "openai" provider serves the model it asks for as
// "openai/gpt-4o": org-first resolution, and the id the org
// registered sent upstream. The mock upstream answers any model, so the
// benchmark measures the gateway's own work.
//
// Reproduce: go test -run '^$' -bench=BenchmarkChatCompletionOwnKey -benchmem ./internal/server/
func BenchmarkChatCompletionOwnKey(b *testing.B) {
	mock := startBenchMockOpenAI(b)
	defer mock.Close()

	cfg := config.DefaultConfig()
	cfg.Auth.Enabled = true
	cfg.Auth.Keys = []config.AuthKeyConfig{{
		Name: "bench-org", Key: "bench-org-key-123", Owner: "bench", KeyType: "byok",
		Metadata: map[string]string{"org_id": "bench-org"},
	}}
	cfg.Providers["openai"] = config.ProviderConfig{BaseURL: mock.URL, APIKey: "dummy", APIFormat: "openai"}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		b.Fatalf("creating registry: %v", err)
	}
	keyStore := auth.NewKeyStore(cfg.Auth)
	tenants := tenant.NewStore()
	tenants.Set("bench-org", &tenant.OrgConfig{Providers: map[string]*tenant.ProviderConfig{
		"openai": {Enabled: true, APIKey: "bench-org-openai-key", Models: []string{"gpt-4o"}},
	}})
	engine := pipeline.NewEngine(authplugin.New(keyStore, true))
	srv := New(cfg, "", registry, engine, keyStore, nil, nil, nil, testModelDBPtr(), tenants, nil)
	srv.ready.Store(true)

	reqBody := []byte(`{"model":"openai/gpt-4o","messages":[{"role":"user","content":"Hello"}]}`)

	b.ResetTimer()
	b.ReportAllocs()
	for i := 0; i < b.N; i++ {
		req := httptest.NewRequest("POST", "/v1/chat/completions", bytes.NewReader(reqBody))
		req.Header.Set("Content-Type", "application/json")
		req.Header.Set("Authorization", "Bearer bench-org-key-123")
		w := httptest.NewRecorder()
		srv.httpServer.Handler.ServeHTTP(w, req)
		if w.Code != 200 {
			b.Fatalf("status = %d, body = %s", w.Code, w.Body.String())
		}
	}
}
