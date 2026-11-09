package mcp

import (
	"bytes"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"net/url"
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

type independentPrivacyCase struct {
	name    string
	value   any
	needles []string
}

func independentPrivacyCases() []independentPrivacyCase {
	opaque := base64.RawURLEncoding.EncodeToString([]byte(strings.Repeat("independent-synthetic-fixture-", 2)))
	basic := base64.StdEncoding.EncodeToString([]byte("fixture-user:" + opaque))
	provider := "sk-" + opaque[:32]
	jwt := base64.RawURLEncoding.EncodeToString([]byte(`{"alg":"HS256","typ":"JWT"}`)) + "." + base64.RawURLEncoding.EncodeToString([]byte(`{"sub":"fixture"}`)) + "." + opaque
	pct := func(s string) string {
		var b strings.Builder
		for _, c := range []byte(s) {
			fmt.Fprintf(&b, "%%%02X", c)
		}
		return b.String()
	}
	var cases []independentPrivacyCase
	add := func(name string, value any, needles ...string) {
		cases = append(cases, independentPrivacyCase{name, value, needles})
	}
	text := func(name, value string, needles ...string) {
		add(name, map[string]any{"message": value, "safe": "safe-control"}, needles...)
	}
	for i, field := range []string{"AUTHORIZATION", "Proxy-Authorization", "Cookie", "Set-Cookie", "X-API-Key", "X_Auth_42", "Ocp-Apim-Subscription-Key", "X-Amz-Security-Token", "X-Hub-Signature-256", "CF-Access-Jwt-Assertion", "clientSecret", "privateKey", "refreshToken", "sessionId", "credential", "passWord", "X.C_o-o k/i/e99", "Assertion", "Subscription", "api_key", "passwd", "pwd", "passphrase", "clientPassphrase", "jwk", "p%61ssword", "%61%70%69%5F%6B%65%79"} {
		add(fmt.Sprintf("field-%02d", i), map[string]any{"headers": map[string]any{field: opaque}, "safe": "safe-control"}, opaque)
	}
	add("nested-pairs", map[string]any{"headers": []any{[]any{"Authorization", basic}, []any{"Content-Type", "text/plain"}}, "safe": "safe-control"}, basic)
	add("nested-name-values", map[string]any{"headers": []any{map[string]any{"name": "Authorization", "values": []any{basic}}}, "safe": "safe-control"}, basic)
	add("nested-header-value", map[string]any{"headers": []any{map[string]any{"header": "Authorization", "value": "Basic " + basic}}, "safe": "safe-control"}, basic)
	add("nested-key-value", map[string]any{"headers": []any{map[string]any{"key": "Authorization", "value": "Basic " + basic}}, "safe": "safe-control"}, basic)
	add("jwk-private", map[string]any{"jwk": map[string]any{"kty": "oct", "k": opaque}, "safe": "safe-control"}, opaque)
	for i, v := range []string{
		"Authorization: Basic " + basic,
		"Cookie: SID=" + opaque + "; Path=/",
		"Set-Cookie: SID=" + opaque + "; HttpOnly; Secure",
		"Content-Type: text/plain\r\nAuthorization: Basic " + basic + "\r\nAccept: application/json",
		"Authorization: Basic\r\n " + basic + "\r\nAccept: application/json",
		"Authorization:\n\tBasic " + basic + "\nAccept: application/json",
		"Cookie: SID=\r\n\t" + opaque + "\r\nAccept: text/plain",
		"X-API-Key:\r\n " + opaque + "\r\nAccept: text/plain",
		"X-API-Key = '" + opaque + "' safe=retained",
		"X-API-Key = \"" + opaque + " with spaces\"; safe=retained",
		"request failed: Authorization: Basic " + basic,
		"request failed:\nSet-Cookie: SID=" + opaque,
		"X_API_KEY=" + opaque + "; safe=retained",
		"'Authorization': 'Basic " + basic + "'",
		"api%5Fkey%3D" + pct(opaque),
		pct("Authorization: Basic " + basic),
		base64.StdEncoding.EncodeToString([]byte("Authorization: Basic " + basic)),
		"event " + jwt,
		"provider " + provider,
	} {
		text(fmt.Sprintf("text-%02d", i), v, opaque, basic, provider, pct(opaque), pct(basic), base64.StdEncoding.EncodeToString([]byte("Authorization: Basic "+basic)))
	}
	nested := `{"Authorization":"Basic ` + basic + `","safe":"retained"}`
	for i := 0; i < 4; i++ {
		text(fmt.Sprintf("json-in-json-%d", i), "envelope: "+nested, basic)
		b, _ := json.Marshal(nested)
		nested = string(b)
	}
	for i, v := range []string{
		"https://user:" + opaque + "@service.invalid/path",
		"//" + opaque + "@service.invalid/path",
		"https://service.invalid/path?api_key=" + opaque + "&safe=1",
		"https://service.invalid/path?%61%70%69%5f%6b%65%79=" + opaque,
		"https://service.invalid/path?%2561%2570%2569%255f%256b%2565%2579=" + opaque,
		"https://service.invalid/path?message=" + url.QueryEscape("Authorization: Basic "+basic),
		"https://service.invalid/path?message=" + url.QueryEscape("Authorization: Basic\r\n "+basic),
		"https://service.invalid/path?payload=" + url.QueryEscape(nested),
		"https://service.invalid/path#Cookie:SID=" + opaque,
		"https://service.invalid/" + provider,
		"https://" + provider + ".service.invalid/path",
		"https://service.invalid/path?" + provider + "=safe",
		"https://service.invalid/" + pct(provider),
		"https://service.invalid/" + url.PathEscape(pct(provider)),
		"https://service.invalid/path?next=" + url.QueryEscape("https://user:"+opaque+"@service.invalid/path"),
		"see (https://user:" + opaque + "@service.invalid/path?session=" + opaque + ") for details",
	} {
		text(fmt.Sprintf("url-%02d", i), v, opaque, basic, provider, pct(provider), url.PathEscape(pct(provider)))
	}
	add("url-map-key", map[string]any{"https://user:" + opaque + "@service.invalid/path": "retained", "safe": "safe-control"}, opaque)
	add("error-envelope", map[string]any{"error": map[string]any{"code": -32000, "message": "failed; Authorization: Basic " + basic, "data": map[string]any{"headers": map[string]any{"Cookie": opaque}}}, "safe": "safe-control"}, opaque, basic)
	add("stream-content-chunks", map[string]any{"content": []any{map[string]any{"type": "text", "text": "Authorization: Basic "}, map[string]any{"type": "text", "text": basic}}, "safe": "safe-control"}, basic)
	add("stream-string-chunks", map[string]any{"chunks": []any{"Cookie: SID=", opaque}, "safe": "safe-control"}, opaque)
	add("encoded-envelope", map[string]any{"encoding": "base64", "body": base64.StdEncoding.EncodeToString([]byte(`{"Authorization":"Basic ` + basic + `"}`)), "safe": "safe-control"}, base64.StdEncoding.EncodeToString([]byte(`{"Authorization":"Basic `+basic+`"}`)))
	add("event-link-baggage", map[string]any{"_meta": map[string]any{"events": []any{map[string]any{"name": opaque}}, "links": []any{map[string]any{"attributes": map[string]any{"private": opaque}}}}, "safe": "safe-control"}, opaque)
	return cases
}

func independentPrivacyContains(raw []byte, needles []string) bool {
	for _, n := range needles {
		if n != "" && bytes.Contains(raw, []byte(n)) {
			return true
		}
	}
	return false
}

func TestIndependentPrivacyContent(t *testing.T) {
	for _, f := range independentPrivacyCases() {
		t.Run(f.name, func(t *testing.T) {
			raw, err := json.Marshal(f.value)
			if err != nil {
				t.Fatal("fixture encoding failed")
			}
			for _, mode := range []string{meter.ModeFull, meter.ModeMetadata, meter.ModeNone, "", "unknown"} {
				t.Run("mode-"+mode, func(t *testing.T) {
					for i, r := range []*privacy.Redactor{nil, privacy.New(privacy.ModeNone, nil), privacy.New(privacy.ModeFull, nil)} {
						got := telemetry.Content(raw, mode, r)
						if independentPrivacyContains([]byte(got), f.needles) {
							t.Errorf("credential retained in content, redactor %d", i)
						}
						if mode != meter.ModeFull && got != "" {
							t.Error("closed content mode retained a body")
						}
						if mode == meter.ModeFull && i != 2 && !strings.Contains(got, "safe-control") {
							t.Error("safe control removed")
						}
						if len(got) > meter.MaxContentBytes {
							t.Error("byte bound exceeded")
						}
					}
				})
			}
		})
	}
}

func TestIndependentPrivacyCollector(t *testing.T) {
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
			for _, f := range independentPrivacyCases() {
				t.Run(f.name, func(t *testing.T) {
					received := make(chan []byte, 4)
					srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
						b, err := io.ReadAll(r.Body)
						if err != nil {
							t.Error("collector read failed")
						}
						received <- b
						w.WriteHeader(http.StatusOK)
					}))
					defer srv.Close()
					exporter, err := otel.NewOTLPExporter(otel.OTLPOptions{Endpoint: srv.URL})
					if err != nil {
						t.Fatal("exporter initialization failed")
					}
					defer exporter.Shutdown()
					capture := &traceCapture{}
					h := tracedHarness(t, capture)
					d := *h.s.Deps()
					d.Tracer = NewToolTracer(telemetry.Options{IncludeBodies: p.capture, Export: func(s []*otel.Span) error { _ = capture.Export(s); return exporter.Export(s) }})
					h.s.SetDeps(d)
					raw, _ := json.Marshal(f.value)
					result, _ := json.Marshal(map[string]any{"content": []any{}, "structuredContent": f.value, "_meta": map[string]any{"private": f.needles[0]}})
					h.onHook(func(hook string, cc *CallContext) {
						attribute(p.mode)(hook, cc)
						if hook == hookEvidenceMeta {
							cc.Terms.Retention = p.retention
							cc.Result = result
							cc.ToolCallID = string(raw)
						}
					})
					response := h.call(t, "up_ok", f.value)
					if response.Error != nil {
						t.Fatal("synthetic tools call failed")
					}
					_, _ = spanPair(t, capture)
					for _, s := range capture.spans {
						b, _ := json.Marshal(s)
						if independentPrivacyContains(b, f.needles) {
							t.Error("credential retained in exported span object")
						}
						for _, key := range []string{"gen_ai.tool.call.arguments", "gen_ai.tool.call.result"} {
							body, ok := s.Attributes[key].(string)
							if ok != (p.name == "full") {
								t.Error("body capture policy changed")
							}
							if ok && !strings.Contains(body, "safe-control") {
								t.Error("safe control removed from exported content")
							}
							if independentPrivacyContains([]byte(body), f.needles) {
								t.Errorf("credential retained in %s", key)
							}
						}
					}
					if exporter.Shutdown() != nil {
						t.Fatal("collector flush failed")
					}
					spanCount := 0
					deadline := time.After(10 * time.Second)
					for spanCount < 2 {
						select {
						case b := <-received:
							if independentPrivacyContains(b, f.needles) {
								t.Error("credential retained in collector bytes")
							}
							var td tracepb.TracesData
							if proto.Unmarshal(b, &td) != nil {
								t.Fatal("invalid collector protobuf")
							}
							for _, r := range td.ResourceSpans {
								for _, scope := range r.ScopeSpans {
									for _, s := range scope.Spans {
										spanCount++
										b, _ := proto.Marshal(s)
										if independentPrivacyContains(b, f.needles) {
											t.Error("credential retained in decoded span")
										}
										for _, event := range s.Events {
											b, _ := proto.Marshal(event)
											if independentPrivacyContains(b, f.needles) {
												t.Error("credential retained in event")
											}
										}
										for _, link := range s.Links {
											b, _ := proto.Marshal(link)
											if independentPrivacyContains(b, f.needles) {
												t.Error("credential retained in link")
											}
										}
									}
								}
							}
						case <-deadline:
							t.Fatal("collector pair not received")
						}
					}
					if spanCount != 2 {
						t.Fatal("unexpected collector span count")
					}
				})
			}
		})
	}
}

func TestIndependentPrivacySafeControls(t *testing.T) {
	values := []any{
		"Content-Type: text/plain\r\nAccept: application/json",
		"https://service.invalid/path?message=retained&count=1#section",
		"http://[::1]/path?message=a%2Bb%26c",
		"version 1.2.3 and docs.service.invalid",
		map[string]any{"items": []any{123, true, nil, "retained"}, "safe": "safe-control"},
		map[string]any{"headers": []any{[]any{"Content-Type", "application/json"}, map[string]any{"name": "Accept", "value": "text/plain"}}},
	}
	for i, v := range values {
		t.Run(fmt.Sprintf("control-%d", i), func(t *testing.T) {
			raw, _ := json.Marshal(v)
			if got := telemetry.Content(raw, meter.ModeFull, nil); got != string(raw) {
				t.Error("safe content changed")
			}
		})
	}
}
