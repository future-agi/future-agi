package guardrails_test

import (
	"context"
	"encoding/json"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/guardrails"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/validation"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// Expected old-source failures: empty arrays bypass minimum and required-pattern
// checks; adding the exact validation leaf to the selected adapter preserves one
// text per message, including empty messages. No test execution is claimed.
func TestSelectedValidationR5MessageRules(t *testing.T) {
	for _, tc := range []struct {
		name     string
		config   map[string]interface{}
		contents []string
		messages []string
	}{
		{"empty-array-min", map[string]interface{}{"min_message_length": 1}, []string{`[]`}, []string{"message 0 below min length"}},
		{"empty-part-min", map[string]interface{}{"min_message_length": 1}, []string{`[{"type":"text","text":""}]`}, []string{"message 0 below min length"}},
		{"empty-array-required", map[string]interface{}{"require_patterns": []interface{}{"required"}}, []string{`[]`}, []string{"failed require pattern"}},
		{"per-message-count", map[string]interface{}{"min_message_length": 1}, []string{`[]`, `[{"type":"text","text":""}]`}, []string{"message 0 below min length", "message 1 below min length"}},
		{"multipart-max", map[string]interface{}{"max_message_length": 2}, []string{`[{"type":"text","text":"ab"},{"type":"text","text":"cd"}]`}, []string{"message 0 exceeds max length (5 > 2)"}},
		{"total-count", map[string]interface{}{"max_total_length": 3}, []string{`[{"type":"text","text":"ab"}]`, `"cd"`}, []string{"total length exceeds max (4 > 3)"}},
		{"deny-multipart", map[string]interface{}{"deny_patterns": []interface{}{"first\\s+second"}}, []string{`[{"type":"text","text":"first"},{"type":"text","text":"second"}]`}, []string{"matched deny pattern"}},
		{"required-multipart", map[string]interface{}{"require_patterns": []interface{}{"first\\s+second"}}, []string{`[{"type":"text","text":"first"},{"type":"text","text":"second"}]`}, nil},
		{"per-message-not-flattened", map[string]interface{}{"max_message_length": 2}, []string{`"ab"`, `[{"type":"text","text":"cd"}]`}, nil},
		{"safe-empty-unconstrained", nil, []string{`[]`, `[{"type":"text","text":""}]`}, nil},
	} {
		for _, mode := range []string{"sequential", "parallel"} {
			t.Run(tc.name+"/"+mode, func(t *testing.T) {
				store := tenant.NewStore()
				store.Set("org", &tenant.OrgConfig{Guardrails: &tenant.GuardrailConfig{PipelineMode: mode, KeyPolicies: []*tenant.KeyGuardrailPolicy{{
					ID: "validation", Name: "validation", Mode: "enforce", KeyIDs: []string{"selected"},
					Checks: map[string]*tenant.GuardrailCheck{"input-validation": {Enabled: true, Action: "block", ConfidenceThreshold: .5, Config: tc.config}},
				}}}})
				engine := guardrails.NewEngine(config.GuardrailsConfig{Enabled: true, DefaultTimeout: time.Second}, nil)
				plugin := guardrails.NewPlugin(engine, nil, func(_ string, cfg map[string]interface{}) guardrails.Guardrail { return validation.New(cfg) }, nil, store)
				rc := models.AcquireRequestContext()
				defer rc.Release()
				rc.Metadata["org_id"], rc.Metadata["auth_key_id"] = "org", "selected"
				rc.Request = &models.ChatCompletionRequest{Model: "synthetic"}
				for _, raw := range tc.contents {
					rc.Request.Messages = append(rc.Request.Messages, models.Message{Role: "user", Content: json.RawMessage(raw)})
				}
				before, _ := json.Marshal(rc.Request)
				rc.Inspection = models.NewInspectionState(models.InspectionContract{Operation: "validation.test", Input: models.InspectionSupported})
				rc.Inspection.BindPrincipal("org", "selected")
				rc.Inspection.Complete(models.InspectionInput, rc.Request)
				err := plugin.AdmitSelectedRequest(context.Background(), rc)
				if len(tc.messages) == 0 {
					if err != nil || rc.Inspection.ReleaseError() != nil {
						t.Fatalf("safe control refused: %v", err)
					}
				} else {
					if err == nil || err.Code != "content_blocked" || rc.Inspection.ReleaseError() == nil {
						t.Fatalf("validation did not block: %v", err)
					}
					for _, message := range tc.messages {
						if !strings.Contains(err.Message, message) {
							t.Fatalf("missing %q in %q", message, err.Message)
						}
					}
				}
				after, _ := json.Marshal(rc.Request)
				if string(after) != string(before) {
					t.Fatal("selected adapter rewrote original content")
				}
			})
		}
	}
}
