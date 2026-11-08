package server

import (
	"bytes"
	"context"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"go.opentelemetry.io/collector/pdata/ptrace/ptraceotlp"
	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/credentials/insecure"
	"google.golang.org/grpc/mem"
	"google.golang.org/grpc/status"
	"google.golang.org/protobuf/encoding/protowire"
	"google.golang.org/protobuf/proto"
	"google.golang.org/protobuf/types/known/wrapperspb"
)

// Tests for the nesting limit on OTLP protobuf requests (otlp_nesting.go).
// pdata's protobuf decoder recurses once per nested message with no limit, so
// a deep enough body overflows the goroutine stack: a fatal error that no
// recover catches, which kills the process. The HTTP handler and the gRPC
// server's codec refuse such a body first.

// pbField is one length-delimited field: tag, length, payload.
func pbField(field protowire.Number, payload []byte) []byte {
	b := protowire.AppendTag(nil, field, protowire.BytesType)
	return protowire.AppendBytes(b, payload)
}

func pbVarint(field protowire.Number, v uint64) []byte {
	b := protowire.AppendTag(nil, field, protowire.VarintType)
	return protowire.AppendVarint(b, v)
}

func pbCat(parts ...[]byte) []byte { return bytes.Join(parts, nil) }

// pbOneSpan wraps the fields of one span (with valid ids) in a request.
func pbOneSpan(spanFields []byte) []byte {
	ids := pbCat(pbField(1, bytes.Repeat([]byte{0xaa}, 16)), pbField(2, bytes.Repeat([]byte{0xbb}, 8)))
	return pbField(1, pbField(2, pbField(2, pbCat(ids, spanFields))))
}

// pbDeepArray is an AnyValue holding an array holding an AnyValue ... with n
// arrays, built inside-out in O(n). It spans 2n message levels.
func pbDeepArray(n int) []byte {
	anySz := make([]int, n+1)
	arrSz := make([]int, n+1)
	for i := 1; i <= n; i++ {
		arrSz[i] = 1 + protowire.SizeVarint(uint64(anySz[i-1])) + anySz[i-1]
		anySz[i] = 1 + protowire.SizeVarint(uint64(arrSz[i])) + arrSz[i]
	}
	out := make([]byte, 0, anySz[n])
	for i := n; i >= 1; i-- {
		out = append(out, 0x2a) // AnyValue.array_value
		out = protowire.AppendVarint(out, uint64(arrSz[i]))
		out = append(out, 0x0a) // ArrayValue.values
		out = protowire.AppendVarint(out, uint64(anySz[i-1]))
	}
	return out
}

// pbDeepAttr is a request whose one span attribute nests n arrays. Its
// deepest message is at depth 5+2n: request 0, ResourceSpans 1, ScopeSpans 2,
// Span 3, KeyValue 4, the attribute's AnyValue 5, then an ArrayValue and an
// AnyValue per array.
func pbDeepAttr(n int) []byte {
	return pbOneSpan(pbField(9, pbCat(pbField(1, []byte("k")), pbField(2, pbDeepArray(n)))))
}

// arraysAtLimit is the most nested arrays the limit lets through:
// 5+2n <= maxDecodeNesting, so 4,997.
const arraysAtLimit = (maxDecodeNesting - 5) / 2

