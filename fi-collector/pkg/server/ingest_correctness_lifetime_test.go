package server

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"runtime"
	"strings"
	"sync"
	"testing"
	"time"

	chexp "github.com/future-agi/future-agi/fi-collector/exporter/clickhouse25exporter"
	"github.com/future-agi/future-agi/fi-collector/pkg/observedcatalog"
	"github.com/future-agi/future-agi/fi-collector/pkg/traceavailable"
	"go.opentelemetry.io/collector/pdata/ptrace"
	"go.opentelemetry.io/collector/pdata/ptrace/ptraceotlp"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/metadata"
	"google.golang.org/grpc/status"
)

func TestCorrectnessPromotedPrecedenceAndOverwrites(t *testing.T) {
	long := strings.Repeat("\x01\"\\<\u2028", 1024)
	val, _ := json.Marshal(long)
	stringKV := func(k, v string) string {
		kJSON, _ := json.Marshal(k)
		vJSON, _ := json.Marshal(v)
		return `{"key":` + string(kJSON) + `,"value":{"stringValue":` + string(vJSON) + `}}`
	}
	for _, tc := range []struct{ name, attrs, model, provider, system, operation string }{
		{name: "preferred model", attrs: stringKV("gen_ai.response.model", long) + "," + stringKV("gen_ai.request.model", "request") + "," + stringKV("llm.model_name", "preferred"), model: "preferred"},
		{name: "empty model falls through", attrs: stringKV("llm.model_name", "") + "," + stringKV("gen_ai.request.model", long), model: long},
		{name: "preferred provider and raw system", attrs: stringKV("gen_ai.system", long) + "," + stringKV("llm.system", "fi") + "," + stringKV("gen_ai.provider.name", "canonical"), provider: "canonical", system: long},
		{name: "empty provider falls through", attrs: stringKV("gen_ai.provider.name", "") + "," + stringKV("llm.vendor", long) + "," + stringKV("llm.provider", "legacy"), provider: long},
		{name: "duplicate attribute replaces string", attrs: stringKV("llm.provider", long) + "," + stringKV("llm.provider", "last"), provider: "last"},
		{name: "duplicate attribute nonstring keeps typed string", attrs: stringKV("llm.provider", long) + `,{"key":"llm.provider","value":{"intValue":"1"}}`, provider: long},
		{name: "repeated key last wins", attrs: `{"key":"llm.model_name","key":"gen_ai.system","value":{"stringValue":` + string(val) + `}}`, provider: long, system: long},
		{name: "repeated mixed oneof last string", attrs: `{"key":"gen_ai.system","value":{"intValue":"1","stringValue":` + string(val) + `}}`, provider: long, system: long},
		{name: "repeated value empty merges", attrs: `{"key":"gen_ai.system","value":{"stringValue":` + string(val) + `},"value":{}}`, provider: long, system: long},
		{name: "repeated mixed oneof last number", attrs: `{"key":"gen_ai.system","value":{"stringValue":` + string(val) + `,"intValue":"1"}}`},
		{name: "operation independent of normalized kind", attrs: stringKV("gen_ai.operation.name", long) + "," + stringKV("fi.span.kind", "LLM"), operation: long},
	} {
		t.Run(tc.name, func(t *testing.T) {
			body := []byte(`{"resourceSpans":[{"scopeSpans":[{"spans":[{"attributes":[` + tc.attrs + `]}]}]}]}`)
			req := ptraceotlp.NewExportRequest()
			if err := req.UnmarshalJSON(body); err != nil {
				t.Fatal(err)
			}
			rows, err := chexp.Convert(req.Traces())
			if err != nil {
				t.Fatal(err)
			}
			for key, want := range map[string]string{"model": tc.model, "provider": tc.provider, "gen_ai_system": tc.system, "gen_ai_operation": tc.operation} {
				if got := rows[0][key]; got != want {
					t.Fatalf("converter %s mismatch", key)
				}
			}
			assertCorrectnessWirePrice(t, body, true)
			// Proto after the real JSON decoder still carries duplicate attributes;
			// repeated singular fields have already been resolved by that decoder.
			pb, err := req.MarshalProto()
			if err != nil {
				t.Fatal(err)
			}
			assertCorrectnessWirePrice(t, pb, false)
			// Remove promotions but preserve byte lengths/encoding and every value.
			plain := append([]byte(nil), body...)
			for _, key := range []string{"llm.model_name", "gen_ai.request.model", "gen_ai.response.model", "llm.request.model", "gen_ai.provider.name", "llm.system", "gen_ai.system", "llm.vendor", "llm.provider", "gen_ai.operation.name"} {
				plain = bytes.ReplaceAll(plain, []byte(`"`+key+`"`), []byte(`"~`+key[1:]+`"`))
			}
			scan, _ := scanOTLPJSON(body)
			base, _ := scanOTLPJSON(plain)
			expected := int64(0)
			for _, key := range []string{"model", "provider", "gen_ai_system", "gen_ai_operation"} {
				value := rows[0][key].(string)
				if value != "" {
					n, _ := jsonEscapedLen([]byte(value))
					expected += n + 2
				}
			}
			if delta := scan.rowJSONBytes - base.rowJSONBytes; delta != expected {
				t.Errorf("promotions price=%d want actual selected copies=%d", delta, expected)
			}
		})
	}
	// Repeated protobuf oneof/value/key fields must follow pdata merge semantics.
	for name, kv := range map[string][]byte{
		"string after int":             pbKV("gen_ai.system", pbCat(pbVarint(3, 1), pbField(1, []byte(long)))),
		"int after string":             pbKV("gen_ai.system", pbCat(pbField(1, []byte(long)), pbVarint(3, 1))),
		"empty value preserves string": pbCat(pbKV("gen_ai.system", pbField(1, []byte(long))), pbField(2, nil)),
		"final key":                    pbCat(pbKV("llm.model_name", pbField(1, []byte(long))), pbField(1, []byte("gen_ai.system"))),
	} {
		t.Run(name, func(t *testing.T) { assertCorrectnessWirePrice(t, pbOneSpan(pbField(9, kv)), false) })
	}
}

