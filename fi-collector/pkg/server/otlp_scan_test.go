package server

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"math/rand"
	"strings"
	"testing"

	chexp "github.com/future-agi/future-agi/fi-collector/exporter/clickhouse25exporter"
	"go.opentelemetry.io/collector/pdata/pcommon"
	"go.opentelemetry.io/collector/pdata/ptrace"
	"go.opentelemetry.io/collector/pdata/ptrace/ptraceotlp"
)

// Tests for the scan (otlp_scan.go, otlp_scan_json.go): its counts must be
// at least what pdata decodes, for any body pdata accepts, and its JSON byte
// counts at least what the rows write; its limits refuse what they say.

// decodedCounts walks a decoded request and counts what the scan counts.
type decodedCounts struct {
	resourceSpans, scopeSpans, spans, events, links, keyValues, arrayValues int64
	stringBytes, bytesValues                                                int64
}

func countDecoded(td ptrace.Traces) decodedCounts {
	var c decodedCounts
	var value func(v pcommon.Value)
	attrs := func(m pcommon.Map) {
		m.Range(func(k string, v pcommon.Value) bool {
			c.keyValues++
			c.stringBytes += int64(len(k))
			value(v)
			return true
		})
	}
	value = func(v pcommon.Value) {
		switch v.Type() {
		case pcommon.ValueTypeStr:
			c.stringBytes += int64(len(v.Str()))
		case pcommon.ValueTypeBytes:
			c.bytesValues += int64(v.Bytes().Len())
		case pcommon.ValueTypeSlice:
			for i := 0; i < v.Slice().Len(); i++ {
				c.arrayValues++
				value(v.Slice().At(i))
			}
		case pcommon.ValueTypeMap:
			attrs(v.Map())
		}
	}
	for i := 0; i < td.ResourceSpans().Len(); i++ {
		rs := td.ResourceSpans().At(i)
		c.resourceSpans++
		attrs(rs.Resource().Attributes())
		for j := 0; j < rs.ScopeSpans().Len(); j++ {
			ss := rs.ScopeSpans().At(j)
			c.scopeSpans++
			attrs(ss.Scope().Attributes())
			c.stringBytes += int64(len(ss.Scope().Name()) + len(ss.Scope().Version()))
			for k := 0; k < ss.Spans().Len(); k++ {
				sp := ss.Spans().At(k)
				c.spans++
				c.stringBytes += int64(len(sp.Name()) + len(sp.Status().Message()))
				attrs(sp.Attributes())
				for e := 0; e < sp.Events().Len(); e++ {
					c.events++
					c.stringBytes += int64(len(sp.Events().At(e).Name()))
					attrs(sp.Events().At(e).Attributes())
				}
				for l := 0; l < sp.Links().Len(); l++ {
					c.links++
					attrs(sp.Links().At(l).Attributes())
				}
			}
		}
	}
	return c
}

// rowJSON encodes rows as chwriter does and returns the total and the
// largest row.
func rowJSON(t *testing.T, td ptrace.Traces) (total, largest int64) {
	t.Helper()
	rows, _, err := chexp.ConvertWithIdentities(context.Background(), td, nil)
	if err != nil {
		t.Fatal(err)
	}
	for _, r := range rows {
		var b bytes.Buffer
		enc := json.NewEncoder(&b)
		enc.SetEscapeHTML(false)
		if err := enc.Encode(r); err != nil {
			t.Fatal(err)
		}
		total += int64(b.Len())
		largest = max(largest, int64(b.Len()))
	}
	return total, largest
}