func TestCheckProtoNesting(t *testing.T) {
	if err := checkProtoNesting(pbDeepAttr(arraysAtLimit)); err != nil {
		t.Errorf("%d nested arrays (depth %d): err = %v, want nil", arraysAtLimit, 5+2*arraysAtLimit, err)
	}
	if err := checkProtoNesting(pbDeepAttr(arraysAtLimit + 1)); !errors.Is(err, errNestingTooDeep) {
		t.Errorf("%d nested arrays (depth %d): err = %v, want errNestingTooDeep", arraysAtLimit+1, 5+2*(arraysAtLimit+1), err)
	}
	// kvlist values nest too: AnyValue > KeyValueList > KeyValue > AnyValue.
	kv := pbVarint(3, 1) // AnyValue.int_value
	for i := 0; i < maxDecodeNesting/3+1; i++ {
		kv = pbField(6, pbField(1, pbCat(pbField(1, []byte("k")), pbField(2, kv))))
	}
	deepKV := pbOneSpan(pbField(9, pbCat(pbField(1, []byte("k")), pbField(2, kv))))
	if err := checkProtoNesting(deepKV); !errors.Is(err, errNestingTooDeep) {
		t.Errorf("deep kvlist: err = %v, want errNestingTooDeep", err)
	}
	// Strings and unknown fields are not descended into, however they look.
	looksNested := pbOneSpan(pbCat(pbField(5, pbDeepArray(arraysAtLimit+10)), pbField(99, pbDeepArray(arraysAtLimit+10))))
	if err := checkProtoNesting(looksNested); err != nil {
		t.Errorf("deep bytes inside a string and an unknown field: err = %v, want nil", err)
	}
	if err := checkProtoNesting([]byte{0x0a, 0x05, 0x00}); err == nil {
		t.Error("a truncated length-delimited field must be an error")
	}
}

// A protobuf body whose attribute value nests past maxValueNesting is a 400,
// compressed or not, and one at the limit still decodes. The scan refuses it
// before pdata decodes it, bounding decoder recursion and request stack growth.
func TestOTLPHTTPDeeplyNestedProtobufIs400(t *testing.T) {
	cases := []struct {
		name   string
		arrays int
		want   int
		gzip   bool // also send it gzipped (slow for the 13.8 MiB body under -race)
	}{
		{"at the limit", maxValueNesting, http.StatusOK, true},
		{"one level past the limit", maxValueNesting + 1, http.StatusBadRequest, true},
		{"at the old structural limit", arraysAtLimit, http.StatusBadRequest, true},
		{"deep enough to overflow the decoder's stack", 1_500_000, http.StatusBadRequest, false},
	}
	for _, tc := range cases {
		raw := pbDeepAttr(tc.arrays)
		variants := []bool{false}
		if tc.gzip {
			variants = append(variants, true)
		}
		for _, gz := range variants {
			t.Run(tc.name+map[bool]string{false: "", true: " gzip"}[gz], func(t *testing.T) {
				body := raw
				if gz {
					body = gzipBytes(t, raw)
				}
				rec := postBody(acceptingServer(), "application/x-protobuf", gz, nil, body)
				if rec.Code != tc.want {
					t.Fatalf("status %d, want %d (%s)", rec.Code, tc.want, rec.Body.String())
				}
				if tc.want == http.StatusBadRequest && !strings.Contains(rec.Body.String(), "nested") {
					t.Errorf("400 body should name the nesting: %q", rec.Body.String())
				}
			})
		}
	}
}

// rawProtoCodec sends and receives already-encoded protobuf bytes, so a test
// can put any bytes on the gRPC wire.
type rawProtoCodec struct{}

func (rawProtoCodec) Marshal(v any) ([]byte, error) { return *v.(*[]byte), nil }
func (rawProtoCodec) Unmarshal(b []byte, v any) error {
	*v.(*[]byte) = append([]byte(nil), b...)
	return nil
}
func (rawProtoCodec) Name() string { return "proto" }

// gRPC requests get the same nesting limit, checked by the scan before pdata
// decodes the request, and refused as INVALID_ARGUMENT.
func TestGRPCDeeplyNestedProtobufIsRefused(t *testing.T) {
	addr, _ := startGRPCOnly(t, Config{})
	conn, err := grpc.NewClient(addr, grpc.WithTransportCredentials(insecure.NewCredentials()))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { conn.Close() })
	export := func(body []byte) error {
		var resp []byte
		return conn.Invoke(context.Background(), "/opentelemetry.proto.collector.trace.v1.TraceService/Export",
			&body, &resp, grpc.ForceCodec(rawProtoCodec{}))
	}

	if err := export(pbDeepAttr(maxValueNesting)); err != nil {
		t.Errorf("%d nested arrays (at the limit): %v, want OK", maxValueNesting, err)
	}
	for _, arrays := range []int{maxValueNesting + 1, arraysAtLimit, 1_500_000} {
		err := export(pbDeepAttr(arrays))
		if status.Code(err) != codes.InvalidArgument || !strings.Contains(err.Error(), "nested") {
			t.Errorf("%d nested arrays: %v, want INVALID_ARGUMENT naming the nesting", arrays, err)
		}
	}
	// The server is still up and decodes ordinary requests.
	req := ptraceotlp.NewExportRequestFromTraces(makeTraces("after-deep", "17171717-1717-4717-8717-171717171717"))
	if _, err := ptraceotlp.NewGRPCClient(conn).Export(context.Background(), req); err != nil {
		t.Errorf("ordinary Export after the deep ones: %v", err)
	}
}

