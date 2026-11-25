package server

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http/httptest"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// Internal registrations have no HTTP writer/status. Exercise the installed
// contracts at the real authenticated Process seam. This is not evidence that
// an async/scheduled/batch job was queued or that an A2A remote agent ran.
func TestInspectionR3InstalledInternalContractMatrix(t *testing.T) {
	for _, stage := range []string{"pre", "post"} {
		for _, mode := range []string{"block", "monitor", "unselected", "unauthenticated"} {
			t.Run(stage+"/"+mode, func(t *testing.T) {
				name, check := "runtime-post", map[string]interface{}(nil)
				if stage == "pre" {
					name, check = "custom-pre", map[string]interface{}{"expression": "false"}
				}
				policy := runtimePolicy(name, "enforce", "block", true, check)
				if mode == "monitor" {
					policy.Mode = "monitor"
				}
				up := httptest.NewServer(&dialectUpstream{reply: "unused"})
				defer up.Close()
				base, _ := runtimePolicyServer(t, up.URL, &tenant.GuardrailConfig{FailOpen: true, KeyPolicies: []*tenant.KeyGuardrailPolicy{policy}}, nil)
				base.asyncWorker.Stop()
				base.cfg.MCP.Enabled, base.cfg.A2A.Enabled, base.cfg.Routing.Scheduled.Enabled = true, true, true
				srv := New(base.cfg, "", base.registry, base.engine, base.keyStore, nil, nil, nil, testModelDBPtr(), base.TenantStore, nil)
				defer srv.asyncWorker.Stop()
				count := 0
				for _, row := range srv.InspectionInventory() {
					if row.Method != "INTERNAL" {
						continue
					}
					t.Run(row.Contract.Operation, func(t *testing.T) {
						rc := models.AcquireRequestContext()
						defer rc.Release()
						rc.Inspection = models.NewInspectionState(row.Contract)
						rc.Request = &models.ChatCompletionRequest{Model: "gpt-5.5"}
						rc.Model = "gpt-5.5"
						if mode != "unauthenticated" {
							key := "a"
							if mode == "unselected" {
								key = "b"
							}
							rc.Metadata["authorization"] = "Bearer sk-agentcc-runtime-" + key
						}
						calls := 0
						err := srv.engine.Process(context.Background(), rc, func(context.Context, *models.RequestContext) error { calls++; return nil })
						status := 200
						if err != nil {
							api, ok := err.(*models.APIError)
							if !ok {
								t.Fatalf("unexpected error %v", err)
							}
							status = api.Status
						}
						want, wantCalls := 200, 1
						if mode == "block" {
							want, wantCalls = 403, 0
						}
						if mode == "unauthenticated" {
							want, wantCalls = 401, 0
						}
						if status != want || calls != wantCalls {
							t.Fatalf("status=%d calls=%d err=%v", status, calls, err)
						}
						encoded, _ := json.Marshal(map[string]interface{}{"method": "INTERNAL", "template": row.Pattern, "operation": row.Contract.Operation, "auth": row.Auth, "stage": stage, "policy_case": mode, "mode": policy.Mode, "fail_open": true, "api_error_status": status, "provider_callback_calls": calls, "released_content_bytes": 0, "sse_events": 0, "audit": rc.GuardrailResults, "usage": nil, "pipeline_path": "actual Process seam with installed internal contract; synthetic provider callback; not queued-job execution", "reason": row.Contract.Reason})
						t.Logf("internal_inspection_matrix=%s", encoded)
					})
					count++
				}
				if count != 4 {
					t.Fatal(fmt.Sprintf("expected async/batch/scheduled/A2A registrations, got %d", count))
				}
			})
		}
	}
}
