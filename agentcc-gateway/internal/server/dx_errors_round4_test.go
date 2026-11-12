package server

import (
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

func dxR4Error(t *testing.T, w *httptest.ResponseRecorder) map[string]any {
	t.Helper()
	var envelope map[string]any
	if err := json.Unmarshal(w.Body.Bytes(), &envelope); err != nil {
		t.Fatalf("HTTP %d: invalid error envelope %q: %v", w.Code, w.Body.String(), err)
	}
	e, ok := envelope["error"].(map[string]any)
	if !ok {
		t.Fatalf("HTTP %d: missing error: %s", w.Code, w.Body.String())
	}
	return e
}

func dxR4Evidence(t *testing.T, w *httptest.ResponseRecorder, google bool) (string, map[string]any) {
	t.Helper()
	e := dxR4Error(t, w)
	if google {
		details, _ := e["details"].([]any)
		if len(details) != 1 {
			t.Fatalf("missing Google ErrorInfo: %s", w.Body.String())
		}
		detail, _ := details[0].(map[string]any)
		if detail["@type"] != "type.googleapis.com/google.rpc.ErrorInfo" || detail["domain"] != "agentcc-gateway" {
			t.Fatalf("invalid Google ErrorInfo: %s", w.Body.String())
		}
		code, _ := detail["reason"].(string)
		meta, _ := detail["metadata"].(map[string]any)
		return code, meta
	}
	code, _ := e["code"].(string)
	meta, _ := e["metadata"].(map[string]any)
	return code, meta
}

// Both foreign SDK dialects must retain attribution when the routed provider
// speaks OpenAI. Test startup failures before any successful SSE handshake too.
func TestDXR4CanonicalProviderEvidence(t *testing.T) {
	for _, google := range []bool{false, true} {
		for _, stream := range []bool{false, true} {
			for _, upstream := range []int{401, 403, 500} {
				t.Run(fmt.Sprintf("google=%v/stream=%v/status=%d", google, stream, upstream), func(t *testing.T) {
					g := newManagedTestGateway(t, dxBYOKOrg())
					g.operator.status.Store(int32(upstream))
					var w *httptest.ResponseRecorder
					if google {
						action := "generateContent"
						if stream {
							action = "streamGenerateContent?alt=sse"
						}
						w = dxRequest(g.srv, http.MethodPost, "/v1beta/models/gpt-4o:"+action,
							`{"contents":[{"role":"user","parts":[{"text":"hi"}]}]}`,
							map[string]string{"Authorization": "Bearer " + managedOrgKey})
					} else {
						w = postMessages(t, g.srv, "/v1/messages", managedOrgKey, managedMessagesBody("gpt-4o", stream, ""))
					}
					code, meta := dxR4Evidence(t, w, google)
					want := fmt.Sprintf("provider_%d", upstream)
					if upstream == 401 || upstream == 403 {
						want = "provider_auth_failed"
					}
					if w.Code != 502 || code != want || meta["provider"] != "operator" || fmt.Sprint(meta["provider_status"]) != fmt.Sprint(upstream) || meta["provider_message"] != "upstream down" {
						t.Fatalf("HTTP %d: %s", w.Code, w.Body.String())
					}
					e := dxR4Error(t, w)
					if google && (e["status"] != "INTERNAL" || e["code"] != float64(502)) || !google && e["type"] != "api_error" {
						t.Fatalf("wrong dialect: %s", w.Body.String())
					}
					if w.Header().Get("WWW-Authenticate") != "" || g.operator.calls() != 1 {
						t.Fatalf("challenge=%q calls=%d", w.Header().Get("WWW-Authenticate"), g.operator.calls())
					}
				})
			}
		}
	}
}

// A 200 stream that closes without output is a gateway-detected failure, not
// evidence that the provider sent a 502. Never invent provider type or code.
func TestDXR4SyntheticFailureEvidence(t *testing.T) {
	rc := models.AcquireRequestContext()
	defer rc.Release()
	rc.Provider = "operator"
	for _, message := range []string{"upstream stream closed before model output", "upstream provider returned no stream", "failover provider not found"} {
		original := models.ErrUpstreamProvider(502, message)
		got := providerFailure(rc, original).(*models.APIError)
		meta, _ := got.Metadata.(map[string]any)
		if got.Message != `Provider "operator" failed: `+message || meta["provider_status"] != 0 || meta["provider_type"] != nil || meta["provider_code"] != nil {
			t.Errorf("synthetic failure: %+v metadata=%v", got, meta)
		}
		if original.ProviderStatus != 0 || original.Message != message {
			t.Fatal("attribution mutated the original error")
		}
	}
	for _, surface := range []string{"chat", "messages", "gemini"} {
		t.Run(surface, func(t *testing.T) {
			g := newManagedTestGateway(t, dxBYOKOrg())
			fn := upstreamStream(func(w http.ResponseWriter, r *http.Request, model string) {
				w.Header().Set("Content-Type", "text/event-stream")
				fmt.Fprint(w, "data: [DONE]\n\n")
			})
			g.operator.streamWith.Store(&fn)
			var w *httptest.ResponseRecorder
			switch surface {
			case "chat":
				w = g.chat(t, `{"model":"gpt-4o","stream":true,"messages":[{"role":"user","content":"hi"}]}`, nil)
			case "messages":
				w = postMessages(t, g.srv, "/v1/messages", managedOrgKey, managedMessagesBody("gpt-4o", true, ""))
			default:
				w = dxRequest(g.srv, http.MethodPost, "/v1beta/models/gpt-4o:streamGenerateContent?alt=sse",
					`{"contents":[{"role":"user","parts":[{"text":"hi"}]}]}`, map[string]string{"Authorization": "Bearer " + managedOrgKey})
			}
			_, meta := dxR4Evidence(t, w, surface == "gemini")
			if w.Code != 502 || strings.Contains(w.Body.String(), "returned HTTP") || fmt.Sprint(meta["provider_status"]) != "0" || meta["provider_type"] != nil || meta["provider_code"] != nil {
				t.Fatalf("HTTP %d: %s", w.Code, w.Body.String())
			}
		})
	}
}

// Waiting for model output must neither lose reported startup usage on a
// failure nor drop the first output/prefix chunks on a successful stream.
func TestDXR4GenAIStreamStartupKeepsUsage(t *testing.T) {
	for _, succeeds := range []bool{false, true} {
		t.Run(fmt.Sprint(succeeds), func(t *testing.T) {
			g := newScriptedGateway(t, func(chunk func(string) string) []string {
				rows := []string{chunk(`"choices":[],"usage":{"prompt_tokens":10,"completion_tokens":20,"total_tokens":30}`)}
				if succeeds {
					rows = append(rows, chunk(`"choices":[{"index":0,"delta":{"content":"first output"},"finish_reason":"stop"}]`))
				}
				return append(rows, "data: [DONE]")
			})
			status, body := g.post(t, "/v1beta/models/gpt-4o:streamGenerateContent?alt=sse",
				`{"contents":[{"role":"user","parts":[{"text":"hi"}]}]}`, nil)
			want := 502
			if succeeds {
				want = 200
				if !strings.Contains(body, "first output") {
					t.Fatalf("first output was lost: %s", body)
				}
			}
			var usage models.Usage
			if err := json.Unmarshal([]byte(g.probe.last(t).usage), &usage); err != nil {
				t.Fatal(err)
			}
			if status != want || usage.PromptTokens != 10 || usage.CompletionTokens != 20 || usage.TotalTokens != 30 {
				t.Fatalf("status=%d usage=%+v body=%s", status, usage, body)
			}
		})
	}
}

func TestDXR4NativeStreamHandshakeEvidence(t *testing.T) {
	for _, format := range []string{"anthropic", "gemini"} {
		for _, status := range []int{400, 401, 403, 404, 429, 500} {
			t.Run(fmt.Sprintf("%s/%d", format, status), func(t *testing.T) {
				srv := dxNativeGateway(t, format, status)
				path, body := "/v1/messages", `{"model":"claude-probe","max_tokens":5,"stream":true,"messages":[{"role":"user","content":"hi"}]}`
				if format == "gemini" {
					path, body = "/v1beta/models/gemini-probe:streamGenerateContent?alt=sse", `{"contents":[{"role":"user","parts":[{"text":"hi"}]}]}`
				}
				w := dxRequest(srv, http.MethodPost, path, body, map[string]string{"Authorization": "Bearer sk-agentcc-dx-native"})
				code, meta := dxR4Evidence(t, w, format == "gemini")
				wantStatus, wantCode := status, fmt.Sprintf("provider_%d", status)
				if status >= 500 {
					wantStatus = 502
				}
				if status == 401 || status == 403 {
					wantStatus, wantCode = 502, "provider_auth_failed"
				}
				if w.Code != wantStatus || code != wantCode || meta["provider"] != "native" || fmt.Sprint(meta["provider_status"]) != fmt.Sprint(status) || meta["provider_message"] != "synthetic upstream refusal" || meta["provider_type"] != nil || meta["provider_code"] != nil {
					t.Fatalf("HTTP %d: %s", w.Code, w.Body.String())
				}
			})
		}
	}
}
