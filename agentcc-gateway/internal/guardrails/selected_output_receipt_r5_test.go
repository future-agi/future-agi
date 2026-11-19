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

func TestSelectedOutputR5ReceiptBindsEveryChoice(t *testing.T) {
	store := tenant.NewStore()
	store.Set("org", &tenant.OrgConfig{Guardrails: &tenant.GuardrailConfig{KeyPolicies: []*tenant.KeyGuardrailPolicy{{
		ID: "output", Name: "output", Mode: "enforce", KeyIDs: []string{"key"},
		Checks: map[string]*tenant.GuardrailCheck{"data-leakage-prevention": {Enabled: true, Action: "block", ConfidenceThreshold: .5}},
	}}}})
	plugin := guardrails.NewPlugin(nil, nil, func(_ string, cfg map[string]interface{}) guardrails.Guardrail { return leakage.New(cfg) }, nil, store)
	rc := models.AcquireRequestContext()
	defer rc.Release()
	rc.Metadata["org_id"], rc.Metadata["auth_key_id"] = "org", "key"
	rc.Response = &models.ChatCompletionResponse{Choices: []models.Choice{{Message: models.Message{Role: "assistant", Content: json.RawMessage(`"first safe"`)}}}, Usage: &models.Usage{TotalTokens: 8}}
	rc.SelectedGuardrailResponse = &models.ChatCompletionResponse{Choices: []models.Choice{
		{Index: 0, Message: models.Message{Role: "assistant", Content: json.RawMessage(`"first safe"`)}},
		{Index: 1, Message: models.Message{Role: "assistant", Content: json.RawMessage(`"second safe"`)}},
	}}
	rc.Inspection = models.NewInspectionState(models.InspectionContract{Operation: "native.completion", Output: models.InspectionSupported})
	rc.Inspection.BindPrincipal("org", "key")
	rc.Inspection.Complete(models.InspectionOutput, rc.SelectedGuardrailResponse)
	if err := plugin.AdmitSelectedOutput(context.Background(), rc); err != nil || rc.Inspection.ReleaseError() != nil {
		t.Fatalf("safe all-choice output refused: %v", err)
	}
	// The legacy first-choice response is intentionally unchanged. Altering the
	// selected second choice must still stale the receipt and require a new check.
	rc.SelectedGuardrailResponse.Choices[1].Message.Content = json.RawMessage(`"stack trace"`)
	if rc.Inspection.ReleaseError() == nil {
		t.Fatal("second choice change reused first-choice receipt")
	}
	if err := plugin.AdmitSelectedOutput(context.Background(), rc); err == nil || err.Code != "output_guardrail_unsupported" {
		t.Fatalf("stale all-choice payload accepted: %v", err)
	}
	rc.Inspection.Complete(models.InspectionOutput, rc.SelectedGuardrailResponse)
	if err := plugin.AdmitSelectedOutput(context.Background(), rc); err == nil || err.Code != "content_blocked" {
		t.Fatalf("second-choice content was not inspected: %v", err)
	}
	if rc.Inspection.ReleaseError() == nil || rc.Response.Usage.TotalTokens != 8 {
		t.Fatal("blocked response retained release authority or lost accounting usage")
	}
}
