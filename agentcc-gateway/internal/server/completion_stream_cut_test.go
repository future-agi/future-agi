package server

import (
	"context"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/providers"
)

// cutAtDeadlineStream sends one chunk, then, as an adapter does, closes its
// channels with no error once the request's context ends.
type cutAtDeadlineStream struct {
	providers.Provider
}

func (cutAtDeadlineStream) StreamChatCompletion(ctx context.Context, _ *models.ChatCompletionRequest) (<-chan models.StreamChunk, <-chan error) {
	chunks, errs := make(chan models.StreamChunk, 1), make(chan error, 1)
	text := "partial"
	chunks <- models.StreamChunk{ID: "cut", Model: "resolved", Choices: []models.StreamChoice{{Index: 0, Delta: models.Delta{Content: &text}}}}
	go func() {
		<-ctx.Done()
		close(errs)
		close(chunks)
	}()
	return chunks, errs
}

// slowFirstWrite is a client whose first write takes past the request's
// deadline, so the stream loop starts with the context ended and the
// adapter's channels closed: both are ready at once.
type slowFirstWrite struct {
	*httptest.ResponseRecorder
	wrote bool
	delay time.Duration
}

func (w *slowFirstWrite) Write(b []byte) (int, error) {
	if !w.wrote {
		w.wrote = true
		time.Sleep(w.delay)
	}
	return w.ResponseRecorder.Write(b)
}

func (w *slowFirstWrite) Flush() { w.ResponseRecorder.Flush() }

// A /v1/completions stream the request's deadline cuts never ends with
// [DONE], whichever of the adapter's close and the context's end the stream
// loop sees first.
func TestCompletionStreamCutByDeadlineNeverEndsDone(t *testing.T) {
	for i := 0; i < 24; i++ {
		rc := models.AcquireRequestContext()
		rc.Provider, rc.Model, rc.IsStream = "fixture", "probe", true
		rc.Request = &models.ChatCompletionRequest{Model: "probe", Stream: true}
		post := &admissionPostObserver{}
		h := &Handlers{engine: pipeline.NewEngine(post)}
		w := &slowFirstWrite{ResponseRecorder: httptest.NewRecorder(), delay: 120 * time.Millisecond}
		ctx, cancel := context.WithTimeout(context.Background(), 30*time.Millisecond)
		h.handleCompletionStream(ctx, w, rc, cutAtDeadlineStream{})
		cancel()
		body := w.Body.String()
		rc.Release()
		if !strings.Contains(body, "partial") {
			t.Fatalf("run %d: the chunk sent before the cut is missing: %q", i, body)
		}
		if strings.Contains(body, "[DONE]") {
			t.Fatalf("run %d: a stream the deadline cut ended with [DONE]: %q", i, body)
		}
		if post.calls != 1 {
			t.Fatalf("run %d: post-plugins ran %d times; want 1", i, post.calls)
		}
	}
}