// checkCovers asserts the scan of body covers what pdata decodes from it.
func checkCovers(t *testing.T, body []byte, jsonBody bool) {
	t.Helper()
	shape, err := scanBody(body, jsonBody)
	req := ptraceotlp.NewExportRequest()
	var derr error
	if jsonBody {
		derr = req.UnmarshalJSON(body)
	} else {
		derr = req.UnmarshalProto(body)
	}
	if err != nil {
		// Refusing is safe, but a JSON body pdata decodes must not be
		// refused as malformed (a limit is another matter).
		var se *scanError
		if jsonBody && derr == nil && (!errors.As(err, &se) || se.reason == "malformed") {
			t.Errorf("pdata decodes it, the scan refuses it: %v", err)
		}
		return
	}
	if derr != nil {
		return // the decoder refuses it: nothing is held
	}
	d := countDecoded(req.Traces())
	for _, c := range []struct {
		name          string
		scan, decoded int64
	}{
		{"resource spans", shape.resourceSpans, d.resourceSpans},
		{"scope spans", shape.scopeSpans, d.scopeSpans},
		{"spans", shape.spans, d.spans},
		{"events", shape.events, d.events},
		{"links", shape.links, d.links},
		{"key values", shape.keyValues, d.keyValues},
		{"array values", shape.arrayValues, d.arrayValues},
		{"string bytes", shape.stringBytes, d.stringBytes},
		{"bytes values", shape.bytesValues, d.bytesValues},
	} {
		if c.scan < c.decoded {
			t.Errorf("%s: scan counted %d, pdata decoded %d", c.name, c.scan, c.decoded)
		}
	}
	total, largest := rowJSON(t, req.Traces())
	if shape.rowJSONBytes < total || shape.maxRowJSON < largest {
		t.Errorf("row JSON: scan %d (largest %d), rows wrote %d (largest %d)", shape.rowJSONBytes, shape.maxRowJSON, total, largest)
	}
}

// randString draws a string that exercises every escape: control bytes,
// quotes, HTML characters, U+2028, multi-byte runes and invalid UTF-8.
func randString(r *rand.Rand) string {
	parts := []string{"a", "key", "\x01", "\"", "\\", "<&>", " ", "é", "日本", "😀", "\xff", "\n", "input.value", " "}
	var b strings.Builder
	for n := r.Intn(12); n > 0; n-- {
		b.WriteString(parts[r.Intn(len(parts))])
	}
	return b.String()
}

func randValue(r *rand.Rand, v pcommon.Value, depth int) {
	switch k := r.Intn(9); {
	case k < 3 || depth > 3:
		v.SetStr(randString(r))
	case k == 3:
		v.SetInt(r.Int63() - r.Int63())
	case k == 4:
		v.SetDouble(r.NormFloat64() * 1e300)
	case k == 5:
		v.SetBool(true)
	case k == 6:
		v.SetEmptyBytes().FromRaw([]byte(randString(r)))
	case k == 7:
		s := v.SetEmptySlice()
		for n := r.Intn(4); n > 0; n-- {
			randValue(r, s.AppendEmpty(), depth+1)
		}
	default:
		m := v.SetEmptyMap()
		for n := r.Intn(4); n > 0; n-- {
			randValue(r, m.PutEmpty(randString(r)), depth+1)
		}
	}
}

func randAttrs(r *rand.Rand, m pcommon.Map) {
	keys := []string{"input.value", "output.value", "user.id", "session.id", "user.metadata", "service.name", "fi.project_id"}
	for n := r.Intn(6); n > 0; n-- {
		k := randString(r)
		if r.Intn(3) == 0 {
			k = keys[r.Intn(len(keys))]
		}
		randValue(r, m.PutEmpty(k), 0)
	}
}