func TestCorrectnessEscapedZeroParent(t *testing.T) {
	for _, fields := range []string{
		`"parentSpanId":"0000000000000000"`,
		`"parentSpanId":"` + strings.Repeat(`\u0030`, 16) + `"`,
		`"parentSpanId":"0102030405060708","parent_span_id":"` + strings.Repeat(`\u0030`, 16) + `"`,
		`"parentSpanId":"0102030405060708","parent_span_id":null`,
		`"parentSpanId":"0000000000000000","parent_span_id":"0102030405060708"`,
	} {
		body := []byte(`{"resourceSpans":[{"scopeSpans":[{"spans":[{` + fields + `}]}]}]}`)
		req := ptraceotlp.NewExportRequest()
		if err := req.UnmarshalJSON(body); err != nil {
			t.Fatal(err)
		}
		shape, err := scanOTLPJSON(body)
		if err != nil {
			t.Fatal(err)
		}
		want := int64(0)
		if req.Traces().ResourceSpans().At(0).ScopeSpans().At(0).Spans().At(0).ParentSpanID().IsEmpty() {
			want = 1
		}
		if shape.rootSpans != want {
			t.Errorf("escaped/repeated parent scan=%d decoded=%d", shape.rootSpans, want)
		}
	}
}

func TestCorrectnessFinalShapeGRPCAndResourceBound(t *testing.T) {
	for _, resourceLimit := range []bool{false, true} {
		t.Run(fmt.Sprintf("resource-limit=%t", resourceLimit), func(t *testing.T) {
			td := calTraces(1, 1, 4, nil)
			attrs := td.ResourceSpans().At(0).Resource().Attributes()
			attrs.Clear()
			if resourceLimit {
				attrs.PutStr("fill", strings.Repeat("x", maxResourceAttrBytes-80))
			}
			body := calBody(t, td, false)
			shape, err := scanOTLPProto(body)
			if err != nil {
				t.Fatal(err)
			}
			a := realTestAuth(t, &ingestResolver{}, time.Minute)
			usage := &recordingUsage{}
			s, conn, _ := startGRPCServer(t, Config{}, func(s *Server) {
				attachRealAuth(s, a)
				s.usage = usage
				if !resourceLimit {
					s.limits.maxEncoded = shape.rowJSONBytes + 256
				}
			})
			ctx := metadata.AppendToOutgoingContext(withKeys(context.Background()), "x-project-name", strings.Repeat("a", 255))
			if err := exportRaw(conn, ctx, body); status.Code(err) != codes.InvalidArgument {
				t.Errorf("final shape must be permanent INVALID_ARGUMENT: %v", err)
			}
			s.pendMu.Lock()
			n := len(s.pend)
			s.pendMu.Unlock()
			if n != 0 || usage.calls.Load() != 0 {
				t.Errorf("rejected shape parked=%d billed=%d", n, usage.calls.Load())
			}
			if s.work.inUse.Load() != 0 || s.wire.inUse.Load() != 0 {
				t.Error("refusal retained leases")
			}
		})
	}
}

