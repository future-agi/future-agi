package server

import (
	"bytes"
	"context"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"strconv"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	authplugin "github.com/futureagi/agentcc-gateway/internal/plugins/auth"
	"github.com/futureagi/agentcc-gateway/internal/plugins/ratelimit"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/redisstate"
)

// rateLimitedSend builds a gateway whose internal key allows one request a
// minute through limiter, and returns a function that sends one chat
// completion, streamed (as the evaluator asks) or not.
func rateLimitedSend(t *testing.T, limiter ratelimit.Limiter, key string, stream bool) func() *httptest.ResponseRecorder {
	t.Helper()
	mock := startMockOpenAI(t)
	t.Cleanup(mock.Close)

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
	engine := pipeline.NewEngine(
		authplugin.New(keyStore, true),
		ratelimit.New(config.RateLimitConfig{Enabled: true}, keyStore, limiter),
	)
	srv := New(cfg, "", registry, engine, keyStore, nil, nil, nil, testModelDBPtr(), nil, nil)
	srv.ready.Store(true)
	t.Cleanup(func() { srv.Shutdown(context.Background()) })

	body := fmt.Sprintf(`{"model":"gpt-4o","messages":[{"role":"user","content":"synthetic probe"}],"stream":%v}`, stream)
	return func() *httptest.ResponseRecorder {
		req := httptest.NewRequest(http.MethodPost, "/v1/chat/completions", bytes.NewBufferString(body))
		req.Header.Set("Content-Type", "application/json")
		req.Header.Set("Authorization", "Bearer "+key)
		rr := httptest.NewRecorder()
		srv.httpServer.Handler.ServeHTTP(rr, req)
		return rr
	}
}

// refusalRetryAfter sends twice and returns the second request's Retry-After.
func refusalRetryAfter(t *testing.T, send func() *httptest.ResponseRecorder) int {
	t.Helper()
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
	return wait
}

// The managed evaluator times its one retry from Retry-After. When the
// gateway's own per-key limit refuses a chat completion, streamed (as the
// evaluator asks) or not, the refusal has to say when the window reopens.
func TestChatCompletionRateLimitRefusalCarriesRetryAfter(t *testing.T) {
	for _, stream := range []bool{true, false} {
		t.Run(fmt.Sprintf("stream=%v", stream), func(t *testing.T) {
			limiter := ratelimit.NewRateLimiter()
			t.Cleanup(limiter.Stop)
			wait := refusalRetryAfter(t, rateLimitedSend(t, limiter, "sk-agentcc-retry-after-internal", stream))
			// The window opened with the first request a moment ago, so the
			// caller must wait (almost) all of it.
			if wait < 59 || wait > 60 {
				t.Errorf("Retry-After = %d, want the rest of a window opened just now (59-60)", wait)
			}
		})
	}
}

// With a Redis client the gateway counts requests in the shared sliding
// window (cmd/agentcc wires redisstate's limiter whenever Redis is
// configured). Its refusal must name the same thing: the seconds until the
// window reopens, not the counter key's two-window TTL, which sent 120.
func TestChatCompletionRateLimitRefusalRetryAfterRedisBacked(t *testing.T) {
	addr := os.Getenv("TEST_REDIS_ADDR")
	if addr == "" {
		addr = "localhost:6379"
	}
	client, err := redisstate.NewClient(redisstate.Config{Address: addr, Timeout: time.Second})
	if err != nil {
		t.Skipf("skipping: Redis not available at %s: %v", addr, err)
	}
	t.Cleanup(func() { client.Close() })

	for _, stream := range []bool{true, false} {
		t.Run(fmt.Sprintf("stream=%v", stream), func(t *testing.T) {
			// Keep both requests inside one minute.
			if s := time.Now().Unix() % 60; s > 57 {
				time.Sleep(time.Duration(61-s) * time.Second)
			}
			ts := strconv.FormatInt(time.Now().UnixNano(), 10)
			local := ratelimit.NewRateLimiter()
			t.Cleanup(local.Stop)
			limiter := redisstate.NewRateLimiter(client, local, "test:server:retry-after:"+ts+":rl:")
			send := rateLimitedSend(t, limiter, "sk-agentcc-retry-after-redis-"+ts, stream)

			before := time.Now().Unix()
			wait := refusalRetryAfter(t, send)
			after := time.Now().Unix()

			// One request fills the window; the next is admitted one second
			// into the next window, once that request's weighted share is 0.
			longest := int(60 - before%60 + 1)
			shortest := int(60 - after%60 + 1)
			if before/60 != after/60 {
				shortest = 1
			}
			if wait < shortest || wait > longest {
				t.Errorf("Retry-After = %d on a Redis-backed gateway, want %d-%d: the seconds until the window reopens", wait, shortest, longest)
			}
		})
	}
}
