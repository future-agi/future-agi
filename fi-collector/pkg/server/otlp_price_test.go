package server

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"runtime"
	"runtime/metrics"
	"strings"
	"sync"
	"testing"
	"time"

	chexp "github.com/future-agi/future-agi/fi-collector/exporter/clickhouse25exporter"
	"github.com/future-agi/future-agi/fi-collector/pkg/chwriter"
	"github.com/future-agi/future-agi/fi-collector/pkg/observedcatalog"
	"github.com/future-agi/future-agi/fi-collector/pkg/traceavailable"
	"go.opentelemetry.io/collector/pdata/pcommon"
	"go.opentelemetry.io/collector/pdata/ptrace"
	"go.opentelemetry.io/collector/pdata/ptrace/ptraceotlp"
)

// Calibration: the price of a request (otlp_price.go) must cover the heap it
// holds, for every shape of request, and the JSON byte counts of the scan
// must cover the JSON a drain writes. These tests decode, convert and park
// real requests, and measure.

const (
	calProject = "11111111-2222-4333-8444-555555555555"
	calOrg     = "66666666-7777-4888-8999-aaaaaaaaaaaa"
	calWS      = "bbbbbbbb-cccc-4ddd-8eee-ffffffffffff"
)

// calEncodings are the body encodings the calibration runs: under the race
// detector only protobuf (the JSON scan is held to pdata by
// TestScanCoversRandomRequests), as JSON decoding is many times slower there.
var calEncodings = map[bool][]bool{false: {false, true}, true: {false}}[raceEnabled]

// calN is just past a power of two, where appended slices carry the most
// spare capacity (smaller under the race detector).
var calN = map[bool]int{false: 8193, true: 1025}[raceEnabled]

// calBig is the size of the large-string shapes.
var calBig = map[bool]int{false: 4 << 20, true: 1 << 20}[raceEnabled]

func calTraces(rsN, ssN, spanN int, fill func(rs ptrace.ResourceSpans, sp ptrace.Span, i int)) ptrace.Traces {
	td := ptrace.NewTraces()
	for r := 0; r < rsN; r++ {
		rs := td.ResourceSpans().AppendEmpty()
		rs.Resource().Attributes().PutStr("fi.project_id", calProject)
		rs.Resource().Attributes().PutStr("fi.org_id", calOrg)
		rs.Resource().Attributes().PutStr("project_type", "observe")
		for s := 0; s < ssN; s++ {
			ss := rs.ScopeSpans().AppendEmpty()
			for i := 0; i < spanN; i++ {
				sp := ss.Spans().AppendEmpty()
				sp.SetTraceID(pcommon.TraceID{1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, byte(i)})
				sp.SetSpanID(pcommon.SpanID{1, 2, 3, 4, 5, 6, byte(i >> 8), byte(i)})
				if i%8 != 0 {
					sp.SetParentSpanID(pcommon.SpanID{9, 9, 9, 9, 9, 9, 9, 9})
				}
				sp.SetStartTimestamp(pcommon.Timestamp(1_700_000_000_000_000_000 + int64(i)))
				sp.SetEndTimestamp(pcommon.Timestamp(1_700_000_001_000_000_000 + int64(i)))
				if fill != nil {
					fill(rs, sp, i)
				}
			}
		}
	}
	return td
}

