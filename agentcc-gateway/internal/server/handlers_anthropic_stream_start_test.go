package server

import (
	"context"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
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
	h := &Handlers{engine: pipeline.NewEngine()}
	rc := models.AcquireRequestContext()
	defer rc.Release()
	w := httptest.NewRecorder()
	h.handleAnthropicStreamViaCanonical(context.Background(), w, rc, provider, nil, &models.ChatCompletionRequest{Model: "test"})
	if provider.attempts != 2 {
		t.Fatalf("expected one retry before emitting a response, got %d attempts", provider.attempts)
	}
	if w.Code != http.StatusBadGateway {
		t.Fatalf("expected HTTP 502 rather than a broken 200 stream, got %d: %s", w.Code, w.Body.String())
	}
	if strings.HasPrefix(w.Header().Get("Content-Type"), "text/event-stream") {
		t.Fatal("pre-stream provider error must not be reported as SSE success")
	}
}
