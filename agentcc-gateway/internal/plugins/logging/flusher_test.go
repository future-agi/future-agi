package logging

import (
	"context"
	"encoding/json"
	"errors"
	"log/slog"
	"net"
	"net/http"
	"net/http/httptest"
	"runtime"
	"slices"
	"strconv"
	"sync"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// dropConnection makes fakeLogWebhook close the connection without answering,
// as a backend that is restarting does.
const dropConnection = -1

// fakeLogWebhook stands in for the backend's request-log webhook. It records
// the request IDs of every batch it is sent and answers with answer(n, r),
// where n counts requests from 1.
type fakeLogWebhook struct {
	*httptest.Server

	mu       sync.Mutex
	batches  [][]string
	statuses []int
}

func newFakeLogWebhook(t *testing.T, answer func(n int, r *http.Request) int) *fakeLogWebhook {
	t.Helper()
	wh := &fakeLogWebhook{}
	wh.Server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var payload logFlushPayload
		_ = json.NewDecoder(r.Body).Decode(&payload)
		ids := make([]string, len(payload.Logs))
		for i, entry := range payload.Logs {
			ids[i] = entry.RequestID
		}
		wh.mu.Lock()
		wh.batches = append(wh.batches, ids)
		n := len(wh.batches)
		wh.mu.Unlock()

		status := answer(n, r)
		wh.mu.Lock()
		wh.statuses = append(wh.statuses, status)
		wh.mu.Unlock()
		if status == dropConnection {
			if conn, _, err := w.(http.Hijacker).Hijack(); err == nil {
				conn.Close()
			}
			return
		}
		w.WriteHeader(status)
	}))
	t.Cleanup(wh.Close)
	return wh
}

// releaseAtCleanup returns a func that closes ch once, and also calls it when
// the test ends. Called after newFakeLogWebhook, it runs before the webhook
// closes, so an answer blocked on ch lets the webhook close.
func releaseAtCleanup(t *testing.T, ch chan struct{}) func() {
	t.Helper()
	var once sync.Once
	release := func() { once.Do(func() { close(ch) }) }
	t.Cleanup(release)
	return release
}

// requests reports how many requests the webhook has received.
func (wh *fakeLogWebhook) requests() int {
	wh.mu.Lock()
	defer wh.mu.Unlock()
	return len(wh.batches)
}

// delivered returns the request IDs of the batches it answered with a 2xx.
func (wh *fakeLogWebhook) delivered() []string {
	wh.mu.Lock()
	defer wh.mu.Unlock()
	var ids []string
	for i, status := range wh.statuses {
		if status >= 200 && status < 300 {
			ids = append(ids, wh.batches[i]...)
		}
	}
	return ids
}

func enqueue(f *LogFlusher, requestIDs ...string) {
	for _, id := range requestIDs {
		f.Enqueue(TraceRecord{RequestID: id, Timestamp: time.Now(), Model: "gpt-4", Provider: "openai"})
	}
}

// enqueueN enqueues n records, numbered from 1.
func enqueueN(f *LogFlusher, n int) {
	for i := 1; i <= n; i++ {
		enqueue(f, "req-"+strconv.Itoa(i))
	}
}

// undeliveredLogged returns the "undelivered" count of the record that says
// request logs were not delivered, its level, and whether there is one.
func undeliveredLogged(t *testing.T, h *capturingHandler) (int64, slog.Level, bool) {
	t.Helper()
	h.mu.Lock()
	defer h.mu.Unlock()
	for _, rec := range h.records {
		if v, ok := findAttr(rec, "undelivered"); ok {
			return v.Int64(), rec.Level, true
		}
	}
	return 0, 0, false
}

// shortRetryWait makes Close's retries 10ms apart for the test.
func shortRetryWait(t *testing.T) {
	t.Helper()
	prev := finalFlushRetryWait
	finalFlushRetryWait = 10 * time.Millisecond
	t.Cleanup(func() { finalFlushRetryWait = prev })
}