// llmSpan fills a span the way an instrumented LLM call looks: model and
// token attributes, a session and user, and input and output values of
// about inLen and outLen bytes.
func llmSpan(inLen, outLen int) func(rs ptrace.ResourceSpans, sp ptrace.Span, i int) {
	in := strings.Repeat("Explain the \"plan\" step by step.\n", inLen/33+1)[:inLen]
	out := strings.Repeat("Sure <b>here</b> is the plan: 1) do X & Y.\t", outLen/44+1)[:outLen]
	return func(rs ptrace.ResourceSpans, sp ptrace.Span, i int) {
		sp.SetName(fmt.Sprintf("ChatCompletion %d", i%7))
		a := sp.Attributes()
		a.PutStr("fi.span.kind", "LLM")
		a.PutStr("llm.model_name", "gpt-4o-mini")
		a.PutStr("llm.provider", "openai")
		a.PutInt("llm.token_count.prompt", 812)
		a.PutInt("llm.token_count.completion", 213)
		a.PutInt("llm.token_count.total", 1025)
		a.PutStr("session.id", fmt.Sprintf("session-%d", i%50))
		a.PutStr("user.id", fmt.Sprintf("user-%d", i%20))
		a.PutStr("input.value", in)
		a.PutStr("input.mime_type", "text/plain")
		a.PutStr("output.value", out)
		a.PutStr("output.mime_type", "text/plain")
		a.PutStr("llm.input_messages.0.message.role", "user")
		a.PutStr("llm.input_messages.0.message.content", in[:min(len(in), 200)])
		a.PutDouble("llm.invocation_parameters.temperature", 0.2)
		if i == 0 {
			r := rs.Resource().Attributes()
			r.PutStr("service.name", "checkout-agent")
			r.PutStr("telemetry.sdk.language", "python")
			r.PutStr("telemetry.sdk.name", "opentelemetry")
			r.PutStr("telemetry.sdk.version", "1.27.0")
			r.PutStr("project_name", "checkout")
		}
		sp.Status().SetCode(ptrace.StatusCodeOk)
	}
}

type calShape struct {
	name string
	td   func() ptrace.Traces
}

