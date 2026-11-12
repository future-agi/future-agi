package logging

import (
	"context"
	"encoding/json"
	"log/slog"
	"net/http"
	"testing"
	"time"
)

func enqueueShutdownEvents(f *LogFlusher, n int) {
	for i := 0; i < n; i++ {
		f.EnqueueEvent("tool_call", map[string]any{"event_id": i, "content": "shutdown-event-content"})
	}
}

// The existing event-loss line must report a count only and precede the
// terminal stopped line. Successful delivery must not emit a loss line.
func assertShutdownEventLoss(t *testing.T, logs *capturingHandler, want int64) {
	t.Helper()
	logs.mu.Lock()
	defer logs.mu.Unlock()
	lossLines, stoppedLines := 0, 0
	for i, rec := range logs.records {
		switch rec.Message {
		case "log flusher: events not delivered before shutdown":
			lossLines++
			count, ok := findAttr(rec, "events")
			if !ok || count.Kind() != slog.KindInt64 || count.Int64() != want {
				t.Errorf("event loss count = %v (present: %v), want %d", count, ok, want)
			}
			if rec.Level != slog.LevelWarn {
				t.Errorf("event loss level = %s, want WARN", rec.Level)
			}
			if rec.NumAttrs() != 1 {
				t.Errorf("event loss line has %d attributes, want only the event count", rec.NumAttrs())
			}
		case "log flusher stopped":
			stoppedLines++
			if i != len(logs.records)-1 || rec.Level != slog.LevelInfo {
				t.Error("stopped must remain the final INFO line")
			}
		}
	}
	wantLines := 0
	if want > 0 {
		wantLines = 1
	}
	if lossLines != wantLines {
		t.Errorf("event loss lines = %d, want %d reporting %d lost events", lossLines, wantLines, want)
	}
	if stoppedLines != 1 {
		t.Errorf("stopped lines = %d, want 1", stoppedLines)
	}
}

// Events must be counted once, not once per retry, whether they went alone or
// rode with a failed record batch. A client error still skips to the next batch.
func TestLogFlusherClose_ReportsLostEvents(t *testing.T) {
	for _, recordCount := range []int{0, 4} {
		name := "events_only"
		if recordCount > 0 {
			name = "records_and_events"
		}
		t.Run(name, func(t *testing.T) {
			for _, tc := range []struct {
				name   string
				status int
			}{
				{"server_error", http.StatusServiceUnavailable},
				{"dropped_connection", dropConnection},
				{"client_error", http.StatusBadRequest},
			} {
				t.Run(tc.name, func(t *testing.T) {
					shortRetryWait(t)
					logs, restore := installCapturingLogger()
					defer restore()
					wh := newFakeLogWebhook(t, func(n int, _ *http.Request) int {
						if tc.status == http.StatusBadRequest && n > 1 {
							return http.StatusOK
						}
						return tc.status
					})
					f := NewLogFlusher(wh.URL, "", time.Hour, 3)
					enqueueN(f, recordCount)
					enqueueShutdownEvents(f, 3)
					ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
					defer cancel()
					f.Close(ctx)

					assertShutdownEventLoss(t, logs, 3)
					wantRequests, wantRecordsLost := finalFlushAttempts, recordCount
					if tc.status == http.StatusBadRequest {
						wantRequests = 1
						if recordCount > 0 {
							wantRequests, wantRecordsLost = 2, 3
						}
					}
					if got := wh.requests(); got != wantRequests {
						t.Errorf("requests = %d, want %d", got, wantRequests)
					}
					if got, level, ok := undeliveredLogged(t, logs); got != int64(wantRecordsLost) || ok != (wantRecordsLost > 0) || (ok && level != slog.LevelError) {
						t.Errorf("undelivered records = %d at %s (logged: %v), want %d at ERROR when nonzero", got, level, ok, wantRecordsLost)
					}
					wh.mu.Lock()
					defer wh.mu.Unlock()
					for i, batch := range wh.batches {
						events := 0
						for _, id := range batch {
							if id == "" { // events have no request_id
								events++
							}
						}
						wantEvents := 3
						if tc.status == http.StatusBadRequest && i > 0 {
							wantEvents = 0 // dropped events must not be sent again
						}
						if events != wantEvents {
							t.Errorf("batch %d has %d events, want %d", i, events, wantEvents)
						}
					}
				})
			}
		})
	}
}

func TestLogFlusherClose_DeliversEventsWithoutLoss(t *testing.T) {
	for _, tc := range []struct {
		name  string
		first int
	}{
		{"immediately", http.StatusOK},
		{"after_server_error", http.StatusServiceUnavailable},
		{"after_dropped_connection", dropConnection},
	} {
		t.Run(tc.name, func(t *testing.T) {
			shortRetryWait(t)
			logs, restore := installCapturingLogger()
			defer restore()
			wh := newFakeLogWebhook(t, func(n int, _ *http.Request) int {
				if n == 1 {
					return tc.first
				}
				return http.StatusOK
			})
			f := NewLogFlusher(wh.URL, "", time.Hour, 100)
			enqueueShutdownEvents(f, 3)
			ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
			defer cancel()
			f.Close(ctx)

			assertShutdownEventLoss(t, logs, 0)
			wantRequests := 1
			if tc.first != http.StatusOK {
				wantRequests = 2
			}
			if got := wh.requests(); got != wantRequests {
				t.Errorf("requests = %d, want %d", got, wantRequests)
			}
			if got := len(wh.delivered()); got != 3 {
				t.Errorf("delivered events = %d, want 3", got)
			}
			if f.hasEvents() {
				t.Error("delivered events remain queued")
			}
		})
	}
}

