package guardrails_test

import (
	"context"
	"encoding/json"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/guardrails"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/topic"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

func TestSelectedTextViewR4ReceiptBindsOriginalAndProjection(t *testing.T) {
	cfg := &tenant.GuardrailConfig{KeyPolicies: []*tenant.KeyGuardrailPolicy{{ID: "synthetic-policy", Name: "synthetic", Mode: "enforce", KeyIDs: []string{"selected"}, Checks: map[string]*tenant.GuardrailCheck{"topic-restriction": {Enabled: true, Action: "block", ConfidenceThreshold: .5, Config: map[string]interface{}{"forbidden_topics": []interface{}{map[string]interface{}{"name": "synthetic", "keywords": []interface{}{"restricted"}}}}}}}}}
	store := tenant.NewStore()
	store.Set("org", &tenant.OrgConfig{Guardrails: cfg})
	engine := guardrails.NewEngine(config.GuardrailsConfig{Enabled: true, DefaultTimeout: time.Second}, nil)
	p := guardrails.NewPlugin(engine, nil, func(name string, cfg map[string]interface{}) guardrails.Guardrail { return topic.New(cfg) }, nil, store)
	rc := models.AcquireRequestContext()
	defer rc.Release()
	rc.Metadata["org_id"] = "org"
	rc.Metadata["auth_key_id"] = "selected"
	rc.Request = &models.ChatCompletionRequest{Model: "synthetic", Messages: []models.Message{{Role: "user", Content: json.RawMessage(`[{"type":"text","text":"safe"}]`)}}}
	rc.Inspection = models.NewInspectionState(models.InspectionContract{Operation: "synthetic.chat", Input: models.InspectionSupported, Output: models.InspectionSupported})
	rc.Inspection.BindPrincipal("org", "selected")
	rc.Inspection.Complete(models.InspectionInput, rc.Request)
	if err := p.AdmitSelectedRequest(context.Background(), rc); err != nil {
		t.Fatal(err)
	}
	if rc.Inspection.ReleaseError() != nil || !rc.Inspection.IsDecided(models.InspectionInput, rc.Request) {
		t.Fatal("safe array did not complete selected decision")
	}
	if string(rc.Request.Messages[0].Content) != `[{"type":"text","text":"safe"}]` {
		t.Fatal("original replaced by text view")
	}
	// Identical projected text, different original generation/shape: no reusable
	// receipt. The release gate remains tied to the original, not the adapter copy.
	rc.Request.Messages[0].Content = json.RawMessage(`"safe"`)
	if rc.Inspection.ReleaseError() == nil {
		t.Fatal("same text projection concealed changed original")
	}
	if err := p.AdmitSelectedRequest(context.Background(), rc); err == nil || err.Code != "input_guardrail_unsupported" {
		t.Fatalf("stale original accepted: %v", err)
	}
	rc.Inspection.Complete(models.InspectionInput, rc.Request)
	if err := p.AdmitSelectedRequest(context.Background(), rc); err != nil {
		t.Fatal(err)
	}
	// A fresh complete array is checked again; a changed projection cannot reuse
	// the safe decision merely because the policy config and principal are equal.
	rc.Request.Messages[0].Content = json.RawMessage(`[{"type":"text","text":"restricted"}]`)
	rc.Inspection.Complete(models.InspectionInput, rc.Request)
	if err := p.AdmitSelectedRequest(context.Background(), rc); err == nil || err.Code != "content_blocked" {
		t.Fatalf("changed checked projection not blocked: %v", err)
	}
	if rc.Inspection.ReleaseError() == nil {
		t.Fatal("blocked array retained release decision")
	}
}