func calShapes() []calShape {
	big := strings.Repeat("x\x01\"<", calBig/4) // every escape kind
	return []calShape{
		{"empty ResourceSpans", func() ptrace.Traces {
			td := ptrace.NewTraces()
			for i := 0; i < calN; i++ {
				td.ResourceSpans().AppendEmpty()
			}
			return td
		}},
		{"empty ScopeSpans", func() ptrace.Traces {
			td := ptrace.NewTraces()
			rs := td.ResourceSpans().AppendEmpty()
			for i := 0; i < calN; i++ {
				rs.ScopeSpans().AppendEmpty()
			}
			return td
		}},
		{"empty spans", func() ptrace.Traces {
			td := ptrace.NewTraces()
			ss := td.ResourceSpans().AppendEmpty().ScopeSpans().AppendEmpty()
			for i := 0; i < calN; i++ {
				ss.Spans().AppendEmpty()
			}
			return td
		}},
		{"spans with ids and times", func() ptrace.Traces { return calTraces(1, 1, calN, nil) }},
		{"spans x 9 int attributes", func() ptrace.Traces {
			return calTraces(1, 1, 1025, func(_ ptrace.ResourceSpans, sp ptrace.Span, i int) {
				for k := 0; k < 9; k++ {
					sp.Attributes().PutInt(fmt.Sprintf("k%d", k), int64(i))
				}
			})
		}},
		{"spans x 17 string attributes", func() ptrace.Traces {
			return calTraces(1, 1, 1025, func(_ ptrace.ResourceSpans, sp ptrace.Span, i int) {
				for k := 0; k < 17; k++ {
					sp.Attributes().PutStr(fmt.Sprintf("key-%d", k), fmt.Sprintf("value-%d-%d", k, i))
				}
			})
		}},
		{"empty attributes", func() ptrace.Traces {
			return calTraces(1, 1, 1, func(_ ptrace.ResourceSpans, sp ptrace.Span, _ int) {
				raw := make(map[string]any, calN*8)
				for k := 0; k < calN*8; k++ {
					raw[fmt.Sprintf("%d", k)] = nil
				}
				_ = sp.Attributes().FromRaw(raw)
			})
		}},
		{"empty events", func() ptrace.Traces {
			return calTraces(1, 1, 1, func(_ ptrace.ResourceSpans, sp ptrace.Span, _ int) {
				for k := 0; k < calN; k++ {
					sp.Events().AppendEmpty()
				}
			})
		}},
		{"empty links", func() ptrace.Traces {
			return calTraces(1, 1, 1, func(_ ptrace.ResourceSpans, sp ptrace.Span, _ int) {
				for k := 0; k < calN; k++ {
					sp.Links().AppendEmpty()
				}
			})
		}},
		{"kvlist of 8 x calN", func() ptrace.Traces {
			return calTraces(1, 1, 1, func(_ ptrace.ResourceSpans, sp ptrace.Span, _ int) {
				raw := make(map[string]any, 8*calN)
				for k := 0; k < 8*calN; k++ {
					raw[fmt.Sprintf("%d", k)] = 1
				}
				_ = sp.Attributes().PutEmptyMap("m").FromRaw(raw)
			})
		}},
		{"array of 8 x calN", func() ptrace.Traces {
			return calTraces(1, 1, 1, func(_ ptrace.ResourceSpans, sp ptrace.Span, _ int) {
				a := sp.Attributes().PutEmptySlice("a")
				for k := 0; k < 8*calN; k++ {
					a.AppendEmpty().SetInt(int64(1000 + k)) // boxed: no static small-int value
				}
			})
		}},
		{"1025 resources x 9 attributes, a span each", func() ptrace.Traces {
			return calTraces(1025, 1, 1, func(rs ptrace.ResourceSpans, _ ptrace.Span, _ int) {
				for k := 0; k < 9; k++ {
					rs.Resource().Attributes().PutStr(fmt.Sprintf("resource.key.%d", k), fmt.Sprintf("value %d", k))
				}
			})
		}},
		{"root spans x 33 events with attributes", func() ptrace.Traces {
			return calTraces(1, 1, 257, func(_ ptrace.ResourceSpans, sp ptrace.Span, i int) {
				sp.SetParentSpanID(pcommon.SpanID{})
				sp.SetName(fmt.Sprintf("root %d", i))
			})
		}},
		{"input.value kvlist of 2 x calN strings", func() ptrace.Traces {
			return calTraces(1, 1, 1, func(_ ptrace.ResourceSpans, sp ptrace.Span, _ int) {
				raw := make(map[string]any, 2*calN)
				for k := 0; k < 2*calN; k++ {
					raw[fmt.Sprintf("%d", k)] = "<&>"
				}
				_ = sp.Attributes().PutEmptyMap("input.value").FromRaw(raw)
			})
		}},
		{"nesting at the limit", func() ptrace.Traces {
			return calTraces(1, 1, calN/64, func(_ ptrace.ResourceSpans, sp ptrace.Span, _ int) {
				v := sp.Attributes().PutEmpty("deep")
				for d := 0; d < maxValueNesting; d++ {
					v = v.SetEmptySlice().AppendEmpty()
				}
				v.SetStr("leaf")
			})
		}},
		{"one large string", func() ptrace.Traces {
			return calTraces(1, 1, 1, func(_ ptrace.ResourceSpans, sp ptrace.Span, _ int) {
				sp.Attributes().PutStr("s", big)
			})
		}},
		{"one large input.value", func() ptrace.Traces {
			return calTraces(1, 1, 1, func(_ ptrace.ResourceSpans, sp ptrace.Span, _ int) {
				sp.Attributes().PutStr("input.value", big)
			})
		}},
		{"one large bytes value", func() ptrace.Traces {
			return calTraces(1, 1, 1, func(_ ptrace.ResourceSpans, sp ptrace.Span, _ int) {
				sp.Attributes().PutEmptyBytes("b").FromRaw([]byte(big))
			})
		}},
		{"64 KiB resource x calN/32 spans", func() ptrace.Traces {
			return calTraces(1, 1, calN/32+1, func(rs ptrace.ResourceSpans, _ ptrace.Span, i int) {
				if i == 0 {
					rs.Resource().Attributes().PutStr("big", strings.Repeat("\x01", maxResourceAttrBytes-200))
				}
			})
		}},
		{"513 LLM spans (SDK batch)", func() ptrace.Traces { return calTraces(1, 1, 513, llmSpan(2000, 800)) }},
		{"calN LLM spans (Collector batch)", func() ptrace.Traces { return calTraces(4, 2, calN/8, llmSpan(900, 300)) }},
		{"events with names and attributes", func() ptrace.Traces {
			return calTraces(1, 1, 257, func(_ ptrace.ResourceSpans, sp ptrace.Span, i int) {
				for k := 0; k < 33; k++ {
					ev := sp.Events().AppendEmpty()
					ev.SetName("exception\x01\"")
					ev.Attributes().PutStr("exception.message", "boom")
				}
			})
		}},
	}
}

