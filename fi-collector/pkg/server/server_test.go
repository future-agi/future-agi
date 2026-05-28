package server

import (
	"bytes"
	"context"
	"net"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/future-agi/future-agi/fi-collector/pkg/chwriter"
	"go.opentelemetry.io/collector/pdata/pcommon"
	"go.opentelemetry.io/collector/pdata/ptrace"
	"go.opentelemetry.io/collector/pdata/ptrace/ptraceotlp"
	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"
)

// Spin up the server, point it at an httptest CH, fire one OTLP request,
// confirm the resulting CH HTTP POST contains the converted row.
func TestServerEnd2End(t *testing.T) {
	var seen int32
	var seenBody string
	chSrv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		atomic.AddInt32(&seen, 1)
		b := make([]byte, 1<<14)
		n, _ := r.Body.Read(b)
		seenBody = string(b[:n])
		w.WriteHeader(200)
	}))
	defer chSrv.Close()

	w, _ := chwriter.New(chwriter.Config{
		URL:            chSrv.URL,
		Database:       "default",
		Table:          "spans",
		MaxRetries:     1,
		InitialBackoff: time.Millisecond,
		MaxBackoff:     time.Millisecond,
		RequestTimeout: 2 * time.Second,
		DeadLetterFile: t.TempDir() + "/dl.jsonl",
	})

	s := New(Config{GRPCAddr: "127.0.0.1:0", BatchMaxRows: 1, BatchMaxAge: 50 * time.Millisecond}, w)
	// We need a known listen address to dial; replicate Run's bind step.
	// Easier: use a non-zero port — pick one that's likely free.
	addr := "127.0.0.1:24317"
	s.cfg.GRPCAddr = addr

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	go func() { _ = s.Run(ctx) }()
	// Wait for listener to be ready.
	if !waitPort(addr, 2*time.Second) {
		t.Fatalf("server didn't listen on %s", addr)
	}

	conn, err := grpc.NewClient(addr, grpc.WithTransportCredentials(insecure.NewCredentials()))
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	client := ptraceotlp.NewGRPCClient(conn)

	traces := ptrace.NewTraces()
	rs := traces.ResourceSpans().AppendEmpty()
	rs.Resource().Attributes().PutStr("fi.project_id", "33333333-3333-4333-8333-333333333333")
	sp := rs.ScopeSpans().AppendEmpty().Spans().AppendEmpty()
	sp.SetName("e2e-test-span")
	sp.SetTraceID([16]byte{0xaa})
	sp.SetSpanID([8]byte{0xbb})
	sp.SetStartTimestamp(pcommon.NewTimestampFromTime(time.Now()))
	sp.SetEndTimestamp(pcommon.NewTimestampFromTime(time.Now().Add(50 * time.Millisecond)))

	req := ptraceotlp.NewExportRequestFromTraces(traces)
	if _, err := client.Export(context.Background(), req); err != nil {
		t.Fatalf("OTLP Export: %v", err)
	}

	// Wait up to 1 s for the batcher to flush.
	deadline := time.Now().Add(time.Second)
	for time.Now().Before(deadline) && atomic.LoadInt32(&seen) == 0 {
		time.Sleep(10 * time.Millisecond)
	}
	if atomic.LoadInt32(&seen) != 1 {
		t.Fatalf("CH not POST'd; seen=%d", seen)
	}
	if !strings.Contains(seenBody, "e2e-test-span") {
		t.Errorf("CH body missing span name: %q", seenBody)
	}
	if !strings.Contains(seenBody, "33333333-3333-4333-8333-333333333333") {
		t.Errorf("CH body missing project_id: %q", seenBody)
	}
}

