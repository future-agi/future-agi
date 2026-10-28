package server

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
)

type triggeredDeadlineContext struct{ context.Context }

func (c triggeredDeadlineContext) Err() error {
	if c.Context.Err() != nil {
		return context.DeadlineExceeded
	}
	return nil
}

// Expire during select setup, after its preceding Err check saw a live context.
// Suppress that select's cancellation arm once to deterministically choose EOF,
// rather than relying on Go's random choice between two ready channels.
type eofDeadlineContext struct {
	triggeredDeadlineContext
	partial, closed <-chan struct{}
	expire          context.CancelFunc
	once            sync.Once
}

func (c *eofDeadlineContext) Done() <-chan struct{} {
	select {
	case <-c.partial:
		forceEOF := false
		c.once.Do(func() {
			c.expire()
			<-c.closed
			forceEOF = true
		})
		if forceEOF {
			return nil
		}
	default:
	}
	return c.Context.Done()
}

type partialStreamProvider struct {
	failedStartProvider
	consumed chan struct{}
	closed   chan struct{}
}

func (p *partialStreamProvider) StreamChatCompletion(ctx context.Context, _ *models.ChatCompletionRequest) (<-chan models.StreamChunk, <-chan error) {
	p.attempts++
	chunks := make(chan models.StreamChunk)
	go func() {
		defer close(p.closed)
		defer close(chunks)
		text := "rejected private stream"
		for i := 0; i < 2; i++ {
			if i == 1 {
				// The opener received the first frame. The collector adds it before
				// its next select; a second frame wakes it if it sampled Done early.
				close(p.consumed)
			}
			select {
			case chunks <- models.StreamChunk{Choices: []models.StreamChoice{{Delta: models.Delta{Content: &text}}}}:
			case <-ctx.Done():
				return
			}
		}
		<-ctx.Done()
	}()
	return chunks, nil
}

func TestReflexionStreamDeadlineAfterPartialCandidate(t *testing.T) {
	watchdog, stop := context.WithTimeout(context.Background(), 5*time.Second)
	defer stop()
	base, expire := context.WithCancel(watchdog)
	defer expire()
	provider := &partialStreamProvider{consumed: make(chan struct{}), closed: make(chan struct{})}
	ctx := &eofDeadlineContext{
		triggeredDeadlineContext: triggeredDeadlineContext{base},
		partial:                  provider.consumed,
		closed:                   provider.closed,
		expire:                   expire,
	}
	observer := &reflectionObserver{}
	h := &Handlers{engine: pipeline.NewEngine(reflectionPlugin(&reflectionCheck{name: "output"}, reflectionPolicy(), "block", "post"), observer)}
	rc := models.AcquireRequestContext()
	defer rc.Release()
	rc.Request = &models.ChatCompletionRequest{Model: "gpt-4o", Messages: []models.Message{{Role: "user", Content: json.RawMessage(`"original"`)}}}
	w := httptest.NewRecorder()
	done := make(chan struct{})
	go func() {
		h.handleStream(ctx, w, rc, provider, nil)
		close(done)
	}()
	select {
	case <-done:
	case <-watchdog.Done():
		t.Fatal("deadline did not stop the buffered stream")
	}
	if w.Code != http.StatusRequestTimeout || provider.attempts != 1 || observer.count != 1 || observer.snapshot.Response != nil || !observer.snapshot.Flags.Timeout || strings.Contains(w.Body.String(), "data:") || strings.Contains(w.Body.String(), "private stream") {
		t.Fatalf("deadline leaked a partial candidate or skipped finalization: status=%d calls=%d observers=%d body=%s", w.Code, provider.attempts, observer.count, w.Body.String())
	}
}

