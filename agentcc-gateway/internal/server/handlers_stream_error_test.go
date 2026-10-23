package server

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
)

// failingStreamProvider streams `chunks` chunks and then fails with err,
// leaving its channels exactly as a real provider does when the upstream
// breaks: the error buffered on errs, then errs and chunks closed.
type failingStreamProvider struct {
	chunks int
	err    *models.APIError
}

func (p *failingStreamProvider) ID() string { return "failing-stream" }
func (p *failingStreamProvider) ChatCompletion(context.Context, *models.ChatCompletionRequest) (*models.ChatCompletionResponse, error) {
	return nil, nil
}
func (p *failingStreamProvider) StreamChatCompletion(context.Context, *models.ChatCompletionRequest) (<-chan models.StreamChunk, <-chan error) {
	content := "partial"
	chunks := make(chan models.StreamChunk, p.chunks)
	errs := make(chan error, 1)
	for i := 0; i < p.chunks; i++ {
		chunks <- models.StreamChunk{
			ID:      "chatcmpl-partial",
			Model:   "test-model",
			Choices: []models.StreamChoice{{Delta: models.Delta{Content: &content}}},
		}
	}
	errs <- p.err
	close(errs)
	close(chunks)
	return chunks, errs
}
func (p *failingStreamProvider) ListModels(context.Context) ([]models.ModelObject, error) {
	return nil, nil
}
func (p *failingStreamProvider) Close() error { return nil }

var errOverloaded = &models.APIError{
	Status:  http.StatusBadGateway,
	Type:    models.ErrTypeUpstream,
	Code:    "provider_overloaded_error",
	Message: "Overloaded",
}

func newFailingStreamRequestContext() *models.RequestContext {
	rc := models.AcquireRequestContext()
	rc.RequestID = "req-failing-stream"
	rc.Model = "test-model"
	rc.Provider = "failing-stream"
	rc.RequestHeaders = http.Header{}
	rc.Request = &models.ChatCompletionRequest{
		Model:    "test-model",
		Stream:   true,
		Messages: []models.Message{{Role: "user", Content: json.RawMessage(`"hello"`)}},
	}
	return rc
}

func TestPendingStreamError(t *testing.T) {
	open := make(chan error, 1)
	if err := pendingStreamError(open); err != nil {
		t.Fatalf("open channel with nothing buffered: got %v, want nil", err)
	}

	closedEmpty := make(chan error, 1)
	close(closedEmpty)
	if err := pendingStreamError(closedEmpty); err != nil {
		t.Fatalf("closed channel with nothing buffered: got %v, want nil", err)
	}

	want := errors.New("upstream failed")
	closedWithErr := make(chan error, 1)
	closedWithErr <- want
	close(closedWithErr)
	if err := pendingStreamError(closedWithErr); err != want {
		t.Fatalf("closed channel with a buffered error: got %v, want %v", err, want)
	}

	if err := pendingStreamError(nil); err != nil {
		t.Fatalf("nil channel: got %v, want nil", err)
	}
}

// Once the provider has failed, its error and its closed chunk channel are
// ready at the same time and select picks between them at random, so each
// handler runs many times to cover both orders.
const failingStreamRuns = 100

func TestHandleStreamReportsProviderErrorAfterChunksClose(t *testing.T) {
	h := &Handlers{engine: pipeline.NewEngine()}
	provider := &failingStreamProvider{chunks: 2, err: errOverloaded}
	streamed := 0
	for i := 0; i < failingStreamRuns; i++ {
		rc := newFailingStreamRequestContext()
		w := httptest.NewRecorder()

		h.handleStream(context.Background(), w, rc, provider, nil)

		body := w.Body.String()
		if !strings.Contains(body, "provider_overloaded_error") {
			t.Fatalf("run %d: response does not carry the provider error (status %d): %s", i, w.Code, body)
		}
		// Taking the error before the first chunk is also correct: it becomes a
		// plain HTTP error that failover can act on.
		if w.Code == http.StatusOK {
			streamed++
			if strings.Contains(body, "data: [DONE]") {
				t.Fatalf("run %d: failed stream was terminated with [DONE]: %s", i, body)
			}
			if len(rc.Errors) == 0 {
				t.Fatalf("run %d: mid-stream provider error was not recorded on the request context", i)
			}
		} else if w.Code != http.StatusBadGateway {
			t.Fatalf("run %d: status = %d, want 200 (streamed) or 502 (failed before first chunk)", i, w.Code)
		}
		rc.Release()
	}
	if streamed == 0 {
		t.Fatal("no run reached the streaming phase; the mid-stream path was never exercised")
	}
}

func TestHandleCompletionStreamReportsProviderErrorAfterChunksClose(t *testing.T) {
	h := &Handlers{engine: pipeline.NewEngine()}
	provider := &failingStreamProvider{chunks: 2, err: errOverloaded}
	for i := 0; i < failingStreamRuns; i++ {
		rc := newFailingStreamRequestContext()
		w := httptest.NewRecorder()

		h.handleCompletionStream(context.Background(), w, rc, provider)

		body := w.Body.String()
		if !strings.Contains(body, "provider_overloaded_error") {
			t.Fatalf("run %d: stream ended without the provider error: %s", i, body)
		}
		if strings.Contains(body, "data: [DONE]") {
			t.Fatalf("run %d: failed stream was terminated with [DONE]: %s", i, body)
		}
		if len(rc.Errors) == 0 {
			t.Fatalf("run %d: mid-stream provider error was not recorded on the request context", i)
		}
		rc.Release()
	}
}

// A provider that fails before its first chunk must surface its own status,
// not the generic "closed before sending any chunks" 502: failover decides on
// that status.
func TestHandleStreamKeepsProviderStatusWhenFailingBeforeFirstChunk(t *testing.T) {
	h := &Handlers{engine: pipeline.NewEngine()}
	provider := &failingStreamProvider{err: &models.APIError{
		Status:  http.StatusTooManyRequests,
		Type:    models.ErrTypeRateLimit,
		Code:    "provider_rate_limit_error",
		Message: "Rate limited",
	}}
	for i := 0; i < failingStreamRuns; i++ {
		rc := newFailingStreamRequestContext()
		w := httptest.NewRecorder()

		h.handleStream(context.Background(), w, rc, provider, nil)

		if w.Code != http.StatusTooManyRequests {
			t.Fatalf("run %d: status = %d, want %d: %s", i, w.Code, http.StatusTooManyRequests, w.Body.String())
		}
		if !strings.Contains(w.Body.String(), "provider_rate_limit_error") {
			t.Fatalf("run %d: response does not carry the provider error: %s", i, w.Body.String())
		}
		rc.Release()
	}
}
