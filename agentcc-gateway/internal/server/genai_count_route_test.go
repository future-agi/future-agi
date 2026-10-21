package server

import (
	"bytes"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/providers"
)

func TestProviderQualifiedNativeTokenCount(t *testing.T) {
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/v1/models" { // Startup connectivity probe, not counting.
			_, _ = w.Write([]byte(`{"data":[]}`))
			return
		}
		if r.URL.Path != "/v1beta/models/gemini-3.8-flash:countTokens" {
			t.Errorf("unexpected upstream path: %s", r.URL.Path)
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"totalTokens":123}`))
	}))
	defer upstream.Close()
	cfg := config.DefaultConfig()
	cfg.Providers["count_fixture"] = config.ProviderConfig{
		BaseURL: upstream.URL, APIFormat: "gemini",
		Models: []string{"vertex_ai/gemini-3.8-flash"},
	}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatal(err)
	}
	srv := New(cfg, "", registry, pipeline.NewEngine(), nil, nil, nil, nil, testModelDBPtr(), nil, nil)
	srv.ready.Store(true)
	req := httptest.NewRequest("POST", "/v1beta/models/vertex_ai/gemini-3.8-flash:countTokens",
		bytes.NewBufferString(`{"contents":[{"role":"user","parts":[{"text":"count"}]}]}`))
	w := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(w, req)
	if w.Code != http.StatusOK || w.Body.String() != `{"totalTokens":123}` {
		t.Fatalf("status=%d body=%s", w.Code, w.Body.String())
	}
}