// waitUntilClosing waits until Close has begun on f.
func waitUntilClosing(t *testing.T, f *LogFlusher) {
	t.Helper()
	deadline := time.Now().Add(5 * time.Second)
	for !f.closing() {
		if time.Now().After(deadline) {
			t.Fatal("Close did not begin")
		}
		time.Sleep(time.Millisecond)
	}
}

// While a send is slow, the records that arrive wait for one pending flush
// instead of each starting one, and the buffer stops at twice maxBuffer: the
// records past that are refused, with a single warning.
func TestLogFlusher_StaysBoundedWhileASendIsSlow(t *testing.T) {
	logs, restore := installCapturingLogger()
	defer restore()
	sending := make(chan struct{}, 1)
	released := make(chan struct{})
	wh := newFakeLogWebhook(t, func(_ int, r *http.Request) int {
		select {
		case sending <- struct{}{}:
		default:
		}
		select {
		case <-r.Context().Done():
		case <-released:
		}
		return http.StatusOK
	})
	release := releaseAtCleanup(t, released)
	const maxBuffer = 100
	f := NewLogFlusher(wh.URL, "secret", time.Hour, maxBuffer)
	ctx, cancel := context.WithCancel(context.Background())
	stopped := make(chan struct{})
	go func() {
		defer close(stopped)
		f.Run(ctx)
	}()
	defer func() {
		cancel()
		release()
		<-stopped
	}()
	enqueueN(f, maxBuffer)
	<-sending

	goroutines := runtime.NumGoroutine()
	enqueueN(f, 10*maxBuffer)

	if n := runtime.NumGoroutine() - goroutines; n > 5 {
		t.Errorf("%d goroutines started while %d records were enqueued during a slow send, want none", n, 10*maxBuffer)
	}
	f.mu.Lock()
	buffered := len(f.buffer)
	f.mu.Unlock()
	if buffered != 2*maxBuffer {
		t.Errorf("buffered %d records, want %d", buffered, 2*maxBuffer)
	}
	var warnings int
	logs.mu.Lock()
	for _, rec := range logs.records {
		if rec.Level == slog.LevelWarn && rec.Message == "log flusher: buffer full, dropping new records" {
			warnings++
		}
	}
	logs.mu.Unlock()
	if warnings != 1 {
		t.Errorf("logged %d buffer-full warnings, want 1", warnings)
	}
}

// A flush keeps a batch for the next flush after a failed send or a server
// error, until the webhook has failed maxFlushRetries times in a row, and
// drops a batch that met a client error at once.
func TestLogFlusherFlush_RetriesThenDrops(t *testing.T) {
	for _, tc := range []struct {
		name      string
		status    int
		wantSends int
	}{
		{"after a dropped connection", dropConnection, maxFlushRetries + 1},
		{"after a server error", http.StatusServiceUnavailable, maxFlushRetries + 1},
		{"after a client error", http.StatusBadRequest, 1},
	} {
		t.Run(tc.name, func(t *testing.T) {
			_, restore := installCapturingLogger()
			defer restore()
			wh := newFakeLogWebhook(t, func(int, *http.Request) int { return tc.status })
			f := NewLogFlusher(wh.URL, "secret", time.Hour, 100)
			enqueue(f, "req-1", "req-2")
			buffered := func() int {
				f.mu.Lock()
				defer f.mu.Unlock()
				return len(f.buffer)
			}

			for sends := 1; sends <= 2*maxFlushRetries; sends++ {
				f.flush()
				if buffered() == 0 {
					if sends != tc.wantSends {
						t.Errorf("dropped the batch after %d sends, want %d", sends, tc.wantSends)
					}
					if f.consecutiveFails != 0 {
						t.Errorf("consecutive failures = %d after the drop, want 0", f.consecutiveFails)
					}
					return
				}
			}
			t.Errorf("still holds the batch after %d sends, want it dropped after %d", 2*maxFlushRetries, tc.wantSends)
		})
	}
}

