package server

import (
	"bytes"
	"context"
	"net"
	"net/http"
	"net/http/httptest"
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
	"google.golang.org/grpc/credentials/insecure"
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

	s.enqueue(r1, ids1)
	s.enqueue(r2a, ids2a)
	s.enqueue(r2b, ids2b)

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
