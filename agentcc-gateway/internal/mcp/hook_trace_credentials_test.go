package mcp

import (
	"bytes"
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

const traceCredentialCanary = "synthetic-session-credential-value"

func traceCredentialHeaders(field string) map[string]any {
	value := traceCredentialCanary
	if field == "Set-Cookie" {
		value = "session=" + value + "; HttpOnly; Secure"
	}
	return map[string]any{field: value, "Content-Type": "application/json"}
}

func traceCredentialResult(t *testing.T, field string) json.RawMessage {
	t.Helper()
	result, err := json.Marshal(map[string]any{
		"content": []any{}, "structuredContent": map[string]any{"headers": traceCredentialHeaders(field)},
	})
	if err != nil {
		t.Fatal("fixture did not encode")
	}
	return result
}

func assertTraceCredentialContent(t *testing.T, body, field string, result bool) {
	t.Helper()
	var decoded map[string]any
	if json.Unmarshal([]byte(body), &decoded) != nil {
		t.Fatal("captured content did not decode")
	}
	if result {
		decoded, _ = decoded["structuredContent"].(map[string]any)
	}
	headers, ok := decoded["headers"].(map[string]any)
	if !ok || headers[field] != "[redacted]" || headers["Content-Type"] != "application/json" {
		t.Fatal("captured content must retain safe headers and replace the entire credential")
	}
}

// Preserve the verifier's three synthetic fixtures in both arguments and results.
// Full capture must scrub credentials, while all closed policies omit content.
func TestTraceCredentialHeaders(t *testing.T) {
	for _, field := range []string{"Set-Cookie", "X-Access-Key", "Ocp-Apim-Subscription-Key"} {
		t.Run(field, func(t *testing.T) {
			for _, tc := range []struct {
				name      string
				mode      string
				retention TermsRetention
				bodies    bool
				want      bool
			}{
				{"full", "full", "", true, true},
				{"none", "none", "", true, false},
				{"metadata", "metadata", "", true, false},
				{"unspecified", "", "", true, false},
				{"capture-off", "full", "", false, false},
				{"metadata-retention", "full", RetentionMetadataOnly, true, false},
				{"hashed-retention", "full", RetentionHashed, true, false},
			} {
				t.Run(tc.name, func(t *testing.T) {
					e := &traceCapture{}
					h := tracedHarness(t, e)
					d := *h.s.Deps()
					d.Tracer = NewToolTracer(telemetry.Options{Export: e.Export, IncludeBodies: tc.bodies})
					h.s.SetDeps(d)
					result := traceCredentialResult(t, field)
					h.onHook(func(hook string, cc *CallContext) {
						attribute(tc.mode)(hook, cc)
						if hook == hookEvidenceMeta {
							cc.Terms.Retention = tc.retention
							cc.Result = result
						}
					})
					resp := h.call(t, "up_ok", map[string]any{"headers": traceCredentialHeaders(field)})
					if resp.Error != nil || h.sink.only(t).Outcome != "success" {
						t.Fatal("synthetic call did not succeed")
					}
					server, client := spanPair(t, e)
					raw, err := json.Marshal(e.spans)
					if err != nil {
						t.Fatal("spans did not encode")
					}
					if bytes.Contains(raw, []byte(traceCredentialCanary)) {
						t.Fatal("standard header credential retained in exported spans")
					}
					for _, span := range []*otel.Span{server, client} {
						for _, key := range []string{"gen_ai.tool.call.arguments", "gen_ai.tool.call.result"} {
							body, ok := span.Attributes[key].(string)
							if ok != tc.want {
								t.Fatal("content capture policy changed")
							}
							if tc.want {
								assertTraceCredentialContent(t, body, field, key == "gen_ai.tool.call.result")
							}
						}
					}
				})
			}
		})
	}
}

// Inspect the bytes received by a loopback collector, not just exporter enqueue.
func TestTraceCredentialHeadersOTLP(t *testing.T) {
	for _, field := range []string{"Set-Cookie", "X-Access-Key", "Ocp-Apim-Subscription-Key"} {
		t.Run(field, func(t *testing.T) {
			received := make(chan []byte, 8)
			collector := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				raw, err := io.ReadAll(r.Body)
				if err != nil {
					t.Error("collector read failed")
				}
				w.WriteHeader(http.StatusOK)
				select {
				case received <- raw:
				default:
					t.Error("unexpected collector batch count")
				}
			}))
			defer collector.Close()
			exp, err := otel.NewOTLPExporter(otel.OTLPOptions{Endpoint: collector.URL})
			if err != nil {
				t.Fatal("collector exporter creation failed")
			}
			defer exp.Shutdown()
			h := tracedHarness(t, exp)
			result := traceCredentialResult(t, field)
			h.onHook(func(hook string, cc *CallContext) {
				attribute("full")(hook, cc)
				if hook == hookEvidenceMeta {
					cc.Result = result
				}
			})
			resp := h.call(t, "up_ok", map[string]any{"headers": traceCredentialHeaders(field)})
			if resp.Error != nil || h.sink.only(t).Outcome != "success" {
				t.Fatal("synthetic call did not succeed")
			}
			if exp.Shutdown() != nil {
				t.Fatal("collector exporter did not flush")
			}
			var spans []*tracepb.Span
			deadline := time.After(10 * time.Second)
			for len(spans) < 2 {
				select {
				case raw := <-received:
					if bytes.Contains(raw, []byte(traceCredentialCanary)) {
						t.Fatal("credential retained in collector-received OTLP protobuf bytes")
					}
					var td tracepb.TracesData
					if proto.Unmarshal(raw, &td) != nil {
						t.Fatal("collector received invalid OTLP protobuf")
					}
					for _, rs := range td.ResourceSpans {
						for _, ss := range rs.ScopeSpans {
							spans = append(spans, ss.Spans...)
						}
					}
				case <-deadline:
					t.Fatal("collector did not receive the span pair")
				}
			}
			if len(spans) != 2 {
				t.Fatal("unexpected collector span count")
			}
			for _, span := range spans {
				attrs := map[string]string{}
				for _, attr := range span.Attributes {
					attrs[attr.Key] = attr.Value.GetStringValue()
				}
				for _, key := range []string{"gen_ai.tool.call.arguments", "gen_ai.tool.call.result"} {
					assertTraceCredentialContent(t, attrs[key], field, key == "gen_ai.tool.call.result")
				}
			}
		})
	}
}
