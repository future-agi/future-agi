package server

import (
	"bytes"
	"context"
	"errors"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/future-agi/future-agi/fi-collector/pkg/chwriter"
	"go.opentelemetry.io/collector/pdata/ptrace/ptraceotlp"
	"google.golang.org/genproto/googleapis/rpc/errdetails"
	statuspb "google.golang.org/genproto/googleapis/rpc/status"
	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/credentials/insecure"
	"google.golang.org/grpc/status"
	"google.golang.org/protobuf/encoding/protojson"
	"google.golang.org/protobuf/proto"
)

// otlpHTTPRetryable is the retry rule of the OTLP/HTTP specification
// (https://opentelemetry.io/docs/specs/otlp/#failures-1 and #otlphttp-throttling):
// 429, 502, 503 and 504 are retryable, every other 4xx and 5xx must not be retried.
func otlpHTTPRetryable(code int) bool {
	switch code {
	case http.StatusTooManyRequests, http.StatusBadGateway, http.StatusServiceUnavailable, http.StatusGatewayTimeout:
		return true
	}
	return false
}

// otlpGRPCRetryable is the retry rule of the OTLP/gRPC specification: CANCELLED,
// DEADLINE_EXCEEDED, ABORTED, OUT_OF_RANGE, UNAVAILABLE and DATA_LOSS are
// retryable, and RESOURCE_EXHAUSTED only when the server supplied RetryInfo.
func otlpGRPCRetryable(err error) bool {
	st, _ := status.FromError(err)
	switch st.Code() {
	case codes.Canceled, codes.DeadlineExceeded, codes.Aborted, codes.OutOfRange, codes.Unavailable, codes.DataLoss:
		return true
	case codes.ResourceExhausted:
		for _, d := range st.Details() {
			if _, ok := d.(*errdetails.RetryInfo); ok {
				return true
			}
		}
	}
	return false
}

func quietServer(cfg Config) *Server {
	return New(cfg, nil, nil, nil, nil, WithLogger(slog.New(slog.NewTextHandler(io.Discard, nil))))
}

// postWithSpecRetry is an OTLP/HTTP exporter that follows the specification: it
// retries only retryable statuses, at most maxAttempts times, and returns how
// many requests it sent. beforeRetry runs between attempts.
func postWithSpecRetry(t *testing.T, url string, body []byte, contentType string, maxAttempts int, beforeRetry func()) (attempts int, last *http.Response) {
	t.Helper()
	for attempts < maxAttempts {
		attempts++
		req, _ := http.NewRequest(http.MethodPost, url, bytes.NewReader(body))
		req.Header.Set("Content-Type", contentType)
		resp, err := http.DefaultClient.Do(req)
		if err != nil {
			t.Fatal(err)
		}
		if last != nil {
			last.Body.Close()
		}
		last = resp
		if resp.StatusCode == http.StatusOK || !otlpHTTPRetryable(resp.StatusCode) {
			return attempts, last
		}
		if beforeRetry != nil {
			beforeRetry()
		}
	}
	return attempts, last
}

// Over a real HTTP listener, a spec-following exporter delivers an export
// larger than the whole budget: in one attempt into an empty buffer, and after
// retrying (never giving up) when it met a full buffer. One that merely met a
// full buffer is retried to success.
func TestSpecFollowingHTTPClientDeliversOverBudgetAndRetriesFullBuffer(t *testing.T) {
	for _, tc := range []struct {
		encoding, contentType string
	}{{"json", "application/json"}, {"protobuf", "application/x-protobuf"}} {
		t.Run(tc.encoding, func(t *testing.T) {
			const budget = 4
			s := quietServer(Config{MaxPendingRows: budget, BatchMaxAge: time.Hour})
			var calls atomic.Int32
			ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				calls.Add(1)
				s.handleHTTPTraces(w, r)
			}))
			defer ts.Close()
			encode := func(n int) []byte {
				req := ptraceotlp.NewExportRequestFromTraces(spansTraces(n))
				if tc.encoding == "json" {
					b, _ := req.MarshalJSON()
					return b
				}
				b, _ := req.MarshalProto()
				return b
			}
			drain := func() {
				s.pendMu.Lock()
				s.pend, s.bufferedRows = nil, 0 // the flush loop would do this
				s.pendMu.Unlock()
			}

			attempts, resp := postWithSpecRetry(t, ts.URL, encode(budget+1), tc.contentType, 50, nil)
			defer resp.Body.Close()
			if attempts != 1 || calls.Load() != 1 || resp.StatusCode != http.StatusOK {
				t.Fatalf("over-budget export into an empty buffer: %d attempts, %d server calls, final %d; want one 200", attempts, calls.Load(), resp.StatusCode)
			}
			if s.bufferedRows != budget+1 {
				t.Fatalf("over-budget export parked %d rows, want %d", s.bufferedRows, budget+1)
			}

			// With the buffer full, the over-budget export is retried, not dropped.
			drain()
			if s.enqueue(spansRows(budget), nil) != enqueueAccepted {
				t.Fatal("could not fill the buffer")
			}
			attempts, resp2 := postWithSpecRetry(t, ts.URL, encode(budget+1), tc.contentType, 10, drain)
			defer resp2.Body.Close()
			if attempts != 2 || resp2.StatusCode != http.StatusOK {
				t.Fatalf("over-budget export into a full buffer: %d attempts, final %d; want a retryable refusal then 200", attempts, resp2.StatusCode)
			}

			// Fill the buffer; a client that sees 503 must retry and succeed once it drains.
			drain()
			if s.enqueue(spansRows(budget), nil) != enqueueAccepted {
				t.Fatal("could not fill the buffer")
			}
			attempts, resp3 := postWithSpecRetry(t, ts.URL, encode(2), tc.contentType, 10, drain)
			defer resp3.Body.Close()
			if attempts != 2 || resp3.StatusCode != http.StatusOK {
				t.Fatalf("a retryable refusal was not retried to success: %d attempts, final %d", attempts, resp3.StatusCode)
			}
		})
	}
}