// startServerWithHTTP boots the server with both gRPC and HTTP receivers,
// pointed at a stub CH httptest server, and returns the HTTP receiver addr
// + a cancel func. Shared by the OTLP/HTTP tests below so each test only
// declares the per-case body + media type + assertion.
func startServerWithHTTP(t *testing.T) (httpAddr, grpcAddr string, sawCH func() (int32, string), cancel func()) {
	t.Helper()
	var seen int32
	var seenBody string
	chSrv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		atomic.AddInt32(&seen, 1)
		b := make([]byte, 1<<14)
		n, _ := r.Body.Read(b)
		seenBody = string(b[:n])
		w.WriteHeader(200)
	}))

	w, err := chwriter.New(chwriter.Config{
		URL:            chSrv.URL,
		Database:       "default",
		Table:          "spans",
		MaxRetries:     1,
		InitialBackoff: time.Millisecond,
		MaxBackoff:     time.Millisecond,
		RequestTimeout: 2 * time.Second,
		DeadLetterFile: t.TempDir() + "/dl.jsonl",
	})
	if err != nil {
		t.Fatalf("chwriter.New: %v", err)
	}

	httpAddr = "127.0.0.1:24318"
	grpcAddr = "127.0.0.1:24319" // dynamic enough that grpc test above on :24317 doesn't clash
	s := New(Config{
		GRPCAddr:     grpcAddr,
		HTTPAddr:     httpAddr,
		BatchMaxRows: 1,
		BatchMaxAge:  50 * time.Millisecond,
	}, w)

	ctx, cancelFn := context.WithCancel(context.Background())
	go func() { _ = s.Run(ctx) }()

	// Wait for the HTTP listener — once it accepts we know both bound.
	deadline := time.Now().Add(2 * time.Second)
	for time.Now().Before(deadline) {
		conn, err := net.DialTimeout("tcp", httpAddr, 50*time.Millisecond)
		if err == nil {
			conn.Close()
			break
		}
		time.Sleep(20 * time.Millisecond)
	}

	return httpAddr, grpcAddr, func() (int32, string) { return atomic.LoadInt32(&seen), seenBody },
		func() {
			cancelFn()
			chSrv.Close()
		}
}

// makeTraces builds a minimal one-span Traces with stable identity. Used by
// the HTTP tests below so each can focus on the wire-layer concern.
func makeTraces(name, projectID string) ptrace.Traces {
	traces := ptrace.NewTraces()
	rs := traces.ResourceSpans().AppendEmpty()
	rs.Resource().Attributes().PutStr("fi.project_id", projectID)
	sp := rs.ScopeSpans().AppendEmpty().Spans().AppendEmpty()
	sp.SetName(name)
	sp.SetTraceID([16]byte{0xaa})
	sp.SetSpanID([8]byte{0xbb})
	sp.SetStartTimestamp(pcommon.NewTimestampFromTime(time.Now()))
	sp.SetEndTimestamp(pcommon.NewTimestampFromTime(time.Now().Add(50 * time.Millisecond)))
	return traces
}

