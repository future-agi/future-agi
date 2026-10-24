package server

// The two stream helpers chat_stream.go calls, tested directly. A stream's
// upstream call runs on streamAttemptContext, which the request's deadline
// ends, and a stream whose call context ended at a deadline ends with
// streamCutError's event instead of [DONE] (the OpenAI adapter closes a cut
// stream without an error).

import (
	"context"
	"net/http"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

func TestAgentDeadlineStreamHelpers(t *testing.T) {
	t.Run("streamAttemptContext without a deadline is the request's, cancellable", func(t *testing.T) {
		parent, cancelParent := context.WithCancel(context.Background())
		defer cancelParent()
		ctx, cancel := streamAttemptContext(parent, deadlineRC(t, 0, time.Now()))
		if _, ok := ctx.Deadline(); ok {
			t.Fatal("a request with no deadline-ms got a deadline")
		}
		cancel()
		if ctx.Err() == nil {
			t.Fatal("its cancel does not end it: the stream path cancels a failed or replaced attempt")
		}
		if parent.Err() != nil {
			t.Fatal("its cancel ended the request's context")
		}
	})

	t.Run("streamAttemptContext ends at the request's deadline", func(t *testing.T) {
		start := time.Now()
		rc := deadlineRC(t, 80, start)
		ctx, cancel := streamAttemptContext(context.Background(), rc)
		defer cancel()
		if d, ok := ctx.Deadline(); !ok || !d.Equal(start.Add(80*time.Millisecond)) {
			t.Fatalf("deadline %v %v, want request start + 80 ms", d, ok)
		}
		select {
		case <-ctx.Done():
		case <-time.After(2 * time.Second):
			t.Fatal("a stream's call context outlived the 80 ms deadline")
		}
		if ctx.Err() != context.DeadlineExceeded {
			t.Fatalf("ended with %v, want the deadline", ctx.Err())
		}
	})

	t.Run("streamAttemptContext still ends with the request", func(t *testing.T) {
		parent, cancelParent := context.WithCancel(context.Background())
		ctx, cancel := streamAttemptContext(parent, deadlineRC(t, 60000, time.Now()))
		defer cancel()
		cancelParent()
		if ctx.Err() != context.Canceled {
			t.Fatalf("the client left but the stream's call context is %v", ctx.Err())
		}
	})

	t.Run("streamCutError: a call context ended at the request's deadline", func(t *testing.T) {
		rc := deadlineRC(t, 1, time.Now().Add(-time.Second))
		ctx, cancel := streamAttemptContext(context.Background(), rc)
		defer cancel()
		<-ctx.Done()
		apiErr := streamCutError(ctx, rc)
		if apiErr == nil || apiErr.Status != http.StatusGatewayTimeout || apiErr.Code != models.ErrTypeGatewayTimeout {
			t.Fatalf("got %+v, want 504 gateway_timeout", apiErr)
		}
		if m, _ := apiErr.Metadata.(map[string]any); m["deadline_ms"] != 1 {
			t.Fatalf("metadata %v, want deadline_ms 1", apiErr.Metadata)
		}
	})

	t.Run("streamCutError: another deadline (the request's timeout) is a timeout too", func(t *testing.T) {
		ctx, cancel := context.WithTimeout(context.Background(), time.Nanosecond)
		defer cancel()
		<-ctx.Done()
		apiErr := streamCutError(ctx, deadlineRC(t, 0, time.Now()))
		if apiErr == nil || apiErr.Status != http.StatusGatewayTimeout || apiErr.Code != models.ErrTypeGatewayTimeout {
			t.Fatalf("got %+v, want 504 gateway_timeout", apiErr)
		}
	})

	t.Run("streamCutError: nil for a stream that ended otherwise", func(t *testing.T) {
		rc := deadlineRC(t, 60000, time.Now())
		live, cancelLive := streamAttemptContext(context.Background(), rc)
		defer cancelLive()
		if apiErr := streamCutError(live, rc); apiErr != nil {
			t.Fatalf("a stream that completed: %+v, want nil (its [DONE] stands)", apiErr)
		}
		left, cancelLeft := streamAttemptContext(context.Background(), rc)
		cancelLeft()
		if apiErr := streamCutError(left, rc); apiErr != nil {
			t.Fatalf("a stream the client left: %+v, want nil", apiErr)
		}
	})
}
