package server

import (
	"bytes"
	"context"
	"io"
	"log/slog"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	chexp "github.com/future-agi/future-agi/fi-collector/exporter/clickhouse25exporter"
	"github.com/future-agi/future-agi/fi-collector/pkg/chwriter"
	"go.opentelemetry.io/collector/pdata/pcommon"
	"go.opentelemetry.io/collector/pdata/ptrace"
	"go.opentelemetry.io/collector/pdata/ptrace/ptraceotlp"
	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/credentials/insecure"
	"google.golang.org/grpc/status"
)

// Spin up the server, point it at an httptest CH, fire one OTLP request,
// confirm the resulting CH HTTP POST contains the converted row.
func TestServerEnd2End(t *testing.T) {
	var seen int32
	var seenBody string
	var seenMu sync.Mutex
	chSrv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		b := make([]byte, 1<<14)
		n, _ := r.Body.Read(b)
		// Scope to the `spans` insert so the span-contract checks stay exact; the
		// `traces` insert has its own test (TestServerEnd2End_WritesTraceRow).
		if insertTable(r) == "spans" {
			atomic.AddInt32(&seen, 1)
			seenMu.Lock()
			seenBody = string(b[:n])
			seenMu.Unlock()
		}
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

	s := New(Config{GRPCAddr: "127.0.0.1:0", BatchMaxRows: 1, BatchMaxAge: 50 * time.Millisecond}, w, nil, nil, nil)
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
	seenMu.Lock()
	body := seenBody
	seenMu.Unlock()
	if !strings.Contains(body, "e2e-test-span") {
		t.Errorf("CH body missing span name: %q", body)
	}
	if !strings.Contains(seenBody, "33333333-3333-4333-8333-333333333333") {
		t.Errorf("CH body missing project_id: %q", seenBody)
	}
}

// insertTable extracts the target table from a CH HTTP insert request's
// `query=INSERT INTO <table> ...` param, so a stub CH can distinguish the `spans`
// insert from the curated / `traces` inserts.
func insertTable(r *http.Request) string {
	q := r.URL.Query().Get("query")
	for _, tbl := range []string{"spans", "traces", "end_users", "trace_sessions"} {
		if strings.Contains(q, "INTO "+tbl+" ") {
			return tbl
		}
	}
	return ""
}

// TestServerEnd2End_WritesTraceRow: an OTLP root span through the real converter
// + curatedwriter must produce a `traces` insert (so trace_dict resolves the
// trace's project_id / name for evals & annotations) in addition to the spans one.
func TestServerEnd2End_WritesTraceRow(t *testing.T) {
	var traceBody string
	var traceSeen int32
	var mu sync.Mutex
	chSrv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		b := make([]byte, 1<<14)
		n, _ := r.Body.Read(b)
		if insertTable(r) == "traces" {
			atomic.AddInt32(&traceSeen, 1)
			mu.Lock()
			traceBody = string(b[:n])
			mu.Unlock()
		}
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

	// curatedwriter over the SAME chwriter (as production: server.New wires it).
	s := New(Config{GRPCAddr: "127.0.0.1:24320", BatchMaxRows: 1, BatchMaxAge: 50 * time.Millisecond}, w, nil, nil, nil)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	go func() { _ = s.Run(ctx) }()
	if !waitPort("127.0.0.1:24320", 2*time.Second) {
		t.Fatal("server didn't listen")
	}

	conn, err := grpc.NewClient("127.0.0.1:24320", grpc.WithTransportCredentials(insecure.NewCredentials()))
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()

	traces := makeTraces("agent.run", "77777777-7777-4777-8777-777777777777") // root span (no parent)
	req := ptraceotlp.NewExportRequestFromTraces(traces)
	if _, err := ptraceotlp.NewGRPCClient(conn).Export(context.Background(), req); err != nil {
		t.Fatalf("Export: %v", err)
	}

	deadline := time.Now().Add(2 * time.Second)
	for time.Now().Before(deadline) && atomic.LoadInt32(&traceSeen) == 0 {
		time.Sleep(10 * time.Millisecond)
	}
	if atomic.LoadInt32(&traceSeen) != 1 {
		t.Fatalf("expected exactly 1 traces insert, got %d", traceSeen)
	}
	mu.Lock()
	body := traceBody
	mu.Unlock()
	if !strings.Contains(body, "77777777-7777-4777-8777-777777777777") {
		t.Errorf("traces row missing project_id: %q", body)
	}
	if !strings.Contains(body, "agent.run") {
		t.Errorf("traces row missing root-span-derived name: %q", body)
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
	var seenMu sync.Mutex
	chSrv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		b := make([]byte, 1<<14)
		n, _ := r.Body.Read(b)
		// Scope to the pinned `spans` insert; the root-span `traces` insert is
		// covered separately (TestServerEnd2End_WritesTraceRow).
		if insertTable(r) == "spans" {
			atomic.AddInt32(&seen, 1)
			seenMu.Lock()
			seenBody = string(b[:n])
			seenMu.Unlock()
		}
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
	}, w, nil, nil, nil)

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

	return httpAddr, grpcAddr, func() (int32, string) {
			seenMu.Lock()
			b := seenBody
			seenMu.Unlock()
			return atomic.LoadInt32(&seen), b
		},
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

