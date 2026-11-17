package server

import (
	"context"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/translation"
)

// Expire only after Process has entered the provider callback; expiring
// beforehand tests the pipeline's separate request-timeout (408) policy.
// Channel ordering makes the startup deadline deterministic, without sleeps.
type dxStartupDeadlineContext struct {
	context.Context
	done chan struct{}
}

func (c *dxStartupDeadlineContext) Done() <-chan struct{} { return c.done }
func (c *dxStartupDeadlineContext) Err() error {
	select {
	case <-c.done:
		return context.DeadlineExceeded
	default:
		return nil
	}
}

type dxWaitingStream struct {
	providers.Provider
	chunks <-chan models.StreamChunk
	expire func()
}

func (p dxWaitingStream) StreamChatCompletion(ctx context.Context, _ *models.ChatCompletionRequest) (<-chan models.StreamChunk, <-chan error) {
	p.expire()
	<-ctx.Done()
	return p.chunks, nil
}

func TestDXGenAIStartupDeadlinePreservesAdmissionAndUsage(t *testing.T) {
	for _, phase := range []string{"waiting", "closed", "reported-prefix"} {
		t.Run(phase, func(t *testing.T) {
			ctx := &dxStartupDeadlineContext{Context: context.Background(), done: make(chan struct{})}
			chunks := make(chan models.StreamChunk, 1)
			if phase == "closed" {
				close(chunks)
			} else {
				t.Cleanup(func() { close(chunks) })
			}
			if phase == "reported-prefix" {
				chunks <- models.StreamChunk{Model: "resolved", Usage: models.MeasuredUsage(3, 5)}
			}
			rc := models.AcquireRequestContext()
			defer rc.Release()
			rc.Model, rc.Provider, rc.IsStream = "probe", "fixture", true
			rc.Request = &models.ChatCompletionRequest{Model: "probe", Stream: true}
			post := &admissionPostObserver{}
			h := &Handlers{engine: pipeline.NewEngine(post)}
			translator, ok := translation.InboundFor("google")
			if !ok {
				t.Fatal("no GenAI translator")
			}
			w := httptest.NewRecorder()
			h.handleGenAIStreamViaCanonical(ctx, w, rc, dxWaitingStream{chunks: chunks, expire: func() { close(ctx.done) }}, translator, rc.Request)
			if w.Code != 504 || w.Flushed || w.Header().Get("Content-Type") != "application/json" || !strings.Contains(w.Body.String(), `"status":"DEADLINE_EXCEEDED"`) {
				t.Fatalf("deadline HTTP %d %s", w.Code, w.Body.String())
			}
			if post.calls != 1 {
				t.Fatalf("post calls=%d", post.calls)
			}
			if phase == "reported-prefix" {
				if post.usage == nil || post.usage.PromptTokens != 3 || post.usage.CompletionTokens != 5 {
					t.Fatalf("lost prefix usage: %+v", post.usage)
				}
			} else if post.usage != nil {
				t.Fatalf("invented usage: %+v", post.usage)
			}
		})
	}
}
