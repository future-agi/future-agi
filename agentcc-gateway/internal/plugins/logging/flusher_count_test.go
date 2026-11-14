package logging

import (
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"net/http"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/metrics"
)

func flushTestConfig() config.LogFlushConfig {
	return config.LogFlushConfig{Interval: time.Hour, Timeout: 30 * time.Millisecond, MaxBatch: 2, MaxRetries: 1, RetryBackoff: 60 * time.Millisecond, MaxRetryBackoff: 60 * time.Millisecond}
}

func TestLogFlusherDropReasons(t *testing.T) {
	for _, reason := range []string{"max_retries", "client_error", "encode_error", "buffer_full", "requeue_overflow", "closed", "shutdown"} {
		t.Run(reason, func(t *testing.T) {
			wh := newFakeLogWebhook(t, func(int, *http.Request) int {
				if reason == "max_retries" {
					return http.StatusServiceUnavailable
				}
				return http.StatusBadRequest
			})
			cfg := flushTestConfig()
			f := NewLogFlusher(wh.URL, "", cfg.Interval, cfg.MaxBatch, cfg)
			want := int64(2)
			switch reason {
			case "max_retries":
				enqueue(f, "a", "b")
				f.flush()
				time.Sleep(cfg.RetryBackoff + 20*time.Millisecond)
				f.flush()
			case "client_error":
				enqueue(f, "a", "b")
				f.flush()
			case "encode_error":
				f.Enqueue(TraceRecord{RequestBodyJSON: json.RawMessage("{")})
				want = 1
				f.flush()
			case "buffer_full":
				enqueue(f, "a", "b", "c", "d", "e", "f")
			case "requeue_overflow":
				enqueue(f, "a", "b", "c", "d")
				f.reEnqueue([]TraceRecord{{RequestID: "e"}, {RequestID: "f"}})
			case "closed":
				f.Close(context.Background())
				enqueue(f, "a", "b")
			case "shutdown":
				enqueue(f, "a", "b")
				f.Close(context.Background())
			}
			if got := f.Dropped()[reason]; got != want {
				t.Fatalf("%s = %d, want %d", reason, got, want)
			}
		})
	}
}

func TestLogFlusherBackoffAndBatchLimit(t *testing.T) {
	wh := newFakeLogWebhook(t, func(n int, _ *http.Request) int {
		if n == 1 {
			return http.StatusServiceUnavailable
		}
		return http.StatusOK
	})
	cfg := flushTestConfig()
	cfg.RetryBackoff = 200 * time.Millisecond
	cfg.MaxRetryBackoff = 200 * time.Millisecond
	f := NewLogFlusher(wh.URL, "", cfg.Interval, cfg.MaxBatch, cfg)
	enqueue(f, "a", "b", "c", "d")
	f.flush()
	f.flush()
	if got := wh.requests(); got != 1 {
		t.Fatalf("sent during backoff: %d", got)
	}
	wh.mu.Lock()
	first := len(wh.batches[0])
	wh.mu.Unlock()
	if first > cfg.MaxBatch {
		t.Fatalf("batch size %d > %d", first, cfg.MaxBatch)
	}
	time.Sleep(cfg.RetryBackoff + 20*time.Millisecond)
	f.flush()
	if got := wh.requests(); got != 2 {
		t.Fatalf("after backoff requests = %d", got)
	}
}

func TestLogFlusherTimeoutAndMetric(t *testing.T) {
	wh := newFakeLogWebhook(t, func(_ int, r *http.Request) int { <-r.Context().Done(); return http.StatusOK })
	cfg := flushTestConfig()
	f := NewLogFlusher(wh.URL, "", cfg.Interval, cfg.MaxBatch, cfg)
	reg := metrics.NewRegistry()
	f.SetMetrics(reg)
	enqueue(f, "a")
	started := time.Now()
	f.flush()
	if elapsed := time.Since(started); elapsed > time.Second {
		t.Fatalf("timeout ignored: %v", elapsed)
	}
	enqueue(f, "b", "c", "d") // failed record plus three fills the 2x buffer
	f.reEnqueue([]TraceRecord{{RequestID: "e"}, {RequestID: "f"}})
	if got := reg.Render(); !strings.Contains(got, `agentcc_request_log_records_dropped_total{reason="requeue_overflow"} 2`) {
		t.Fatalf("counter not rendered: %s", got)
	}
	f.SetMetrics(nil)
	f.reEnqueue([]TraceRecord{{RequestID: "d"}, {RequestID: "e"}})
}

