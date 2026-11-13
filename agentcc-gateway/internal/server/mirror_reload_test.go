package server

import (
	"bytes"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/providers"
)

// routing.mirror.max_concurrent must hold across POST /-/reload. A reload
// builds a new mirror, but shadow requests the previous one started are still
// running and must keep counting against the limit, including when mirroring
// is switched off and back on in between.
func TestMirrorLimitHoldsAcrossConfigReload(t *testing.T) {
	prod := startMockOpenAI(t)
	t.Cleanup(prod.Close)

	var calls, inFlight atomic.Int64
	release := make(chan struct{})
	var releaseOnce sync.Once
	releaseAll := func() { releaseOnce.Do(func() { close(release) }) }
	shadow := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost || r.URL.Path != "/v1/chat/completions" {
			// The registry's start-up check of a local provider: not a mirror.
			w.Header().Set("Content-Type", "application/json")
			w.Write([]byte(`{"object":"list","data":[]}`))
			return
		}
		calls.Add(1)
		inFlight.Add(1)
		defer inFlight.Add(-1)
		select {
		case <-release:
		case <-r.Context().Done():
			return
		}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(models.ChatCompletionResponse{
			ID:     "shadow",
			Object: "chat.completion",
			Model:  "shadow-model",
			Choices: []models.Choice{{
				Message:      models.Message{Role: "assistant", Content: json.RawMessage(`"ok"`)},
				FinishReason: "stop",
			}},
		})
	}))
	// Cleanups run last-in first-out: unblock the shadow handler before its
	// server closes, or Close waits on it.
	t.Cleanup(shadow.Close)
	t.Cleanup(releaseAll)

	path := filepath.Join(t.TempDir(), "gateway.yaml")
	writeConfig := func(enabled bool, maxConcurrent int) {
		t.Helper()
		yaml := fmt.Sprintf(`admin:
  token: reload-test-token
providers:
  openai:
    base_url: %q
    api_format: openai
    models: ["gpt-4o"]
  shadow:
    base_url: %q
    api_format: openai
    models: ["shadow-model"]
routing:
  mirror:
    enabled: %t
    max_concurrent: %d
    rules:
      - source_model: "*"
        target_provider: shadow
        target_model: shadow-model
        sample_rate: 1
`, prod.URL, shadow.URL, enabled, maxConcurrent)
		if err := os.WriteFile(path, []byte(yaml), 0o600); err != nil {
			t.Fatal(err)
		}
	}

	writeConfig(true, 1)
	cfg, err := config.Load(path)
	if err != nil {
		t.Fatalf("loading config: %v", err)
	}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatalf("creating registry: %v", err)
	}
	srv := New(cfg, path, registry, pipeline.NewEngine(), nil, nil, nil, nil, testModelDBPtr(), nil, nil)
	srv.ready.Store(true)
	adminToken := cfg.Admin.Token

	chat := func() {
		t.Helper()
		req := httptest.NewRequest("POST", "/v1/chat/completions",
			bytes.NewBufferString(`{"model":"gpt-4o","messages":[{"role":"user","content":"Hello"}]}`))
		req.Header.Set("Content-Type", "application/json")
		w := httptest.NewRecorder()
		srv.httpServer.Handler.ServeHTTP(w, req)
		if w.Code != http.StatusOK {
			t.Fatalf("chat: status %d, body %s", w.Code, w.Body.String())
		}
	}
	reload := func() {
		t.Helper()
		req := httptest.NewRequest("POST", "/-/reload", nil)
		req.Header.Set("Authorization", "Bearer "+adminToken)
		w := httptest.NewRecorder()
		srv.httpServer.Handler.ServeHTTP(w, req)
		if w.Code != http.StatusOK {
			t.Fatalf("reload: status %d, body %s", w.Code, w.Body.String())
		}
	}
	waitForCalls := func(want int64) {
		t.Helper()
		deadline := time.Now().Add(2 * time.Second)
		for calls.Load() < want && time.Now().Before(deadline) {
			time.Sleep(10 * time.Millisecond)
		}
		if got := calls.Load(); got < want {
			t.Fatalf("shadow calls = %d, want %d", got, want)
		}
	}
	// settle gives a mirror goroutine that should not exist time to show up.
	settle := func() { time.Sleep(300 * time.Millisecond) }

	// max_concurrent=1: the first chat's shadow call holds the only slot.
	chat()
	waitForCalls(1)

	// Reloading the unchanged config must not reset the count.
	reload()
	chat()
	settle()
	if got := calls.Load(); got != 1 {
		t.Fatalf("after reloading the unchanged config, shadow calls = %d (in flight %d), want 1: max_concurrent=1 and the first is still running", got, inFlight.Load())
	}

	// Nor must switching mirroring off and on again.
	writeConfig(false, 1)
	reload()
	writeConfig(true, 1)
	reload()
	chat()
	settle()
	if got := calls.Load(); got != 1 {
		t.Fatalf("after disabling and re-enabling mirroring, shadow calls = %d (in flight %d), want 1", got, inFlight.Load())
	}

	// Raising the limit to 2 admits exactly one more next to the running call.
	writeConfig(true, 2)
	reload()
	chat()
	waitForCalls(2)
	chat()
	settle()
	if got := calls.Load(); got != 2 {
		t.Fatalf("with max_concurrent=2 and two calls in flight, shadow calls = %d, want 2", got)
	}

	// Once the running calls finish, the reloaded mirror sends shadow traffic again.
	releaseAll()
	deadline := time.Now().Add(3 * time.Second)
	for calls.Load() < 3 && time.Now().Before(deadline) {
		chat()
		time.Sleep(50 * time.Millisecond)
	}
	if got := calls.Load(); got < 3 {
		t.Fatalf("shadow calls = %d after the in-flight calls finished, want a new one", got)
	}
}
