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

// An org that enables data-leakage-prevention with action block gets an
// answer that holds a SQL statement. The org check runs on the output and
// withholds that answer.
func TestPlugin_OrgLeakageBlockWithholdsTheAnswer(t *testing.T) {
	tenants := tenant.NewStore()
	tenants.Set("org-leak", &tenant.OrgConfig{Guardrails: &tenant.GuardrailConfig{
		Checks: map[string]*tenant.GuardrailCheck{
			"data-leakage-prevention": {Enabled: true, Action: "block", ConfidenceThreshold: 0.1},
		},
	}})
	registry := map[string]guardrails.Guardrail{"data-leakage-prevention": leakage.New(nil)}
	plugin := guardrails.NewPlugin(nil, registry, nil, nil, tenants)

	rc := models.AcquireRequestContext()
	defer rc.Release()
	rc.Metadata["org_id"] = "org-leak"
	rc.Model = "gpt-4o-mini"
	rc.Request = &models.ChatCompletionRequest{Model: "gpt-4o-mini",
		Messages: []models.Message{{Role: "user", Content: json.RawMessage(`"how do I reset my test database?"`)}}}
	rc.Response = &models.ChatCompletionResponse{ID: "c1", Choices: []models.Choice{{
		Message: models.Message{Role: "assistant", Content: json.RawMessage(`"You can run DROP TABLE users; to delete everything"`)},
	}}}

	if pre := plugin.ProcessRequest(context.Background(), rc); pre.Error != nil {
		t.Fatalf("the output check refused the request: %v", pre.Error)
	}
	post := plugin.ProcessResponse(context.Background(), rc)
	if post.Error == nil || post.Error.Code != "content_blocked" {
		t.Fatalf("post error = %+v, want content_blocked", post.Error)
	}
	if rc.Response != nil {
		t.Fatal("the leaking answer was kept: rc.Response is still set after the org's block-mode output check fired")
	}
	if got := rc.Metadata["guardrail_name"]; got != "data-leakage-prevention" {
		t.Errorf("guardrail_name = %q, want data-leakage-prevention", got)
	}
}