func TestCorrectnessFinalShapeGrowSharesWaitBudget(t *testing.T) {
	s := budgetServer(96)
	body := calBody(t, calTraces(1, 1, 1, nil), false)
	ctx := withLeaseBudget(context.Background(), 20*time.Millisecond)
	held, rej := s.leaseWork(ctx, body, false)
	if rej != nil {
		t.Fatal(rej)
	}
	defer held.work.release()
	defer held.rows.release()
	before := held.work.size() + held.rows.size()
	release := holdPool(t, s.work, s.work.size-before)
	req := ptraceotlp.NewExportRequest()
	if err := req.UnmarshalProto(body); err != nil {
		t.Fatal(err)
	}
	req.Traces().ResourceSpans().At(0).Resource().Attributes().PutStr("project_name", strings.Repeat("x", 255))
	rej = s.pricePreparedExport(ctx, req.Traces(), held)
	release()
	if rej == nil || rej.httpStatus() != 503 || rej.grpcCode() != codes.Unavailable {
		t.Fatalf("occupied pool must be retryable: %v", rej)
	}
	if held.work.size()+held.rows.size() != before {
		t.Error("failed grow changed reservations")
	}
	// A separate request budget succeeds, without altering the production wait.
	ctx = withLeaseBudget(context.Background(), s.limits.maxWait)
	if rej = s.pricePreparedExport(ctx, req.Traces(), held); rej != nil {
		t.Fatal(rej)
	}
	if held.work.size()+held.rows.size() <= before {
		t.Fatal("final resource additions were not reserved")
	}
}

type correctnessCatalog func([]observedcatalog.ScopedSpan) error

func (f correctnessCatalog) EnqueueCanonicalSpans(rows []observedcatalog.ScopedSpan) error {
	return f(rows)
}

func TestCorrectnessCancelledPartialDrainRetainsSidecarLease(t *testing.T) {
	second := make(chan struct{})
	catalogEntered := make(chan struct{})
	catalogRelease := make(chan struct{})
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		b, _ := io.ReadAll(r.Body)
		if bytes.Contains(b, []byte(`"id":"cancel"`)) {
			close(second)
			<-r.Context().Done()
			return
		}
		w.WriteHeader(200)
	}))
	defer srv.Close()
	writer := newSpanTestWriter(t, srv.URL, t.TempDir()+"/dl.jsonl")
	defer writer.Close()
	var notified []traceavailable.Root
	s := New(Config{}, writer, nil, nil, nil, WithTraceNotifier(handoffRootNotifier(func(roots []traceavailable.Root) error { notified = append(notified, roots...); return nil })), WithPropertyCatalogWriter(correctnessCatalog(func(rows []observedcatalog.ScopedSpan) error {
		if len(rows) != 1 || rows[0].Row["id"] != "confirmed" {
			t.Error("cancelled or unsent row handed off")
		}
		close(catalogEntered)
		<-catalogRelease
		return errors.New("durable spool unavailable")
	})))
	writer.SetMaxInsertBytes(1)
	writer.SetMaxConcurrentInserts(1)
	for _, id := range []string{"confirmed", "cancel", "unsent"} {
		s.enqueueScoped([]map[string]any{{"id": id, "trace_id": "44444444-4444-4444-8444-444444444444", "parent_span_id": "", "org_id": calOrg, "project_id": calProject, "start_time": "2026-10-27 01:00:00.000000", "end_time": "2026-10-27 01:00:01.000000"}}, nil, calOrg, calWS, map[string]struct{}{calProject: {}})
	}
	leased, err := s.work.acquire(context.Background(), 1<<20)
	if err != nil {
		t.Fatal(err)
	}
	s.adoptRows(leased)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	done := make(chan struct{})
	go func() { defer close(done); s.drainNow(ctx) }()
	select {
	case <-second:
	case <-time.After(5 * time.Second):
		t.Fatal("second canonical chunk did not start")
	}
	cancel()
	select {
	case <-catalogEntered:
	case <-time.After(5 * time.Second):
		t.Fatal("confirmed sibling lost after cancellation")
	}
	// Shutdown cannot declare the drain finished or return its bytes while the
	// synchronous handoff still owns the map, even though its request was cancelled.
	close(s.stopCh)
	if s.work.inUse.Load() != 1<<20 || s.draining.Load() != 1 {
		t.Fatal("row budget returned while catalog still owns the row")
	}
	select {
	case <-done:
		t.Fatal("drain ended before handoff released its source")
	default:
	}
	close(catalogRelease)
	<-done
	if len(notified) != 1 || notified[0].SpanID != "confirmed" {
		t.Fatal("wrong confirmed root notifications")
	}
	if s.work.inUse.Load() != 0 || s.draining.Load() != 0 {
		t.Fatal("completed drain did not release its lease")
	}
	if stats := writer.Snapshot(); stats.RowsInserted != 1 || stats.RowsDeadLettered != 2 {
		t.Fatalf("partial cancellation outcomes: %+v", stats)
	}
}

