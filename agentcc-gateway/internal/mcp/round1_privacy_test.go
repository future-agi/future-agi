package mcp

import (
	"bytes"
	"encoding/base64"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/mcp/meter"
	"github.com/futureagi/agentcc-gateway/internal/mcp/telemetry"
	otel "github.com/futureagi/agentcc-gateway/internal/otel"
	"github.com/futureagi/agentcc-gateway/internal/privacy"
	tracepb "go.opentelemetry.io/proto/otlp/trace/v1"
	"google.golang.org/protobuf/proto"
)

// Synthetic fixtures only. Failures identify a case, never print payloads.
func w5CanaryValue() string {
	return strings.Join([]string{"synthetic", "w5", "verifier", "private", "value"}, "-")
}

var w5Canary = w5CanaryValue()

type w5Fixture struct {
	name   string
	value  any
	needle string
}

func w5Fixtures() []w5Fixture {
	var fixtures []w5Fixture
	fields := []string{
		"Set-Cookie", "X-Access-Key", "Ocp-Apim-Subscription-Key",
		"sEt_CoOkIe", "SET_COOKIE", "set.cookie", "Set Cookie", "Cookie", "COOKIE",
		"X-API-Key", "x_api_key", "x.api.key", "ApiKey", "Authorization", "Proxy_Authorization",
		"X-Amz-Security-Token", "X-Hub-Signature-256", "X-Session-ID", "refreshToken", "Client-Secret",
		"DD-API-KEY", "X-Vault-Token", "X-Consul-Token", "Private-Token", "X-Shopify-Access-Token",
		"x-api-secret", "X-Goog-Api-Key", "api_key", "nestedCredential",
		"X-Cookie", "X_Set_Cookie", "Set-Cookie2", "CF-Access-Jwt-Assertion", "X-Goog-Iap-Jwt-Assertion",
	}
	for _, field := range fields {
		fixtures = append(fixtures, w5Fixture{"field-" + field, map[string]any{"headers": map[string]any{field: w5Canary}, "safe": "retained"}, w5Canary})
	}
	basic := base64.StdEncoding.EncodeToString([]byte("synthetic-user:" + w5Canary))
	fixtureText := []struct{ name, text, needle string }{
		{"text-cookie", "Cookie: SID=" + w5Canary, w5Canary},
		{"text-set-cookie", "Set-Cookie: SID=" + w5Canary + "; HttpOnly; Secure", w5Canary},
		{"text-session-cookie", "Set-Cookie: session=" + w5Canary + "; HttpOnly", w5Canary},
		{"text-basic-authorization", "Authorization: Basic " + basic, basic},
		{"text-bearer-authorization", "Authorization: Bearer " + w5Canary, w5Canary},
		{"text-vendor-header", "Ocp-Apim-Subscription-Key: " + w5Canary, w5Canary},
		{"text-underscore-header", "X_Access_Key: " + w5Canary, w5Canary},
		{"text-json-cookie", `{"Set-Cookie":"SID=` + w5Canary + `"}`, w5Canary},
		{"text-json-api-key", `{"X_API_KEY":"` + w5Canary + `"}`, w5Canary},
		{"text-json-password", `{"password":"` + w5Canary + `"}`, w5Canary},
		{"url-userinfo", "https://synthetic-user:" + w5Canary + "@service.invalid/path", w5Canary},
		{"url-query-api-key", "https://service.invalid/path?api_key=" + w5Canary + "&safe=1", w5Canary},
		{"url-query-access-key", "https://service.invalid/path?X-Access-Key=" + w5Canary, w5Canary},
		{"url-query-cookie", "https://service.invalid/path?session=" + w5Canary, w5Canary},
		{"url-query-password", "https://service.invalid/path?password=" + w5Canary, w5Canary},
		{"url-query-encoded-name", "https://service.invalid/path?api%5Fkey=" + w5Canary, w5Canary},
	}
	for _, f := range fixtureText {
		fixtures = append(fixtures, w5Fixture{f.name, map[string]any{"nested": []any{map[string]any{"text": f.text}}, "safe": "retained"}, f.needle})
	}
	// A URL and text error response are ordinary string content, not special map fields.
	fixtures = append(fixtures,
		w5Fixture{"tool-error-text", map[string]any{"content": []any{map[string]any{"type": "text", "text": "request failed; Cookie: SID=" + w5Canary}}, "isError": true}, w5Canary},
		w5Fixture{"array-header-pairs", map[string]any{"headers": []any{[]any{"Set-Cookie", "SID=" + w5Canary}}, "safe": "retained"}, w5Canary},
		w5Fixture{"header-name-value", map[string]any{"headers": []any{map[string]any{"name": "Set-Cookie", "value": "SID=" + w5Canary}}, "safe": "retained"}, w5Canary},
	)
	return fixtures
}

func TestW5IndependentContent(t *testing.T) {
	for _, f := range w5Fixtures() {
		t.Run(f.name, func(t *testing.T) {
			raw, err := json.Marshal(f.value)
			if err != nil {
				t.Fatal("fixture encoding failed")
			}
			for _, mode := range []string{meter.ModeFull, meter.ModeMetadata, meter.ModeNone, "", "unknown"} {
				t.Run("mode-"+mode, func(t *testing.T) {
					for i, r := range []*privacy.Redactor{nil, privacy.New(privacy.ModeNone, nil), privacy.New(privacy.ModeFull, nil)} {
						got := telemetry.Content(raw, mode, r)
						if strings.Contains(got, f.needle) {
							t.Errorf("credential retained in content; redactor variant %d", i)
						}
						if mode != meter.ModeFull && got != "" {
							t.Error("closed mode exported content")
						}
						if len(got) > meter.MaxContentBytes {
							t.Error("content exceeded bound")
						}
					}
				})
			}
		})
	}
}

