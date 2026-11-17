package server

import (
	"bytes"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/metrics"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/providers"
)

// server.max_request_timeout may be set above server.write_timeout: the
// Timeout middleware moves the response write deadline to the request's own
// deadline. That only holds when every wrapper in the server's middleware
// chain lets http.ResponseController reach the connection. This runs the real
// server's handler chain behind a real listener whose write_timeout expires
// long before a slow upstream answers, and the answer must still arrive.
func TestWriteDeadlineThroughTheServerChain(t *testing.T) {
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/v1/chat/completions" {
			http.NotFound(w, r)
			return
		}
		time.Sleep(150 * time.Millisecond) // past write_timeout, inside the ceiling
		w.Header().Set("Content-Type", "application/json")
		io.WriteString(w, `{"id":"chatcmpl-slow","object":"chat.completion","created":1700000000,"model":"gpt-4o",`+
			`"choices":[{"index":0,"message":{"role":"assistant","content":"late but here"},"finish_reason":"stop"}],`+
			`"usage":{"prompt_tokens":3,"completion_tokens":3,"total_tokens":6}}`)
	}))
	t.Cleanup(upstream.Close)

	cfg := config.DefaultConfig()
	cfg.Server.WriteTimeout = 30 * time.Millisecond
	cfg.Server.MaxRequestTimeout = 5 * time.Second
	cfg.Server.DefaultRequestTimeout = 5 * time.Second
	cfg.Providers["openai"] = config.ProviderConfig{BaseURL: upstream.URL, APIFormat: "openai", Models: []string{"gpt-4o"}}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatalf("creating registry: %v", err)
	}
	srv := New(cfg, "", registry, pipeline.NewEngine(), nil, nil, nil, metrics.NewRegistry(), testModelDBPtr(), nil, nil)
	srv.ready.Store(true)

	ts := httptest.NewUnstartedServer(srv.httpServer.Handler)
	ts.Config.WriteTimeout = cfg.Server.WriteTimeout
	ts.Start()
	t.Cleanup(ts.Close)

	body := `{"model":"gpt-4o","messages":[{"role":"user","content":"hi"}]}`
	resp, err := http.Post(ts.URL+"/v1/chat/completions", "application/json", bytes.NewBufferString(body))
	if err != nil {
		t.Fatalf("the response never arrived (the write deadline cut it): %v", err)
	}
	defer resp.Body.Close()
	raw, err := io.ReadAll(resp.Body)
	if err != nil {
		t.Fatalf("reading the body: %v", err)
	}
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("status %d, want 200. Body: %s", resp.StatusCode, raw)
	}
	var out models.ChatCompletionResponse
	if err := json.Unmarshal(raw, &out); err != nil || len(out.Choices) != 1 {
		t.Fatalf("body %s: %v", raw, err)
	}
}
