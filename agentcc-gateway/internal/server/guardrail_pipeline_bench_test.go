package server

import (
	"bytes"
	"net/http/httptest"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/guardrails"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/orgfactory"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/policy"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	authplugin "github.com/futureagi/agentcc-gateway/internal/plugins/auth"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// BenchmarkChatCompletionGuardrailPipeline is one org key's chat completion
// for an organization that saved no guardrail check, through a gateway whose
// config leaves the guardrail plugin off ("disabled") and through one that
// turns it on with no rule of its own ("enabled_no_rule", the shape the
// shipped configs carry), wired as cmd/agentcc wires it (engine, policy store,
// the org-config factory, the tenant store). The difference between the two is
// what turning the plugin on costs a request that has nothing to check.
//
// Reproduce: go test -run '^$' -bench '^BenchmarkChatCompletionGuardrailPipeline$' -benchmem -count 10 ./internal/server/
func BenchmarkChatCompletionGuardrailPipeline(b *testing.B) {
	for _, tc := range []struct {
		name string
		on   bool
	}{
		{"disabled", false},
		{"enabled_no_rule", true},
	} {
		b.Run(tc.name, func(b *testing.B) {
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

			plugins := []pipeline.Plugin{authplugin.New(keyStore, true)}
			var grEngine *guardrails.Engine
			var policyStore *policy.Store
			if tc.on {
				cfg.Guardrails.Enabled = true
				grRegistry := map[string]guardrails.Guardrail{}
				grEngine = guardrails.NewEngine(cfg.Guardrails, grRegistry)
				policyStore = policy.NewStore()
				plugins = append(plugins, guardrails.NewPlugin(grEngine, grRegistry, orgfactory.New(nil), policyStore, tenants))
			}
			engine := pipeline.NewEngine(plugins...)
			srv := New(cfg, "", registry, engine, keyStore, grEngine, policyStore, nil, testModelDBPtr(), tenants, nil)
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
		})
	}
}