// The codec checks only ExportTraceServiceRequest, whose schema the walk
// follows; any other message decodes as grpc-go's proto codec decodes it.
func TestNestingCheckedCodecChecksOnlyTraceExports(t *testing.T) {
	codec := newNestingCheckedCodec()
	// As an ExportTraceServiceRequest, field 1 would be a truncated
	// ResourceSpans; as a BytesValue it is plain bytes.
	payload := []byte{0x0a, 0x05, 0x00}
	wire, err := proto.Marshal(wrapperspb.Bytes(payload))
	if err != nil {
		t.Fatal(err)
	}
	var got wrapperspb.BytesValue
	if err := codec.Unmarshal(mem.BufferSlice{mem.SliceBuffer(wire)}, &got); err != nil {
		t.Fatalf("BytesValue: %v, want it decoded unchecked", err)
	}
	if !bytes.Equal(got.GetValue(), payload) {
		t.Errorf("BytesValue decoded to %x, want %x", got.GetValue(), payload)
	}
	if isTraceExportRequest(&got) || isTraceExportRequest(struct{}{}) {
		t.Error("only an ExportTraceServiceRequest is a trace export")
	}
}

// JSON gets the same attribute nesting limit, and a limit on JSON nesting
// anywhere, including unknown keys that the decoder skips recursively.
func TestOTLPHTTPDeeplyNestedJSONIs400(t *testing.T) {
	// Each nested array is 3 JSON levels: {"arrayValue":{"values":[ ... ]}}.
	nested := func(arrays int) string {
		value := `{"intValue":"1"}`
		for i := 0; i < arrays; i++ {
			value = `{"arrayValue":{"values":[` + value + `]}}`
		}
		return value
	}
	span := func(attr string) []byte {
		return []byte(`{"resourceSpans":[{"scopeSpans":[{"spans":[{"traceId":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","spanId":"bbbbbbbbbbbbbbbb","attributes":[` +
			attr + `]}]}]}]}`)
	}
	cases := []struct {
		name string
		body []byte
		want int
	}{
		{"at the limit", span(`{"key":"k","value":` + nested(maxValueNesting) + `}`), http.StatusOK},
		{"past the limit", span(`{"key":"k","value":` + nested(maxValueNesting+1) + `}`), http.StatusBadRequest},
		{"past jsoniter's limit", span(`{"key":"k","value":` + nested(maxDecodeNesting/3+1) + `}`), http.StatusBadRequest},
		{"deep unknown key", span(`{"key":"k","unknown":` + strings.Repeat("[", maxJSONNesting) + strings.Repeat("]", maxJSONNesting) + `}`), http.StatusBadRequest},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			rec := postBody(acceptingServer(), "application/json", false, nil, tc.body)
			if rec.Code != tc.want {
				t.Fatalf("status %d %q, want %d", rec.Code, rec.Body.String(), tc.want)
			}
			if tc.want == http.StatusBadRequest && !strings.Contains(rec.Body.String(), "nested deeper") {
				t.Errorf("400 should name the nesting: %q", rec.Body.String())
			}
		})
	}
}

// postBody sends one OTLP/HTTP request to the handler in-process.
func postBody(s *Server, contentType string, gzipped bool, header map[string]string, body []byte) *httptest.ResponseRecorder {
	req := httptest.NewRequest(http.MethodPost, "/v1/traces", bytes.NewReader(body))
	req.Header.Set("Content-Type", contentType)
	if gzipped {
		req.Header.Set("Content-Encoding", "gzip")
	}
	for k, v := range header {
		req.Header.Set(k, v)
	}
	rec := httptest.NewRecorder()
	s.handleHTTPTraces(rec, req)
	return rec
}