func TestCorrectnessPromotedRetainedAndEncodingHeap(t *testing.T) {
	// A provider is stored three times, including heavy JSON escaping. The
	// transport barrier retains its encoded body while we measure live heap.
	// No request/body sampling from another process and no vendor calls.
	for _, root := range []bool{false, true} {
		t.Run(fmt.Sprintf("root=%t", root), func(t *testing.T) {
			td := calTraces(1, 1, 1, func(_ ptrace.ResourceSpans, sp ptrace.Span, _ int) {
				if !root {
					sp.SetParentSpanID([8]byte{1})
				}
				sp.Attributes().PutStr("gen_ai.system", strings.Repeat("\x01\"\\<", calBig/4))
				sp.Attributes().PutStr("input.value", strings.Repeat("\x02\"<", 64<<10))
			})
			body := calBody(t, td, false)
			td = ptrace.Traces{}
			shape, err := scanOTLPProto(body)
			if err != nil {
				t.Fatal(err)
			}
			p := shape.price()
			base := liveHeap()
			req := ptraceotlp.NewExportRequest()
			if err = req.UnmarshalProto(body); err != nil {
				t.Fatal(err)
			}
			rows, ids, err := chexp.ConvertWithIdentities(context.Background(), req.Traces(), nil)
			if err != nil {
				t.Fatal(err)
			}
			entered, release := make(chan struct{}), make(chan struct{})
			old := http.DefaultTransport
			defer func() { http.DefaultTransport = old }()
			var once sync.Once
			http.DefaultTransport = handoffRoundTripper(func(r *http.Request) (*http.Response, error) {
				if insertTable(r) == "spans" {
					once.Do(func() { close(entered); <-release })
				}
				_, err := io.Copy(io.Discard, r.Body)
				r.Body.Close()
				return &http.Response{StatusCode: 200, Header: make(http.Header), Body: io.NopCloser(strings.NewReader("")), ContentLength: 0, Request: r}, err
			})
			writer := newSpanTestWriter(t, "http://offline.invalid", t.TempDir()+"/dl")
			defer writer.Close()
			s := New(Config{}, writer, nil, nil, nil)
			s.enqueue(rows, ids)
			done := make(chan struct{})
			go func() { defer close(done); s.drainNow(context.Background()) }()
			<-entered
			held := liveHeap() - base
			allowance := p.total() + 2*s.limits.insertMax*int64(s.limits.inserts)
			t.Logf("active encoded body held heap=%d reservation=%d fixed encoding allowance=%d", held, p.total(), allowance-p.total())
			close(release)
			<-done
			runtime.KeepAlive(req)
			runtime.KeepAlive(body)
			runtime.KeepAlive(rows)
			runtime.KeepAlive(ids)
			if held > allowance {
				t.Errorf("active encoding heap %d exceeds priced+fixed %d", held, allowance)
			}
		})
	}
}
