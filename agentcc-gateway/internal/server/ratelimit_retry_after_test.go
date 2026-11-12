package server

import (
	"bytes"
	"context"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strconv"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	authplugin "github.com/futureagi/agentcc-gateway/internal/plugins/auth"
	"github.com/futureagi/agentcc-gateway/internal/plugins/ratelimit"
	"github.com/futureagi/agentcc-gateway/internal/providers"
)

// The managed evaluator times its one retry from Retry-After. When the
// gateway's own per-key limit refuses a chat completion, streamed (as the
// evaluator asks) or not, the refusal has to say when the window reopens.
func TestChatCompletionRateLimitRefusalCarriesRetryAfter(t *testing.T) {
	for _, stream := range []bool{true, false} {
		t.Run(fmt.Sprintf("stream=%v", stream), func(t *testing.T) {
			mock := startMockOpenAI(t)
			defer mock.Close()

			const key = "sk-agentcc-retry-after-internal"
			cfg := config.DefaultConfig()
			cfg.Auth.Enabled = true
			cfg.Auth.Keys = []config.AuthKeyConfig{{
				Name:         "internal-evaluator",
				Key:          key,
				Owner:        "svc",
				KeyType:      "internal",
				RateLimitRPM: 1,
			}}
			cfg.Providers["openai"] = config.ProviderConfig{
				BaseURL:   mock.URL,
				APIFormat: "openai",
				Models:    []string{"gpt-4o"},
			}
			registry, err := providers.NewRegistry(cfg)
			if err != nil {
				t.Fatalf("creating registry: %v", err)
			}
			t.Cleanup(func() { registry.Close() })

			keyStore := auth.NewKeyStore(cfg.Auth)
			limiter := ratelimit.NewRateLimiter()
			t.Cleanup(limiter.Stop)
			engine := pipeline.NewEngine(
				authplugin.New(keyStore, true),
				ratelimit.New(config.RateLimitConfig{Enabled: true}, keyStore, limiter),
			)
			srv := New(cfg, "", registry, engine, keyStore, nil, nil, nil, testModelDBPtr(), nil, nil)
			srv.ready.Store(true)
			t.Cleanup(func() { srv.Shutdown(context.Background()) })

			body := fmt.Sprintf(`{"model":"gpt-4o","messages":[{"role":"user","content":"synthetic probe"}],"stream":%v}`, stream)
			send := func() *httptest.ResponseRecorder {
				req := httptest.NewRequest(http.MethodPost, "/v1/chat/completions", bytes.NewBufferString(body))
				req.Header.Set("Content-Type", "application/json")
				req.Header.Set("Authorization", "Bearer "+key)
				rr := httptest.NewRecorder()
				srv.httpServer.Handler.ServeHTTP(rr, req)
				return rr
			}

			if first := send(); first.Code != http.StatusOK {
				t.Fatalf("first request: %d %s", first.Code, first.Body.String())
			}
			refused := send()
			if refused.Code != http.StatusTooManyRequests {
				t.Fatalf("second request in the window: %d %s, want 429", refused.Code, refused.Body.String())
			}
			header := refused.Header().Get("Retry-After")
			wait, err := strconv.Atoi(header)
			if err != nil {
				t.Fatalf("the gateway's own 429 has no usable Retry-After (%q); body %s", header, refused.Body.String())
			}
			// The window opened with the first request a moment ago, so the
			// caller must wait (almost) all of it.
			if wait < 59 || wait > 60 {
				t.Errorf("Retry-After = %d, want the rest of a window opened just now (59-60)", wait)
			}
		})
	}
}