var calHold struct {
	req ptraceotlp.ExportRequest
	ids [][16]byte
}

func liveHeap() int64 {
	runtime.GC()
	runtime.GC()
	var m runtime.MemStats
	runtime.ReadMemStats(&m)
	return int64(m.HeapAlloc)
}

type nopRoots struct{}

func (nopRoots) EnqueueRoots([]traceavailable.Root) error { return nil }

type nopCatalog struct{}

func (nopCatalog) EnqueueCanonicalSpans([]observedcatalog.ScopedSpan) error { return nil }

// heldByRequest decodes, converts and parks one request as the handler does,
// and returns the heap held by the decoded request and by what is parked.
func heldByRequest(t *testing.T, body []byte, jsonBody bool) (handler, rows int64, s *Server) {
	t.Helper()
	s = New(Config{BatchMaxRows: 1 << 30, BatchMaxAge: time.Hour}, nil, nil, nil, nil,
		WithTraceNotifier(nopRoots{}), WithPropertyCatalogWriter(nopCatalog{}))
	base := liveHeap()
	calHold.req = ptraceotlp.NewExportRequest()
	var err error
	if jsonBody {
		err = calHold.req.UnmarshalJSON(body)
	} else {
		err = calHold.req.UnmarshalProto(body)
	}
	if err != nil {
		t.Fatalf("decode: %v", err)
	}
	r, ids, err := chexp.ConvertWithIdentities(context.Background(), calHold.req.Traces(), nil)
	if err != nil {
		t.Fatalf("convert: %v", err)
	}
	s.enqueueScoped(r, ids, calOrg, calWS, map[string]struct{}{calProject: {}})
	calHold.ids = distinctTraceIDs(calHold.req.Traces())
	withReq := liveHeap()
	calHold.req, calHold.ids = ptraceotlp.ExportRequest{}, nil
	parked := liveHeap()
	runtime.KeepAlive(body)
	return withReq - parked, parked - base, s
}

func calBody(t *testing.T, td ptrace.Traces, jsonBody bool) []byte {
	t.Helper()
	req := ptraceotlp.NewExportRequestFromTraces(td)
	var b []byte
	var err error
	if jsonBody {
		b, err = req.MarshalJSON()
	} else {
		b, err = req.MarshalProto()
	}
	if err != nil {
		t.Fatal(err)
	}
	return b
}

func scanBody(body []byte, jsonBody bool) (otlpShape, error) {
	if jsonBody {
		return scanOTLPJSON(body)
	}
	return scanOTLPProto(body)
}

