package server

import (
	"bytes"
	"compress/gzip"
	"context"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/future-agi/future-agi/fi-collector/pkg/chwriter"
	"go.opentelemetry.io/collector/pdata/ptrace/ptraceotlp"
	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/credentials/insecure"
	"google.golang.org/grpc/status"
)

func benchmarkGzipBytes(t *testing.T, in []byte) []byte {
	t.Helper()
	var buf bytes.Buffer
	zw := gzip.NewWriter(&buf)
	if _, err := zw.Write(in); err != nil {
		t.Fatal(err)
	}
	if err := zw.Close(); err != nil {
		t.Fatal(err)
	}
	return buf.Bytes()
}

func benchmarkPostBody(s *Server, body []byte, contentType, encoding string) *httptest.ResponseRecorder {
	r := httptest.NewRequest(http.MethodPost, "/v1/traces", bytes.NewReader(body))
	r.Header.Set("Content-Type", contentType)
	if encoding != "" {
		r.Header.Set("Content-Encoding", encoding)
	}
	w := httptest.NewRecorder()
	s.handleHTTPTraces(w, r)
	return w
}

func TestHTTPGzipRequestBodiesAreDecoded(t *testing.T) {
	req := ptraceotlp.NewExportRequestFromTraces(spansTraces(3))
	proto, _ := req.MarshalProto()
	jsonBody, _ := req.MarshalJSON()
	for _, tc := range []struct {
		name, contentType string
		body              []byte
	}{{"protobuf", "application/x-protobuf", proto}, {"json", "application/json", jsonBody}} {
		t.Run(tc.name, func(t *testing.T) {
			s := New(Config{}, nil, nil, nil, nil, WithLogger(slog.New(slog.NewTextHandler(io.Discard, nil))))
			w := benchmarkPostBody(s, benchmarkGzipBytes(t, tc.body), tc.contentType, "gzip")
			if w.Code != http.StatusOK {
				t.Fatalf("gzip %s body got %d: %s", tc.name, w.Code, w.Body.String())
			}
			if len(s.pend) != 3 {
				t.Fatalf("decoded request queued %d rows, want 3", len(s.pend))
			}
		})
	}
}

func TestHTTPContentEncodingFailuresAreExplicitAndBounded(t *testing.T) {
	s := New(Config{}, nil, nil, nil, nil, WithLogger(slog.New(slog.NewTextHandler(io.Discard, nil))))
	proto, _ := ptraceotlp.NewExportRequestFromTraces(spansTraces(1)).MarshalProto()
	if w := benchmarkPostBody(s, proto, "application/x-protobuf", "br"); w.Code != http.StatusUnsupportedMediaType || w.Header().Get("Accept-Encoding") == "" {
		t.Fatalf("unsupported coding got %d (Accept-Encoding %q)", w.Code, w.Header().Get("Accept-Encoding"))
	}
	if w := benchmarkPostBody(s, []byte("not gzip"), "application/x-protobuf", "gzip"); w.Code != http.StatusBadRequest {
		t.Fatalf("corrupt gzip got %d, want 400", w.Code)
	}
	// A few KiB of compressed zeros must not expand past the body cap.
	bomb := benchmarkGzipBytes(t, make([]byte, defaultMaxBodyBytes+1))
	if len(bomb) > 1<<20 {
		t.Fatalf("test bomb is not small (%d bytes)", len(bomb))
	}
	if w := benchmarkPostBody(s, bomb, "application/x-protobuf", "gzip"); w.Code != http.StatusRequestEntityTooLarge {
		t.Fatalf("decompression bomb got %d, want 413", w.Code)
	}
	if len(s.pend) != 0 {
		t.Fatal("a refused body queued rows")
	}
	// Identity stays accepted, with or without the explicit header.
	for _, enc := range []string{"", "identity"} {
		if w := benchmarkPostBody(s, proto, "application/x-protobuf", enc); w.Code != http.StatusOK {
			t.Fatalf("Content-Encoding %q got %d", enc, w.Code)
		}
	}
}

func TestGRPCGzipRequestsRemainRejectedOutsideWireBudget(t *testing.T) {
	ch := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = io.Copy(io.Discard, r.Body)
	}))
	defer ch.Close()
	writer, err := chwriter.New(chwriter.Config{URL: ch.URL, Database: "default", Table: "spans", MaxRetries: 1, DeadLetterFile: t.TempDir() + "/dl.jsonl"})
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	s := New(Config{GRPCAddr: testTCPAddr(t), BatchMaxRows: 1000, BatchMaxAge: time.Hour}, writer, nil, nil, nil, WithLogger(slog.New(slog.NewTextHandler(io.Discard, nil))))
	s.cfg.HTTPAddr = ""
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan struct{})
	go func() { _ = s.Run(ctx); close(done) }()
	defer func() { cancel(); <-done }()
	if !waitPort(s.cfg.GRPCAddr, 2*time.Second) {
		t.Fatal("server did not start")
	}
	conn, err := grpc.NewClient(s.cfg.GRPCAddr, grpc.WithTransportCredentials(insecure.NewCredentials()), grpc.WithCompressor(grpc.NewGZIPCompressor()))
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	_, err = ptraceotlp.NewGRPCClient(conn).Export(context.Background(), ptraceotlp.NewExportRequestFromTraces(spansTraces(2)))
	if status.Code(err) != codes.Unimplemented {
		t.Fatalf("gzip gRPC must remain disabled: %v", err)
	}
	if a := s.Admission(); a.BufferedRows != 0 {
		t.Fatalf("unsupported gzip enqueued: %+v", a)
	}
}