func spansRows(n int) []map[string]any {
	rows := make([]map[string]any, n)
	for i := range rows {
		rows[i] = map[string]any{"id": i}
	}
	return rows
}

// The OTLP/HTTP specification requires every 4xx/5xx body to be a
// google.rpc.Status. The refusals must decode as one, in the request's encoding,
// and name the limit, and the status line must stay the authority.
func TestHTTPRefusalsCarryAnOTLPStatusBody(t *testing.T) {
	const budget = 4
	for _, tc := range []struct {
		name, contentType string
		spans             int
		fill              int
		wantCode          int
		retryAfter        bool
		wantInMessage     string
	}{
		{"over-budget-full-json", "application/json", budget + 1, budget, http.StatusServiceUnavailable, true, "max_pending_rows (4)"},
		{"over-budget-full-protobuf", "application/x-protobuf", budget + 1, budget, http.StatusServiceUnavailable, true, "max_pending_rows (4)"},
		{"full-json", "application/json", 2, budget, http.StatusServiceUnavailable, true, "retry with backoff"},
		{"full-protobuf", "application/x-protobuf", 2, budget, http.StatusServiceUnavailable, true, "retry with backoff"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			s := quietServer(Config{MaxPendingRows: budget})
			if tc.fill > 0 && s.enqueue(spansRows(tc.fill), nil) != enqueueAccepted {
				t.Fatal("could not fill the buffer")
			}
			req := ptraceotlp.NewExportRequestFromTraces(spansTraces(tc.spans))
			var body []byte
			if tc.contentType == "application/json" {
				body, _ = req.MarshalJSON()
			} else {
				body, _ = req.MarshalProto()
			}
			w := postBody(s, body, tc.contentType, "")
			if w.Code != tc.wantCode {
				t.Fatalf("got %d, want %d", w.Code, tc.wantCode)
			}
			if (w.Header().Get("Retry-After") != "") != tc.retryAfter {
				t.Fatalf("Retry-After=%q, want present=%v", w.Header().Get("Retry-After"), tc.retryAfter)
			}
			if got := w.Header().Get("Content-Type"); got != tc.contentType {
				t.Fatalf("error body media type %q, want %q (the request's)", got, tc.contentType)
			}
			var st statuspb.Status
			var err error
			if tc.contentType == "application/json" {
				err = protojson.Unmarshal(w.Body.Bytes(), &st)
			} else {
				err = proto.Unmarshal(w.Body.Bytes(), &st)
			}
			if err != nil {
				t.Fatalf("body is not a google.rpc.Status: %v (%q)", err, w.Body.String())
			}
			if !strings.Contains(st.Message, tc.wantInMessage) {
				t.Fatalf("status message %q does not contain %q", st.Message, tc.wantInMessage)
			}
		})
	}
}

