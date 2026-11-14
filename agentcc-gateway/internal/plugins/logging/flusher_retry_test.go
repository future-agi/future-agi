package logging

import (
	"context"
	"fmt"
	"net/http"
	"testing"
	"time"
)

func TestLogFlusherOverflowStartsNextBatchWithFreshRetryBudget(t *testing.T) {
	cfg := flushTestConfig()
	cfg.MaxRetries = 2
	cfg.RetryBackoff = 70 * time.Millisecond
	cfg.MaxRetryBackoff = 140 * time.Millisecond
	var f *LogFlusher
	wh := newFakeLogWebhook(t, func(n int, _ *http.Request) int {
		if n == 1 {
			// The send has removed a,b; refill the entire 2*MaxBatch buffer
			// before its failure can put a,b back.
			enqueue(f, "c", "d", "e", "f")
		}
		return http.StatusServiceUnavailable
	})
	f = NewLogFlusher(wh.URL, "", cfg.Interval, cfg.MaxBatch, cfg)
	enqueue(f, "a", "b")
	f.flush()
	if got := f.Dropped()["requeue_overflow"]; got != 2 {
		t.Fatalf("requeue_overflow = %d, want 2", got)
	}
	if f.consecutiveFails != 0 || !f.nextAttempt.IsZero() {
		t.Fatalf("discarded batch left failure count %d and next attempt %v", f.consecutiveFails, f.nextAttempt)
	}

	// No backoff from a,b may delay the first send of c,d.
	f.flush()
	if got := wh.requests(); got != 2 {
		t.Fatalf("new batch was delayed by discarded batch: %d sends, want 2", got)
	}
	for attempt := 0; attempt < cfg.MaxRetries; attempt++ {
		time.Sleep(cfg.MaxRetryBackoff + 20*time.Millisecond)
		f.flush()
	}
	wh.mu.Lock()
	batches := fmt.Sprint(wh.batches)
	wh.mu.Unlock()
	if want := "[[a b] [c d] [c d] [c d]]"; batches != want {
		t.Fatalf("sent %s, want %s", batches, want)
	}
	if got := f.Dropped()["max_retries"]; got != 2 {
		t.Fatalf("max_retries = %d, want 2 (c,d)", got)
	}
}

func TestLogFlusherRetriesAtBackoffWithoutWaitingForTick(t *testing.T) {
	cfg := flushTestConfig()
	cfg.Interval = 2 * time.Second
	cfg.RetryBackoff = 50 * time.Millisecond
	cfg.MaxRetryBackoff = 100 * time.Millisecond
	cfg.MaxRetries = 2
	attempts := make(chan time.Time, 3)
	wh := newFakeLogWebhook(t, func(n int, _ *http.Request) int {
		attempts <- time.Now()
		if n < 3 {
			return http.StatusServiceUnavailable
		}
		return http.StatusOK
	})
	f := NewLogFlusher(wh.URL, "", cfg.Interval, cfg.MaxBatch, cfg)
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan struct{})
	go func() { defer close(done); f.Run(ctx) }()
	t.Cleanup(func() {
		cancel()
		select {
		case <-done:
		case <-time.After(time.Second):
			t.Error("Run did not stop after cancellation")
		}
	})
	enqueue(f, "a", "b")
	var times [3]time.Time
	for i := range times {
		select {
		case times[i] = <-attempts:
		case <-time.After(700 * time.Millisecond):
			t.Fatalf("only %d sends before the next tick; want 3", i)
		}
	}
	for i, minDelay := range []time.Duration{cfg.RetryBackoff, cfg.MaxRetryBackoff} {
		elapsed := times[i+1].Sub(times[i])
		if elapsed < minDelay-15*time.Millisecond || elapsed > 450*time.Millisecond {
			t.Errorf("retry %d waited %s, expected about %s (not a tick)", i+1, elapsed, minDelay)
		}
	}
}