func TestLogFlusherPartialRequeueAndLateMetrics(t *testing.T) {
	cfg := flushTestConfig()
	f := NewLogFlusher("http://127.0.0.1:1", "", cfg.Interval, cfg.MaxBatch, cfg)
	enqueue(f, "a", "b", "c")
	f.reEnqueue([]TraceRecord{{RequestID: "d"}, {RequestID: "e"}, {RequestID: "f"}})
	if got := f.Dropped()["requeue_overflow"]; got != 2 {
		t.Fatalf("partial overflow = %d, want 2", got)
	}
	reg := metrics.NewRegistry()
	f.SetMetrics(reg)
	f.SetMetrics(reg) // attaching the same registry again must not double counts
	if got := reg.Render(); !strings.Contains(got, `agentcc_request_log_records_dropped_total{reason="requeue_overflow"} 2`) {
		t.Fatalf("late attachment: %s", got)
	}
}

// A full buffer refuses records one request at a time. Every one is counted,
// but it is logged at most once per overflowWarnInterval, not once a record.
func TestLogFlusherBufferFullLogsAtMostOncePerInterval(t *testing.T) {
	var logs strings.Builder
	previous := slog.Default()
	slog.SetDefault(slog.New(slog.NewTextHandler(&logs, nil)))
	t.Cleanup(func() { slog.SetDefault(previous) })

	cfg := flushTestConfig()
	f := NewLogFlusher("http://127.0.0.1:1/unused", "", cfg.Interval, cfg.MaxBatch, cfg)
	enqueue(f, "a", "b", "c", "d") // the buffer holds 2*MaxBatch
	for i := 0; i < 50; i++ {
		enqueue(f, "over")
	}

	if got := f.Dropped()["buffer_full"]; got != 50 {
		t.Fatalf("buffer_full = %d, want 50", got)
	}
	if got := strings.Count(logs.String(), "buffer full"); got != 1 {
		t.Fatalf("buffer-full lines = %d, want 1:\n%s", got, logs.String())
	}
	if !strings.Contains(logs.String(), "dropped_total=1") {
		t.Fatalf("the warning does not carry the total:\n%s", logs.String())
	}
	if strings.Contains(logs.String(), "records dropped") {
		t.Fatalf("a buffer-full drop was logged one record at a time:\n%s", logs.String())
	}
}

// A failed batch goes back to the front of the buffer, so the next send
// retries the same records, in order, and max_retries counts that batch's own
// failures: a batch behind it is never dropped for failures it did not have.
func TestLogFlusherRetriesTheFailedBatchFirst(t *testing.T) {
	wh := newFakeLogWebhook(t, func(int, *http.Request) int { return http.StatusServiceUnavailable })
	cfg := flushTestConfig() // MaxBatch 2, MaxRetries 1
	f := NewLogFlusher(wh.URL, "", cfg.Interval, cfg.MaxBatch, cfg)
	enqueue(f, "a", "b", "c", "d")

	f.flush()
	time.Sleep(cfg.RetryBackoff + 20*time.Millisecond)
	f.flush()

	wh.mu.Lock()
	batches := fmt.Sprint(wh.batches)
	wh.mu.Unlock()
	if batches != "[[a b] [a b]]" {
		t.Fatalf("sent %s, want the failed batch retried first: [[a b] [a b]]", batches)
	}
	if got := f.Dropped()["max_retries"]; got != 2 {
		t.Fatalf("max_retries = %d, want 2 (a, b)", got)
	}
	f.mu.Lock()
	left := len(f.buffer)
	first := f.buffer[0].RequestID
	f.mu.Unlock()
	if left != 2 || first != "c" {
		t.Fatalf("buffer holds %d records starting %q, want c, d untouched", left, first)
	}
}
