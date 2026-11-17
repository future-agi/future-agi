package main

import (
	"bytes"
	"compress/gzip"
	"encoding/hex"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"reflect"
	"strings"
	"testing"

	commonpb "go.opentelemetry.io/proto/otlp/common/v1"
	resourcepb "go.opentelemetry.io/proto/otlp/resource/v1"
	tracepb "go.opentelemetry.io/proto/otlp/trace/v1"
	"google.golang.org/protobuf/encoding/protojson"
	"google.golang.org/protobuf/proto"
)

// identityJSON is OTLP/JSON written by hand as the OTLP spec defines it, not
// produced by protojson: trace and span ids are hex (case-insensitive), the
// span kind and status code are integers, and 64-bit times are strings.
const identityJSON = `{"resourceSpans":[{"resource":{"attributes":[{"key":"service.name","value":{"stringValue":"walk-fixture"}}]},
"scopeSpans":[{"scope":{"name":"walk"},"spans":[{"traceId":"5B8EFFF798038103D269B633813FC60C","spanId":"eee19b7ec3c1b174",
"parentSpanId":"eee19b7ec3c1b173","name":"identity-fixture","kind":2,"startTimeUnixNano":"1544712660000000000",
"endTimeUnixNano":"1544712661000000000","attributes":[{"key":"tool.input","value":{"stringValue":"fixture-content"}}],
"links":[{"traceId":"5b8efff798038103d269b633813fc60d","spanId":"eee19b7ec3c1b175"}],"status":{"code":1}}]}]}]}`

func mustHex(t *testing.T, s string) []byte {
	t.Helper()
	b, err := hex.DecodeString(s)
	if err != nil {
		t.Fatal(err)
	}
	return b
}

// identitySpan is identityJSON's span as the protobuf message.
func identitySpan(t *testing.T) *tracepb.TracesData {
	str := func(s string) *commonpb.AnyValue {
		return &commonpb.AnyValue{Value: &commonpb.AnyValue_StringValue{StringValue: s}}
	}
	return &tracepb.TracesData{ResourceSpans: []*tracepb.ResourceSpans{{
		Resource: &resourcepb.Resource{Attributes: []*commonpb.KeyValue{{Key: "service.name", Value: str("walk-fixture")}}},
		ScopeSpans: []*tracepb.ScopeSpans{{Scope: &commonpb.InstrumentationScope{Name: "walk"}, Spans: []*tracepb.Span{{
			TraceId: mustHex(t, "5b8efff798038103d269b633813fc60c"), SpanId: mustHex(t, "eee19b7ec3c1b174"),
			ParentSpanId: mustHex(t, "eee19b7ec3c1b173"), Name: "identity-fixture", Kind: tracepb.Span_SPAN_KIND_SERVER,
			StartTimeUnixNano: 1544712660000000000, EndTimeUnixNano: 1544712661000000000,
			Attributes: []*commonpb.KeyValue{{Key: "tool.input", Value: str("fixture-content")}},
			Links:      []*tracepb.Span_Link{{TraceId: mustHex(t, "5b8efff798038103d269b633813fc60d"), SpanId: mustHex(t, "eee19b7ec3c1b175")}},
			Status:     &tracepb.Status{Code: tracepb.Status_STATUS_CODE_OK},
		}}}},
	}}}
}

func postTraces(data http.Handler, contentType string, body []byte) *httptest.ResponseRecorder {
	r := httptest.NewRequest("POST", "/v1/traces", bytes.NewReader(body))
	r.Header.Set("Content-Type", contentType)
	w := httptest.NewRecorder()
	data.ServeHTTP(w, r)
	return w
}

func receipts(t *testing.T, control http.Handler) []json.RawMessage {
	t.Helper()
	w := httptest.NewRecorder()
	control.ServeHTTP(w, httptest.NewRequest("GET", "/__received", nil))
	var received struct {
		Batches []json.RawMessage `json:"batches"`
	}
	if err := json.Unmarshal(w.Body.Bytes(), &received); err != nil {
		t.Fatal(err)
	}
	return received.Batches
}

