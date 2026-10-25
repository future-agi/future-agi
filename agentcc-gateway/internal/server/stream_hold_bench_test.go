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

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/guardrails"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/policy"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	authplugin "github.com/futureagi/agentcc-gateway/internal/plugins/auth"
	"github.com/futureagi/agentcc-gateway/internal/providers"
)

// The stream path a request with no blocking check on the answer takes is not
// held. These benchmarks measure one streamed chat
// completion on that path, in process (ServeHTTP with a recorder, the mock
// upstream on loopback), to compare its allocations with another tree's:
// with no guardrail, and with a rule on the answer in warn mode, which
// reads the answer but never blocks it. They are not named
// BenchmarkChatCompletion*, so the release benchmark guard (baseline_bench.sh,
// whose pin lacks them) does not judge them.
//
// Reproduce: GOMAXPROCS=4 go test ./internal/server -run '^$' -bench '^BenchmarkStreamNotHeld' -benchmem -count 10

func BenchmarkStreamNotHeld(b *testing.B) {
	benchStreamNotHeld(b, nil)
}

func BenchmarkStreamNotHeldWarnCheck(b *testing.B) {
	benchStreamNotHeld(b, &config.GuardrailRuleConfig{Name: "bench-answer-check", Stage: "post", Mode: "sync", Action: "warn", Threshold: 0.5})
}

// benchAnswerCheck passes every answer.
type benchAnswerCheck struct{}

func (benchAnswerCheck) Name() string            { return "bench-answer-check" }
func (benchAnswerCheck) Stage() guardrails.Stage { return guardrails.StagePost }
func (benchAnswerCheck) Check(context.Context, *guardrails.CheckInput) *guardrails.CheckResult {
	return &guardrails.CheckResult{Pass: true}
}

func benchStreamNotHeld(b *testing.B, rule *config.GuardrailRuleConfig) {
	mock := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/event-stream")
		flusher := w.(http.Flusher)
		for _, data := range []string{
			`{"id":"c1","object":"chat.completion.chunk","created":1760000000,"model":"gpt-4o","choices":[{"index":0,"delta":{"role":"assistant"}}]}`,
			`{"id":"c1","object":"chat.completion.chunk","created":1760000000,"model":"gpt-4o","choices":[{"index":0,"delta":{"content":"Hello "}}]}`,
			`{"id":"c1","object":"chat.completion.chunk","created":1760000000,"model":"gpt-4o","choices":[{"index":0,"delta":{"content":"there."}}]}`,
			`{"id":"c1","object":"chat.completion.chunk","created":1760000000,"model":"gpt-4o","choices":[{"index":0,"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":10,"completion_tokens":2,"total_tokens":12}}`,
			"[DONE]",
		} {
			fmt.Fprintf(w, "data: %s\n\n", data)
			flusher.Flush()
		}
	}))
	defer mock.Close()

	cfg := config.DefaultConfig()
	cfg.Auth.Enabled = true
	cfg.Auth.Keys = []config.AuthKeyConfig{{Name: "bench-internal", Key: "bench-key-123", Owner: "bench", KeyType: "internal"}}
	cfg.Providers["openai"] = config.ProviderConfig{BaseURL: mock.URL, APIKey: "dummy", APIFormat: "openai", Models: []string{"gpt-4o"}}
	plugins := []pipeline.Plugin{}
	var grEngine *guardrails.Engine
	var policies *policy.Store
	keyStore := auth.NewKeyStore(cfg.Auth)
	plugins = append(plugins, authplugin.New(keyStore, true))
	if rule != nil {
		cfg.Guardrails = config.GuardrailsConfig{Enabled: true, DefaultTimeout: time.Second, Rules: []config.GuardrailRuleConfig{*rule}}
		reg := map[string]guardrails.Guardrail{rule.Name: benchAnswerCheck{}}
		grEngine = guardrails.NewEngine(cfg.Guardrails, reg)
		policies = policy.NewStore()
		plugins = append(plugins, guardrails.NewPlugin(grEngine, reg, nil, policies, nil))
	}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		b.Fatalf("creating registry: %v", err)
	}
	srv := New(cfg, "", registry, pipeline.NewEngine(plugins...), keyStore, grEngine, policies, nil, testModelDBPtr(), nil, nil)
	srv.ready.Store(true)

	reqBody := []byte(`{"model":"gpt-4o","stream":true,"messages":[{"role":"user","content":"Hello"}]}`)
	b.ResetTimer()
	b.ReportAllocs()
	for i := 0; i < b.N; i++ {
		req := httptest.NewRequest("POST", "/v1/chat/completions", bytes.NewReader(reqBody))
		req.Header.Set("Content-Type", "application/json")
		req.Header.Set("Authorization", "Bearer bench-key-123")
		w := httptest.NewRecorder()
		srv.httpServer.Handler.ServeHTTP(w, req)
		if w.Code != 200 || !strings.Contains(w.Body.String(), "there.") {
			b.Fatalf("status = %d, body = %s", w.Code, w.Body.String())
		}
	}
}