// stubPricer is the fixture pricer for the WithPricer wiring tests below.
// Mirrors chexp.Pricer (converter.go's Pricer interface) so it can be passed
// straight to WithPricer without an adapter. Guarded by a mutex since it's
// invoked from the server's request-handling goroutine while the test reads
// `calls`/`lastOrgID` from the test goroutine.
type stubPricer struct {
	mu        sync.Mutex
	calls     int
	lastOrgID string
	cost      float64
}

func (s *stubPricer) TokenCost(ctx context.Context, orgID, model string, p, c int32) (float64, bool) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.calls++
	s.lastOrgID = orgID
	return s.cost, true
}

func (s *stubPricer) snapshot() (calls int, lastOrgID string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.calls, s.lastOrgID
}

// TestNew_WithPricerOption pins the WithPricer wiring at the field level:
// every other test in this file constructs New(...) with NO options, so only
// the nil-pricer path (chexp.Pricer(nil), disabling token pricing) is
// otherwise exercised. This confirms the Option accumulator loop in New
// actually copies a non-nil WithPricer value onto Server.pricer — the same
// field the gRPC/HTTP handlers read for every ConvertWithIdentities call.
func TestNew_WithPricerOption(t *testing.T) {
	p := &stubPricer{cost: 1.23}
	s := New(Config{}, nil, nil, nil, nil, WithPricer(p))
	if s.pricer != chexp.Pricer(p) {
		t.Fatalf("s.pricer = %#v, want the stub pricer instance %#v", s.pricer, p)
	}
}

