package server

// The deadline helpers chat_nonstream.go and chat_stream.go call, tested
// directly: withRequestDeadline bounds a whole run of
// attempts (the waits between them too), startAttempt refuses an attempt past
// the deadline without dispatching it, and attemptLimitError answers a
// request the deadline ended.

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/routing"
)

func deadlineRC(t *testing.T, deadlineMs int, started time.Time) *models.RequestContext {
	t.Helper()
	rc := models.AcquireRequestContext()
	t.Cleanup(rc.Release)
	rc.RequestID = "req-agent"
	rc.StartTime = started
	rc.Agent.DeadlineMs = deadlineMs
	rc.Request = &models.ChatCompletionRequest{Model: "gpt-4o"}
	return rc
}

func TestAgentDeadlineHelpers(t *testing.T) {
	t.Run("requestDeadline is request start + deadline-ms, none without one", func(t *testing.T) {
		start := time.Now()
		if d, ok := requestDeadline(deadlineRC(t, 250, start)); !ok || !d.Equal(start.Add(250*time.Millisecond)) {
			t.Fatalf("deadline %v %v, want start + 250 ms", d, ok)
		}
		if _, ok := requestDeadline(deadlineRC(t, 0, start)); ok {
			t.Fatal("a request with no deadline-ms has a deadline")
		}
		if _, ok := requestDeadline(deadlineRC(t, 250, time.Time{})); ok {
			t.Fatal("a request with no start time has a deadline")
		}
	})

	t.Run("withRequestDeadline ends a wait between attempts at the deadline", func(t *testing.T) {
		rc := deadlineRC(t, 80, time.Now())
		ctx, release := withRequestDeadline(context.Background(), rc)
		defer release()
		start := time.Now()
		select { // a retry backoff of 2 s
		case <-time.After(2 * time.Second):
			t.Fatal("the 2 s backoff outlived the 80 ms deadline")
		case <-ctx.Done():
		}
		if elapsed := time.Since(start); elapsed > time.Second || !errors.Is(ctx.Err(), context.DeadlineExceeded) {
			t.Fatalf("the wait ended after %s with %v", elapsed, ctx.Err())
		}
		// Without a deadline the context is the caller's, unchanged.
		plain := deadlineRC(t, 0, time.Now())
		parent := context.Background()
		got, release2 := withRequestDeadline(parent, plain)
		release2()
		if got != parent {
			t.Fatal("withRequestDeadline changed the context of a request with no deadline")
		}
	})

	t.Run("startAttempt refuses an attempt past the deadline and never dispatches it", func(t *testing.T) {
		h := &Handlers{}
		rc := deadlineRC(t, 10, time.Now().Add(-time.Second))
		ctx, finish, refusal := h.startAttempt(context.Background(), rc, models.AttemptRetry, "", "openai", "gpt-4o", models.CredentialBYOK)
		if refusal != models.ReasonDeadline || finish != nil || ctx.Err() != nil {
			t.Fatalf("refusal %q, finish %v, ctx err %v; want %q, nil and the caller's live context", refusal, finish != nil, ctx.Err(), models.ReasonDeadline)
		}
		got := rc.AttemptsSnapshot()
		if len(got) != 1 || got[0].Outcome != models.OutcomeSkipped || got[0].Reason != models.ReasonDeadline ||
			got[0].ErrorClass != models.ErrClassTimeout || got[0].Kind != models.AttemptRetry {
			t.Fatalf("attempts %+v, want one skipped retry for reason deadline", got)
		}
		// beginAttempt hands such an attempt a context that is already done,
		// so a caller that dispatches anyway sends nothing.
		called := false
		up := httptest.NewServer(http.HandlerFunc(func(http.ResponseWriter, *http.Request) { called = true }))
		defer up.Close()
		dead, noop := h.beginAttempt(context.Background(), rc, models.AttemptRetry, "", "openai", "gpt-4o", models.CredentialBYOK)
		noop(nil, nil, nil)
		req, _ := http.NewRequestWithContext(dead, http.MethodPost, up.URL, nil)
		if _, err := http.DefaultClient.Do(req); err == nil || called {
			t.Fatalf("a refused attempt was sent (err %v, upstream called %v)", err, called)
		}
		if reason, _, ok := routing.SkippedHop(context.Cause(dead)); !ok || reason != models.ReasonDeadline {
			t.Fatalf("the refused context's cause is %v, want the hop-skipped sentinel for deadline", context.Cause(dead))
		}
	})

	t.Run("startAttempt's context carries the deadline; a non-stream finish releases it", func(t *testing.T) {
		h := &Handlers{}
		rc := deadlineRC(t, 5000, time.Now())
		ctx, finish, refusal := h.startAttempt(context.Background(), rc, models.AttemptPrimary, "", "openai", "gpt-4o", models.CredentialBYOK)
		if refusal != "" {
			t.Fatalf("refused %q", refusal)
		}
		want, _ := requestDeadline(rc)
		if d, ok := ctx.Deadline(); !ok || !d.Equal(want) {
			t.Fatalf("the attempt's deadline is %v %v, want %v", d, ok, want)
		}
		finish(nil, nil, errors.New("upstream failed"))
		if ctx.Err() == nil {
			t.Fatal("the finish did not release the attempt's deadline context")
		}
	})

	t.Run("startAttempt without a deadline returns the caller's context", func(t *testing.T) {
		h := &Handlers{}
		rc := deadlineRC(t, 0, time.Now())
		parent := context.Background()
		ctx, finish, refusal := h.startAttempt(parent, rc, models.AttemptPrimary, "", "openai", "gpt-4o", models.CredentialBYOK)
		if refusal != "" || ctx != parent {
			t.Fatalf("refusal %q, a new context %v", refusal, ctx != parent)
		}
		finish(nil, nil, nil)
	})
}

