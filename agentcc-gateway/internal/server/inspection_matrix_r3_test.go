package server

import (
	"encoding/json"
	"fmt"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// Exercise the actual expanded registration table, including each native action,
// rather than treating one representative URL as proof for its siblings. This
// matrix proves early refusal only: supported payloads, observation modes and
// legacy controls have their own behavioral tests, not invented results here.
func TestInspectionR3ExpandedUnknownStageMatrixHTTP(t *testing.T) {
	for _, stage := range []string{"pre", "post"} {
		for _, failOpen := range []bool{false, true} {
			t.Run(fmt.Sprintf("%s/fail_open=%v", stage, failOpen), func(t *testing.T) {
				upstream := &dialectUpstream{reply: "matrix-uninspected-content"}
				up := httptest.NewServer(upstream)
				defer up.Close()
				name, check := "runtime-post", map[string]interface{}(nil)
				if stage == "pre" {
					name, check = "custom-pre", map[string]interface{}{"expression": "false"}
				}
				cfg := &tenant.GuardrailConfig{FailOpen: failOpen, KeyPolicies: []*tenant.KeyGuardrailPolicy{runtimePolicy(name, "enforce", "block", true, check)}}
				base, audit := runtimePolicyServer(t, up.URL, cfg, nil)
				base.asyncWorker.Stop()
				base.cfg.MCP.Enabled, base.cfg.A2A.Enabled = true, true
				base.cfg.Routing.Scheduled.Enabled = true
				srv := New(base.cfg, "", base.registry, base.engine, base.keyStore, nil, nil, nil, testModelDBPtr(), base.TenantStore, nil)
				defer srv.asyncWorker.Stop()
				gateway := httptest.NewServer(srv.httpServer.Handler)
				defer gateway.Close()
				cases := 0
				for _, row := range srv.InspectionInventory() {
					if stage == "pre" && !failOpen {
						encoded, _ := json.Marshal(row)
						t.Logf("expanded_registered_inventory=%s", encoded)
					}
					if row.Method == "INTERNAL" || row.Auth == inspectionAuthPublic || row.Auth == inspectionAuthAdmin {
						continue
					}
					variants := []string{""}
					if row.Discriminator == "genai-action" {
						variants = []string{"unknownAction"}
						for _, v := range row.Variants {
							variants = append(variants, v.Operation)
						}
					}
					for _, action := range variants {
						path := row.Pattern
						replacements := []string{"{model_action}", "gemini-3.8-flash:" + action, "{provider}", "gemini", "{model}", "gpt-5.5"}
						path = strings.NewReplacer(replacements...).Replace(path)
						parts := strings.Split(path, "/")
						for i, part := range parts {
							if strings.HasPrefix(part, "{") {
								parts[i] = "synthetic"
							}
						}
						path = strings.Join(parts, "/")
						request := httptest.NewRequest(row.Method, path, nil)
						if action != "" {
							q := request.URL.Query()
							q.Set("model_action", "gemini-3.8-flash:"+action)
							request.URL.RawQuery = q.Encode()
						}
						contract := row.contractFor(request)
						support := contract.Output
						code := "output_guardrail_unsupported"
						if stage == "pre" {
							support = contract.Input
							code = "input_guardrail_unsupported"
						}
						if support != models.InspectionUnknown {
							continue
						}
						t.Run(row.Method+" "+path, func(t *testing.T) {
							audit.mu.Lock()
							audit.results = nil
							audit.usage = nil
							audit.mu.Unlock()
							status, body := inspectionR3HTTP(t, gateway, row.Method, path, `{"jsonrpc":"2.0","id":1,"method":"tasks/get","params":{"id":"synthetic"}}`, inspectionR3Headers("a"))
							results, usage := audit.snapshot()
							if status != 403 || upstream.served() != 0 || !strings.Contains(body, code) || strings.Contains(body, "matrix-uninspected-content") {
								t.Fatalf("status=%d calls=%d body=%s", status, upstream.served(), body)
							}
							if len(results) != 1 || results[0].Outcome != "blocked" || results[0].PolicyID != "runtime-policy" || usage != nil {
								t.Fatalf("attribution=%+v usage=%+v", results, usage)
							}
							encoded, _ := json.Marshal(map[string]interface{}{"method": row.Method, "template": row.Pattern, "path": path, "auth": row.Auth, "operation": contract.Operation, "stage": stage, "mode": "enforce", "action": "block", "fail_open": failOpen, "status": status, "provider_calls": upstream.served(), "released_content_bytes": 0, "gateway_error_bytes": len(body), "sse_events": 0, "usage": usage, "audit": results, "reason": contract.Reason, "pipeline_path": "dispatch selected-operation admission; post observers; no handler Process/CheckAccess"})
							t.Logf("expanded_inspection_matrix=%s", encoded)
							cases++
						})
					}
				}
				if cases < 65 {
					t.Fatalf("expanded matrix unexpectedly small: %d", cases)
				}
			})
		}
	}
}