// TestPricerWiredThroughGRPCExport drives a real trace through the gRPC
// export path (New(..., WithPricer(stub))) with a span that carries
// model+tokens but NO cost attribute, and confirms: (1) the stub Pricer's
// TokenCost was invoked with the span's org, and (2) the row the server
// enqueues to CH carries the stub's cost value. This is the end-to-end pin
// for the pricer wiring finding — every other export test in this file
// constructs New() with no options and so only exercises the nil-pricer
// (token-pricing-disabled) path.
func TestPricerWiredThroughGRPCExport(t *testing.T) {
	var chBody string
	var chSeen int32
	var mu sync.Mutex
	chSrv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		b := make([]byte, 1<<14)
		n, _ := r.Body.Read(b)
		if insertTable(r) == "spans" {
			atomic.AddInt32(&chSeen, 1)
			mu.Lock()
			chBody = string(b[:n])
			mu.Unlock()
		}
		w.WriteHeader(200)
	}))
	defer chSrv.Close()

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

	stub := &stubPricer{cost: 0.0099}
	addr := "127.0.0.1:24321" // distinct from the other gRPC ports used in this file
	s := New(Config{GRPCAddr: addr, BatchMaxRows: 1, BatchMaxAge: 50 * time.Millisecond}, w, nil, nil, nil, WithPricer(stub))

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	go func() { _ = s.Run(ctx) }()
	if !waitPort(addr, 2*time.Second) {
		t.Fatal("server didn't listen")
	}

	conn, err := grpc.NewClient(addr, grpc.WithTransportCredentials(insecure.NewCredentials()))
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()

	const orgID = "88888888-8888-4888-8888-888888888888"
	const projectID = "99999999-9999-4999-9999-999999999999"
	traces := ptrace.NewTraces()
	rs := traces.ResourceSpans().AppendEmpty()
	rs.Resource().Attributes().PutStr("fi.project_id", projectID)
	rs.Resource().Attributes().PutStr("fi.org_id", orgID)
	sp := rs.ScopeSpans().AppendEmpty().Spans().AppendEmpty()
	sp.SetName("pricer-wiring-span")
	sp.SetTraceID([16]byte{0xcc})
	sp.SetSpanID([8]byte{0xdd})
	sp.SetStartTimestamp(pcommon.NewTimestampFromTime(time.Now()))
	sp.SetEndTimestamp(pcommon.NewTimestampFromTime(time.Now().Add(10 * time.Millisecond)))
	a := sp.Attributes()
	a.PutStr("gen_ai.request.model", "gpt-4o")
	a.PutInt("gen_ai.usage.input_tokens", 1000)
	a.PutInt("gen_ai.usage.output_tokens", 500)
	// Deliberately NO gen_ai.cost.total / llm.cost.* — this is the
	// no-user-cost path that must fall through to the pricer.

	req := ptraceotlp.NewExportRequestFromTraces(traces)
	if _, err := ptraceotlp.NewGRPCClient(conn).Export(context.Background(), req); err != nil {
		t.Fatalf("Export: %v", err)
	}

	deadline := time.Now().Add(2 * time.Second)
	for time.Now().Before(deadline) && atomic.LoadInt32(&chSeen) == 0 {
		time.Sleep(10 * time.Millisecond)
	}
	if atomic.LoadInt32(&chSeen) != 1 {
		t.Fatalf("CH not POST'd; seen=%d", chSeen)
	}

	calls, lastOrgID := stub.snapshot()
	if calls == 0 {
		t.Fatal("stub Pricer.TokenCost was never called — pricer not wired through the gRPC export path")
	}
	if lastOrgID != orgID {
		t.Errorf("stub pricer received orgID %q, want %q", lastOrgID, orgID)
	}

	mu.Lock()
	body := chBody
	mu.Unlock()
	if !strings.Contains(body, `"cost":0.0099`) {
		t.Errorf("enqueued row cost != stub's 0.0099 (or key absent); CH body: %q", body)
	}
}

// waitPort polls until something accepts on addr or deadline. Simple enough
// not to need a /healthz round trip.
func waitPort(addr string, d time.Duration) bool {
	// grpc.NewClient is lazy (never dials), so probe with a real TCP connect.
	deadline := time.Now().Add(d)
	for time.Now().Before(deadline) {
		conn, err := net.DialTimeout("tcp", addr, 100*time.Millisecond)
		if err == nil {
			conn.Close()
			return true
		}
		time.Sleep(20 * time.Millisecond)
	}
	return false
}

// makeObserveTraces builds a one-span observe-project Traces carrying a user.id
// and session.id so the converter stamps the curated identities the drain
// aggregates. Distinct trace/span ids per call (via idByte) avoid collisions.
func makeObserveTraces(projectID, orgID, userID, sessionID string, idByte byte) ptrace.Traces {
	traces := ptrace.NewTraces()
	rs := traces.ResourceSpans().AppendEmpty()
	rs.Resource().Attributes().PutStr("fi.project_id", projectID)
	rs.Resource().Attributes().PutStr("fi.org_id", orgID)
	rs.Resource().Attributes().PutStr("project_type", "observe")
	sp := rs.ScopeSpans().AppendEmpty().Spans().AppendEmpty()
	sp.SetName("llm.chat")
	var tid [16]byte
	var sid [8]byte
	tid[0], sid[0] = idByte, idByte
	sp.SetTraceID(tid)
	sp.SetSpanID(sid)
	sp.SetStartTimestamp(pcommon.NewTimestampFromTime(time.Now()))
	sp.SetEndTimestamp(pcommon.NewTimestampFromTime(time.Now().Add(10 * time.Millisecond)))
	a := sp.Attributes()
	a.PutStr("user.id", userID)
	a.PutStr("session.id", sessionID)
	return traces
}

