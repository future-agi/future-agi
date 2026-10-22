package guardrails_test

import (
	"context"
	"encoding/json"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/guardrails"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/leakage"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// An organization's check pushed without an action or a threshold runs as
// log, at threshold 0. The control plane pushes a guardrails section that the
// console stored as a checks map as it is stored, and the console saves such
// a section as that map (frontend useConfigPatch.js, guardrailsForSave), so a
// check stored without them keeps running this way.

// orgLeakage is a plugin whose org runs data-leakage-prevention as check,
// the JSON the control plane pushes for it.
func orgLeakage(t *testing.T, check string) *guardrails.GuardrailPlugin {
	t.Helper()
	var cfg tenant.GuardrailConfig
	if err := json.Unmarshal([]byte(`{"checks":{"data-leakage-prevention":`+check+`}}`), &cfg); err != nil {
		t.Fatal(err)
	}
	tenants := tenant.NewStore()
	tenants.Set("org-defaults", &tenant.OrgConfig{Guardrails: &cfg})
	registry := map[string]guardrails.Guardrail{"data-leakage-prevention": leakage.New(nil)}
	return guardrails.NewPlugin(nil, registry, nil, nil, tenants)
}

// oneLeakAnswer is an answer that matches one data leakage pattern (SQL
// injection), which the check scores 0.7.
func oneLeakAnswer() *models.RequestContext {
	rc := models.AcquireRequestContext()
	rc.Metadata["org_id"] = "org-defaults"
	rc.Model = "gpt-4o"
	rc.Request = &models.ChatCompletionRequest{Model: "gpt-4o"}
	content, _ := json.Marshal("You can run DROP TABLE users; to delete everything")
	rc.Response = &models.ChatCompletionResponse{ID: "answer", Choices: []models.Choice{
		{Message: models.Message{Role: "assistant", Content: content}, FinishReason: "stop"},
	}}
	return rc
}

func TestPlugin_OrgCheckWithoutAnActionLogs(t *testing.T) {
	rc := oneLeakAnswer()
	defer rc.Release()

	result := orgLeakage(t, `{"enabled":true}`).ProcessResponse(context.Background(), rc)
	if result.Error != nil {
		t.Fatalf("a check without an action refused the answer: %v", result.Error)
	}
	if rc.Response == nil || rc.Response.ID != "answer" {
		t.Error("a check without an action dropped the answer")
	}
	if got := rc.Metadata["guardrail_action"]; got != "logged" {
		t.Errorf("guardrail_action = %q, want logged: the check ran as log", got)
	}
}

func TestPlugin_OrgCheckWithoutAThresholdTriggersAboveZero(t *testing.T) {
	for _, tc := range []struct {
		name, check string
		blocked     bool
	}{
		// Stored without a threshold: 0.7 is above 0, so the block holds.
		{"no threshold", `{"enabled":true,"action":"block"}`, true},
		// A threshold of 0.8: 0.7 is not above it.
		{"threshold 0.8", `{"enabled":true,"action":"block","confidence_threshold":0.8}`, false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			rc := oneLeakAnswer()
			defer rc.Release()

			result := orgLeakage(t, tc.check).ProcessResponse(context.Background(), rc)
			if blocked := result.Error != nil; blocked != tc.blocked {
				t.Errorf("blocked = %t, want %t (error %v)", blocked, tc.blocked, result.Error)
			}
		})
	}
}