func TestAgentAttemptLimitError(t *testing.T) {
	providerErr := models.ErrGatewayTimeout("provider request timed out")
	for _, c := range []struct {
		name       string
		deadlineMs int
		started    time.Time
		refused    bool // a skipped attempt for reason deadline is recorded
		err        error
		status     int
		code       string
		header     string
	}{
		{"no attempt could start before the deadline", 10, time.Now().Add(-time.Second), true,
			routing.SkipHop(nil, models.ReasonDeadline), http.StatusGatewayTimeout, errCodeDeadlineExceeded, "true"},
		{"a later attempt refused: the dispatched attempt's own error", 10, time.Now().Add(-time.Second), true,
			routing.SkipHop(providerErr, models.ReasonDeadline), http.StatusGatewayTimeout, "gateway_timeout", "true"},
		{"a stream's first-chunk wait the deadline ended", 10, time.Now().Add(-time.Second), false,
			fmt.Errorf("first chunk: %w", context.DeadlineExceeded), http.StatusGatewayTimeout, "gateway_timeout", "true"},
		{"a provider error before the deadline is unchanged", 60000, time.Now(), false,
			models.ErrUpstreamProvider(http.StatusBadGateway, "upstream down"), http.StatusBadGateway, "", ""},
		{"no deadline: a bare context error is unchanged", 0, time.Now(), false,
			context.DeadlineExceeded, 0, "", ""},
	} {
		t.Run(c.name, func(t *testing.T) {
			h := &Handlers{}
			rc := deadlineRC(t, c.deadlineMs, c.started)
			if c.refused {
				h.recordRefusedAttempt(rc, models.AttemptRetry, models.ReasonDeadline, "openai", "gpt-4o")
			}
			w := httptest.NewRecorder()
			got := h.attemptLimitError(w, rc, c.err)
			if hdr := w.Header().Get(headerDeadlineExceeded); hdr != c.header {
				t.Errorf("%s = %q, want %q", headerDeadlineExceeded, hdr, c.header)
			}
			var apiErr *models.APIError
			if c.status == 0 {
				if got != c.err {
					t.Fatalf("got %v, want the error unchanged", got)
				}
				return
			}
			if !errors.As(got, &apiErr) || apiErr.Status != c.status || (c.code != "" && apiErr.Code != c.code) {
				t.Fatalf("got %#v, want HTTP %d %q", got, c.status, c.code)
			}
		})
	}
}
