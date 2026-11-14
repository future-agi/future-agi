package server

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"go.opentelemetry.io/collector/pdata/ptrace/ptraceotlp"
)

// OTLP exporters do not retry partial_success. Assert rows at the writer, not
// just a 200 response: native detailed usage and unrelated SDK spans both survive.
func TestNativeCostCoverageDoesNotRejectMixedExport(t *testing.T) {
	for _, producer := range []struct{ name, version string }{{"github.copilot", "1.0.98"}, {"gemini-cli", "v1"}} {
		for _, costKey := range []string{"", "gen_ai.usage.total_cost"} {
			for _, transport := range []string{"json", "protobuf", "grpc"} {
				t.Run(producer.name+"/"+costKey+"/"+transport, func(t *testing.T) {
					td := calTraces(1, 1, 1, nil)
					rs := td.ResourceSpans().At(0)
					ss := rs.ScopeSpans().At(0)
					ss.Scope().SetName(producer.name)
					ss.Scope().SetVersion(producer.version)
					sp := ss.Spans().At(0)
					sp.SetName("native")
					sp.Attributes().PutInt("gen_ai.usage.cache_read_tokens", 40)
					if costKey != "" {
						sp.Attributes().PutDouble(costKey, 0.25)
					}
					sdk := rs.ScopeSpans().AppendEmpty()
					sdk.Scope().SetName("unrelated-sdk")
					sdk.Spans().AppendEmpty().SetName("unrelated")
					ch, writer := newCHBodies(t)
					s := New(Config{}, writer, nil, nil, nil)
					var response ptraceotlp.ExportResponse
					if transport == "grpc" {
						var err error
						response, err = (&otlpHandler{s: s}).Export(context.Background(), ptraceotlp.NewExportRequestFromTraces(td))
						if err != nil {
							t.Fatal(err)
						}
					} else {
						isJSON := transport == "json"
						req := httptest.NewRequest(http.MethodPost, "/v1/traces", bytes.NewReader(calBody(t, td, isJSON)))
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
					if response.PartialSuccess().RejectedSpans() != 0 || response.PartialSuccess().ErrorMessage() != "" {
						t.Fatalf("native export rejected: %s", response.PartialSuccess().ErrorMessage())
					}
					s.drainNow(context.Background())
					ch.mu.Lock()
					defer ch.mu.Unlock()
					rows := 0
					cost := 0.0
					for _, batch := range ch.tables["spans"] {
						for _, line := range bytes.Split(bytes.TrimSpace(batch), []byte("\n")) {
							var row map[string]any
							if err := json.Unmarshal(line, &row); err != nil {
								t.Fatal(err)
							}
							rows++
							cost += row["cost"].(float64)
						}
					}
					if rows != 2 || (costKey != "" && cost != 0.25) {
						t.Fatalf("native or unrelated span lost: rows=%d cost=%v", rows, cost)
					}
					if s.work.inUse.Load() != 0 || s.wire.inUse.Load() != 0 {
						t.Fatal("mixed export leaked memory lease")
					}
				})
			}
		}
	}
}