// TestPriceCoversHeldHeap is the calibration: for each shape, the heap the
// decoded request holds must be within its handler price, and the heap the
// parked rows hold within its rows price (less the encode share, which is
// checked against real JSON in TestPriceCoversRowJSON).
func TestPriceCoversHeldHeap(t *testing.T) {
	if testing.Short() {
		t.Skip("measures the heap; slow")
	}
	for _, sh := range calShapes() {
		for _, jsonBody := range calEncodings {
			name := sh.name
			if jsonBody {
				name += " (JSON)"
			}
			t.Run(name, func(t *testing.T) {
				body := calBody(t, sh.td(), jsonBody)
				shape, err := scanBody(body, jsonBody)
				if err != nil {
					t.Fatalf("scan: %v", err)
				}
				p := shape.price()
				handler, rows, _ := heldByRequest(t, body, jsonBody)
				encodeShare := 2*shape.maxRowJSON + 2*shape.curatedJSONBytes
				t.Logf("body %d B; handler held %d priced %d (x%.2f); rows held %d priced %d (x%.2f, %d of it encoding)",
					len(body), handler, p.handler, ratio(p.handler, handler), rows, p.rows-encodeShare, ratio(p.rows-encodeShare, rows), encodeShare)
				if handler > p.handler {
					t.Errorf("the decoded request holds %d bytes, over its handler price %d", handler, p.handler)
				}
				if rows > p.rows-encodeShare {
					t.Errorf("the parked rows hold %d bytes, over their rows price %d", rows, p.rows-encodeShare)
				}
			})
		}
	}
}

func ratio(a, b int64) float64 {
	if b <= 0 {
		return 0
	}
	return float64(a) / float64(b)
}

// chBodies is a stub ClickHouse that records every insert body by table.
type chBodies struct {
	mu     sync.Mutex
	tables map[string][][]byte
}

func newCHBodies(t *testing.T) (*chBodies, *chwriter.Writer) {
	t.Helper()
	c := &chBodies{tables: map[string][][]byte{}}
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		b, _ := io.ReadAll(r.Body)
		c.mu.Lock()
		c.tables[insertTable(r)] = append(c.tables[insertTable(r)], b)
		c.mu.Unlock()
		w.WriteHeader(200)
	}))
	t.Cleanup(srv.Close)
	w, err := chwriter.New(chwriter.Config{URL: srv.URL, MaxRetries: 1, InitialBackoff: time.Millisecond,
		MaxBackoff: time.Millisecond, RequestTimeout: 5 * time.Second, DeadLetterFile: t.TempDir() + "/dl.jsonl"})
	if err != nil {
		t.Fatal(err)
	}
	return c, w
}

// lines returns the total bytes and the longest line of every body sent for
// a table.
func (c *chBodies) lines(table string) (total, longest int64) {
	c.mu.Lock()
	defer c.mu.Unlock()
	for _, b := range c.tables[table] {
		sc := bufio.NewScanner(bytes.NewReader(b))
		sc.Buffer(nil, 1<<30)
		for sc.Scan() {
			n := int64(len(sc.Bytes())) + 1
			total += n
			longest = max(longest, n)
		}
	}
	return total, longest
}

// TestPriceCoversRowJSON checks the scan's JSON counts against what a drain
// actually sends: the rows' total and largest row, and the curated rows.
func TestPriceCoversRowJSON(t *testing.T) {
	for _, sh := range calShapes() {
		for _, jsonBody := range calEncodings {
			name := sh.name
			if jsonBody {
				name += " (JSON)"
			}
			t.Run(name, func(t *testing.T) {
				body := calBody(t, sh.td(), jsonBody)
				shape, err := scanBody(body, jsonBody)
				if err != nil {
					t.Fatalf("scan: %v", err)
				}
				ch, w := newCHBodies(t)
				s := New(Config{BatchMaxRows: 1 << 30, BatchMaxAge: time.Hour}, w, nil, nil, nil)
				req := ptraceotlp.NewExportRequest()
				if jsonBody {
					err = req.UnmarshalJSON(body)
				} else {
					err = req.UnmarshalProto(body)
				}
				if err != nil {
					t.Fatal(err)
				}
				rows, ids, err := chexp.ConvertWithIdentities(context.Background(), req.Traces(), nil)
				if err != nil {
					t.Fatal(err)
				}
				s.enqueue(rows, ids)
				s.drainNow(context.Background())
				total, longest := ch.lines("spans")
				var curated int64
				for _, table := range []string{"end_users", "trace_sessions", "traces"} {
					n, _ := ch.lines(table)
					curated += n
				}
				t.Logf("rows JSON %d priced %d (x%.2f); largest row %d priced %d (x%.2f); curated %d priced %d",
					total, shape.rowJSONBytes, ratio(shape.rowJSONBytes, total), longest, shape.maxRowJSON,
					ratio(shape.maxRowJSON, longest), curated, shape.curatedJSONBytes)
				if total > shape.rowJSONBytes {
					t.Errorf("rows wrote %d JSON bytes, over the scan's %d", total, shape.rowJSONBytes)
				}
				if longest > shape.maxRowJSON {
					t.Errorf("largest row is %d JSON bytes, over the scan's %d", longest, shape.maxRowJSON)
				}
				if curated > shape.curatedJSONBytes {
					t.Errorf("curated rows wrote %d JSON bytes, over the scan's %d", curated, shape.curatedJSONBytes)
				}
			})
		}
	}
}