// TestOTLPHTTPProtobuf — POST /v1/traces with application/x-protobuf is the
// default for every server-side OTel SDK using the OTLP/HTTP exporter.
// We verify: status 200, response body decodable as ExportTraceServiceResponse,
// CH stub received a write with the span name + project_id.
func TestOTLPHTTPProtobuf(t *testing.T) {
	httpAddr, _, sawCH, stop := startServerWithHTTP(t)
	defer stop()

	traces := makeTraces("http-proto-test-span", "44444444-4444-4444-8444-444444444444")
	req := ptraceotlp.NewExportRequestFromTraces(traces)
	pb, err := req.MarshalProto()
	if err != nil {
		t.Fatalf("MarshalProto: %v", err)
	}

	resp, err := http.Post(
		"http://"+httpAddr+"/v1/traces",
		"application/x-protobuf",
		bytes.NewReader(pb),
	)
	if err != nil {
		t.Fatalf("POST: %v", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != 200 {
		t.Fatalf("status %d (want 200)", resp.StatusCode)
	}
	if got := resp.Header.Get("Content-Type"); got != "application/x-protobuf" {
		t.Errorf("Content-Type = %q, want application/x-protobuf (spec requires "+
			"echoing the request media type)", got)
	}

	// Wait for the batcher to flush.
	deadline := time.Now().Add(time.Second)
	for time.Now().Before(deadline) {
		if n, _ := sawCH(); n > 0 {
			break
		}
		time.Sleep(10 * time.Millisecond)
	}
	n, body := sawCH()
	if n != 1 {
		t.Fatalf("CH not POST'd; seen=%d", n)
	}
	if !strings.Contains(body, "http-proto-test-span") {
		t.Errorf("CH body missing span name: %q", body)
	}
	if !strings.Contains(body, "44444444-4444-4444-8444-444444444444") {
		t.Errorf("CH body missing project_id: %q", body)
	}
}

// TestOTLPHTTPJSON — browser SDKs and many lightweight clients use the JSON
// codec for OTLP/HTTP. We verify the JSON path (UnmarshalJSON in the
// handler, MarshalJSON in the response).
func TestOTLPHTTPJSON(t *testing.T) {
	httpAddr, _, sawCH, stop := startServerWithHTTP(t)
	defer stop()

	traces := makeTraces("http-json-test-span", "55555555-5555-4555-8555-555555555555")
	req := ptraceotlp.NewExportRequestFromTraces(traces)
	js, err := req.MarshalJSON()
	if err != nil {
		t.Fatalf("MarshalJSON: %v", err)
	}

	resp, err := http.Post(
		"http://"+httpAddr+"/v1/traces",
		"application/json",
		bytes.NewReader(js),
	)
	if err != nil {
		t.Fatalf("POST: %v", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != 200 {
		t.Fatalf("status %d (want 200)", resp.StatusCode)
	}
	if got := resp.Header.Get("Content-Type"); got != "application/json" {
		t.Errorf("Content-Type = %q, want application/json", got)
	}

	deadline := time.Now().Add(time.Second)
	for time.Now().Before(deadline) {
		if n, _ := sawCH(); n > 0 {
			break
		}
		time.Sleep(10 * time.Millisecond)
	}
	n, body := sawCH()
	if n != 1 {
		t.Fatalf("CH not POST'd; seen=%d", n)
	}
	if !strings.Contains(body, "http-json-test-span") {
		t.Errorf("CH body missing span name: %q", body)
	}
}

// TestOTLPHTTPCharsetSuffix — spec doesn't ban a `;charset=` suffix on the
// content-type, and JSON clients commonly include it. We must tolerate it.
func TestOTLPHTTPCharsetSuffix(t *testing.T) {
	httpAddr, _, sawCH, stop := startServerWithHTTP(t)
	defer stop()

	traces := makeTraces("charset-suffix-span", "66666666-6666-4666-8666-666666666666")
	req := ptraceotlp.NewExportRequestFromTraces(traces)
	js, _ := req.MarshalJSON()

	httpReq, _ := http.NewRequest("POST", "http://"+httpAddr+"/v1/traces", bytes.NewReader(js))
	httpReq.Header.Set("Content-Type", "application/json; charset=utf-8")
	resp, err := http.DefaultClient.Do(httpReq)
	if err != nil {
		t.Fatalf("POST: %v", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != 200 {
		t.Fatalf("status %d (want 200), charset suffix should be tolerated", resp.StatusCode)
	}

	deadline := time.Now().Add(time.Second)
	for time.Now().Before(deadline) {
		if n, _ := sawCH(); n > 0 {
			break
		}
		time.Sleep(10 * time.Millisecond)
	}
	if n, _ := sawCH(); n != 1 {
		t.Fatalf("CH not POST'd; seen=%d", n)
	}
}

// TestOTLPHTTPRejectsBadMethod — GET / PUT / etc. must be rejected with 405
// per the spec. The `Allow` header tells well-behaved clients they should retry
// as POST.
func TestOTLPHTTPRejectsBadMethod(t *testing.T) {
	httpAddr, _, _, stop := startServerWithHTTP(t)
	defer stop()

	resp, err := http.Get("http://" + httpAddr + "/v1/traces")
	if err != nil {
		t.Fatalf("GET: %v", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusMethodNotAllowed {
		t.Errorf("GET /v1/traces: status %d, want 405", resp.StatusCode)
	}
	if got := resp.Header.Get("Allow"); got != "POST" {
		t.Errorf("Allow header = %q, want POST", got)
	}
}

// TestOTLPHTTPRejectsBadContentType — anything other than the two OTLP
// media types must return 415 with an Accept header listing the supported
// types. Browser SDKs use this to negotiate.
func TestOTLPHTTPRejectsBadContentType(t *testing.T) {
	httpAddr, _, _, stop := startServerWithHTTP(t)
	defer stop()

	resp, err := http.Post(
		"http://"+httpAddr+"/v1/traces",
		"text/plain",
		strings.NewReader("not OTLP"),
	)
	if err != nil {
		t.Fatalf("POST: %v", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusUnsupportedMediaType {
		t.Errorf("status %d, want 415", resp.StatusCode)
	}
	// Spec recommends an Accept header listing the supported media types so
	// SDKs can pick. We list both.
	if got := resp.Header.Get("Accept"); !strings.Contains(got, "application/x-protobuf") ||
		!strings.Contains(got, "application/json") {
		t.Errorf("Accept header missing supported media types: %q", got)
	}
}

// waitPort polls until something accepts on addr or deadline. Simple enough
// not to need a /healthz round trip.
func waitPort(addr string, d time.Duration) bool {
	deadline := time.Now().Add(d)
	for time.Now().Before(deadline) {
		conn, err := grpcDial(addr)
		if err == nil {
			conn.Close()
			return true
		}
		time.Sleep(20 * time.Millisecond)
	}
	return false
}

func grpcDial(addr string) (*grpc.ClientConn, error) {
	return grpc.NewClient(addr, grpc.WithTransportCredentials(insecure.NewCredentials()))
}