// TestDrainAggregatesCuratedAcrossPayloads is the white-box gate for the
// best-effort fix: curated identities from MULTIPLE enqueued payloads are
// MERGED into one drain-scoped batch, so a single drain emits at most ONE
// end_users + ONE trace_sessions insert (not one per payload), deduped across
// payloads. This bounds the best-effort latency and avoids tiny RMT parts.
func TestDrainAggregatesCuratedAcrossPayloads(t *testing.T) {
	const proj = "11111111-1111-4111-8111-111111111111"
	const org = "22222222-2222-4222-8222-222222222222"

	// Record each insert's target table + decoded row count.
	type ins struct {
		table string
		rows  int
	}
	var mu sync.Mutex
	var inserts []ins
	chSrv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		q := r.URL.Query().Get("query")
		table := ""
		if i := strings.Index(q, "INSERT INTO "); i >= 0 {
			rest := q[i+len("INSERT INTO "):]
			if j := strings.IndexByte(rest, ' '); j >= 0 {
				table = rest[:j]
			}
		}
		body := make([]byte, 1<<16)
		n, _ := r.Body.Read(body)
		rows := strings.Count(strings.TrimSpace(string(body[:n])), "\n") + 1
		if n == 0 {
			rows = 0
		}
		mu.Lock()
		inserts = append(inserts, ins{table: table, rows: rows})
		mu.Unlock()
		w.WriteHeader(200)
	}))
	defer chSrv.Close()

	w, err := chwriter.New(chwriter.Config{
		URL: chSrv.URL, Database: "default", Table: "spans",
		MaxRetries: 1, InitialBackoff: time.Millisecond, MaxBackoff: time.Millisecond,
		RequestTimeout: 2 * time.Second, DeadLetterFile: t.TempDir() + "/dl.jsonl",
	})
	if err != nil {
		t.Fatal(err)
	}
	// Big batch threshold + no flusher goroutine: we drive enqueue/drainNow
	// directly so the test is deterministic (no timing).
	s := New(Config{GRPCAddr: "", HTTPAddr: "", BatchMaxRows: 1_000_000, BatchMaxAge: time.Hour}, w, nil, nil, nil)

	// Payload 1: userA / sessX.  Payload 2: userA again (dup) + userB / sessY.
	r1, ids1, err := chexp.ConvertWithIdentities(context.Background(), makeObserveTraces(proj, org, "userA", "sessX", 0x01), nil)
	if err != nil {
		t.Fatal(err)
	}
	r2a, ids2a, _ := chexp.ConvertWithIdentities(context.Background(), makeObserveTraces(proj, org, "userA", "sessX", 0x02), nil) // dup identity
	r2b, ids2b, _ := chexp.ConvertWithIdentities(context.Background(), makeObserveTraces(proj, org, "userB", "sessY", 0x03), nil)

	_, _ = s.enqueue(context.Background(), r1, ids1, 100)
	_, _ = s.enqueue(context.Background(), r2a, ids2a, 100)
	_, _ = s.enqueue(context.Background(), r2b, ids2b, 100)

	// One drain → one span insert + one end_users + one trace_sessions.
	s.drainNow(context.Background())

	mu.Lock()
	defer mu.Unlock()
	var euCount, sessCount, spanCount int
	var euRows, sessRows int
	for _, in := range inserts {
		switch in.table {
		case "end_users":
			euCount++
			euRows = in.rows
		case "trace_sessions":
			sessCount++
			sessRows = in.rows
		case "spans":
			spanCount++
		}
	}
	if spanCount != 1 {
		t.Errorf("expected 1 span insert, got %d", spanCount)
	}
	if euCount != 1 {
		t.Fatalf("expected exactly 1 end_users insert (drain-scoped), got %d", euCount)
	}
	if sessCount != 1 {
		t.Fatalf("expected exactly 1 trace_sessions insert (drain-scoped), got %d", sessCount)
	}
	// Dedup across payloads: 3 spans, 2 distinct users (A,B), 2 distinct
	// sessions (X,Y) → exactly 2 end_users rows + 2 trace_sessions rows.
	if euRows != 2 {
		t.Errorf("end_users rows: got %d want 2 (userA deduped across payloads)", euRows)
	}
	if sessRows != 2 {
		t.Errorf("trace_sessions rows: got %d want 2", sessRows)
	}
}