// TestOTLPJSONAndProtobufCaptureTheSameSpan: one span sent as OTLP/JSON and
// as OTLP/protobuf is one identity. The receipts are equal and carry the
// spec's hex ids for the trace, span, parent and link.
func TestOTLPJSONAndProtobufCaptureTheSameSpan(t *testing.T) {
	c := &capture{status: 503}
	data, control := c.handlers()
	binary, err := proto.Marshal(identitySpan(t))
	if err != nil {
		t.Fatal(err)
	}
	for _, in := range []struct {
		contentType string
		body        []byte
	}{{"application/json", []byte(identityJSON)}, {"application/x-protobuf", binary}} {
		if w := postTraces(data, in.contentType, in.body); w.Code != 200 {
			t.Fatalf("%s: %d %s", in.contentType, w.Code, w.Body.String())
		}
	}
	got := receipts(t, control)
	if len(got) != 2 {
		t.Fatalf("receipts = %d, want 2", len(got))
	}
	var fromJSON, fromProtobuf any
	if json.Unmarshal(got[0], &fromJSON) != nil || json.Unmarshal(got[1], &fromProtobuf) != nil {
		t.Fatal("receipts are not JSON")
	}
	if !reflect.DeepEqual(fromJSON, fromProtobuf) {
		t.Errorf("the same span differs by transport:\n json:     %s\n protobuf: %s", got[0], got[1])
	}
	for _, id := range []string{"5b8efff798038103d269b633813fc60c", "eee19b7ec3c1b174", "eee19b7ec3c1b173",
		"5b8efff798038103d269b633813fc60d", "eee19b7ec3c1b175"} {
		for i, receipt := range got {
			if !strings.Contains(string(receipt), `"`+id+`"`) {
				t.Errorf("receipt %d lacks the hex id %s: %s", i, id, receipt)
			}
		}
	}
}

// TestOTLPJSONIdsAndUnknownFields: OTLP/JSON ids must be hex of the right
// length (base64, odd or short hex is a bad request), and a receiver ignores
// fields it does not know.
func TestOTLPJSONIdsAndUnknownFields(t *testing.T) {
	c := &capture{status: 503}
	data, _ := c.handlers()
	span := func(fields string) []byte {
		return []byte(`{"resourceSpans":[{"scopeSpans":[{"spans":[{"name":"ids",` + fields + `}]}]}]}`)
	}
	for name, body := range map[string][]byte{
		"base64 trace id":       span(`"traceId":"W47/95gDgQPSabYzgT/GDA==","spanId":"eee19b7ec3c1b174"`),
		"base64-shaped non-hex": span(`"traceId":"0123456789abcdefghij0123456789ab","spanId":"eee19b7ec3c1b174"`),
		"short span id":         span(`"traceId":"5b8efff798038103d269b633813fc60c","spanId":"eee19b7e"`),
		"long parent id":        span(`"traceId":"5b8efff798038103d269b633813fc60c","spanId":"eee19b7ec3c1b174","parentSpanId":"eee19b7ec3c1b17400"`),
		"base64 link span id":   span(`"traceId":"5b8efff798038103d269b633813fc60c","spanId":"eee19b7ec3c1b174","links":[{"traceId":"5b8efff798038103d269b633813fc60d","spanId":"7uGbfsPBsXQ="}]`),
	} {
		if w := postTraces(data, "application/json", body); w.Code != 400 {
			t.Errorf("%s: %d, want 400", name, w.Code)
		}
	}
	if len(c.batches) != 0 {
		t.Errorf("a bad id was stored: %d batches", len(c.batches))
	}
	future := []byte(`{"resourceSpans":[{"scopeSpans":[{"spans":[{"name":"future","traceId":"5b8efff798038103d269b633813fc60c",` +
		`"spanId":"eee19b7ec3c1b174","futureField":{"x":1}}],"futureScopeField":2}]}],"futureTopLevel":true}`)
	if w := postTraces(data, "application/json", future); w.Code != 200 {
		t.Errorf("unknown fields: %d %s, want 200", w.Code, w.Body.String())
	}
}

