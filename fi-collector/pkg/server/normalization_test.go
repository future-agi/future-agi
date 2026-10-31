package server

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"testing"

	"github.com/future-agi/future-agi/fi-collector/pkg/normalization"
	"go.opentelemetry.io/collector/pdata/pcommon"
	"go.opentelemetry.io/collector/pdata/ptrace"
	"go.opentelemetry.io/collector/pdata/ptrace/ptraceotlp"
)

func TestImmutableContractNeverEnqueuedHTTPOrGRPC(t *testing.T) {
	for _, key := range []string{"fi.capture.source_work.id", "fi.capture.identity.profile", "fi.usage.plan.id", "fi.usage.fact.id", "fi.usage.revision", "fi.usage.contribution"} {
		for _, transport := range []string{"json", "protobuf", "grpc"} {
			t.Run(key+"/"+transport, func(t *testing.T) {
				td := calTraces(1, 1, 2, nil)
				sp := td.ResourceSpans().At(0).ScopeSpans().At(0).Spans().At(0)
				sp.Attributes().PutStr(key, "turn")
				s := directServer()
				var response ptraceotlp.ExportResponse
				if transport == "grpc" {
					var err error
					response, err = (&otlpHandler{s: s}).Export(context.Background(), ptraceotlp.NewExportRequestFromTraces(td))
					if err != nil {
						t.Fatal(err)
					}
				} else {
					isJSON := transport == "json"
					body := calBody(t, td, isJSON)
					req := httptest.NewRequest(http.MethodPost, "/v1/traces", bytes.NewReader(body))
					ct := "application/x-protobuf"
					if isJSON {
						ct = "application/json"
					}
					req.Header.Set("Content-Type", ct)
					rec := httptest.NewRecorder()
					s.handleHTTPTraces(rec, req)
					if rec.Code != 200 {
						t.Fatalf("status %d: %s", rec.Code, rec.Body)
					}
					response = ptraceotlp.NewExportResponse()
					var err error
					if isJSON {
						err = response.UnmarshalJSON(rec.Body.Bytes())
					} else {
						err = response.UnmarshalProto(rec.Body.Bytes())
					}
					if err != nil {
						t.Fatal(err)
					}
				}
				if response.PartialSuccess().RejectedSpans() != 2 || response.PartialSuccess().ErrorMessage() != normalization.AdmissionError() {
					t.Fatal("immutable contract silently accepted")
				}
				if s.work.inUse.Load() != 0 || s.wire.inUse.Load() != 0 {
					t.Fatal("held batch leaked memory lease")
				}
			})
		}
	}
}

func TestCopilotHTTPWritesSingleUsageContribution(t *testing.T) {
	body, err := os.ReadFile("../../exporter/clickhouse25exporter/testdata/copilot_1_0_98_otlp.json")
	if err != nil {
		t.Fatal(err)
	}
	ch, writer := newCHBodies(t)
	s := New(Config{}, writer, nil, nil, nil)
	req := httptest.NewRequest(http.MethodPost, "/tracer/v1/traces", bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	rec := httptest.NewRecorder()
	s.handleHTTPTraces(rec, req)
	if rec.Code != 200 {
		t.Fatalf("%d %s", rec.Code, rec.Body)
	}
	s.drainNow(context.Background())
	// Assert actual row encoding at the writer boundary, not mock conversion.
	var input, output float64
	calls := 0
	ch.mu.Lock()
	defer ch.mu.Unlock()
	for _, batch := range ch.tables["spans"] {
		for _, line := range bytes.Split(bytes.TrimSpace(batch), []byte("\n")) {
			var row map[string]any
			if err := json.Unmarshal(line, &row); err != nil {
				t.Fatal(err)
			}
			input += row["prompt_tokens"].(float64)
			output += row["completion_tokens"].(float64)
			if row["observation_type"] == "llm" {
				calls++
			}
			if row["trace_session_id"] == nil {
				t.Fatal("session not persisted")
			}
		}
	}
	if input != 42925 || output != 146 || calls != 3 {
		t.Fatalf("stored totals %v/%v calls=%d", input, output, calls)
	}
}

func TestPreparedNormalizationPriceIncludesDetails(t *testing.T) {
	td := calTraces(1, 1, 2, func(_ ptrace.ResourceSpans, sp ptrace.Span, _ int) {
		sp.Attributes().PutStr("gen_ai.conversation.id", strings.Repeat("s", 4096))
		sp.Attributes().PutStr("gen_ai.operation.name", "invoke_agent")
		for i := 0; i < 64; i++ {
			ev := sp.Events().AppendEmpty()
			ev.SetName("event\x01")
			ev.Attributes().PutStr("exception.type", strings.Repeat("<&\x01", 600))
		}
		for i := 0; i < 32; i++ {
			link := sp.Links().AppendEmpty()
			link.SetTraceID(pcommon.TraceID{1})
			link.SetSpanID(pcommon.SpanID{2})
			link.Attributes().PutStr("fi.agent.caused_by.work_id", strings.Repeat("x", 4096))
		}
	})
	ss := td.ResourceSpans().At(0).ScopeSpans().At(0)
	ss.Scope().SetName("github.copilot")
	ss.Scope().SetVersion("1.0.98")
	normalization.Prepare(td)
	shape, err := scanPreparedTraces(td)
	if err != nil {
		t.Fatal(err)
	}
	// Existing scanner-to-writer assertions also cover the augmented pdata.
	for _, isJSON := range []bool{true, false} {
		got := assertCorrectnessWirePrice(t, calBody(t, td, isJSON), isJSON)
		if got.rowJSONBytes < shape.rowJSONBytes {
			t.Fatalf("wire underprices final rows")
		}
	}
}

// The immutable flag and rejected count must describe the ORIGINAL request,
// even if routing removes the reserved-contract span before normalization.
func TestImmutableHoldSurvivesRoutingDrop(t *testing.T) {
	for _, transport := range []string{"grpc", "json", "protobuf"} {
		t.Run(transport, func(t *testing.T) {
			s := newRouteServer(t, &routeFake{dropReason: "deleted-agent"})
			req := routeRequest("customer-prod")
			req.Traces().ResourceSpans().At(0).ScopeSpans().At(0).Spans().At(0).Attributes().PutStr("fi.capture.source_work.id", "opaque")
			var response ptraceotlp.ExportResponse
			if transport == "grpc" {
				var err error
				response, err = s.export(sandboxKey(), req)
				if err != nil {
					t.Fatal(err)
				}
			} else {
				ct := "application/x-protobuf"
				if transport == "json" {
					ct = "application/json"
				}
				rec := s.post(t, sandboxKey(), req, ct)
				if rec.Code != 200 {
					t.Fatal(rec.Body)
				}
				response = ptraceotlp.NewExportResponse()
				var err error
				if transport == "json" {
					err = response.UnmarshalJSON(rec.Body.Bytes())
				} else {
					err = response.UnmarshalProto(rec.Body.Bytes())
				}
				if err != nil {
					t.Fatal(err)
				}
			}
			if response.PartialSuccess().ErrorMessage() != normalization.AdmissionError() || response.PartialSuccess().RejectedSpans() != 2 {
				t.Fatal("routing hid immutable-contract hold")
			}
			if len(s.events.snapshot()) != 0 {
				t.Fatal("rejected work billed")
			}
		})
	}
}
