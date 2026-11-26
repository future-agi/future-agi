package server

import (
	"bytes"
	"context"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"go.opentelemetry.io/collector/pdata/ptrace/ptraceotlp"
	"google.golang.org/genproto/googleapis/rpc/errdetails"
	statuspb "google.golang.org/genproto/googleapis/rpc/status"
	"google.golang.org/grpc/codes"
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

func spansRows(n int) []map[string]any {
	rows := make([]map[string]any, n)
	for i := range rows {
		rows[i] = map[string]any{"id": i}
	}
	return rows
}

// Exporter retries follow the actual byte-pool busy response, never the old
// larger-than-row-budget admission rule. A permanent body cap remains a drop.
func TestSpecFollowingHTTPClientRetriesByteBudgetAndDropsOversize(t *testing.T) {
	for _, ct := range []string{"application/json", "application/x-protobuf"} {
		t.Run(ct, func(t *testing.T) {
			s := quietServer(Config{IngestMaxWait: time.Millisecond, MaxBodyMiB: 1})
			held, err := s.work.acquire(context.Background(), s.work.size)
			if err != nil {
				t.Fatal(err)
			}
			defer held.release()
			ts := httptest.NewServer(http.HandlerFunc(s.handleHTTPTraces))
			defer ts.Close()
			req := ptraceotlp.NewExportRequestFromTraces(spansTraces(2))
			body, _ := req.MarshalProto()
			if ct == "application/json" {
				body, _ = req.MarshalJSON()
			}
			attempts, resp := postWithSpecRetry(t, ts.URL, body, ct, 3, held.release)
			defer resp.Body.Close()
			if attempts != 2 || resp.StatusCode != 200 || s.Admission().BufferedRows != 2 {
				t.Fatalf("retry: attempts %d status %d backlog %+v", attempts, resp.StatusCode, s.Admission())
			}
			if s.IngestStats().Rejected["http/work_busy/503"] != 1 {
				t.Fatal("byte refusal not counted")
			}
			attempts, oversize := postWithSpecRetry(t, ts.URL, make([]byte, (1<<20)+1), ct, 3, nil)
			defer oversize.Body.Close()
			if attempts != 1 || oversize.StatusCode != 413 {
				t.Fatalf("permanent cap: %d attempts %d", attempts, oversize.StatusCode)
			}
		})
	}
}
func TestByteBudgetRefusalsCarryOTLPStatus(t *testing.T) {
	for _, ct := range []string{"application/json", "application/x-protobuf"} {
		t.Run(ct, func(t *testing.T) {
			s := quietServer(Config{IngestMaxWait: time.Millisecond})
			held, err := s.work.acquire(context.Background(), s.work.size)
			if err != nil {
				t.Fatal(err)
			}
			defer held.release()
			req := ptraceotlp.NewExportRequestFromTraces(spansTraces(1))
			body, _ := req.MarshalProto()
			if ct == "application/json" {
				body, _ = req.MarshalJSON()
			}
			w := benchmarkPostBody(s, body, ct, "")
			var st statuspb.Status
			if ct == "application/json" {
				err = protojson.Unmarshal(w.Body.Bytes(), &st)
			} else {
				err = proto.Unmarshal(w.Body.Bytes(), &st)
			}
			if err != nil || w.Code != 503 || w.Header().Get("Retry-After") != "1" || st.Message == "" || st.Code != int32(codes.Unavailable) {
				t.Fatalf("refusal: %d %s %v", w.Code, w.Body.String(), err)
			}
			if len(s.pend) != 0 || s.Admission().BufferedRows != 0 {
				t.Fatal("refusal enqueued rows")
			}
		})
	}
}
func TestOtherHTTPFailuresCarryAnOTLPStatusBody(t *testing.T) {
	s := quietServer(Config{})
	for _, tc := range []struct {
		name        string
		do          func() *httptest.ResponseRecorder
		wantCode    int
		wantContent string
	}{
		{"unsupported-type", func() *httptest.ResponseRecorder { return benchmarkPostBody(s, []byte("x"), "text/plain", "") }, http.StatusUnsupportedMediaType, "text/plain; charset=utf-8"},
		{"bad-json", func() *httptest.ResponseRecorder { return benchmarkPostBody(s, []byte("{"), "application/json", "") }, http.StatusBadRequest, "application/json"},
		{"bad-protobuf", func() *httptest.ResponseRecorder {
			return benchmarkPostBody(s, []byte{0xff, 0xff}, "application/x-protobuf", "")
		}, http.StatusBadRequest, "application/x-protobuf"},
		{"bad-method", func() *httptest.ResponseRecorder {
			w := httptest.NewRecorder()
			s.handleHTTPTraces(w, httptest.NewRequest(http.MethodGet, "/v1/traces", nil))
			return w
		}, http.StatusMethodNotAllowed, "text/plain; charset=utf-8"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			w := tc.do()
			if w.Code != tc.wantCode || w.Header().Get("Content-Type") != tc.wantContent {
				t.Fatalf("got %d %q, want %d %q", w.Code, w.Header().Get("Content-Type"), tc.wantCode, tc.wantContent)
			}
			if strings.HasPrefix(tc.wantContent, "text/plain") {
				if w.Body.Len() == 0 {
					t.Fatal("empty refusal")
				}
				return
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
