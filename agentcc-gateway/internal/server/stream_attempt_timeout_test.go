package server

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/routing"
)

// A failover attempt's timeout (routing.failover.per_attempt_timeout) bounds
// a stream only until its first chunk: the stream does not run on the
// attempt's context, which failover cancels as soon as the attempt returns,
// so a stream through failover runs to its end.

// startSlowStreamOpenAI streams a role chunk at once, then the content and
// the end of the stream after gap.
func startSlowStreamOpenAI(t *testing.T, gap time.Duration) *httptest.Server {
	t.Helper()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/v1/chat/completions" || r.Method != http.MethodPost {
			w.Header().Set("Content-Type", "application/json")
			json.NewEncoder(w).Encode(models.ModelListResponse{Object: "list"})
			return
		}
		w.Header().Set("Content-Type", "text/event-stream")
		w.WriteHeader(http.StatusOK)
		flusher := w.(http.Flusher)
		write := func(delta models.Delta, finish *string) {
			data, _ := json.Marshal(models.StreamChunk{
				ID: "chatcmpl-slow", Object: "chat.completion.chunk", Created: 1700000000, Model: "gpt-4o",
				Choices: []models.StreamChoice{{Index: 0, Delta: delta, FinishReason: finish}},
			})
			fmt.Fprintf(w, "data: %s\n\n", data)
			flusher.Flush()
		}
		write(models.Delta{Role: "assistant"}, nil)
		select {
		case <-time.After(gap):
		case <-r.Context().Done():
			return
		}
		content := "after the attempt timeout"
		write(models.Delta{Content: &content}, nil)
		stop := "stop"
		write(models.Delta{}, &stop)
		fmt.Fprint(w, "data: [DONE]\n\n")
		flusher.Flush()
	}))
	t.Cleanup(srv.Close)
	return srv
}

func TestStreamOutlivesItsFailoverAttemptTimeout(t *testing.T) {
	const perAttempt = 100 * time.Millisecond
	slow := startSlowStreamOpenAI(t, 4*perAttempt)

	cfg := config.DefaultConfig()
	// Failover runs only for a model with more than one target.
	cfg.Providers["slow"] = config.ProviderConfig{BaseURL: slow.URL, APIFormat: "openai", Models: []string{"gpt-4o"}}
	cfg.Providers["slow-2"] = config.ProviderConfig{BaseURL: slow.URL, APIFormat: "openai", Models: []string{"gpt-4o"}}
	cfg.Routing.DefaultStrategy = "round-robin"
	cfg.Routing.Targets = map[string][]config.RoutingTargetConfig{"gpt-4o": {
		{Provider: "slow", Weight: 1},
		{Provider: "slow-2", Weight: 1},
	}}
	cfg.Routing.Failover = config.FailoverConfig{
		Enabled:           true,
		MaxAttempts:       2,
		OnStatusCodes:     []int{http.StatusBadGateway},
		PerAttemptTimeout: perAttempt,
	}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatalf("creating registry: %v", err)
	}
	provider, ok := registry.GetProvider("slow")
	if !ok {
		t.Fatal("slow provider not registered")
	}
	h := &Handlers{registry: registry, engine: pipeline.NewEngine()}
	h.failover.Store(routing.NewFailover(cfg.Routing.Failover, registry.Router(), nil, nil))

	rc := models.AcquireRequestContext()
	defer rc.Release()
	rc.RequestID = "req-stream-attempt-timeout"
	rc.Model = "gpt-4o"
	rc.Provider = "slow"
	rc.RequestHeaders = http.Header{}
	rc.Request = &models.ChatCompletionRequest{
		Model:    "gpt-4o",
		Stream:   true,
		Messages: []models.Message{{Role: "user", Content: json.RawMessage(`"hello"`)}},
	}

	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	w := httptest.NewRecorder()
	h.handleStream(ctx, w, rc, provider, nil)

	if w.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200. Body: %s", w.Code, w.Body.String())
	}
	body := w.Body.String()
	if rc.Metadata["routing_strategy"] == "" {
		t.Fatalf("the stream did not go through failover (metadata %v)", rc.Metadata)
	}
	if !strings.Contains(body, "after the attempt timeout") {
		t.Fatalf("the stream ended before the content sent after the attempt timeout: %s", body)
	}
	if !strings.Contains(body, "data: [DONE]") {
		t.Fatalf("stream body missing [DONE]: %s", body)
	}
}