func TestCaptureJSONProtobufAndFailure(t *testing.T) {
	c := &capture{status: 503}
	data, control := c.handlers()
	batch := &tracepb.TracesData{ResourceSpans: []*tracepb.ResourceSpans{{ScopeSpans: []*tracepb.ScopeSpans{{Spans: []*tracepb.Span{{Name: "fixture-tool", Attributes: []*commonpb.KeyValue{{Key: "tool.input", Value: &commonpb.AnyValue{Value: &commonpb.AnyValue_StringValue{StringValue: "fixture-content"}}}}}}}}}}}
	binary, _ := proto.Marshal(batch)
	text, _ := protojson.Marshal(batch)
	for _, format := range []string{"application/json", "application/x-protobuf", "gzip"} {
		body := text
		contentType := format
		if format != "application/json" {
			body = binary
			contentType = "application/x-protobuf"
		}
		if format == "gzip" {
			var b bytes.Buffer
			g := gzip.NewWriter(&b)
			_, _ = g.Write(body)
			_ = g.Close()
			body = b.Bytes()
		}
		r := httptest.NewRequest("POST", "/v1/traces", bytes.NewReader(body))
		r.Header.Set("Content-Type", contentType)
		if format == "gzip" {
			r.Header.Set("Content-Encoding", "gzip")
		}
		w := httptest.NewRecorder()
		data.ServeHTTP(w, r)
		if w.Code != 200 || w.Header().Get("Content-Type") != contentType {
			t.Fatalf("%s: %d", format, w.Code)
		}
	}
	w := httptest.NewRecorder()
	control.ServeHTTP(w, httptest.NewRequest("GET", "/__received", nil))
	var received struct {
		Batches  []json.RawMessage `json:"batches"`
		Attempts int               `json:"attempts"`
	}
	_ = json.Unmarshal(w.Body.Bytes(), &received)
	if len(received.Batches) != 3 || received.Attempts != 3 || !strings.Contains(w.Body.String(), "fixture-content") {
		t.Fatal(w.Body.String())
	}
	w = httptest.NewRecorder()
	control.ServeHTTP(w, httptest.NewRequest("POST", "/__failure", strings.NewReader(`{"count":1,"status":503}`)))
	for _, status := range []int{503, 200} {
		r := httptest.NewRequest("POST", "/v1/traces", bytes.NewReader(binary))
		r.Header.Set("Content-Type", "application/x-protobuf")
		w = httptest.NewRecorder()
		data.ServeHTTP(w, r)
		if w.Code != status {
			t.Fatalf("got %d want %d", w.Code, status)
		}
	}
	if len(c.batches) != 4 || c.attempts != 5 {
		t.Fatal("failed delivery was stored")
	}
}
func TestCaptureRejectsAndSeparatesControl(t *testing.T) {
	c := &capture{}
	data, control := c.handlers()
	for _, typ := range []string{"application/json", "application/x-protobuf", "text/plain"} {
		w := httptest.NewRecorder()
		r := httptest.NewRequest("POST", "/v1/traces", strings.NewReader("invalid"))
		r.Header.Set("Content-Type", typ)
		data.ServeHTTP(w, r)
		if w.Code < 400 {
			t.Fatal("accepted bad body")
		}
	}
	w := httptest.NewRecorder()
	data.ServeHTTP(w, httptest.NewRequest("GET", "/__received", nil))
	if w.Code != 404 {
		t.Fatal("read API exposed on data port")
	}
	w = httptest.NewRecorder()
	control.ServeHTTP(w, httptest.NewRequest("POST", "/__failure", strings.NewReader(`{"count":-1,"status":503}`)))
	if w.Code != 400 {
		t.Fatal("bad count accepted")
	}
	if len(c.batches) != 0 {
		t.Fatal("invalid body stored")
	}
}