// A backend that is restarting when the gateway stops refuses the first send;
// the records buffered then are delivered on the retry, once each.
func TestPluginClose_DeliversBufferedLogsWhenTheWebhookComesBack(t *testing.T) {
	for _, tc := range []struct {
		name  string
		first int
	}{
		{"after a server error", http.StatusServiceUnavailable},
		{"after a dropped connection", dropConnection},
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
			f := NewLogFlusher(wh.URL, "secret", time.Hour, 100)
			ctx, cancel := context.WithCancel(context.Background())
			defer cancel()
			go f.Run(ctx)
			p := New(enabledCfg(), nil)
			p.SetFlusher(f)
			enqueue(f, "req-1", "req-2", "req-3")

			p.Close()

			if got, want := wh.delivered(), []string{"req-1", "req-2", "req-3"}; !slices.Equal(got, want) {
				t.Errorf("webhook was delivered %q on shutdown, want %q", got, want)
			}
			if n := wh.requests(); n != 2 {
				t.Errorf("webhook got %d requests, want 2: the failed send and its retry", n)
			}
			if n, _, ok := undeliveredLogged(t, logs); ok {
				t.Errorf("logged %d undelivered records, want none", n)
			}
		})
	}
}

// A backend that stays down: the shutdown still ends, after a few tries, and
// says how many request logs were lost.
func TestPluginClose_LogsHowManyLogsItCouldNotDeliver(t *testing.T) {
	shortRetryWait(t)
	logs, restore := installCapturingLogger()
	defer restore()
	wh := newFakeLogWebhook(t, func(int, *http.Request) int { return http.StatusServiceUnavailable })
	f := NewLogFlusher(wh.URL, "secret", time.Hour, 100)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	go f.Run(ctx)
	p := New(enabledCfg(), nil)
	p.SetFlusher(f)
	enqueue(f, "req-1", "req-2", "req-3", "req-4")

	start := time.Now()
	p.Close()
	if elapsed := time.Since(start); elapsed > shutdownFlushTimeout+time.Second {
		t.Errorf("Close took %s against a webhook that never answers 2xx, want it bounded to about %s", elapsed, shutdownFlushTimeout)
	}

	if n := wh.requests(); n < 2 {
		t.Errorf("webhook got %d requests on shutdown, want the send retried", n)
	}
	if got := wh.delivered(); len(got) != 0 {
		t.Errorf("webhook was delivered %q, want nothing", got)
	}
	if n, level, ok := undeliveredLogged(t, logs); !ok || n != 4 || level != slog.LevelError {
		t.Errorf("logged undelivered = %d at %s (logged: %v), want 4 at ERROR", n, level, ok)
	}
}

// A client error will not change on a retry, so the last flush does not retry it.
func TestLogFlusherClose_DoesNotRetryAClientError(t *testing.T) {
	logs, restore := installCapturingLogger()
	defer restore()
	wh := newFakeLogWebhook(t, func(int, *http.Request) int { return http.StatusBadRequest })
	f := NewLogFlusher(wh.URL, "secret", time.Hour, 100)
	enqueue(f, "req-1", "req-2")

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	f.Close(ctx)

	if n := wh.requests(); n != 1 {
		t.Errorf("webhook got %d requests, want 1", n)
	}
	if n, _, ok := undeliveredLogged(t, logs); !ok || n != 2 {
		t.Errorf("logged undelivered = %d (logged: %v), want 2", n, ok)
	}
}