func TestCollectCandidateClosedChannelContext(t *testing.T) {
	for _, mode := range []string{"live", "canceled", "deadline-at-eof"} {
		t.Run(mode, func(t *testing.T) {
			base, cancel := context.WithCancel(context.Background())
			defer cancel()
			var ctx context.Context = base
			chunks := make(chan models.StreamChunk)
			close(chunks)
			switch mode {
			case "canceled":
				cancel()
			case "deadline-at-eof":
				ready := make(chan struct{})
				close(ready)
				ctx = &eofDeadlineContext{
					triggeredDeadlineContext: triggeredDeadlineContext{base},
					partial:                  ready,
					closed:                   ready,
					expire:                   cancel,
				}
			}
			text := "partial candidate"
			first := &models.StreamChunk{Choices: []models.StreamChoice{{Delta: models.Delta{Content: &text}}}}
			candidate, err := collectCandidate(ctx, chunks, nil, first)
			api, ok := err.(*models.APIError)
			if !ok || candidate == nil || len(candidate.frames) != 1 {
				t.Fatalf("candidate=%+v error=%v", candidate, err)
			}
			if mode == "live" {
				if api.Status != http.StatusBadGateway || api.Message != "upstream stream ended without a finish reason" {
					t.Fatalf("live EOF changed: %v", err)
				}
			} else if api.Status != http.StatusRequestTimeout || api.Code != "request_timeout" {
				t.Fatalf("canceled EOF misclassified: %v", err)
			}
		})
	}
}

func TestReflexionStreamFailureDoesNotLeakCandidate(t *testing.T) {
	for _, mode := range []string{"overflow", "exhaustion"} {
		t.Run(mode, func(t *testing.T) {
			var calls atomic.Int32
			upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if r.Method != http.MethodPost || r.URL.Path != "/v1/chat/completions" {
					w.WriteHeader(404)
					return
				}
				calls.Add(1)
				text := "rejected private stream"
				if mode == "overflow" {
					text += strings.Repeat("x", 100<<10)
				}
				w.Header().Set("Content-Type", "text/event-stream")
				count := 1
				if mode == "overflow" {
					count = 24
				}
				for i := 0; i < count; i++ {
					b, _ := json.Marshal(models.StreamChunk{ID: "private", Model: "gpt-4o", Choices: []models.StreamChoice{{Delta: models.Delta{Content: &text}}}})
					if _, err := fmt.Fprintf(w, "data: %s\n\n", b); err != nil {
						return
					}
					w.(http.Flusher).Flush()
				}
				finish := "stop"
				b, _ := json.Marshal(models.StreamChunk{ID: "private", Model: "gpt-4o", Choices: []models.StreamChoice{{FinishReason: &finish}}, Usage: &models.Usage{PromptTokens: 1, CompletionTokens: 2, TotalTokens: 3}})
				fmt.Fprintf(w, "data: %s\n\ndata: [DONE]\n\n", b)
			}))
			defer upstream.Close()
			observer := &reflectionObserver{}
			s := createTestServerWithPlugins(t, upstream.URL, reflectionPlugin(&reflectionCheck{name: "output"}, reflectionPolicy(), "block", "post"), observer)
			ctx := context.Background()
			r := httptest.NewRequest("POST", "/v1/chat/completions", strings.NewReader(`{"model":"gpt-4o","messages":[{"role":"user","content":"original"}],"stream":true}`)).WithContext(ctx)
			w := httptest.NewRecorder()
			s.httpServer.Handler.ServeHTTP(w, r)
			wantCalls := int32(1)
			if mode == "exhaustion" {
				wantCalls = 2
			}
			if w.Code < 400 || calls.Load() != wantCalls || strings.Contains(w.Body.String(), "data:") || strings.Contains(w.Body.String(), "private stream") || observer.snapshot.Response != nil {
				t.Fatalf("status=%d calls=%d response=%v body=%s", w.Code, calls.Load(), observer.snapshot.Response, w.Body.String())
			}
			if observer.count != 1 {
				t.Fatalf("observers=%d", observer.count)
			}
		})
	}
}