func TestLogFlusherClose_RequeuesEventsOnEncodeFailure(t *testing.T) {
	logs, restore := installCapturingLogger()
	defer restore()
	wh := newBatchRecorder(t)
	f := NewLogFlusher(wh.URL, "", time.Hour, 100)
	enqueueShutdownEvents(f, 3)
	bad := TraceRecord{RequestID: "bad-record", RequestBodyJSON: json.RawMessage("{")}
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	if err := f.deliverBatch(ctx, []TraceRecord{bad}); err == nil {
		t.Fatal("invalid record did not fail encoding")
	}
	if got := len(f.events); got != 3 {
		t.Fatalf("events queued after encode failure = %d, want 3", got)
	}
	if got := len(wh.all()); got != 0 {
		t.Fatalf("encode failure sent %d requests, want 0", got)
	}

	// Close encounters the invalid record again; the requeued events must
	// still go in their own successful batch, not be counted as lost.
	f.Enqueue(bad)
	f.Close(ctx)
	assertShutdownEventLoss(t, logs, 0)
	if got, _, ok := undeliveredLogged(t, logs); !ok || got != 1 {
		t.Errorf("undelivered records = %d (logged: %v), want 1", got, ok)
	}
	batches := wh.all()
	if len(batches) != 1 || len(batches[0]) != 3 {
		t.Fatalf("want one successful batch of 3 requeued events; got %d batches", len(batches))
	}
	for _, event := range batches[0] {
		if event["kind"] != "tool_call" {
			t.Error("delivered item is not a tool-call event")
		}
	}
}

// Hold the first HTTP response until Close begins. This covers event-only and
// mixed client-error drops, requeue truncation, and events refused while closing.
func TestLogFlusherClose_AccountsForInFlightEvents(t *testing.T) {
	for _, tc := range []struct {
		name            string
		records, status int
		extraEvents     int
		refuseLate      bool
		wantRequests    int
		wantDelivered   int
		wantEventsLost  int64
		wantRecordsLost int64
	}{
		{"events_400", 0, http.StatusBadRequest, 0, false, 1, 0, 3, 0},
		{"mixed_400", 2, http.StatusBadRequest, 0, false, 1, 0, 3, 2},
		{"events_429", 0, http.StatusTooManyRequests, 0, false, 1, 0, 3, 0},
		{"mixed_429", 2, http.StatusTooManyRequests, 0, false, 1, 0, 3, 2},
		{"requeue_truncation", 0, http.StatusServiceUnavailable, 3, false, 2, 3, 3, 0},
		{"refused_while_closing", 1, http.StatusOK, 0, true, 1, 4, 1, 1},
		{"retry_preserved", 2, http.StatusServiceUnavailable, 0, false, 2, 5, 0, 0},
	} {
		t.Run(tc.name, func(t *testing.T) {
			logs, restore := installCapturingLogger()
			defer restore()
			sending, proceed := make(chan struct{}, 1), make(chan struct{})
			wh := newFakeLogWebhook(t, func(n int, _ *http.Request) int {
				if n == 1 {
					sending <- struct{}{}
					<-proceed
					return tc.status
				}
				return http.StatusOK
			})
			letItAnswer := releaseAtCleanup(t, proceed)
			f := NewLogFlusher(wh.URL, "", time.Hour, 3)
			f.consecutiveFails = maxFlushRetries
			enqueueN(f, tc.records)
			enqueueShutdownEvents(f, 3)
			flushed := make(chan struct{})
			go func() { defer close(flushed); f.flush() }()
			waitShutdownSignal(t, sending)
			enqueueShutdownEvents(f, tc.extraEvents)

			ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
			defer cancel()
			closed := make(chan struct{})
			go func() { defer close(closed); f.Close(ctx) }()
			waitUntilClosing(t, f)
			if tc.refuseLate {
				enqueue(f, "late-record")
				enqueueShutdownEvents(f, 1)
			}
			letItAnswer()
			waitShutdownSignal(t, closed)
			waitShutdownSignal(t, flushed)

			assertShutdownEventLoss(t, logs, tc.wantEventsLost)
			if got := wh.requests(); got != tc.wantRequests {
				t.Errorf("requests = %d, want %d", got, tc.wantRequests)
			}
			if got := len(wh.delivered()); got != tc.wantDelivered {
				t.Errorf("delivered items = %d, want %d", got, tc.wantDelivered)
			}
			if got, _, ok := undeliveredLogged(t, logs); got != tc.wantRecordsLost || ok != (tc.wantRecordsLost > 0) {
				t.Errorf("undelivered records = %d (logged: %v), want %d", got, ok, tc.wantRecordsLost)
			}
			if f.hasEvents() {
				t.Error("events remain queued after Close")
			}
		})
	}
}

func TestLogFlusherClose_ReportsEventsWhenCanceled(t *testing.T) {
	for _, beforeSend := range []bool{true, false} {
		name := "during_send"
		if beforeSend {
			name = "before_send"
		}
		t.Run(name, func(t *testing.T) {
			logs, restore := installCapturingLogger()
			defer restore()
			ctx, cancel := context.WithCancel(context.Background())
			defer cancel()
			wh := newFakeLogWebhook(t, func(int, *http.Request) int {
				cancel()
				return http.StatusServiceUnavailable
			})
			f := NewLogFlusher(wh.URL, "", time.Hour, 100)
			enqueueShutdownEvents(f, 2)
			if beforeSend {
				cancel()
			}
			f.Close(ctx)
			assertShutdownEventLoss(t, logs, 2)
			wantRequests := 1
			if beforeSend {
				wantRequests = 0
			}
			if got := wh.requests(); got != wantRequests {
				t.Errorf("requests = %d, want %d", got, wantRequests)
			}
		})
	}
}