// A webhook that never answers cannot hold the shutdown past Close's deadline,
// whether it hangs on the last flush or on a flush that was already sending.
// A send cut off at the deadline is not sent again.
func TestLogFlusherClose_IsBoundedWhenTheWebhookHangs(t *testing.T) {
	for _, tc := range []struct {
		name          string
		flushSendsNow bool
	}{
		{"on the last flush", false},
		{"on a flush already sending", true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			logs, restore := installCapturingLogger()
			defer restore()
			sending := make(chan struct{}, 10)
			released := make(chan struct{})
			wh := newFakeLogWebhook(t, func(_ int, r *http.Request) int {
				sending <- struct{}{}
				select {
				case <-r.Context().Done():
				case <-released:
				}
				return http.StatusOK
			})
			releaseAtCleanup(t, released)
			f := NewLogFlusher(wh.URL, "secret", time.Hour, 100)
			enqueue(f, "req-1", "req-2")
			if tc.flushSendsNow {
				go f.flush()
				<-sending
			}

			ctx, cancel := context.WithTimeout(context.Background(), 200*time.Millisecond)
			defer cancel()
			start := time.Now()
			f.Close(ctx)
			if elapsed := time.Since(start); elapsed > 2*time.Second {
				t.Fatalf("Close took %s with a 200ms deadline", elapsed)
			}

			if n := wh.requests(); n != 1 {
				t.Errorf("webhook got %d requests, want 1", n)
			}
			if n, _, ok := undeliveredLogged(t, logs); !ok || n != 2 {
				t.Errorf("logged undelivered = %d (logged: %v), want 2", n, ok)
			}
		})
	}
}

// After a send cut off at Close's deadline, the last flush gives up on the
// backlog without encoding any of it.
func TestLogFlusherDeliver_DoesNotEncodeAfterTheDeadline(t *testing.T) {
	f := NewLogFlusher("http://127.0.0.1:1", "secret", time.Hour, 100)
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	// Encoding this record fails, so any other error means deliver encoded it.
	records := []TraceRecord{{RequestID: "req-1", RequestBodyJSON: json.RawMessage("{")}}
	if n, err := f.deliver(ctx, records); n != 1 || !errors.Is(err, context.Canceled) {
		t.Errorf("deliver after the deadline returned %d, %v, want 1, %v before encoding", n, err, context.Canceled)
	}
}

// Close sends a backlog larger than maxBuffer in batches of maxBuffer. A batch
// whose sends keep failing stops it: that batch and the ones after it are
// undelivered, not the whole backlog. A batch the webhook refuses with a
// client error is undelivered, but the batches after it are still sent.
func TestLogFlusherClose_SendsTheBacklogInBatches(t *testing.T) {
	for _, tc := range []struct {
		name            string
		answerFirst     int
		answerRest      int
		wantDelivered   []string
		wantRequests    int
		wantUndelivered int64
	}{
		{"when the second batch fails", http.StatusOK, http.StatusServiceUnavailable, []string{"req-1", "req-2"}, 1 + finalFlushAttempts, 2},
		{"when the first batch fails", http.StatusServiceUnavailable, http.StatusServiceUnavailable, nil, finalFlushAttempts, 4},
		{"when the first batch is refused", http.StatusBadRequest, http.StatusOK, []string{"req-3", "req-4"}, 2, 2},
	} {
		t.Run(tc.name, func(t *testing.T) {
			shortRetryWait(t)
			logs, restore := installCapturingLogger()
			defer restore()
			wh := newFakeLogWebhook(t, func(n int, _ *http.Request) int {
				if n == 1 {
					return tc.answerFirst
				}
				return tc.answerRest
			})
			const maxBuffer = 2
			f := NewLogFlusher(wh.URL, "secret", time.Hour, maxBuffer)
			enqueueN(f, 2*maxBuffer)

			ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
			defer cancel()
			f.Close(ctx)

			wh.mu.Lock()
			for _, batch := range wh.batches {
				if len(batch) > maxBuffer {
					t.Errorf("webhook was sent a batch of %d records, want at most %d", len(batch), maxBuffer)
				}
			}
			wh.mu.Unlock()
			if got := wh.delivered(); !slices.Equal(got, tc.wantDelivered) {
				t.Errorf("webhook was delivered %q, want %q", got, tc.wantDelivered)
			}
			if n := wh.requests(); n != tc.wantRequests {
				t.Errorf("webhook got %d requests, want %d", n, tc.wantRequests)
			}
			if n, _, _ := undeliveredLogged(t, logs); n != tc.wantUndelivered {
				t.Errorf("logged undelivered = %d, want %d", n, tc.wantUndelivered)
			}
			var delivered int64 // the same line says how many were delivered
			logs.mu.Lock()
			for _, rec := range logs.records {
				if v, ok := findAttr(rec, "delivered"); ok {
					delivered = v.Int64()
				}
			}
			logs.mu.Unlock()
			if want := int64(len(tc.wantDelivered)); delivered != want {
				t.Errorf("logged delivered = %d, want %d", delivered, want)
			}
		})
	}
}

