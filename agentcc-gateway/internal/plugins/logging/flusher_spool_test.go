package logging

// The flusher keeps, in the request-log spool, every batch the webhook could
// not take, at every point it used to drop one, and delivers it later: on a
// running gateway once the webhook takes batches again. Only a malformed
// batch (400 invalid_log_batch) is dropped. A spooled batch never holds a
// body, the request headers or a credential, and each send is signed.

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"strconv"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/logspool"
	"github.com/futureagi/agentcc-gateway/internal/redisstate"
)

const spoolTestSecret = "synthetic-webhook-secret-spool"

var spoolTestKey = logspool.MACKey("", spoolTestSecret)

func spoolForTest(t *testing.T) *logspool.Store {
	t.Helper()
	addr := os.Getenv("TEST_REDIS_ADDR")
	if addr == "" {
		t.Skip("TEST_REDIS_ADDR unset: a synthetic Redis is required")
	}
	db, _ := strconv.Atoi(os.Getenv("TEST_REDIS_DB"))
	c, err := redisstate.NewClient(redisstate.Config{Address: addr, DB: db})
	if err != nil {
		t.Fatal(err)
	}
	var b [8]byte
	_, _ = rand.Read(b[:])
	prefix := "flusherspool:" + hex.EncodeToString(b[:]) + ":"
	t.Cleanup(func() {
		ctx := context.Background()
		if keys, _ := c.Redis().Keys(ctx, prefix+"*").Result(); len(keys) > 0 {
			c.Redis().Del(ctx, keys...)
		}
		c.Close()
	})
	return logspool.NewStore(c, prefix, "monthly", 0)
}

// spoolWebhook answers with the current status, a 400 carrying the current
// code, and records every send's bytes and headers.
type spoolWebhook struct {
	*httptest.Server
	mu      sync.Mutex
	status  int
	code    string
	bodies  [][]byte
	headers []http.Header
}

func newSpoolWebhook(t *testing.T, status int) *spoolWebhook {
	t.Helper()
	wh := &spoolWebhook{status: status}
	wh.Server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		wh.mu.Lock()
		wh.bodies = append(wh.bodies, body)
		wh.headers = append(wh.headers, r.Header.Clone())
		status, code := wh.status, wh.code
		wh.mu.Unlock()
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(status)
		if code != "" {
			_, _ = io.WriteString(w, `{"status":false,"code":"`+code+`"}`)
		}
	}))
	t.Cleanup(wh.Close)
	return wh
}

func (wh *spoolWebhook) set(status int, code string) {
	wh.mu.Lock()
	defer wh.mu.Unlock()
	wh.status, wh.code = status, code
}

// requestIDs are the request ids of every entry the webhook was sent.
func (wh *spoolWebhook) requestIDs() []string {
	wh.mu.Lock()
	defer wh.mu.Unlock()
	var ids []string
	for _, body := range wh.bodies {
		var batch struct {
			Logs []map[string]any `json:"logs"`
		}
		_ = json.Unmarshal(body, &batch)
		for _, e := range batch.Logs {
			if id, ok := e["request_id"].(string); ok {
				ids = append(ids, id)
			}
		}
	}
	return ids
}

func spoolRecord(id string) TraceRecord {
	return TraceRecord{
		RequestID: id, Timestamp: time.Now(), Model: "m-1", AuthKeyID: "key-1", UserID: "user-1",
		RequestBodyJSON:  json.RawMessage(`{"messages":[{"content":"synthetic prompt"}]}`),
		ResponseBodyJSON: json.RawMessage(`{"content":"synthetic answer"}`),
		RequestHeaders:   map[string][]string{"Authorization": {"Bearer sk-synthetic-caller"}, "X-Api-Key": {"sk-synthetic-caller"}},
		Metadata:         map[string]string{"cost": "2.5", "key_org_id": "org-a"},
	}
}

func spooledFlusher(t *testing.T, url string, store *logspool.Store, maxBuffer int) *LogFlusher {
	t.Helper()
	f := NewLogFlusher(url, spoolTestSecret, time.Hour, maxBuffer)
	f.SetSpool(store, spoolTestKey)
	t.Cleanup(func() {
		ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		f.Close(ctx)
	})
	return f
}