func randTraces(r *rand.Rand) ptrace.Traces {
	td := ptrace.NewTraces()
	for i := r.Intn(3) + 1; i > 0; i-- {
		rs := td.ResourceSpans().AppendEmpty()
		randAttrs(r, rs.Resource().Attributes())
		for j := r.Intn(3); j >= 0; j-- {
			ss := rs.ScopeSpans().AppendEmpty()
			ss.Scope().SetName(randString(r))
			randAttrs(r, ss.Scope().Attributes())
			for k := r.Intn(4); k >= 0; k-- {
				sp := ss.Spans().AppendEmpty()
				sp.SetName(randString(r))
				sp.SetTraceID(pcommon.TraceID{byte(r.Intn(256)), 1})
				sp.SetSpanID(pcommon.SpanID{byte(r.Intn(256)), 2})
				if r.Intn(2) == 0 {
					sp.SetParentSpanID(pcommon.SpanID{3})
				}
				sp.Status().SetMessage(randString(r))
				randAttrs(r, sp.Attributes())
				for e := r.Intn(3); e > 0; e-- {
					ev := sp.Events().AppendEmpty()
					ev.SetName(randString(r))
					randAttrs(r, ev.Attributes())
				}
				for l := r.Intn(2); l > 0; l-- {
					randAttrs(r, sp.Links().AppendEmpty().Attributes())
				}
			}
		}
	}
	return td
}

// A differential test: random requests, in protobuf and JSON, scanned and
// decoded; the scan must cover the decoder every time.
func TestScanCoversRandomRequests(t *testing.T) {
	r := rand.New(rand.NewSource(8745))
	n := map[bool]int{false: 400, true: 100}[raceEnabled]
	for i := 0; i < n; i++ {
		td := randTraces(r)
		req := ptraceotlp.NewExportRequestFromTraces(td)
		pb, err := req.MarshalProto()
		if err != nil {
			t.Fatal(err)
		}
		js, err := req.MarshalJSON()
		if err != nil {
			t.Fatal(err)
		}
		t.Run(fmt.Sprint(i), func(t *testing.T) {
			checkCovers(t, pb, false)
			checkCovers(t, js, true)
		})
	}
}

// FuzzScanCovers runs the differential check on arbitrary bodies: whatever
// both the scan and pdata accept, the scan must cover. `go test` runs the
// seeds; `go test -fuzz FuzzScanCovers` explores.
func FuzzScanCovers(f *testing.F) {
	r := rand.New(rand.NewSource(1))
	for i := 0; i < 20; i++ {
		req := ptraceotlp.NewExportRequestFromTraces(randTraces(r))
		pb, _ := req.MarshalProto()
		js, _ := req.MarshalJSON()
		f.Add(pb, false)
		f.Add(js, true)
	}
	for _, c := range edgeCases() {
		f.Add(c.body, c.json)
	}
	f.Fuzz(func(t *testing.T, body []byte, jsonBody bool) {
		checkCovers(t, body, jsonBody)
	})
}

type edgeCase struct {
	name string
	body []byte
	json bool
}

func pbKV(key string, value []byte) []byte {
	return pbCat(pbField(1, []byte(key)), pbField(2, value))
}

