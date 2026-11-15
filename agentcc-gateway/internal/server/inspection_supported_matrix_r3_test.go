package server

import (
	"encoding/json"
	"fmt"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// Emit measured, normalized HTTP/SSE evidence for each native/translated text
// entry. The expanded unknown-route matrix covers conservative refusal; this
// companion proves that selected input-only and observational requests still
// reach the provider and release its actual response.
func TestInspectionR3SupportedReleaseMatrixHTTP(t *testing.T) {
	routes := append([]guardedRoute(nil), guardedRoutes...)
	routes = append(routes, guardedRoute{"genai qualified native", "/v1beta/models/gemini/gemini-3.8-flash:%s", `{"contents":[{"role":"user","parts":[{"text":%[2]q}]}]}`})
	for _, rt := range routes {
		for _, mode := range []string{"pre-block", "post-block", "both-block", "monitor", "warn", "log", "unselected"} {
			for _, stream := range []bool{false, true} {
				t.Run(fmt.Sprintf("%s/%s/stream=%v", rt.name, mode, stream), func(t *testing.T) {
					policy := runtimePolicy("runtime-post", "enforce", "block", true, nil)
					key := "a"
					switch mode {
					case "pre-block":
						policy = runtimePolicy("custom-pre", "enforce", "block", true, map[string]interface{}{"expression": "false"})
					case "both-block":
						policy.Checks["custom-pre"] = &tenant.GuardrailCheck{Enabled: true, Action: "block", Config: map[string]interface{}{"expression": "false"}}
					case "monitor":
						policy.Mode = "monitor"
					case "warn", "log":
						policy.Checks["runtime-post"].Action = mode
					case "unselected":
						key = "b"
					}
					upstream := &dialectUpstream{reply: "matrix-safe-provider-content"}
					up := httptest.NewServer(upstream)
					defer up.Close()
					srv, audit := runtimePolicyServer(t, up.URL, &tenant.GuardrailConfig{KeyPolicies: []*tenant.KeyGuardrailPolicy{policy}}, nil)
					defer srv.asyncWorker.Stop()
					request := rt.request(stream, "matrix-safe-input")
					request.Header.Set("Authorization", "Bearer sk-agentcc-runtime-"+key)
					recorder := httptest.NewRecorder()
					srv.httpServer.Handler.ServeHTTP(recorder, request)
					refused := stream && (mode == "post-block" || mode == "both-block")
					status, calls := 200, 1
					if refused {
						status, calls = 403, 0
					}
					body := recorder.Body.String()
					if recorder.Code != status || upstream.served() != calls {
						t.Fatalf("status=%d calls=%d body=%s", recorder.Code, upstream.served(), body)
					}
					contentBytes := strings.Count(body, upstream.reply) * len(upstream.reply)
					if refused {
						if contentBytes != 0 || !strings.Contains(body, "stream_guardrail_unsupported") || strings.Contains(body, "data:") {
							t.Fatalf("refused release=%s", body)
						}
					} else if contentBytes == 0 {
						t.Fatalf("provider content not released: %s", body)
					}
					results, usage := audit.snapshot()
					if refused {
						assertRuntimeAttribution(t, audit, "blocked", "enforce")
					} else if usage == nil || usage.TotalTokens != 8 {
						t.Fatalf("usage lost: %+v", usage)
					}
					encoded, _ := json.Marshal(map[string]interface{}{"method": request.Method, "path": request.URL.Path, "route": rt.name, "policy_case": mode, "mode": policy.Mode, "stream": stream, "status": recorder.Code, "provider_calls": upstream.served(), "response_bytes": len(body), "released_content_bytes": contentBytes, "sse_events": strings.Count(body, "data:"), "audit": results, "usage": usage, "pipeline_path": "handler Process + selected request/output hooks + release writer", "shape": "complete plain text; post-block streaming refused before provider"})
					t.Logf("supported_inspection_matrix=%s", encoded)
				})
			}
		}
	}
}