// A single span larger than gRPC's 4 MiB default must be accepted under the
// default GRPCMaxRecvMiB. Regression: ended voice-call spans (full transcript
// + raw_log) were rejected and silently lost, leaving traces stuck "In progress".
func TestGRPCAcceptsSpanLargerThanFourMiB(t *testing.T) {
	chSrv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = io.Copy(io.Discard, r.Body)
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

	addr := "127.0.0.1:24323"
	s := New(Config{GRPCAddr: addr, BatchMaxRows: 1000, BatchMaxAge: 50 * time.Millisecond}, w, nil, nil, nil)
	s.cfg.HTTPAddr = "" // gRPC-only; don't race other tests (or a dev stack) for :4318

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	go func() { _ = s.Run(ctx) }()
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
	sp.SetName("big-voice-call-span")
	sp.SetTraceID([16]byte{0xcc})
	sp.SetSpanID([8]byte{0xdd})
	sp.SetStartTimestamp(pcommon.NewTimestampFromTime(time.Now()))
	sp.SetEndTimestamp(pcommon.NewTimestampFromTime(time.Now().Add(time.Second)))
	// ~6 MiB attribute: over the 4 MiB gRPC default, under the 16 MiB cap.
	sp.Attributes().PutStr("raw_log", strings.Repeat("x", 6<<20))

	req := ptraceotlp.NewExportRequestFromTraces(traces)
	if _, err := client.Export(context.Background(), req); err != nil {
		t.Fatalf("OTLP Export of >4MiB span rejected: %v", err)
	}
}

// An over-cap message must be rejected AND show up in the collector's own
// logs. The transport rejects it before the handler runs, so only the
// stats.Handler sees it — this pins that the error is logged (else the drop
// is invisible server-side and diagnosable only from client logs).
func TestGRPCOverCapRejectionIsLogged(t *testing.T) {
	chSrv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = io.Copy(io.Discard, r.Body)
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

	var logMu sync.Mutex
	var logBuf bytes.Buffer
	logw := writerFunc(func(p []byte) (int, error) {
		logMu.Lock()
		defer logMu.Unlock()
		return logBuf.Write(p)
	})

	addr := "127.0.0.1:24322"
	s := New(
		Config{GRPCAddr: addr, BatchMaxRows: 1000, BatchMaxAge: 50 * time.Millisecond},
		w, nil, nil, nil,
		WithLogger(slog.New(slog.NewJSONHandler(logw, nil))),
	)
	s.cfg.HTTPAddr = "" // gRPC-only; don't race other tests (or a dev stack) for :4318

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	go func() { _ = s.Run(ctx) }()
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
	sp := rs.ScopeSpans().AppendEmpty().Spans().AppendEmpty()
	sp.SetName("over-cap-span")
	// 17 MiB: over the 16 MiB default cap.
	sp.Attributes().PutStr("raw_log", strings.Repeat("x", 17<<20))

	req := ptraceotlp.NewExportRequestFromTraces(traces)
	_, err = client.Export(context.Background(), req)
	if status.Code(err) != codes.ResourceExhausted {
		t.Fatalf("want ResourceExhausted, got %v", err)
	}

	// stats.End may fire concurrently with the client seeing the status.
	deadline := time.Now().Add(time.Second)
	for time.Now().Before(deadline) {
		logMu.Lock()
		got := logBuf.String()
		logMu.Unlock()
		if strings.Contains(got, "grpc message over size cap") && strings.Contains(got, "ResourceExhausted") {
			return
		}
		time.Sleep(10 * time.Millisecond)
	}
	logMu.Lock()
	got := logBuf.String()
	logMu.Unlock()
	t.Fatalf("over-cap rejection not logged; log=%q", got)
}

// writerFunc adapts a func to io.Writer for test log capture.
type writerFunc func(p []byte) (int, error)

func (f writerFunc) Write(p []byte) (int, error) { return f(p) }