func edgeCases() []edgeCase {
	span := pbCat(pbField(1, bytes.Repeat([]byte{1}, 16)), pbField(2, bytes.Repeat([]byte{2}, 8)),
		pbField(9, pbKV("input.value", pbField(1, []byte("hi\x01")))))
	return []edgeCase{
		// pdata moves the spans of the deprecated InstrumentationLibrarySpans
		// (field 1000) into ScopeSpans when there are none.
		{"deprecated field 1000", pbField(1, pbField(1000, pbField(2, span))), false},
		{"field 1000 and 2", pbField(1, pbCat(pbField(2, pbField(2, span)), pbField(1000, pbField(2, span)))), false},
		// A singular message sent twice is merged, its attributes appended.
		{"resource sent twice", pbField(1, pbCat(
			pbField(1, pbField(1, pbKV("a", pbField(1, []byte("1"))))),
			pbField(1, pbField(1, pbKV("b", pbField(1, []byte("2"))))),
			pbField(2, pbField(2, span)))), false},
		{"key value with two keys and two values", pbOneSpan(pbField(9, pbCat(
			pbField(1, []byte("x")), pbField(2, pbField(1, []byte("v1"))),
			pbField(1, []byte("input.value")), pbField(2, pbField(1, []byte("v2")))))), false},
		{"unknown fields everywhere", pbCat(pbField(7, []byte("junk")), pbField(1, pbCat(pbField(9, []byte("x")),
			pbField(2, pbCat(pbVarint(99, 1), pbField(2, pbCat(span, pbField(77, pbDeepArray(100))))))))), false},
		{"spans twice", []byte(`{"resourceSpans":[{"scopeSpans":[{"spans":[{"name":"a"}],"spans":[{"name":"b"},null]}]}]}`), true},
		{"snake case", []byte(`{"resource_spans":[{"scope_spans":[{"spans":[{"name":"a","attributes":[{"key":"k","value":{"string_value":"v","kvlist_value":{"values":[{"key":"n","value":{"int_value":"5"}}]}}}]}]}]}]}`), true},
		{"escaped keys", []byte(`{"resourceSpans":[{"scopeSpans":[{"spans":[{"name":"\u0001 😀\ud800x","attributes":[{"key":"input.value","value":{"stringValue":"\"<&>\""}}]}]}]}]}`), true},
		{"value before key", []byte(`{"resourceSpans":[{"scopeSpans":[{"spans":[{"attributes":[{"value":{"arrayValue":{"values":[{"stringValue":"x"},null]}},"key":"output.value"}]}]}]}]}`), true},
		// jsoniter reads null as "" wherever pdata reads a string.
		{"null strings", []byte(`{"resourceSpans":[{"schemaUrl":null,"scopeSpans":[{"scope":{"name":null,"version":null},"schemaUrl":null,"spans":[{"traceId":null,"spanId":null,"parentSpanId":null,"traceState":null,"name":null,"status":{"message":null},"events":[{"name":null}],"links":[{"traceId":null,"spanId":null,"traceState":null}],"attributes":[{"key":null,"value":{"stringValue":null}}]}]}]}]}`), true},
		{"resource after spans", []byte(`{"resourceSpans":[{"scopeSpans":[{"spans":[{},{},{}]}],"resource":{"attributes":[{"key":"service.name","value":{"stringValue":"` + strings.Repeat("\\u0001", 100) + `"}}]}}]}`), true},
	}
}

// Shapes the decoder treats specially are covered.
func TestScanCoversEdgeCases(t *testing.T) {
	for _, c := range edgeCases() {
		t.Run(c.name, func(t *testing.T) {
			if _, err := scanBody(c.body, c.json); err != nil {
				t.Fatalf("scan refused it: %v", err)
			}
			req := ptraceotlp.NewExportRequest()
			var err error
			if c.json {
				err = req.UnmarshalJSON(c.body)
			} else {
				err = req.UnmarshalProto(c.body)
			}
			if err != nil {
				t.Fatalf("test setup: pdata refuses it: %v", err)
			}
			if req.Traces().SpanCount() == 0 {
				t.Fatal("test setup: no spans decoded")
			}
			checkCovers(t, c.body, c.json)
		})
	}
}

// jsonEscapedLen is exactly what encoding/json writes, with and without HTML
// escaping, for every kind of byte; the JSON scan's re-encoded length is the
// same for the decoded string.
func TestJSONEscapedLenIsExact(t *testing.T) {
	r := rand.New(rand.NewSource(2))
	cases := []string{"", "plain", "\x00\x01\x1f\x7f", "\"\\/", "\b\f\n\r\t", "<>&", "  ", "é日😀", "\xff\xfe\xc3", "\xed\xa0\x80"}
	for i := 0; i < 200; i++ {
		cases = append(cases, randString(r))
	}
	for _, s := range cases {
		var plainBuf, htmlBuf bytes.Buffer
		enc := json.NewEncoder(&plainBuf)
		enc.SetEscapeHTML(false)
		_ = enc.Encode(s)
		_ = json.NewEncoder(&htmlBuf).Encode(s)
		plain, html := jsonEscapedLen([]byte(s))
		if want := int64(plainBuf.Len() - 3); plain != want {
			t.Errorf("%q: plain %d, encoding/json %d", s, plain, want)
		}
		if want := int64(htmlBuf.Len() - 3); html != want {
			t.Errorf("%q: html %d, encoding/json %d", s, html, want)
		}
		// The same string inside an OTLP/JSON body: escaped where JSON
		// requires it, every other byte raw (jsoniter keeps invalid UTF-8 as
		// it is).
		j := jsonScan{b: jsonToken(s)}
		info, err := j.str(false)
		if err != nil {
			t.Fatalf("%q: %v", s, err)
		}
		if info.plain != plain || info.html != html || info.decoded != int64(len(s)) {
			t.Errorf("%q from JSON: re-encoded %d/%d from %d bytes, want %d/%d from %d", s, info.plain, info.html, info.decoded, plain, html, len(s))
		}
	}
}