func pending(t *testing.T, store *logspool.Store) int64 {
	t.Helper()
	st, err := store.Stats(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	return st.Pending
}

func spooledIDs(t *testing.T, store *logspool.Store) []string {
	t.Helper()
	all, err := store.All(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	var ids []string
	for _, st := range all {
		for _, l := range st.Batch.Spend {
			ids = append(ids, l.RequestID)
		}
	}
	return ids
}

// The retry-limit drop spools instead, and a running gateway delivers the
// batch once the webhook takes batches again, with no restart.
func TestFlusherSpoolsAfterTheRetryLimitAndDrainsWhileRunning(t *testing.T) {
	store := spoolForTest(t)
	wh := newSpoolWebhook(t, http.StatusServiceUnavailable)
	f := spooledFlusher(t, wh.URL, store, 100)
	f.Enqueue(spoolRecord("req-1"))
	for i := 0; i <= maxFlushRetries; i++ {
		f.flush()
	}
	if got := pending(t, store); got != 1 {
		t.Fatalf("spooled batches = %d, want 1", got)
	}
	wh.set(http.StatusOK, "")
	f.drain(context.Background())
	if got := pending(t, store); got != 0 {
		t.Fatalf("spooled batches after the drain = %d, want 0", got)
	}
	if ids := wh.requestIDs(); ids[len(ids)-1] != "req-1" {
		t.Fatalf("the webhook last received %v, want req-1", ids)
	}
}

// A 400 that is not invalid_log_batch (today's Django answers
// 400 for a refused secret and for a server error) keeps the batch; only
// invalid_log_batch drops it.
func TestFlusherKeepsEvery400ButAMalformedBatch(t *testing.T) {
	store := spoolForTest(t)
	wh := newSpoolWebhook(t, http.StatusBadRequest)
	f := spooledFlusher(t, wh.URL, store, 100)
	f.Enqueue(spoolRecord("req-secret-refused"))
	f.flush()
	if got := pending(t, store); got != 1 {
		t.Fatalf("after a plain 400: spooled = %d, want 1", got)
	}
	wh.set(http.StatusUnauthorized, "")
	f.Enqueue(spoolRecord("req-unauthorized"))
	f.flush()
	if got := pending(t, store); got != 2 {
		t.Fatalf("after a 401: spooled = %d, want 2", got)
	}
	wh.set(http.StatusBadRequest, "invalid_log_batch")
	f.Enqueue(spoolRecord("req-malformed"))
	f.flush()
	if got := pending(t, store); got != 2 {
		t.Fatalf("after invalid_log_batch: spooled = %d, want 2 (dropped, not spooled)", got)
	}
}

// The buffer-full paths spool rather than drop.
func TestFlusherSpoolsWhatTheBufferCannotTake(t *testing.T) {
	store := spoolForTest(t)
	wh := newSpoolWebhook(t, http.StatusServiceUnavailable)
	f := spooledFlusher(t, wh.URL, store, 2)
	var want []string
	for i := 0; i < 8; i++ { // the buffer holds 2*maxBuffer = 4, the spill as many
		id := "req-" + strconv.Itoa(i)
		want = append(want, id)
		f.Enqueue(spoolRecord(id))
	}
	for i := 0; i <= maxFlushRetries+1; i++ {
		f.flush()
	}
	got := map[string]bool{}
	for _, id := range append(spooledIDs(t, store), f.bufferedIDs()...) {
		got[id] = true
	}
	for _, id := range want {
		if !got[id] {
			t.Errorf("%s was neither spooled nor buffered (spooled %v)", id, spooledIDs(t, store))
		}
	}
}

// Close spools what it could not deliver.
func TestFlusherCloseSpoolsWhatItCouldNotDeliver(t *testing.T) {
	store := spoolForTest(t)
	wh := newSpoolWebhook(t, http.StatusServiceUnavailable)
	f := NewLogFlusher(wh.URL, spoolTestSecret, time.Hour, 100)
	f.SetSpool(store, spoolTestKey)
	prevAttempts, prevWait := finalFlushAttempts, finalFlushRetryWait
	finalFlushAttempts, finalFlushRetryWait = 1, time.Millisecond
	t.Cleanup(func() { finalFlushAttempts, finalFlushRetryWait = prevAttempts, prevWait })
	f.Enqueue(spoolRecord("req-at-shutdown"))
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	f.Close(ctx)
	if ids := spooledIDs(t, store); len(ids) != 1 || ids[0] != "req-at-shutdown" {
		t.Fatalf("spooled after Close = %v, want [req-at-shutdown]", ids)
	}
}

// A spooled batch holds no body, no request headers and no
// credential.
func TestASpooledBatchHoldsNoBodyHeaderOrCredential(t *testing.T) {
	store := spoolForTest(t)
	wh := newSpoolWebhook(t, http.StatusBadRequest)
	f := spooledFlusher(t, wh.URL, store, 100)
	f.Enqueue(spoolRecord("req-1"))
	f.flush()
	all, err := store.All(context.Background())
	if err != nil || len(all) != 1 {
		t.Fatalf("spooled = %d, %v", len(all), err)
	}
	raw, _ := json.Marshal(all[0].Batch)
	text := string(raw)
	for _, forbidden := range []string{"request_body", "response_body", "request_headers", "synthetic prompt",
		"synthetic answer", "sk-synthetic-caller", spoolTestSecret, "Authorization"} {
		if strings.Contains(text, forbidden) {
			t.Errorf("the spooled batch contains %q", forbidden)
		}
	}
}

// A tampered batch fails its MAC and is dropped: never sent and
// never counted.
func TestATamperedBatchIsDroppedUnsent(t *testing.T) {
	store := spoolForTest(t)
	wh := newSpoolWebhook(t, http.StatusServiceUnavailable)
	f := spooledFlusher(t, wh.URL, store, 100)
	f.Enqueue(spoolRecord("req-1"))
	for i := 0; i <= maxFlushRetries; i++ {
		f.flush()
	}
	other := NewLogFlusher(wh.URL, spoolTestSecret, time.Hour, 100)
	other.SetSpool(store, logspool.MACKey("a-different-key", ""))
	wh.set(http.StatusOK, "")
	sent := len(wh.requestIDs())
	other.drain(context.Background())
	if got := pending(t, store); got != 0 {
		t.Fatalf("spooled = %d, want 0: a batch that fails its MAC is dropped", got)
	}
	if len(wh.requestIDs()) != sent {
		t.Fatal("a batch that failed its MAC was sent")
	}
	other.spoolMu.Lock()
	defer other.spoolMu.Unlock()
	if other.dropped[logspool.ReasonMAC] != 1 {
		t.Fatalf("dropped = %v, want one for the MAC", other.dropped)
	}
}

// Every send is signed: X-Webhook-Timestamp and X-Webhook-Signature, the
// HMAC-SHA256 of "<timestamp>.<body>" under the webhook secret, next to the
// static header an older control plane reads.
func TestEverySendIsSigned(t *testing.T) {
	wh := newSpoolWebhook(t, http.StatusOK)
	f := NewLogFlusher(wh.URL, spoolTestSecret, time.Hour, 100)
	f.Enqueue(spoolRecord("req-1"))
	f.flush()
	wh.mu.Lock()
	defer wh.mu.Unlock()
	if len(wh.headers) != 1 {
		t.Fatalf("sends = %d", len(wh.headers))
	}
	h, body := wh.headers[0], wh.bodies[0]
	ts := h.Get("X-Webhook-Timestamp")
	n, err := strconv.ParseInt(ts, 10, 64)
	if err != nil || time.Since(time.Unix(n, 0)).Abs() > time.Minute {
		t.Fatalf("X-Webhook-Timestamp = %q", ts)
	}
	if got, want := h.Get("X-Webhook-Signature"), webhookSignature(spoolTestSecret, ts, body); got != want {
		t.Fatalf("X-Webhook-Signature = %q, want %q", got, want)
	}
	if h.Get("X-Webhook-Secret") != spoolTestSecret {
		t.Fatal("the static header an older control plane reads is missing")
	}
}

// Without a spool the flusher drops as before.
func TestWithoutASpoolNothingChanges(t *testing.T) {
	wh := newSpoolWebhook(t, http.StatusBadRequest)
	f := NewLogFlusher(wh.URL, spoolTestSecret, time.Hour, 100)
	f.SetSpool(nil, spoolTestKey)
	f.Enqueue(spoolRecord("req-1"))
	f.flush()
	if ids := f.bufferedIDs(); len(ids) != 0 || f.Spool() != nil {
		t.Fatalf("buffered %v, spool %v; want the batch dropped as before", ids, f.Spool())
	}
}

// bufferedIDs are the request ids waiting in the buffer (a test helper).
func (f *LogFlusher) bufferedIDs() []string {
	f.mu.Lock()
	defer f.mu.Unlock()
	ids := make([]string, 0, len(f.buffer))
	for _, rec := range f.buffer {
		ids = append(ids, rec.RequestID)
	}
	return ids
}