// TestRowJSONOverheadCoversAnEmptySpan pins rowFixedJSON: the JSON of a row
// whose span sets nothing the scan measures.
func TestRowJSONOverheadCoversAnEmptySpan(t *testing.T) {
	td := calTraces(1, 1, 1, func(_ ptrace.ResourceSpans, sp ptrace.Span, _ int) {
		sp.Status().SetCode(ptrace.StatusCodeError)
	})
	td.ResourceSpans().At(0).Resource().Attributes().Clear()
	rows, _, err := chexp.ConvertWithIdentities(context.Background(), td, nil)
	if err != nil {
		t.Fatal(err)
	}
	b, err := json.Marshal(rows[0])
	if err != nil {
		t.Fatal(err)
	}
	t.Logf("empty row: %d JSON bytes", len(b))
	if len(b)+1 > rowFixedJSON {
		t.Errorf("an empty row is %d JSON bytes, over rowFixedJSON %d", len(b)+1, rowFixedJSON)
	}
}

// pdata's JSON decoder allocates and drops strings the decoded request does
// not keep: every object member name, known or not, and for a string with
// escapes the buffer it is unescaped into. The price (less the drain's
// encode share) covers what decoding allocates in all, for bodies where
// those dominate.
func TestPriceCoversJSONDecodeAllocations(t *testing.T) {
	long := strings.Repeat("n", 64<<10)
	esc := strings.Repeat(`\u0041`, 16<<10) // 96 KiB escaped, 16 KiB decoded
	cases := map[string]string{
		"long unknown member names": `{"resourceSpans":[{"scopeSpans":[{"spans":[` +
			strings.TrimSuffix(strings.Repeat(`{"name":"s","`+long+`":1},`, 64), ",") + `]}]}]}`,
		"long names in a skipped object": `{"resourceSpans":[],"x":{` +
			strings.TrimSuffix(strings.Repeat(`"`+long+`":[{"`+long+`":null}],`, 32), ",") + `}}`,
		"escaped member names": `{"resourceSpans":[{"scopeSpans":[{"spans":[` +
			strings.TrimSuffix(strings.Repeat(`{"name":"s","`+esc+`":1},`, 64), ",") + `]}]}]}`,
		"escaped values": `{"resourceSpans":[{"scopeSpans":[{"spans":[` +
			strings.TrimSuffix(strings.Repeat(`{"name":"`+esc+`","attributes":[{"key":"`+esc+`","value":{"stringValue":"`+esc+`"}}]},`, 32), ",") + `]}]}]}`,
	}
	for name, body := range cases {
		t.Run(name, func(t *testing.T) {
			shape, err := scanOTLPJSON([]byte(body))
			if err != nil {
				t.Fatalf("scan: %v", err)
			}
			p := shape.price()
			p.handler += p.rows - 2*shape.maxRowJSON - 2*shape.curatedJSONBytes // the strings pdata keeps are priced with the rows
			allocs := []metrics.Sample{{Name: "/gc/heap/allocs:bytes"}}
			runtime.GC()
			metrics.Read(allocs)
			before := allocs[0].Value.Uint64()
			req := ptraceotlp.NewExportRequest()
			if err := req.UnmarshalJSON([]byte(body)); err != nil {
				t.Fatalf("decode: %v", err)
			}
			metrics.Read(allocs)
			allocated := int64(allocs[0].Value.Uint64() - before)
			t.Logf("body %d B: decoding allocated %d, handler price %d (x%.2f)", len(body), allocated, p.handler, ratio(p.handler, allocated))
			if allocated > p.handler {
				t.Errorf("decoding allocated %d bytes, over the handler price %d", allocated, p.handler)
			}
			runtime.KeepAlive(req)
		})
	}
}

