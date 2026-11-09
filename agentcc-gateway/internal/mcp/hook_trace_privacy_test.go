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
func spanPrivacyCanary() string {
	return base64.RawURLEncoding.EncodeToString([]byte(strings.Repeat("synthetic-fixture-", 3)))
}

type spanPrivacyFixture struct {
	name   string
	value  any
	needle string
}

func spanPrivacyFixtures() []spanPrivacyFixture {
	canary := spanPrivacyCanary()
	var fixtures []spanPrivacyFixture
	fields := []string{
		"Set-Cookie", "X-Access-Key", "Ocp-Apim-Subscription-Key",
		"sEt_CoOkIe", "SET_COOKIE", "set.cookie", "Set Cookie", "Cookie", "COOKIE",
		"X-API-Key", "x_api_key", "x.api.key", "ApiKey", "Authorization", "Proxy_Authorization",
		"X-Amz-Security-Token", "X-Hub-Signature-256", "X-Session-ID", "refreshToken", "Client-Secret",
		"DD-API-KEY", "X-Vault-Token", "X-Consul-Token", "Private-Token", "X-Shopify-Access-Token",
		"x-api-secret", "X-Goog-Api-Key", "api_key", "nestedCredential",
		"X-Cookie", "X_Set_Cookie", "Set-Cookie2", "CF-Access-Jwt-Assertion", "X-Goog-Iap-Jwt-Assertion",
		"Cookie-Checksum", "vendor/cookie/42", "X-JWT2", "Assertion", "Bearer", "Subscription",
		"X.C_o-o k/i/e99", "vendor-Assertion-256",
	}
	for _, field := range fields {
		fixtures = append(fixtures, spanPrivacyFixture{"field-" + field, map[string]any{"headers": map[string]any{field: canary}, "safe": "retained"}, canary})
	}
	basic := base64.StdEncoding.EncodeToString([]byte("synthetic-user:" + canary))
	jwt := base64.RawURLEncoding.EncodeToString([]byte(`{"alg":"HS256"}`)) + "." + base64.RawURLEncoding.EncodeToString([]byte(`{"sub":"fixture"}`)) + "." + canary
	encoded, _ := json.Marshal(`{"X-Cookie":"` + canary + `","safe":"retained"}`)
	fixtureText := []struct{ name, text, needle string }{
		{"text-cookie", "Cookie: SID=" + canary, canary},
		{"text-set-cookie", "Set-Cookie: SID=" + canary + "; HttpOnly; Secure", canary},
		{"text-session-cookie", "Set-Cookie: session=" + canary + "; HttpOnly", canary},
		{"text-basic-authorization", "Authorization: Basic " + basic, basic},
		{"text-bearer-authorization", "Authorization: Bearer " + canary, canary},
		{"text-vendor-header", "Ocp-Apim-Subscription-Key: " + canary, canary},
		{"text-underscore-header", "X_Access_Key: " + canary, canary},
		{"text-json-cookie", `{"Set-Cookie":"SID=` + canary + `"}`, canary},
		{"text-json-api-key", `{"X_API_KEY":"` + canary + `"}`, canary},
		{"text-json-password", `{"password":"` + canary + `"}`, canary},
		{"url-userinfo", "https://synthetic-user:" + canary + "@service.invalid/path", canary},
		{"url-query-api-key", "https://service.invalid/path?api_key=" + canary + "&safe=1", canary},
		{"url-query-access-key", "https://service.invalid/path?X-Access-Key=" + canary, canary},
		{"url-query-cookie", "https://service.invalid/path?session=" + canary, canary},
		{"url-query-password", "https://service.invalid/path?password=" + canary, canary},
		{"url-query-encoded-name", "https://service.invalid/path?api%5Fkey=" + canary, canary},
		{"text-jwt", "response " + jwt, canary},
		{"text-pair-quoted", "X_Assertion='" + canary + " with spaces'", canary},
		{"text-pair-separators", "x.cookie2 = " + canary + "; safe=retained", canary},
		{"text-multiline", "Content-Type: text/plain\r\nCookie: SID=" + canary + "; other=" + canary + "\r\nAccept: text/plain", canary},
		{"text-json-prefixed", `result: {"nested":{"X-Cookie":"` + canary + `"},"safe":"retained"}`, canary},
		{"text-json-pairs", `result: [["X-JWT","` + canary + `"]]`, canary},
		{"text-json-object", `result: [{"name":"Assertion","value":"` + canary + `"}]`, canary},
		{"text-json-escaped-name", `{"api_key":"` + canary + `"}`, canary},
		{"text-json-encoded", string(encoded), canary},
		{"url-user-only", "https://" + canary + "@service.invalid/path", canary},
		{"url-relative-userinfo", "//user:" + canary + "@service.invalid/path", canary},
		{"url-in-prose", "see https://user:" + canary + "@service.invalid/path?X-Cookie=" + canary + "&safe=1 for details", canary},
		{"url-repeated-query", "https://service.invalid/path?jwt=" + canary + "&jwt=" + canary, canary},
		{"url-double-encoded-name", "https://service.invalid/path?api%255Fkey=" + canary, canary},
		{"url-malformed-query", "https://service.invalid/path?session=" + canary + ";invalid", canary},
		{"url-query-jwt", "https://service.invalid/path?message=" + jwt, canary},
		{"url-fragment", "https://service.invalid/path#session=" + canary, canary},
	}
	for _, f := range fixtureText {
		fixtures = append(fixtures, spanPrivacyFixture{f.name, map[string]any{"nested": []any{map[string]any{"text": f.text}}, "safe": "retained"}, f.needle})
	}
	// A URL and text error response are ordinary string content, not special map fields.
	fixtures = append(fixtures,
		spanPrivacyFixture{"tool-error-text", map[string]any{"content": []any{map[string]any{"type": "text", "text": "request failed; Cookie: SID=" + canary}}, "isError": true}, canary},
		spanPrivacyFixture{"array-header-pairs", map[string]any{"headers": []any{[]any{"Set-Cookie", "SID=" + canary}}, "safe": "retained"}, canary},
		spanPrivacyFixture{"header-name-value", map[string]any{"headers": []any{map[string]any{"name": "Set-Cookie", "value": "SID=" + canary}}, "safe": "retained"}, canary},
		spanPrivacyFixture{"header-structured-value", map[string]any{"headers": []any{map[string]any{"NAME": "X-Jwt", "VALUE": []any{canary}}}, "safe": "retained"}, canary},
		spanPrivacyFixture{"header-pair-structured", map[string]any{"headers": []any{[]any{"Assertion", map[string]any{"opaque": canary}}}, "safe": "retained"}, canary},
	)
	return fixtures
}

