package mcp

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/mcp/telemetry"
	otel "github.com/futureagi/agentcc-gateway/internal/otel"
	tracepb "go.opentelemetry.io/proto/otlp/trace/v1"
	"google.golang.org/protobuf/proto"
)

// These calls receive their result from a chunked SSE upstream, not a result
// override or a scalar-dimension injection. Payloads are runtime synthetic only.
func TestIndependentPrivacyStreamedCollector(t *testing.T) {
	opaque := base64.RawURLEncoding.EncodeToString([]byte(strings.Repeat("streamed-synthetic-fixture-", 2)))
	basic := base64.StdEncoding.EncodeToString([]byte("fixture-user:" + opaque))
	cases := []struct {
		name    string
		result  any
		private bool
	}{
		{"folded-header", map[string]any{"content": []any{map[string]any{"type": "text", "text": "Authorization: Basic\r\n " + basic + "\r\nAccept: text/plain"}}, "safe": "safe-control"}, true},
		{"adjacent-text-chunks", map[string]any{"content": []any{map[string]any{"type": "text", "text": "Authorization: Basic "}, map[string]any{"type": "text", "text": basic}}, "safe": "safe-control"}, true},
		{"header-key-value", map[string]any{"content": []any{}, "structuredContent": map[string]any{"headers": []any{map[string]any{"key": "Authorization", "value": "Basic " + basic}}}, "safe": "safe-control"}, true},
		{"safe", map[string]any{"content": []any{map[string]any{"type": "text", "text": "done safe-control"}}}, false},
	}
	policies := []struct {
		name, mode string
		capture    bool
		retention  TermsRetention
	}{
		{"full", "full", true, ""}, {"metadata", "metadata", true, ""}, {"none", "none", true, ""}, {"default", "", true, ""},
		{"capture-off", "full", false, ""}, {"retention-metadata", "full", true, RetentionMetadataOnly}, {"retention-hashed", "full", true, RetentionHashed},
	}
	for _, p := range policies {
		t.Run(p.name, func(t *testing.T) {
			for _, f := range cases {
				t.Run(f.name, func(t *testing.T) {
					received := make(chan []byte, 4)
					collector := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
						raw, err := io.ReadAll(r.Body)
						if err != nil {
							t.Error("collector read failed")
						}
						received <- raw
						w.WriteHeader(http.StatusOK)
					}))
					defer collector.Close()
					exp, err := otel.NewOTLPExporter(otel.OTLPOptions{Endpoint: collector.URL})
					if err != nil {
						t.Fatal("collector initialization failed")
					}
					defer exp.Shutdown()
					upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
						var req Message
						if json.NewDecoder(r.Body).Decode(&req) != nil {
							http.Error(w, "invalid fixture request", http.StatusBadRequest)
							return
						}
						if req.IsNotification() {
							w.WriteHeader(http.StatusAccepted)
							return
						}
						var result any
						switch req.Method {
						case MethodInitialize:
							result = map[string]any{"protocolVersion": "2025-11-25", "capabilities": map[string]any{"tools": map[string]any{}}, "serverInfo": map[string]any{"name": "fixture", "version": "1"}}
						case MethodToolsList:
							result = map[string]any{"tools": []any{map[string]any{"name": "ok", "inputSchema": map[string]any{"type": "object"}}}}
						case MethodToolsCall:
							w.Header().Set("Content-Type", "text/event-stream")
							progress, _ := json.Marshal(map[string]any{"jsonrpc": "2.0", "method": "notifications/progress", "params": map[string]any{"progressToken": "fixture", "progress": 1, "message": opaque}})
							_, _ = fmt.Fprintf(w, "event: message\ndata: %s\n\n", progress)
							final, _ := json.Marshal(map[string]any{"jsonrpc": "2.0", "id": req.ID, "result": f.result})
							event := append([]byte("event: message\ndata: "), final...)
							event = append(event, []byte("\n\n")...)
							for len(event) > 0 {
								n := len(event)
								if n > 19 {
									n = 19
								}
								_, _ = w.Write(event[:n])
								if flusher, ok := w.(http.Flusher); ok {
									flusher.Flush()
								}
								event = event[n:]
							}
							return
						default:
							result = map[string]any{}
						}
						w.Header().Set("Content-Type", "application/json")
						_ = json.NewEncoder(w).Encode(map[string]any{"jsonrpc": "2.0", "id": req.ID, "result": result})
					}))
					defer upstream.Close()
					captured := &traceCapture{}
					h := tracedHarness(t, captured)
					client := NewClient(ClientConfig{ServerID: "stream", URL: upstream.URL, TransportType: "http"})
					ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
					defer cancel()
					if client.Connect(ctx) != nil {
						t.Fatal("stream fixture connection failed")
					}
					h.s.RegisterClient(client)
					d := *h.s.Deps()
					d.Tracer = NewToolTracer(telemetry.Options{IncludeBodies: p.capture, Export: func(s []*otel.Span) error { _ = captured.Export(s); return exp.Export(s) }})
					h.s.SetDeps(d)
					h.onHook(func(hook string, cc *CallContext) {
						attribute(p.mode)(hook, cc)
						if hook == hookEvidenceMeta {
							cc.Terms.Retention = p.retention
						}
					})
					response := h.call(t, "stream_ok", map[string]any{"safe": "safe-control"})
					if response.Error != nil {
						t.Fatal("streamed synthetic call failed")
					}
					_, _ = spanPair(t, captured)
					needles := []string{opaque, basic}
					for _, span := range captured.spans {
						b, _ := json.Marshal(span)
						if independentPrivacyContains(b, needles) {
							t.Error("credential retained in streamed exported span")
						}
						body, ok := span.Attributes["gen_ai.tool.call.result"].(string)
						if ok != (p.name == "full") {
							t.Error("streamed body policy changed")
						}
						if ok && !strings.Contains(body, "safe-control") {
							t.Error("safe streamed result removed")
						}
					}
					if exp.Shutdown() != nil {
						t.Fatal("collector flush failed")
					}
					count := 0
					deadline := time.After(10 * time.Second)
					for count < 2 {
						select {
						case b := <-received:
							if independentPrivacyContains(b, needles) {
								t.Error("credential retained in streamed collector bytes")
							}
							var td tracepb.TracesData
							if proto.Unmarshal(b, &td) != nil {
								t.Fatal("collector protobuf invalid")
							}
							for _, r := range td.ResourceSpans {
								for _, scope := range r.ScopeSpans {
									for _, span := range scope.Spans {
										count++
										b, _ := proto.Marshal(span)
										if independentPrivacyContains(b, needles) {
											t.Error("credential retained in streamed decoded span")
										}
									}
								}
							}
						case <-deadline:
							t.Fatal("collector did not receive streamed span pair")
						}
					}
				})
			}
		})
	}
}
