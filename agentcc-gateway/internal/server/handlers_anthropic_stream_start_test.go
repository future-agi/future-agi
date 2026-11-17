package server

import (
	"context"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/providers"
)

type failedStartProvider struct{ attempts int }

func (p *failedStartProvider) ID() string { return "failed-start" }
func (p *failedStartProvider) ChatCompletion(context.Context, *models.ChatCompletionRequest) (*models.ChatCompletionResponse, error) {
	return nil, nil
}
func (p *failedStartProvider) StreamChatCompletion(context.Context, *models.ChatCompletionRequest) (<-chan models.StreamChunk, <-chan error) {
	p.attempts++
	chunks := make(chan models.StreamChunk)
	errs := make(chan error, 1)
	errs <- models.ErrUpstreamProvider(0, "upstream stream ended before its first chunk")
	close(chunks)
	close(errs)
	return chunks, errs
}
func (p *failedStartProvider) ListModels(context.Context) ([]models.ModelObject, error) {
	return nil, nil
}
func (p *failedStartProvider) Close() error { return nil }

func TestAnthropicStreamStartFailureReturnsRetryableHTTPError(t *testing.T) {
	provider := &failedStartProvider{}
	registry, err := providers.NewRegistry(config.DefaultConfig())
	if err != nil {
		t.Fatal(err)
	}
	h := &Handlers{engine: pipeline.NewEngine(), registry: registry}
	rc := models.AcquireRequestContext()
	defer rc.Release()
	w := httptest.NewRecorder()
	h.handleAnthropicStreamViaCanonical(context.Background(), w, rc, provider, nil, &models.ChatCompletionRequest{Model: "test", Stream: true}, nil)
	if provider.attempts != 1 {
		t.Fatalf("retries are unconfigured, as on chat; got %d attempts, want 1", provider.attempts)
	}
	if w.Code != http.StatusBadGateway {
		t.Fatalf("expected HTTP 502 rather than a broken 200 stream, got %d: %s", w.Code, w.Body.String())
	}
	if strings.HasPrefix(w.Header().Get("Content-Type"), "text/event-stream") {
		t.Fatal("pre-stream provider error must not be reported as SSE success")
	}
}
