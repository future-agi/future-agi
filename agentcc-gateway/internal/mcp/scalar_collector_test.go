package mcp

import (
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/mcp/telemetry"
	otel "github.com/futureagi/agentcc-gateway/internal/otel"
	tracepb "go.opentelemetry.io/proto/otlp/trace/v1"
	"google.golang.org/protobuf/proto"
)

// Every scalar emission site is bounded independently of opt-in content handling.
// The same corpus covers all six representations in every closed-body policy in
// TestIndependentPrivacyCollector; this test pins every scalar, including errors.
func TestScalarPrivacyCollector(t *testing.T) {
	names := []string{"gen_ai.tool.call.id", "gen_ai.tool.name", "agentcc.mcp.server_id", "agentcc.mcp.virtual_server", "agent.id", "agent.run_id", "agent.step", "error.type", "mcp.protocol.version", "agentcc.org_id", "agentcc.api_key_id", "mcp.error.code", "mcp.error.origin", "mcp.method.name", "mcp.content_mode", "agentcc.billing_mode", "mcp.outcome", "mcp.session.id"}
	for _, f := range independentPrivacyCases() {
		t.Run(f.name, func(t *testing.T) {
			received := make(chan []byte, 4)
			collector := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				b, err := io.ReadAll(r.Body)
				if err != nil {
					t.Error("collector read failed")
				}
				received <- b
				w.WriteHeader(http.StatusOK)
			}))
			defer collector.Close()
			exp, err := otel.NewOTLPExporter(otel.OTLPOptions{Endpoint: collector.URL})
			if err != nil {
				t.Fatal("exporter initialization failed")
			}
			defer exp.Shutdown()
			raw, _ := json.Marshal(f.value)
			capture := &traceCapture{}
			tr := telemetry.Start(telemetry.Options{ServiceName: string(raw), Export: func(s []*otel.Span) error { _ = capture.Export(s); return exp.Export(s) }}, "", time.Now())
			tr.Upstream()
			attrs := make(map[string]any, len(names))
			for _, name := range names {
				attrs[name] = string(raw)
			}
			tr.Finish(attrs, true, time.Time{}, time.Time{})
			for _, s := range capture.spans {
				b, _ := json.Marshal(s)
				if independentPrivacyContains(b, f.needles) {
					t.Error("credential-shaped value appears in exported scalar span")
				}
				for _, name := range names {
					if s.Attributes[name] != "[redacted]" {
						t.Errorf("unapproved scalar not replaced: %s", name)
					}
				}
			}
			if exp.Shutdown() != nil {
				t.Fatal("collector flush failed")
			}
			count := 0
			for count < 2 {
				select {
				case b := <-received:
					if independentPrivacyContains(b, f.needles) {
						t.Error("credential-shaped value appears in collector bytes")
					}
					var td tracepb.TracesData
					if proto.Unmarshal(b, &td) != nil {
						t.Fatal("invalid collector protobuf")
					}
					for _, r := range td.ResourceSpans {
						for _, s := range r.ScopeSpans {
							count += len(s.Spans)
						}
					}
				case <-time.After(10 * time.Second):
					t.Fatal("collector did not receive spans")
				}
			}
		})
	}
}
