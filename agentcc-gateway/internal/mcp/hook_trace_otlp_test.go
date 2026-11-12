package mcp

import (
	"bytes"
	"encoding/hex"
	"io"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	otel "github.com/futureagi/agentcc-gateway/internal/otel"
	commonpb "go.opentelemetry.io/proto/otlp/common/v1"
	tracepb "go.opentelemetry.io/proto/otlp/trace/v1"
	"google.golang.org/protobuf/proto"
)

// Collector rejection is asynchronous; it must never replay a tool call.
func TestMCPCollectorFailurePreservesResult(t *testing.T) {
	received := make(chan struct{}, 8)
	sink := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = io.Copy(io.Discard, r.Body)
		w.WriteHeader(http.StatusServiceUnavailable)
		select {
		case received <- struct{}{}:
		default:
		}
	}))
	defer sink.Close()
	exp, err := otel.NewOTLPExporter(otel.OTLPOptions{Endpoint: sink.URL})
	if err != nil {
		t.Fatal(err)
	}
	defer exp.Shutdown()
	h := tracedHarness(t, exp)
	resp := h.call(t, "up_ok", nil)
	if resp.Error != nil || h.sink.only(t).Outcome != "success" {
		t.Fatal("collector failure changed tool result")
	}
	select {
	case <-received:
	case <-time.After(10 * time.Second):
		t.Fatal("collector not attempted")
	}
	if len(h.stats.seen()) != 1 || len(h.sink.records(t)) != 1 {
		t.Fatal("collector failure multiplied tool calls")
	}
}

func TestMCPOTLPSIEMRoundTrip(t *testing.T) {
	received := make(chan []*tracepb.Span, 8)
	sink := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		raw, _ := io.ReadAll(r.Body)
		for _, canary := range []string{"synthetic-private-argument", "synthetic-private-authorization"} {
			if bytes.Contains(raw, []byte(canary)) {
				t.Error("collector received private bytes")
			}
		}
		var td tracepb.TracesData
		if proto.Unmarshal(raw, &td) != nil {
			t.Error("invalid OTLP protobuf")
			w.WriteHeader(400)
			return
		}
		var spans []*tracepb.Span
		for _, rs := range td.ResourceSpans {
			for _, ss := range rs.ScopeSpans {
				spans = append(spans, ss.Spans...)
			}
		}
		w.WriteHeader(http.StatusOK)
		received <- spans
	}))
	defer sink.Close()
	exp, err := otel.NewOTLPExporter(otel.OTLPOptions{Endpoint: sink.URL, ServiceName: "mcp-test"})
	if err != nil {
		t.Fatal(err)
	}
	defer exp.Shutdown()
	h := tracedHarness(t, exp)
	resp := h.call(t, "up_ok", map[string]any{"password": "synthetic-private-argument"}, "Authorization: Bearer synthetic-private-authorization", "traceparent: 00-11111111111111111111111111111111-2222222222222222-01")
	if resp.Error != nil || h.sink.only(t).Outcome != "success" {
		t.Fatal("tool call failed")
	}
	// A decoded receipt from the collector, not merely the successful enqueue.
	var spans []*tracepb.Span
	deadline := time.After(10 * time.Second)
	for len(spans) < 2 {
		select {
		case batch := <-received:
			spans = append(spans, batch...)
		case <-deadline:
			t.Fatal("no collector receipt")
		}
	}
	if len(spans) != 2 {
		t.Fatal("duplicate delivery")
	}
	var server, client *tracepb.Span
	for _, s := range spans {
		if s.Kind == tracepb.Span_SPAN_KIND_SERVER {
			server = s
		}
		if s.Kind == tracepb.Span_SPAN_KIND_CLIENT {
			client = s
		}
	}
	if server == nil || client == nil || !bytes.Equal(client.ParentSpanId, server.SpanId) || !bytes.Equal(client.TraceId, server.TraceId) {
		t.Fatal("wire ancestry incorrect")
	}
	if hex.EncodeToString(server.TraceId) != "11111111111111111111111111111111" || hex.EncodeToString(server.ParentSpanId) != "2222222222222222" {
		t.Fatal("caller ancestry lost")
	}
	for _, s := range spans {
		attrs := map[string]*commonpb.AnyValue{}
		for _, a := range s.Attributes {
			attrs[a.Key] = a.Value
		}
		if attrs["gen_ai.operation.name"].GetStringValue() != "execute_tool" || attrs["agentcc.billing_mode"].GetStringValue() != "byok" || attrs["mcp.billed_cost_micros"] == nil || attrs["mcp.billed_cost_micros"].GetIntValue() != 0 {
			t.Fatal("wire contract mismatch")
		}
		if s.EndTimeUnixNano < s.StartTimeUnixNano || s.EndTimeUnixNano == 0 {
			t.Fatal("wire span unfinished")
		}
	}
}