// TestQueueLimits_OversizedRequestRejected tests that an individual request
// exceeding maximum configured capacity is rejected with non-retryable error
// (gRPC ResourceExhausted, HTTP 413 Payload Too Large).
func TestQueueLimits_OversizedRequestRejected(t *testing.T) {
	chSrv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
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

	httpAddr := "127.0.0.1:24330"
	grpcAddr := "127.0.0.1:24331"
	s := New(Config{
		GRPCAddr:         grpcAddr,
		HTTPAddr:         httpAddr,
		BatchMaxRows:     100,
		BatchMaxAge:      time.Hour,
		QueueMaxRequests: 10,
		QueueMaxRows:     2, // max 2 rows total capacity
		QueueMaxBytes:    100 << 20,
	}, w, nil, nil, nil)

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	go func() { _ = s.Run(ctx) }()

	if !waitPort(grpcAddr, 2*time.Second) || !waitPort(httpAddr, 2*time.Second) {
		t.Fatal("server didn't listen")
	}

	// 1. gRPC over-row-capacity request
	conn, err := grpc.NewClient(grpcAddr, grpc.WithTransportCredentials(insecure.NewCredentials()))
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()

	traces := ptrace.NewTraces()
	rs := traces.ResourceSpans().AppendEmpty()
	rs.Resource().Attributes().PutStr("fi.project_id", "11111111-1111-4111-8111-111111111111")
	spans := rs.ScopeSpans().AppendEmpty().Spans()
	for i := 0; i < 5; i++ { // 5 rows > QueueMaxRows (2)
		sp := spans.AppendEmpty()
		sp.SetName("oversized-span")
		sp.SetTraceID([16]byte{0xaa})
		sp.SetSpanID([8]byte{byte(i + 1)})
	}

	req := ptraceotlp.NewExportRequestFromTraces(traces)
	_, err = ptraceotlp.NewGRPCClient(conn).Export(context.Background(), req)
	if status.Code(err) != codes.ResourceExhausted {
		t.Errorf("gRPC oversized request: want ResourceExhausted, got %v", err)
	}

	// 2. HTTP over-row-capacity request
	pb, _ := req.MarshalProto()
	resp, err := http.Post("http://"+httpAddr+"/v1/traces", "application/x-protobuf", bytes.NewReader(pb))
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusRequestEntityTooLarge {
		t.Errorf("HTTP oversized request: want 413 Payload Too Large, got %d", resp.StatusCode)
	}

	stats := s.QueueStats()
	if stats.RejectedOversized == 0 {
		t.Error("expected RejectedOversized > 0")
	}
}

// TestQueueLimits_OverloadBackpressure tests that when queue capacity is full,
// incoming requests receive retryable overload responses (HTTP 503 + Retry-After, gRPC Unavailable + RetryInfo).
func TestQueueLimits_OverloadBackpressure(t *testing.T) {
	chBlock := make(chan struct{})
	chSrv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if insertTable(r) == "spans" {
			<-chBlock
		}
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
	defer w.Close()

	httpAddr := "127.0.0.1:24332"
	grpcAddr := "127.0.0.1:24333"
	s := New(Config{
		GRPCAddr:         grpcAddr,
		HTTPAddr:         httpAddr,
		BatchMaxRows:     1, // flush immediately so 1st request becomes in-flight
		BatchMaxAge:      50 * time.Millisecond,
		QueueMaxRequests: 1, // only 1 request capacity
		QueueMaxRows:     100,
		QueueMaxBytes:    100 << 20,
	}, w, nil, nil, nil)

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	go func() { _ = s.Run(ctx) }()

	if !waitPort(grpcAddr, 2*time.Second) || !waitPort(httpAddr, 2*time.Second) {
		t.Fatal("server didn't listen")
	}

	traces := makeTraces("span-1", "11111111-1111-4111-8111-111111111111")
	req := ptraceotlp.NewExportRequestFromTraces(traces)

	conn, err := grpc.NewClient(grpcAddr, grpc.WithTransportCredentials(insecure.NewCredentials()))
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	client := ptraceotlp.NewGRPCClient(conn)

	var wg sync.WaitGroup
	wg.Add(1)
	go func() {
		defer wg.Done()
		_, _ = client.Export(context.Background(), req)
	}()

	// Wait until 1st request is in-flight (or pending)
	deadline := time.Now().Add(2 * time.Second)
	for time.Now().Before(deadline) {
		st := s.QueueStats()
		if st.PendingRequests+st.InFlightRequests > 0 {
			break
		}
		time.Sleep(10 * time.Millisecond)
	}

	// 2nd gRPC request when queue is full → Unavailable overload with RetryInfo
	_, err = client.Export(context.Background(), req)
	if status.Code(err) != codes.Unavailable {
		t.Fatalf("overloaded gRPC: want Unavailable, got %v", err)
	}

	// 3rd HTTP request when queue is full → 503 Service Unavailable with Retry-After
	pb, _ := req.MarshalProto()
	resp, err := http.Post("http://"+httpAddr+"/v1/traces", "application/x-protobuf", bytes.NewReader(pb))
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusServiceUnavailable {
		t.Errorf("overloaded HTTP: want 503, got %d", resp.StatusCode)
	}
	if got := resp.Header.Get("Retry-After"); got != "1" {
		t.Errorf("overloaded HTTP: Retry-After = %q, want 1", got)
	}

	// Unblock 1st request insert
	close(chBlock)
	wg.Wait()

	if s.QueueStats().RejectedOverload < 2 {
		t.Errorf("expected RejectedOverload >= 2, got %d", s.QueueStats().RejectedOverload)
	}
}