// Close lets a flush that is sending finish rather than sending its batch
// again, and refuses records that arrive once it has begun, counting them. A
// flush that gives up on its batch leaves the batch to Close: Close retries it
// after the flush's last server error, and counts it after a client error,
// which a retry would not change.
func TestLogFlusherClose_WaitsForAFlushInProgress(t *testing.T) {
	for _, tc := range []struct {
		name            string
		status          int
		late            []string
		wantDelivered   []string
		wantRequests    int
		wantUndelivered int64
	}{
		{"after it delivers", http.StatusOK, []string{"req-3"}, []string{"req-1", "req-2"}, 1, 1},
		{"after its last server error", http.StatusServiceUnavailable, nil, []string{"req-1", "req-2"}, 2, 0},
		{"after a client error", http.StatusBadRequest, nil, nil, 1, 2},
	} {
		t.Run(tc.name, func(t *testing.T) {
			logs, restore := installCapturingLogger()
			defer restore()
			sending := make(chan struct{}, 10)
			proceed := make(chan struct{})
			wh := newFakeLogWebhook(t, func(n int, _ *http.Request) int {
				if n == 1 {
					sending <- struct{}{}
					<-proceed
					return tc.status
				}
				return http.StatusOK
			})
			letItAnswer := releaseAtCleanup(t, proceed)
			f := NewLogFlusher(wh.URL, "secret", time.Hour, 100)
			f.consecutiveFails = maxFlushRetries // a failing send is the flush's last try
			enqueue(f, "req-1", "req-2")
			go f.flush()
			<-sending

			closed := make(chan struct{})
			go func() {
				defer close(closed)
				ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
				defer cancel()
				f.Close(ctx)
			}()
			waitUntilClosing(t, f)
			enqueue(f, tc.late...)
			letItAnswer()

			select {
			case <-closed:
			case <-time.After(5 * time.Second):
				t.Fatal("Close did not return")
			}
			if got := wh.delivered(); !slices.Equal(got, tc.wantDelivered) {
				t.Errorf("webhook was delivered %q, want %q", got, tc.wantDelivered)
			}
			if n := wh.requests(); n != tc.wantRequests {
				t.Errorf("webhook got %d requests, want %d", n, tc.wantRequests)
			}
			if n, _, _ := undeliveredLogged(t, logs); n != tc.wantUndelivered {
				t.Errorf("logged undelivered = %d, want %d", n, tc.wantUndelivered)
			}
		})
	}
}

// A flush that has given up on its batch after maxFlushRetries as Close begins
// drops it, and Close counts it as undelivered.
func TestLogFlusherClose_CountsTheBatchAFlushIsDropping(t *testing.T) {
	logs := &capturingHandler{}
	dropping := make(chan struct{}, 1)
	released := make(chan struct{})
	prev := slog.Default()
	slog.SetDefault(slog.New(blockingLogHandler{
		msg:     "log flusher: max retries exceeded, dropping records",
		started: dropping,
		release: released,
		next:    logs,
	}))
	defer slog.SetDefault(prev)
	letItDrop := releaseAtCleanup(t, released)
	wh := newFakeLogWebhook(t, func(int, *http.Request) int { return http.StatusServiceUnavailable })
	f := NewLogFlusher(wh.URL, "secret", time.Hour, 100)
	f.consecutiveFails = maxFlushRetries // a failing send is the flush's last try
	enqueue(f, "req-1", "req-2")
	go f.flush()
	<-dropping

	closed := make(chan struct{})
	go func() {
		defer close(closed)
		ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		f.Close(ctx)
	}()
	waitUntilClosing(t, f)
	letItDrop()

	select {
	case <-closed:
	case <-time.After(5 * time.Second):
		t.Fatal("Close did not return")
	}
	if n, _, ok := undeliveredLogged(t, logs); !ok || n != 2 {
		t.Errorf("logged undelivered = %d (logged: %v), want 2", n, ok)
	}
}

