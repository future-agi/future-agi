package middleware

import (
	"bufio"
	"crypto/rand"
	"net"
	"net/http"
	"strconv"
	"sync"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/oklog/ulid/v2"
)

// requestIDEntropy is the entropy of every request and trace id this process
// makes: crypto/rand, read through ulid's monotonic reader behind a lock.
// Within one millisecond each id's entropy is the previous id's plus a random
// step, so ids stamped in the same millisecond still differ, and a new
// millisecond starts from fresh random bytes.
var requestIDEntropy = &ulid.LockedMonotonicReader{MonotonicReader: ulid.Monotonic(rand.Reader, 0)}

// now is the clock ids are stamped with. Tests stop it.
var now = time.Now

// generateULID returns a new request or trace id, unique in this process.
// MustNew can fail only if the 80-bit entropy overflows inside one
// millisecond.
func generateULID() string {
	return ulid.MustNew(ulid.Timestamp(now()), requestIDEntropy).String()
}

// NewID returns a new request or trace id from the source RequestID gives
// every request its ids from. A run of the pipeline that no HTTP request
// carries in (an A2A task, a /-/batches item, a scheduled job) takes its ids
// here, so it never runs with an empty id, or with one another run has.
func NewID() string {
	return generateULID()
}

// RequestID generates a ULID for each request and sets it in the context and
// response header. It is the first middleware that reads the request, so it
// also reads the documented request headers that stand for others
// (readAgentccHeaders: x-agentcc-config, the guardrail-policy alias, the
// property, session name/path and client request id headers) before any
// other reader runs. The client's own x-agentcc-request-id never replaces
// the gateway's id: it is echoed as x-agentcc-client-request-id and logged
// as metadata client_request_id. What x-agentcc-config holds that is not
// read (an SDK GatewayConfig section) is named in x-agentcc-config-ignored.
// Every response, errors included, carries x-agentcc-latency-ms
// (latencyWriter).
func RequestID(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		lw := acquireLatencyWriter(w)
		defer releaseLatencyWriter(lw)

		id := generateULID()
		w.Header().Set("x-agentcc-request-id", id)

		clientID, configIgnored, apiErr := readAgentccHeaders(r.Header)

		// Use caller-provided trace ID (x-agentcc-config may carry it) or
		// generate one.
		traceID := r.Header.Get("x-agentcc-trace-id")
		if traceID == "" {
			traceID = generateULID()
		}
		w.Header().Set("x-agentcc-trace-id", traceID)
		if clientID != "" {
			w.Header()[HeaderClientRequestID] = []string{clientID}
		}
		if configIgnored != "" {
			w.Header()[HeaderConfigIgnored] = []string{configIgnored}
		}
		if apiErr != nil {
			models.WriteError(lw, apiErr)
			return
		}

		ctx := models.WithRequestID(r.Context(), id)
		next.ServeHTTP(lw, r.WithContext(ctx))
	})
}

// HeaderLatencyMs is the gateway's latency in whole milliseconds, on every
// response. A handler that sets it itself (the chat path's, from the
// request context's start) keeps its value.
const HeaderLatencyMs = "X-Agentcc-Latency-Ms"

// latencyWriter stamps x-agentcc-latency-ms, the time since RequestID began,
// on a response whose handler did not set it, when its header is written.
// Pooled: a request allocates none.
type latencyWriter struct {
	http.ResponseWriter
	start   time.Time
	stamped bool
}

var latencyWriterPool = sync.Pool{New: func() any { return new(latencyWriter) }}

func acquireLatencyWriter(w http.ResponseWriter) *latencyWriter {
	lw := latencyWriterPool.Get().(*latencyWriter)
	lw.ResponseWriter, lw.start, lw.stamped = w, time.Now(), false
	return lw
}

// releaseLatencyWriter returns lw to the pool once its request is done; net/http
// never lets a handler write after it returns.
func releaseLatencyWriter(lw *latencyWriter) {
	lw.ResponseWriter = nil
	latencyWriterPool.Put(lw)
}

func (w *latencyWriter) stamp() {
	if w.stamped {
		return
	}
	w.stamped = true
	h := w.ResponseWriter.Header()
	if _, ok := h[HeaderLatencyMs]; !ok {
		h[HeaderLatencyMs] = []string{strconv.FormatInt(time.Since(w.start).Milliseconds(), 10)}
	}
}

// WriteHeader stamps the latency on the final header (not on a 1xx).
func (w *latencyWriter) WriteHeader(code int) {
	if code >= http.StatusOK {
		w.stamp()
	}
	w.ResponseWriter.WriteHeader(code)
}

func (w *latencyWriter) Write(b []byte) (int, error) {
	w.stamp()
	return w.ResponseWriter.Write(b)
}

// Flush writes the header too, so it stamps first.
func (w *latencyWriter) Flush() {
	w.stamp()
	if f, ok := w.ResponseWriter.(http.Flusher); ok {
		f.Flush()
	}
}

// Unwrap lets http.ResponseController reach the writer below.
func (w *latencyWriter) Unwrap() http.ResponseWriter {
	return w.ResponseWriter
}

// Hijack hands the connection to a WebSocket handler (the speech relays,
// /v1/realtime): gorilla/websocket asserts http.Hijacker on the writer it is
// given rather than unwrapping it, as Recovery's writer also allows. The
// upgrade writes its own 101 response, so there is no header to stamp.
func (w *latencyWriter) Hijack() (net.Conn, *bufio.ReadWriter, error) {
	w.stamped = true
	return http.NewResponseController(w.ResponseWriter).Hijack()
}