func TestSpanPrivacyContent(t *testing.T) {
	for _, f := range spanPrivacyFixtures() {
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

func TestSpanPrivacyCollector(t *testing.T) {
	canary := spanPrivacyCanary()
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
			for _, f := range spanPrivacyFixtures() {
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
					result, err := json.Marshal(map[string]any{"content": []any{}, "structuredContent": f.value, "_meta": map[string]any{"unapproved": canary}})
					if err != nil {
						t.Fatal("fixture encoding failed")
					}
					h.onHook(func(hook string, cc *CallContext) {
						attribute(p.mode)(hook, cc)
						if hook == hookEvidenceMeta {
							cc.Terms.Retention = p.retention
							cc.Result = result
							// Scalar dimensions are retained in closed modes too; they
							// must use the same sanitizer before the column byte limit.
							cc.ToolCallID = string(result)
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
							if wantContent && f.value.(map[string]any)["safe"] == "retained" && !strings.Contains(body, "retained") {
								t.Error("safe-field control missing from exported content")
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

func TestSpanPrivacyFailureOmitted(t *testing.T) {
	canary := spanPrivacyCanary()
	for _, mode := range []string{"full", "metadata", "none", ""} {
		t.Run("mode-"+mode, func(t *testing.T) {
			e := &traceCapture{}
			h := tracedHarness(t, e)
			h.onHook(func(hook string, cc *CallContext) {
				attribute(mode)(hook, cc)
				if hook == hookEvidenceMeta && cc.Decision != nil {
					cc.Decision.Message = "upstream error; Cookie: SID=" + canary
				}
			})
			_ = h.call(t, "up_pii", nil)
			raw, _ := json.Marshal(e.spans)
			if bytes.Contains(raw, []byte(canary)) {
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