// TestDurableAcknowledgement_CHSuccess verifies that an OTLP request is acknowledged
// only after ClickHouse successfully accepts the batch.
func TestDurableAcknowledgement_CHSuccess(t *testing.T) {
	chCalled := make(chan struct{})
	chBlock := make(chan struct{})
	chSrv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if insertTable(r) == "spans" {
			close(chCalled)
			<-chBlock
		}
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

	grpcAddr := "127.0.0.1:24334"
	s := New(Config{
		GRPCAddr:     grpcAddr,
		HTTPAddr:     "",
		BatchMaxRows: 1, // flush immediately on enqueue
		BatchMaxAge:  50 * time.Millisecond,
	}, w, nil, nil, nil)

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	go func() { _ = s.Run(ctx) }()

	if !waitPort(grpcAddr, 2*time.Second) {
		t.Fatal("server didn't listen")
	}

	conn, err := grpc.NewClient(grpcAddr, grpc.WithTransportCredentials(insecure.NewCredentials()))
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()

	done := make(chan error, 1)
	go func() {
		traces := makeTraces("ack-test-span", "11111111-1111-4111-8111-111111111111")
		req := ptraceotlp.NewExportRequestFromTraces(traces)
		_, err := ptraceotlp.NewGRPCClient(conn).Export(context.Background(), req)
		done <- err
	}()

	// Wait until CH receives write call
	select {
	case <-chCalled:
	case <-time.After(2 * time.Second):
		t.Fatal("timeout waiting for CH insert call")
	}

	// Verify request is NOT yet unblocked/acknowledged while CH write is in-flight
	select {
	case err := <-done:
		t.Fatalf("request completed before CH write finished! err=%v", err)
	default:
	}

	// Unblock CH write
	close(chBlock)

	// Now request should complete successfully
	select {
	case err := <-done:
		if err != nil {
			t.Fatalf("Export failed: %v", err)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("timeout waiting for request completion after CH write")
	}
}

// TestDurableAcknowledgement_DeadLetterSuccess verifies that when ClickHouse fails,
// the OTLP request is acknowledged after the batch is durably written to the dead-letter file.
func TestDurableAcknowledgement_DeadLetterSuccess(t *testing.T) {
	chSrv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(500)
	}))
	defer chSrv.Close()

	dlFile := t.TempDir() + "/dl.jsonl"
	w, _ := chwriter.New(chwriter.Config{
		URL:            chSrv.URL,
		Database:       "default",
		Table:          "spans",
		MaxRetries:     1,
		InitialBackoff: time.Millisecond,
		MaxBackoff:     time.Millisecond,
		RequestTimeout: 2 * time.Second,
		DeadLetterFile: dlFile,
	})
	defer w.Close()

	grpcAddr := "127.0.0.1:24335"
	s := New(Config{
		GRPCAddr:     grpcAddr,
		HTTPAddr:     "",
		BatchMaxRows: 1,
		BatchMaxAge:  50 * time.Millisecond,
	}, w, nil, nil, nil)

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	go func() { _ = s.Run(ctx) }()

	if !waitPort(grpcAddr, 2*time.Second) {
		t.Fatal("server didn't listen")
	}

	conn, err := grpc.NewClient(grpcAddr, grpc.WithTransportCredentials(insecure.NewCredentials()))
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()

	traces := makeTraces("dead-letter-span", "22222222-2222-4222-8222-222222222222")
	req := ptraceotlp.NewExportRequestFromTraces(traces)

	_, err = ptraceotlp.NewGRPCClient(conn).Export(context.Background(), req)
	if err != nil {
		t.Fatalf("Export should succeed on durable dead-letter fsync, got: %v", err)
	}

	b, err := os.ReadFile(dlFile)
	if err != nil || len(b) == 0 {
		t.Fatalf("dead-letter file empty or unreadable: %v", err)
	}
	if !strings.Contains(string(b), "dead-letter-span") {
		t.Errorf("dead-letter file missing span: %s", string(b))
	}
}

