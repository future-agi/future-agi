package server

import (
	"context"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"testing/synctest"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/routing"
)

func TestClosedStreamOutcomePrefersQueuedErrorAtDeadline(t *testing.T) {
	ctx, cancel := context.WithDeadline(context.Background(), time.Time{})
	defer cancel()
	errs := make(chan error, 1)
	limited := models.ErrTooManyRequests("primary rate limit")
	errs <- limited
	outcome := closedStreamOutcome(ctx, errs)
	if outcome.err != limited || outcome.apiError(ctx) != limited {
		t.Fatalf("lost provider outcome: raw=%v presented=%v", outcome.err, outcome.apiError(ctx))
	}
	// The channel is open and empty. Bound the assertion even if a blocking read regresses.
	result := make(chan error, 1)
	go func() { result <- closedStreamOutcome(ctx, errs).err }()
	select {
	case got := <-result:
		if got != context.DeadlineExceeded {
			t.Fatalf("empty channel: got %v, want deadline", got)
		}
	case <-time.After(time.Second):
		close(errs)
		t.Fatal("blocked on the open, empty error channel")
	}
}

func TestEstablishedStreamKeepsBuffered429AtDeadline(t *testing.T) {
	for _, endpoint := range []string{"chat", "completion"} {
		t.Run(endpoint, func(t *testing.T) {
			h, p := reconciliationHandler(t)
			synctest.Test(t, func(t *testing.T) {
				rc := reconciliationRC(t)
				rc.Request.Stream, rc.IsStream = true, true
				chunks := make(chan models.StreamChunk, 1)
				errs := make(chan error, 1)
				chunks <- models.StreamChunk{ID: "first-chunk", Model: "m"}
				p.stream = func(context.Context) (<-chan models.StreamChunk, <-chan error) { return chunks, errs }
				ctx, cancel := context.WithTimeout(context.Background(), time.Second)
				defer cancel()
				limited := models.ErrTooManyRequests("stream rate limit")
				w := &reconciliationWriter{ResponseRecorder: httptest.NewRecorder(), first: func() {
					errs <- limited
					close(errs)
					close(chunks)
					<-ctx.Done() // First write is committed; resume the reader after expiry.
				}}
				if endpoint == "chat" {
					h.handleStream(ctx, w, rc, p, nil)
				} else {
					h.handleCompletionStream(ctx, w, rc, p)
				}
				body := w.Body.String()
				if len(rc.Errors) != 1 || rc.Errors[0] != limited || limited.Status != http.StatusTooManyRequests ||
					w.Code != http.StatusOK || !strings.Contains(body, `"code":"rate_limit_exceeded"`) || strings.Contains(body, "[DONE]") {
					t.Fatalf("status=%d stream errors=%v body=%s", w.Code, rc.Errors, body)
				}
			})
		})
	}
}

func TestStreamStartupBuffered429DispatchesBackupAfterAttemptDeadline(t *testing.T) {
	h, _ := newStreamingFailoverHandler(t, "https://first.invalid", "https://second.invalid")
	h.failover.Store(routing.NewFailover(config.FailoverConfig{
		Enabled: true, MaxAttempts: 2, PerAttemptTimeout: time.Second,
		OnStatusCodes: []int{http.StatusTooManyRequests}, OnTimeout: false,
	}, h.registry.Router(), nil, nil))
	first := &reconciliationProvider{id: "first", stream: func(context.Context) (<-chan models.StreamChunk, <-chan error) {
		chunks, errs := make(chan models.StreamChunk), make(chan error, 1)
		errs <- models.ErrTooManyRequests("primary rate limit")
		close(errs)
		close(chunks)
		time.Sleep(2 * time.Second) // Virtual time: expire the attempt, not the request.
		return chunks, errs
	}}
	second := &reconciliationProvider{id: "second", stream: func(context.Context) (<-chan models.StreamChunk, <-chan error) {
		chunks := make(chan models.StreamChunk, 1)
		chunks <- models.StreamChunk{ID: "backup-chunk", Model: "gpt-4o"}
		close(chunks)
		return chunks, nil
	}}
	h.registry.RegisterProvider("first", first, nil)
	h.registry.RegisterProvider("second", second, nil)
	synctest.Test(t, func(t *testing.T) {
		rc := reconciliationRC(t)
		rc.Model, rc.Request.Model, rc.Provider = "gpt-4o", "gpt-4o", "first"
		rc.Request.Stream, rc.IsStream = true, true
		w := httptest.NewRecorder()
		h.handleStream(context.Background(), w, rc, first, nil)
		body := w.Body.String()
		if first.calls != 1 || second.calls != 1 || w.Code != http.StatusOK || w.Header().Get("x-agentcc-provider") != "second" ||
			!strings.Contains(body, "backup-chunk") || !strings.Contains(body, "[DONE]") || strings.Contains(body, `"error"`) {
			t.Fatalf("calls=%d/%d status=%d headers=%v body=%s", first.calls, second.calls, w.Code, w.Header(), body)
		}
	})
}
