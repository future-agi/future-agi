package server

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"slices"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/routing"
)

// Shutdown sends the shadow results captured so far rather than leaving them
// for a periodic flush that the stopping process never reaches.
func TestShutdown_SendsCapturedShadowResults(t *testing.T) {
	received := make(chan []string, 1)
	backend := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/agentcc/webhook/shadow-results/" {
			http.NotFound(w, r)
			return
		}
		var payload struct {
			Results []routing.ShadowResult `json:"results"`
		}
		_ = json.NewDecoder(r.Body).Decode(&payload)
		var ids []string
		for _, res := range payload.Results {
			ids = append(ids, res.RequestID)
		}
		received <- ids
	}))
	defer backend.Close()
	cfg := config.DefaultConfig()
	cfg.ControlPlane.URL = backend.URL
	cfg.Routing.Mirror = config.MirrorConfig{
		Enabled:        true,
		CaptureResults: true,
		Rules:          []config.MirrorRule{{SourceModel: "gpt-4o", TargetProvider: "openai", SampleRate: 1}},
	}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatal(err)
	}
	srv := New(cfg, "", registry, pipeline.NewEngine(), nil, nil, nil, nil, testModelDBPtr(), nil, nil)
	srv.handlers.mirror.Load().Store.Add(routing.ShadowResult{RequestID: "req-1"})

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	if err := srv.Shutdown(ctx); err != nil {
		t.Fatalf("Shutdown: %v", err)
	}

	select {
	case ids := <-received:
		if want := []string{"req-1"}; !slices.Equal(ids, want) {
			t.Errorf("backend was sent shadow results %q, want %q", ids, want)
		}
	default:
		t.Error("Shutdown returned without sending the captured shadow results")
	}
}