// Failures before the request is decoded have no request media type yet; they
// still answer with a Status body (protobuf), including an unsupported type and a
// bad method.
func TestOtherHTTPFailuresCarryAnOTLPStatusBody(t *testing.T) {
	s := quietServer(Config{MaxPendingRows: 4})
	for _, tc := range []struct {
		name        string
		do          func() *httptest.ResponseRecorder
		wantCode    int
		wantContent string
	}{
		{"unsupported-type", func() *httptest.ResponseRecorder { return postBody(s, []byte("x"), "text/plain", "") }, http.StatusUnsupportedMediaType, "application/x-protobuf"},
		{"bad-json", func() *httptest.ResponseRecorder { return postBody(s, []byte("{"), "application/json", "") }, http.StatusBadRequest, "application/json"},
		{"bad-protobuf", func() *httptest.ResponseRecorder {
			return postBody(s, []byte{0xff, 0xff}, "application/x-protobuf", "")
		}, http.StatusBadRequest, "application/x-protobuf"},
		{"bad-method", func() *httptest.ResponseRecorder {
			w := httptest.NewRecorder()
			s.handleHTTPTraces(w, httptest.NewRequest(http.MethodGet, "/v1/traces", nil))
			return w
		}, http.StatusMethodNotAllowed, "application/x-protobuf"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			w := tc.do()
			if w.Code != tc.wantCode || w.Header().Get("Content-Type") != tc.wantContent {
				t.Fatalf("got %d %q, want %d %q", w.Code, w.Header().Get("Content-Type"), tc.wantCode, tc.wantContent)
			}
			var st statuspb.Status
			var err error
			if tc.wantContent == "application/json" {
				err = protojson.Unmarshal(w.Body.Bytes(), &st)
			} else {
				err = proto.Unmarshal(w.Body.Bytes(), &st)
			}
			if err != nil || st.Message == "" {
				t.Fatalf("body is not a populated google.rpc.Status: %v (%q)", err, w.Body.String())
			}
		})
	}
}

// Over a real gRPC listener with a real writer, a spec-following exporter
// delivers an export larger than the whole budget in one attempt into an empty
// buffer, and a request that meets the buffer over the budget (the big insert
// still in flight) is refused with RetryInfo and retried to success once the
// insert completes.
func TestSpecFollowingGRPCClientDeliversOverBudgetAndRetriesFullBuffer(t *testing.T) {
	const budget = 4
	// A real writer on a stub ClickHouse whose first insert blocks until released,
	// so the admitted over-budget batch holds the budget while in flight.
	entered, release := make(chan struct{}), make(chan struct{})
	var once, releaseOnce sync.Once
	stub := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = io.Copy(io.Discard, r.Body)
		once.Do(func() { close(entered) })
		<-release
	}))
	defer stub.Close()
	unblock := func() { releaseOnce.Do(func() { close(release) }) }
	defer unblock()
	writer, err := chwriter.New(chwriter.Config{URL: stub.URL, Database: "default", Table: "spans", MaxRetries: 1, DeadLetterFile: t.TempDir() + "/dl.jsonl"})
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	s := New(Config{GRPCAddr: testTCPAddr(t), MaxPendingRows: budget, BatchMaxAge: time.Hour}, writer, nil, nil, nil, WithLogger(slog.New(slog.NewTextHandler(io.Discard, nil))))
	s.cfg.HTTPAddr = ""
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan struct{})
	go func() { _ = s.Run(ctx); close(done) }()
	defer func() { cancel(); <-done }()
	if !waitPort(s.cfg.GRPCAddr, 2*time.Second) {
		t.Fatal("server did not start")
	}
	conn, err := grpc.NewClient(s.cfg.GRPCAddr, grpc.WithTransportCredentials(insecure.NewCredentials()))
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	client := ptraceotlp.NewGRPCClient(conn)
	export := func(n int, maxAttempts int, beforeRetry func()) (attempts int, err error) {
		for attempts < maxAttempts {
			attempts++
			_, err = client.Export(context.Background(), ptraceotlp.NewExportRequestFromTraces(spansTraces(n)))
			if err == nil || !otlpGRPCRetryable(err) {
				return attempts, err
			}
			if beforeRetry != nil {
				beforeRetry()
			}
		}
		return attempts, err
	}

	attempts, err := export(budget+1, 50, nil)
	if attempts != 1 || err != nil {
		t.Fatalf("over-budget gRPC export into an empty buffer: %d attempts, %v; want 1 attempt and success", attempts, err)
	}
	// The admitted batch fills past the budget and wakes the flusher at once.
	select {
	case <-entered:
	case <-time.After(5 * time.Second):
		t.Fatal("the over-budget batch was not flushed")
	}
	var refusal error
	attempts, err = export(2, 10, func() {
		if refusal == nil {
			refusal = errors.New("first attempt was refused")
		}
		unblock()
		deadline := time.Now().Add(5 * time.Second)
		for s.Admission().BufferedRows != 0 && time.Now().Before(deadline) {
			time.Sleep(5 * time.Millisecond)
		}
	})
	if attempts != 2 || err != nil || refusal == nil {
		t.Fatalf("export while the over-budget insert was in flight: %d attempts, %v; want a retryable refusal then success", attempts, err)
	}
	if r := s.Admission().Refusals; r.OverBudgetAdmittedRequests != 1 || r.FullRequests != 1 {
		t.Fatalf("refusal counters = %+v", r)
	}
}