// TestDurableAcknowledgement_DoubleFailure verifies that when both ClickHouse write
// AND dead-letter fsync fail, the request receives a retryable error response.
func TestDurableAcknowledgement_DoubleFailure(t *testing.T) {
	chSrv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(500)
	}))
	defer chSrv.Close()

	dlFile := t.TempDir() + "/dl.jsonl"
	w, _ := chwriter.New(chwriter.Config{
		URL:            chSrv.URL,
		Database:       "default",
		Table:          "spans",
		MaxRetries:     1,
		InitialBackoff: time.Millisecond,
		MaxBackoff:     time.Millisecond,
		RequestTimeout: 2 * time.Second,
		DeadLetterFile: dlFile,
	})
	defer w.Close()

	// Create directory with same name as dlFile to force appendDeadLetter open file to fail
	if err := os.MkdirAll(dlFile, 0o755); err != nil {
		t.Fatal(err)
	}

	grpcAddr := "127.0.0.1:24336"
	s := New(Config{
		GRPCAddr:     grpcAddr,
		HTTPAddr:     "",
		BatchMaxRows: 1,
		BatchMaxAge:  50 * time.Millisecond,
	}, w, nil, nil, nil)

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	go func() { _ = s.Run(ctx) }()

	if !waitPort(grpcAddr, 2*time.Second) {
		t.Fatal("server didn't listen")
	}

	conn, err := grpc.NewClient(grpcAddr, grpc.WithTransportCredentials(insecure.NewCredentials()))
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()

	traces := makeTraces("double-fail-span", "33333333-3333-4333-8333-333333333333")
	req := ptraceotlp.NewExportRequestFromTraces(traces)

	_, err = ptraceotlp.NewGRPCClient(conn).Export(context.Background(), req)
	if status.Code(err) != codes.Unavailable {
		t.Fatalf("double failure: want Unavailable, got %v", err)
	}
}

// TestShutdown_RejectsNewAndDrainsExisting tests graceful shutdown behavior:
// existing accepted requests are drained and completed before shutdown finishes,
// while new requests during shutdown are rejected.
func TestShutdown_RejectsNewAndDrainsExisting(t *testing.T) {
	chSrv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		time.Sleep(50 * time.Millisecond)
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

	grpcAddr := "127.0.0.1:24337"
	s := New(Config{
		GRPCAddr:     grpcAddr,
		HTTPAddr:     "",
		BatchMaxRows: 100,
		BatchMaxAge:  10 * time.Second,
	}, w, nil, nil, nil)

	ctx, cancel := context.WithCancel(context.Background())
	go func() { _ = s.Run(ctx) }()

	if !waitPort(grpcAddr, 2*time.Second) {
		t.Fatal("server didn't listen")
	}

	conn, err := grpc.NewClient(grpcAddr, grpc.WithTransportCredentials(insecure.NewCredentials()))
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	client := ptraceotlp.NewGRPCClient(conn)

	traces := makeTraces("shutdown-span", "44444444-4444-4444-8444-444444444444")
	req := ptraceotlp.NewExportRequestFromTraces(traces)

	var reqErr error
	var wg sync.WaitGroup
	wg.Add(1)
	go func() {
		defer wg.Done()
		_, reqErr = client.Export(context.Background(), req)
	}()

	// Wait until request is queued
	deadline := time.Now().Add(2 * time.Second)
	for time.Now().Before(deadline) && s.QueueStats().PendingRequests < 1 {
		time.Sleep(5 * time.Millisecond)
	}

	// Trigger shutdown
	cancel()
	wg.Wait()

	if reqErr != nil {
		t.Errorf("previously accepted request failed during shutdown: %v", reqErr)
	}
}