func TestW5IndependentCollector(t *testing.T) {
	policies := []struct {
		name, mode string
		capture    bool
		retention  TermsRetention
	}{
		{"full", "full", true, ""}, {"metadata", "metadata", true, ""}, {"none", "none", true, ""},
		{"default", "", true, ""}, {"capture-off", "full", false, ""},
		{"retention-metadata", "full", true, RetentionMetadataOnly}, {"retention-hashed", "full", true, RetentionHashed},
	}
	for _, p := range policies {
		t.Run(p.name, func(t *testing.T) {
			for _, f := range w5Fixtures() {
				t.Run(f.name, func(t *testing.T) {
					received := make(chan []byte, 8)
					collector := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, req *http.Request) {
						raw, err := io.ReadAll(req.Body)
						if err != nil {
							t.Error("collector read failed")
						}
						received <- raw
						w.WriteHeader(http.StatusOK)
					}))
					defer collector.Close()
					exp, err := otel.NewOTLPExporter(otel.OTLPOptions{Endpoint: collector.URL})
					if err != nil {
						t.Fatal("collector exporter initialization failed")
					}
					defer exp.Shutdown()
					captured := &traceCapture{}
					h := tracedHarness(t, captured)
					d := *h.s.Deps()
					d.Tracer = NewToolTracer(telemetry.Options{Export: func(spans []*otel.Span) error { _ = captured.Export(spans); return exp.Export(spans) }, IncludeBodies: p.capture})
					h.s.SetDeps(d)
					result, err := json.Marshal(map[string]any{"content": []any{}, "structuredContent": f.value, "_meta": map[string]any{"unapproved": w5Canary}})
					if err != nil {
						t.Fatal("fixture encoding failed")
					}
					h.onHook(func(hook string, cc *CallContext) {
						attribute(p.mode)(hook, cc)
						if hook == hookEvidenceMeta {
							cc.Terms.Retention = p.retention
							cc.Result = result
						}
					})
					resp := h.call(t, "up_ok", f.value)
					if resp.Error != nil || h.sink.only(t).Outcome != "success" {
						t.Fatal("synthetic call did not succeed")
					}
					_, _ = spanPair(t, captured)
					wantContent := p.name == "full"
					for _, span := range captured.spans {
						for _, key := range []string{"gen_ai.tool.call.arguments", "gen_ai.tool.call.result"} {
							body, ok := span.Attributes[key].(string)
							if ok != wantContent {
								t.Error("content capture policy changed")
							}
							if strings.Contains(body, f.needle) {
								t.Errorf("credential retained in exported span attribute %s", key)
							}
						}
						raw, _ := json.Marshal(span)
						if bytes.Contains(raw, []byte(f.needle)) {
							t.Error("credential retained in exported span object")
						}
					}
					if exp.Shutdown() != nil {
						t.Fatal("collector flush failed")
					}
					spanCount := 0
					deadline := time.After(10 * time.Second)
					for spanCount < 2 {
						select {
						case raw := <-received:
							if bytes.Contains(raw, []byte(f.needle)) {
								t.Error("credential retained in collector-received OTLP bytes")
							}
							var td tracepb.TracesData
							if proto.Unmarshal(raw, &td) != nil {
								t.Fatal("collector received invalid protobuf")
							}
							for _, resource := range td.ResourceSpans {
								for _, scope := range resource.ScopeSpans {
									for _, span := range scope.Spans {
										spanCount++
										// Inspect all status/event fields as well as normal attributes.
										rawSpan, _ := proto.Marshal(span)
										if bytes.Contains(rawSpan, []byte(f.needle)) {
											t.Error("credential retained in decoded collector span")
										}
										for _, event := range span.Events {
											b, _ := proto.Marshal(event)
											if bytes.Contains(b, []byte(f.needle)) {
												t.Error("credential retained in span event")
											}
										}
									}
								}
							}
						case <-deadline:
							t.Fatal("collector did not receive span pair")
						}
					}
					if spanCount != 2 {
						t.Fatal("collector span count changed")
					}
				})
			}
		})
	}
}

func TestW5IndependentFailureCollector(t *testing.T) {
	for _, mode := range []string{"full", "metadata", "none", ""} {
		t.Run("mode-"+mode, func(t *testing.T) {
			e := &traceCapture{}
			h := tracedHarness(t, e)
			h.onHook(func(hook string, cc *CallContext) {
				attribute(mode)(hook, cc)
				if hook == hookEvidenceMeta && cc.Decision != nil {
					cc.Decision.Message = "upstream error; Cookie: SID=" + w5Canary
				}
			})
			_ = h.call(t, "up_pii", nil)
			raw, _ := json.Marshal(e.spans)
			if bytes.Contains(raw, []byte(w5Canary)) {
				t.Error("raw upstream error exported")
			}
			for _, s := range e.spans {
				if _, ok := s.Attributes["error.message"]; ok {
					t.Error("raw error message attribute exported")
				}
			}
		})
	}
}
