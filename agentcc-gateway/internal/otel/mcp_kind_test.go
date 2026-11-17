package otel

import (
	tracepb "go.opentelemetry.io/proto/otlp/trace/v1"
	"google.golang.org/protobuf/proto"
	"testing"
)

func TestEncodeSpanServerKindPreservesLegacyClientDefault(t *testing.T) {
	for _, tc := range []struct {
		kind SpanKind
		want tracepb.Span_SpanKind
	}{{"", tracepb.Span_SPAN_KIND_CLIENT}, {KindClient, tracepb.Span_SPAN_KIND_CLIENT}, {KindServer, tracepb.Span_SPAN_KIND_SERVER}} {
		s := NewSpan("execute_tool", "test")
		s.Kind = tc.kind
		s.End()
		raw, err := proto.Marshal(encodeSpan(s))
		if err != nil {
			t.Fatal(err)
		}
		var got tracepb.Span
		if err := proto.Unmarshal(raw, &got); err != nil {
			t.Fatal(err)
		}
		if got.Kind != tc.want || got.EndTimeUnixNano < got.StartTimeUnixNano {
			t.Fatal("kind or timing lost on protobuf wire")
		}
	}
}