// A deeply nested request makes the goroutine that decodes and converts it
// grow its stack, which its size does not show: the price covers it. Many
// requests run at once, each holding its grown stack, and the average stack
// each holds stays within its price's share for the stack.
func TestPriceCoversDeepNestingStacks(t *testing.T) {
	if raceEnabled {
		t.Skip("the race detector's instrumentation makes every frame larger")
	}
	value := `{"stringValue":"x"}`
	for i := 1; i < maxValueNesting; i++ {
		value = `{"arrayValue":{"values":[` + value + `]}}`
	}
	deepValue := `{"resourceSpans":[{"scopeSpans":[{"spans":[{"name":"n","attributes":[{"key":"k","value":` + value + `}]}]}]}]}`
	req := ptraceotlp.NewExportRequest()
	if err := req.UnmarshalJSON([]byte(deepValue)); err != nil {
		t.Fatal(err)
	}
	deepProto, _ := req.MarshalProto()
	skip := func(d int) []byte {
		return []byte(`{"resourceSpans":[],"x":` + strings.Repeat("[", d) + strings.Repeat("]", d) + `}`)
	}
	cases := map[string]struct {
		body     []byte
		jsonBody bool
	}{
		"unknown member nested 505 levels": {skip(maxJSONNesting - 7), true},
		"unknown member nested 256 levels": {skip(256), true},
		"value nested to the limit (JSON)": {[]byte(deepValue), true},
		"value nested to the limit":        {deepProto, false},
	}
	stacks := func() int64 {
		s := []metrics.Sample{{Name: "/memory/classes/heap/stacks:bytes"}}
		metrics.Read(s)
		return int64(s[0].Value.Uint64())
	}
	for name, c := range cases {
		t.Run(name, func(t *testing.T) {
			shape, err := scanBody(c.body, c.jsonBody)
			if err != nil {
				t.Fatalf("scan: %v", err)
			}
			const n = 128
			runtime.GC()
			base := stacks()
			var wg, ran sync.WaitGroup
			hold := make(chan struct{})
			for i := 0; i < n; i++ {
				wg.Add(1)
				ran.Add(1)
				go func() {
					defer wg.Done()
					_, _ = scanBody(c.body, c.jsonBody)
					r := ptraceotlp.NewExportRequest()
					if c.jsonBody {
						_ = r.UnmarshalJSON(c.body)
					} else {
						_ = r.UnmarshalProto(c.body)
					}
					_, _, _ = chexp.ConvertWithIdentities(context.Background(), r.Traces(), nil)
					ran.Done()
					<-hold // keep the grown stack
				}()
			}
			ran.Wait()
			each := (stacks() - base) / n
			close(hold)
			wg.Wait()
			priced := stackInRequest + stackPrice(shape.depth)
			t.Logf("depth %d: %d KiB of stack each, %d KiB priced", shape.depth, each>>10, priced>>10)
			if each > priced {
				t.Errorf("each request holds %d bytes of stack, over the %d priced", each, priced)
			}
		})
	}
}
