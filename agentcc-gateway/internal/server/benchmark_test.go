// Copyright 2026 Future AGI, Inc.
// SPDX-License-Identifier: Apache-2.0

package server

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	authplugin "github.com/futureagi/agentcc-gateway/internal/plugins/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/providers"
)

// BenchmarkChatCompletionNonStreaming measures one chat-completion request
// through the server's HTTP handler, in process: the request goes straight to
// ServeHTTP with an httptest.NewRecorder, so the client side opens no socket.
// The mock upstream is an httptest.NewServer on 127.0.0.1, so each op also
// makes one HTTP round trip over loopback TCP to it. The bench server loads
// only the auth plugin (no cost, logging or rate-limit plugin). ns/op is not
// end-to-end latency, nor the gateway's overhead in a deployment.
//
// Reproduce: GOMAXPROCS=4 go test ./internal/server -run '^$' -bench '^BenchmarkChatCompletion' -benchmem -count 40
func BenchmarkChatCompletionNonStreaming(b *testing.B) {
	mock := startBenchMockOpenAI(b)
	defer mock.Close()
	srv := createBenchServer(b, mock.URL)

	reqBody := []byte(`{"model":"gpt-4o","messages":[{"role":"user","content":"Hello"}]}`)

	b.ResetTimer()
	b.ReportAllocs()
	for i := 0; i < b.N; i++ {
		req := httptest.NewRequest("POST", "/v1/chat/completions", bytes.NewReader(reqBody))
		req.Header.Set("Content-Type", "application/json")
		req.Header.Set("Authorization", "Bearer bench-key-123")
		w := httptest.NewRecorder()
		srv.httpServer.Handler.ServeHTTP(w, req)
		if w.Code != 200 {
			b.Fatalf("status = %d, body = %s", w.Code, w.Body.String())
		}
	}
}

// BenchmarkChatCompletionParallel sends the same request from GOMAXPROCS
// goroutines at once (RunParallel). ns/op is the wall time divided by all
// requests completed, not the time one request takes, and not throughput
// under load. Useful for gauging contention on shared pipeline state.
func BenchmarkChatCompletionParallel(b *testing.B) {
	mock := startBenchMockOpenAI(b)
	defer mock.Close()
	srv := createBenchServer(b, mock.URL)

	reqBody := []byte(`{"model":"gpt-4o","messages":[{"role":"user","content":"Hello"}]}`)

	b.ResetTimer()
	b.ReportAllocs()
	b.RunParallel(func(pb *testing.PB) {
		for pb.Next() {
			req := httptest.NewRequest("POST", "/v1/chat/completions", bytes.NewReader(reqBody))
			req.Header.Set("Content-Type", "application/json")
			req.Header.Set("Authorization", "Bearer bench-key-123")
			w := httptest.NewRecorder()
			srv.httpServer.Handler.ServeHTTP(w, req)
			if w.Code != 200 {
				b.Fatalf("status = %d, body = %s", w.Code, w.Body.String())
			}
		}
	})
}

// BenchmarkModelsEndpoint measures GET /v1/models through the same handler.
func BenchmarkModelsEndpoint(b *testing.B) {
	mock := startBenchMockOpenAI(b)
	defer mock.Close()
	srv := createBenchServer(b, mock.URL)

	b.ResetTimer()
	b.ReportAllocs()
	for i := 0; i < b.N; i++ {
		req := httptest.NewRequest("GET", "/v1/models", nil)
		req.Header.Set("Authorization", "Bearer bench-key-123")
		w := httptest.NewRecorder()
		srv.httpServer.Handler.ServeHTTP(w, req)
		if w.Code != 200 {
			b.Fatalf("status = %d, body = %s", w.Code, w.Body.String())
		}
	}
}

// ─── helpers ────────────────────────────────────────────────────────────────

// startBenchMockOpenAI is startMockOpenAI but accepting *testing.B.
func startBenchMockOpenAI(b *testing.B) *httptest.Server {
	b.Helper()
	// Canned non-streaming response — instant.
	resp, _ := json.Marshal(map[string]any{
		"id":      "chatcmpl-bench",
		"object":  "chat.completion",
		"created": 1700000000,
		"model":   "gpt-4o",
		"choices": []any{
			map[string]any{
				"index":         0,
				"message":       map[string]string{"role": "assistant", "content": "ok"},
				"finish_reason": "stop",
			},
		},
		"usage": map[string]int{"prompt_tokens": 10, "completion_tokens": 1, "total_tokens": 11},
	})
	return httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(200)
		_, _ = w.Write(resp)
	}))
}

// createBenchServer configures a server with an auth-enabled internal key so
// the handler's model-ACL check lets requests through to the proxy layer.
func createBenchServer(b *testing.B, mockURL string) *Server {
	b.Helper()
	cfg := config.DefaultConfig()
	cfg.Auth.Enabled = true
	cfg.Auth.Keys = []config.AuthKeyConfig{{
		Name:    "bench-internal",
		Key:     "bench-key-123",
		Owner:   "bench",
		KeyType: "internal",
	}}
	cfg.Providers["openai"] = config.ProviderConfig{
		BaseURL:   mockURL,
		APIKey:    "dummy",
		APIFormat: "openai",
		Models:    []string{"gpt-4o", "gpt-4o-mini"},
	}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		b.Fatalf("creating registry: %v", err)
	}
	keyStore := auth.NewKeyStore(cfg.Auth)
	engine := pipeline.NewEngine(authplugin.New(keyStore, true))
	srv := New(cfg, "", registry, engine, keyStore, nil, nil, nil, testModelDBPtr(), nil, nil)
	srv.ready.Store(true)
	return srv
}