// jsonToken writes s as a JSON string token, escaping only what JSON must.
func jsonToken(s string) []byte {
	b := []byte{'"'}
	for i := 0; i < len(s); i++ {
		switch c := s[i]; {
		case c == '"' || c == '\\':
			b = append(b, '\\', c)
		case c < 0x20:
			b = append(b, fmt.Sprintf("\\u%04x", c)...)
		default:
			b = append(b, c)
		}
	}
	return append(b, '"')
}

// Each per-request limit refuses a request just past it, as a permanent
// error that names it, and lets one at it through.
func TestScanLimits(t *testing.T) {
	spansPB := func(n int) []byte {
		ss := bytes.Repeat(pbField(2, nil), n)
		return pbField(1, pbField(2, ss))
	}
	rsPB := func(n int) []byte { return bytes.Repeat(pbField(1, nil), n) }
	kvsPB := func(n int) []byte { return pbOneSpan(bytes.Repeat(pbField(9, nil), n)) }
	resourcePB := func(n int) []byte {
		return pbField(1, pbCat(pbField(1, pbField(1, pbKV("k", pbField(1, bytes.Repeat([]byte("v"), n-1))))), pbField(2, pbField(2, nil))))
	}
	cases := []struct {
		name     string
		at, past []byte
		want     error
	}{
		{"spans", spansPB(maxSpansPerRequest), spansPB(maxSpansPerRequest + 1), errTooManySpans},
		{"resource spans", rsPB(maxResourceSpansPerRequest), rsPB(maxResourceSpansPerRequest + 1), errTooManyResourceSpans},
		{"attributes", kvsPB(maxKeyValuesPerRequest), kvsPB(maxKeyValuesPerRequest + 1), errTooManyKeyValues},
		{"resource attributes", resourcePB(maxResourceAttrBytes), resourcePB(maxResourceAttrBytes + 1), errResourceTooLarge},
		{"value nesting", pbDeepAttr(maxValueNesting), pbDeepAttr(maxValueNesting + 1), errValueTooDeep},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			if _, err := scanOTLPProto(c.at); err != nil {
				t.Errorf("at the limit: %v", err)
			}
			_, err := scanOTLPProto(c.past)
			if !errors.Is(err, c.want) {
				t.Fatalf("past the limit: %v, want %v", err, c.want)
			}
		})
	}
}

// The scan allocates nothing that grows with the body: the protobuf scan
// nothing at all, the JSON scan only its own state.
func TestScanDoesNotAllocate(t *testing.T) {
	req := ptraceotlp.NewExportRequestFromTraces(calTraces(2, 2, 50, llmSpan(500, 200)))
	pb, _ := req.MarshalProto()
	js, _ := req.MarshalJSON()
	if n := testing.AllocsPerRun(10, func() { _, _ = scanOTLPProto(pb) }); n != 0 {
		t.Errorf("protobuf scan: %v allocations per run", n)
	}
	if n := testing.AllocsPerRun(10, func() { _, _ = scanOTLPJSON(js) }); n > 1 {
		t.Errorf("JSON scan: %v allocations per run, want at most 1", n)
	}
	for _, c := range edgeCases() {
		if n := testing.AllocsPerRun(5, func() { _, _ = scanBody(c.body, c.json) }); n > 1 {
			t.Errorf("%s: %v allocations per run", c.name, n)
		}
	}
}
