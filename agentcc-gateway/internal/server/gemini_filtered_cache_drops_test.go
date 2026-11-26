package server

import (
	"net/http"
	"net/http/httptest"
	"sync/atomic"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/providers/gemini"
)

func TestGeminiFilteredDropNotesSurviveCacheHit(t *testing.T) {
	var calls atomic.Int32
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"candidates":[{"content":{"role":"model","parts":[{"text":"ok"}]},"finishReason":"STOP"}]}`))
	}))
	t.Cleanup(upstream.Close)
	p, err := gemini.New("gemini", config.ProviderConfig{BaseURL: upstream.URL, APIKey: "test-key"})
	if err != nil {
		t.Fatal(err)
	}
	srv, _ := newCachedResponsesBridgeServer(t)
	srv.registry.RegisterProvider("gemini", p, []string{"gemini-cache-test"})

	// Keep one real warning: parity alone would pass if both paths warned
	// about the excluded function, or if the cached entry lost its route.
	const body = `{"model":"gemini-cache-test","messages":[{"role":"user","content":"hello"}],
		"parallel_tool_calls":false,
		"tools":[{"type":"function","function":{"name":"selected"}},
			{"type":"function","function":{"name":"excluded"},"allowed_callers":["synthetic"]}],
		"tool_choice":{"type":"allowed_tools","allowed_tools":{"mode":"auto","tools":[{"type":"function","name":"selected"}]}}}`
	for _, status := range []string{"miss", "hit_exact"} {
		w := postCachedBridge(t, srv, "/v1/chat/completions", body)
		if got := w.Header().Get("x-agentcc-cache"); got != status {
			t.Fatalf("cache status = %q, want %q", got, status)
		}
		if got := w.Header().Get("x-agentcc-translation-drops"); got != "parallel_tool_calls_unsupported" {
			t.Errorf("%s: translation drops = %q, want only parallel_tool_calls_unsupported", status, got)
		}
	}
	if got := calls.Load(); got != 1 {
		t.Errorf("upstream calls = %d, want 1", got)
	}
}