// A backend that has already stopped (stop order often stops it before the
// gateway) makes the undelivered logs a WARN, not an ERROR.
func TestLogFlusherClose_WarnsWhenTheBackendIsGone(t *testing.T) {
	gone := httptest.NewServer(http.NotFoundHandler())
	gone.Close()
	for _, tc := range []struct {
		name string
		dial func(ctx context.Context, network, addr string) (net.Conn, error)
	}{
		{"it refuses the connection", nil},
		{"its name no longer resolves", func(context.Context, string, string) (net.Conn, error) {
			return nil, &net.DNSError{Err: "no such host", Name: "backend", IsNotFound: true}
		}},
	} {
		t.Run(tc.name, func(t *testing.T) {
			shortRetryWait(t)
			logs, restore := installCapturingLogger()
			defer restore()
			f := NewLogFlusher(gone.URL, "secret", time.Hour, 100)
			if tc.dial != nil {
				f.client = &http.Client{Transport: &http.Transport{DialContext: tc.dial}}
			}
			enqueue(f, "req-1", "req-2")

			ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
			defer cancel()
			f.Close(ctx)

			if n, level, ok := undeliveredLogged(t, logs); !ok || n != 2 || level != slog.LevelWarn {
				t.Errorf("logged undelivered = %d at %s (logged: %v), want 2 at WARN", n, level, ok)
			}
		})
	}
}

// blockingLogHandler holds each log line whose message is msg until release
// closes, as a slow stdout would. It sends on started, if set, as one begins,
// and passes every line on to next, if set.
type blockingLogHandler struct {
	msg     string
	started chan<- struct{}
	release <-chan struct{}
	next    slog.Handler
}

func (h blockingLogHandler) Enabled(context.Context, slog.Level) bool { return true }
func (h blockingLogHandler) Handle(ctx context.Context, r slog.Record) error {
	if r.Message == h.msg {
		if h.started != nil {
			h.started <- struct{}{}
		}
		<-h.release
	}
	if h.next != nil {
		return h.next.Handle(ctx, r)
	}
	return nil
}
func (h blockingLogHandler) WithAttrs([]slog.Attr) slog.Handler { return h }
func (h blockingLogHandler) WithGroup(string) slog.Handler      { return h }

// The plugin's Close makes the last flush while the trace emitter drains, so
// their waits do not add up in the shutdown's time budget.
func TestPluginClose_FlushesWhileTheEmitterDrains(t *testing.T) {
	flushed := make(chan struct{})
	prev := slog.Default()
	slog.SetDefault(slog.New(blockingLogHandler{msg: "request.trace", release: flushed}))
	defer slog.SetDefault(prev)
	var p *Plugin
	emitterClosing := func() bool {
		p.emitter.mu.RLock()
		defer p.emitter.mu.RUnlock()
		return p.emitter.closed
	}
	wh := newFakeLogWebhook(t, func(n int, _ *http.Request) int {
		if n == 1 {
			// Answer once the emitter has begun to drain, which it finishes
			// only after this: Close must run the two at once.
			for d := time.Now().Add(2 * time.Second); !emitterClosing() && time.Now().Before(d); {
				time.Sleep(time.Millisecond)
			}
			close(flushed)
		}
		return http.StatusOK
	})
	p = New(enabledCfg(), nil)
	p.SetFlusher(NewLogFlusher(wh.URL, "secret", time.Hour, 100))
	rc := newRC()
	rc.Response = &models.ChatCompletionResponse{}
	p.ProcessResponse(context.Background(), rc)

	start := time.Now()
	p.Close()
	if elapsed := time.Since(start); elapsed > time.Second {
		t.Errorf("Close took %s, want the flush and the emitter's drain to overlap", elapsed)
	}
	if got, want := wh.delivered(), []string{"req-123"}; !slices.Equal(got, want) {
		t.Errorf("webhook was delivered %q, want %q", got, want)
	}
}
