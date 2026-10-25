package guardrails

import (
	"context"
	"net/http"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
)

// postOnlyBlockingPlugin: one static guardrail, on the output, that blocks
// every response; nothing runs before the provider.
func postOnlyBlockingPlugin() *GuardrailPlugin {
	engine := NewEngine(config.GuardrailsConfig{
		Enabled:        true,
		DefaultTimeout: 5 * time.Second,
		Rules: []config.GuardrailRuleConfig{
			{Name: "out", Stage: "post", Mode: "sync", Action: "block", Threshold: 0.5},
		},
	}, map[string]Guardrail{
		"out": &mockGuardrail{name: "out", stage: StagePost, result: &CheckResult{Pass: false, Score: 0.99, Message: "blocked"}},
	})
	return NewPlugin(engine, nil, nil, nil, nil)
}

func overrideRequest(header string, allowed bool) *models.RequestContext {
	rc := models.AcquireRequestContext()
	rc.Request = &models.ChatCompletionRequest{Model: "gpt-4o"}
	rc.Metadata["x-guardrail-policy"] = header
	if allowed {
		rc.Metadata["key_allow_policy_override"] = "true"
	}
	return rc
}

// The permission is checked before the provider call even when no static
// rule runs there.
func TestPlugin_UnpermittedOverrideIsRefusedBeforeTheProviderWithOutputOnlyRules(t *testing.T) {
	plug := postOnlyBlockingPlugin()
	rc := overrideRequest("disabled", false)
	defer rc.Release()

	result := plug.ProcessRequest(context.Background(), rc)

	if result.Error == nil || result.Error.Status != http.StatusForbidden {
		t.Fatalf("pre stage answered %+v, want a 403 error: the post-only rule's override must be refused before the provider call", result)
	}
}

// An invalid value from a key with the permission is still a 400 before the
// provider call.
func TestPlugin_InvalidOverrideIsRefusedBeforeTheProviderWithOutputOnlyRules(t *testing.T) {
	plug := postOnlyBlockingPlugin()
	rc := overrideRequest("not-a-policy", true)
	defer rc.Release()

	result := plug.ProcessRequest(context.Background(), rc)

	if result.Error == nil || result.Error.Status != http.StatusBadRequest {
		t.Fatalf("pre stage answered %+v, want a 400 error", result)
	}
}

// A permitted override passes the pre stage and is honoured at the post stage.
func TestPlugin_PermittedOverrideStillAppliesWithOutputOnlyRules(t *testing.T) {
	plug := postOnlyBlockingPlugin()
	rc := overrideRequest("disabled", true)
	defer rc.Release()

	if result := plug.ProcessRequest(context.Background(), rc); result.Action != pipeline.Continue || result.Error != nil {
		t.Fatalf("pre stage answered %+v, want Continue", result)
	}
	rc.Response = &models.ChatCompletionResponse{ID: "resp"}
	if result := plug.ProcessResponse(context.Background(), rc); result.Error != nil {
		t.Fatalf("post stage answered %+v, want Continue: the key may disable its guardrails", result)
	}
	if rc.Response == nil {
		t.Fatal("the post stage dropped a response the key's permitted override let through")
	}
}

// A refused override that reaches the post stage drops the response, and
// the refusal becomes the request's error (withholdAnswer).
func TestPlugin_RefusedOverrideFailsClosedAtThePostStage(t *testing.T) {
	plug := postOnlyBlockingPlugin()
	rc := overrideRequest("disabled", false)
	defer rc.Release()
	rc.Response = &models.ChatCompletionResponse{ID: "resp"}

	result := plug.ProcessResponse(context.Background(), rc)

	if result.Error == nil {
		t.Errorf("post stage answered %+v, want an error", result)
	}
	if rc.Response != nil {
		t.Error("post stage refused the override but left the response in place, so it would be sent unchecked")
	}
}
