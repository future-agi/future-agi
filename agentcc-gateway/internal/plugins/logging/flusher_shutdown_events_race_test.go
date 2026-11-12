package logging

import (
	"context"
	"encoding/json"
	"log/slog"
	"net/http"
	"testing"
	"time"
)

func waitShutdownSignal(t *testing.T, ch <-chan struct{}) {
	t.Helper()
	select {
	case <-ch:
	case <-time.After(5 * time.Second):
		t.Fatal("timed out waiting for shutdown test signal")
	}
}

// A flush may enter a terminal branch before Close starts but drop its batch
// after Close starts. Pause at the existing log line to exercise that boundary,
// especially the gap between the retry-limit closing check and the actual drop.
func TestLogFlusherClose_CountsEventsInTerminalDrop(t *testing.T) {
	for _, tc := range []struct {
		name      string
		status    int
		message   string
		records   int
		badRecord bool
		badEvent  bool
	}{
		{"client_events", http.StatusBadRequest, "log flusher: webhook returned client error, dropping records", 0, false, false},
		{"client_mixed", http.StatusBadRequest, "log flusher: webhook returned client error, dropping records", 1, false, false},
		{"retry_limit_events", http.StatusServiceUnavailable, "log flusher: max retries exceeded, dropping records", 0, false, false},
		{"retry_limit_mixed", http.StatusServiceUnavailable, "log flusher: max retries exceeded, dropping records", 1, false, false},
		{"marshal_mixed", http.StatusOK, "log flusher: marshal failed", 1, true, false},
		{"marshal_events", http.StatusOK, "log flusher: marshal failed", 0, false, true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			for _, duringClose := range []bool{false, true} {
				name := "before_close"
				if duringClose {
					name = "during_close"
				}
				t.Run(name, func(t *testing.T) {
					logs := &capturingHandler{}
					dropping, proceed := make(chan struct{}, 1), make(chan struct{})
					previous := slog.Default()
					slog.SetDefault(slog.New(blockingLogHandler{
						msg: tc.message, started: dropping, release: proceed, next: logs,
					}))
					t.Cleanup(func() { slog.SetDefault(previous) })
					wh := newFakeLogWebhook(t, func(int, *http.Request) int { return tc.status })
					letItDrop := releaseAtCleanup(t, proceed)
					f := NewLogFlusher(wh.URL, "", time.Hour, 100)
					f.consecutiveFails = maxFlushRetries
					if tc.badRecord {
						f.Enqueue(TraceRecord{RequestID: "bad-record", RequestBodyJSON: json.RawMessage("{")})
					} else {
						enqueueN(f, tc.records)
					}
					enqueueShutdownEvents(f, 2)
					if tc.badEvent {
						// EnqueueEvent always encodes valid JSON. Inject one malformed
						// queued event to cover an event-only marshal failure.
						f.events[0] = json.RawMessage("{")
					}
					flushed := make(chan struct{})
					go func() { defer close(flushed); f.flush() }()
					waitShutdownSignal(t, dropping)
					ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
					defer cancel()
					var wantEvents, wantRecords int64
					if duringClose {
						closed := make(chan struct{})
						go func() { defer close(closed); f.Close(ctx) }()
						waitUntilClosing(t, f)
						letItDrop()
						waitShutdownSignal(t, closed)
						wantEvents, wantRecords = 2, int64(tc.records)
					} else {
						letItDrop()
						waitShutdownSignal(t, flushed)
						f.Close(ctx)
					}
					waitShutdownSignal(t, flushed)
					assertShutdownEventLoss(t, logs, wantEvents)
					if got, _, ok := undeliveredLogged(t, logs); got != wantRecords || ok != (wantRecords > 0) {
						t.Errorf("undelivered records = %d (logged: %v), want %d", got, ok, wantRecords)
					}
					wantRequests := 1
					if tc.badRecord || tc.badEvent {
						wantRequests = 0
					}
					if got := wh.requests(); got != wantRequests {
						t.Errorf("requests = %d, want %d; terminal drop must not retry", got, wantRequests)
					}
					if f.hasEvents() {
						t.Error("terminal drop requeued events")
					}
				})
			}
		})
	}
}

// Queue pressure before shutdown keeps the existing bounded-buffer behavior,
// without retroactively adding those drops to Close's loss count.
func TestLogFlusherClose_DoesNotCountEarlierEventOverflow(t *testing.T) {
	logs, restore := installCapturingLogger()
	defer restore()
	wh := newBatchRecorder(t)
	f := NewLogFlusher(wh.URL, "", time.Hour, 2)
	enqueueShutdownEvents(f, 2)
	inFlight := f.takeEvents()
	enqueueShutdownEvents(f, 3) // one refused because the open queue is full
	f.requeueEvents(inFlight)   // two trimmed while still open
	if len(f.events) != 2 {
		t.Fatalf("queued events = %d, want maxBuffer=2", len(f.events))
	}
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	f.Close(ctx)
	assertShutdownEventLoss(t, logs, 0)
	if got := wh.all(); len(got) != 1 || len(got[0]) != 2 {
		t.Error("Close did not deliver the two retained events in one batch")
	}
}
