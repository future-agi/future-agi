package server

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	authplugin "github.com/futureagi/agentcc-gateway/internal/plugins/auth"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/routing"
)

// x-agentcc-max-attempts reaches the failover's retryer.
func TestRetryHeadersReachTheRetryLoop(t *testing.T) {
	var calls atomic.Int32
	up := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if !strings.HasSuffix(r.URL.Path, "/chat/completions") {
			return
		}
		calls.Add(1)
		w.WriteHeader(http.StatusServiceUnavailable)
		_, _ = w.Write([]byte(`{"error":{"message":"down","type":"server_error"}}`))
	}))
	defer up.Close()
	cfg := config.DefaultConfig()
	cfg.Auth.Enabled = true
	cfg.Auth.Keys = []config.AuthKeyConfig{{Name: "k", Key: "sk-retry-headers", Owner: "t", KeyType: "internal"}}
	cfg.Providers["only"] = config.ProviderConfig{BaseURL: up.URL, APIKey: "dummy", APIFormat: "openai", Models: []string{"gpt-4o"}}
	cfg.Routing.DefaultStrategy = "least-latency"
	cfg.Providers["twin"] = config.ProviderConfig{BaseURL: up.URL, APIKey: "dummy", APIFormat: "openai", Models: []string{"gpt-4o"}}
	cfg.Routing.Targets = map[string][]config.RoutingTargetConfig{"gpt-4o": {{Provider: "only", Weight: 1}, {Provider: "twin", Weight: 1}}}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatal(err)
	}
	keyStore := auth.NewKeyStore(cfg.Auth)
	srv := New(cfg, "", registry, pipeline.NewEngine(authplugin.New(keyStore, true)), keyStore, nil, nil, nil, testModelDBPtr(), nil, nil)
	retryer := routing.NewRetryer(config.RetryConfig{Enabled: true, MaxRetries: 3, InitialDelay: time.Millisecond, MaxDelay: time.Millisecond})
	srv.handlers.failover.Store(routing.NewFailover(config.FailoverConfig{Enabled: true, MaxAttempts: 1}, registry.Router(), retryer, nil))

	do := func(headers ...string) int {
		req := httptest.NewRequest(http.MethodPost, "/v1/chat/completions", strings.NewReader(`{"model":"gpt-4o","messages":[{"role":"user","content":"hi"}]}`))
		req.Header.Set("Authorization", "Bearer sk-retry-headers")
		for i := 0; i+1 < len(headers); i += 2 {
			req.Header.Set(headers[i], headers[i+1])
		}
		w := httptest.NewRecorder()
		srv.httpServer.Handler.ServeHTTP(w, req)
		return w.Code
	}
	calls.Store(0)
	do()
	if calls.Load() != 4 {
		t.Fatalf("no header: %d upstream tries, want the org's 4", calls.Load())
	}
	calls.Store(0)
	do("x-agentcc-max-attempts", "2")
	if calls.Load() != 2 {
		t.Fatalf("x-agentcc-max-attempts 2: %d upstream tries, want 2", calls.Load())
	}
	calls.Store(0)
	do("x-agentcc-max-attempts", "5")
	if calls.Load() != 4 {
		t.Fatalf("x-agentcc-max-attempts 5: %d upstream tries, want the org's 4", calls.Load())
	}
	calls.Store(0)
	if code := do("x-agentcc-backoff", "fibonacci"); code != http.StatusBadRequest || calls.Load() != 0 {
		t.Fatalf("bad backoff: status %d after %d tries, want 400 before any", code, calls.Load())
	}
}
