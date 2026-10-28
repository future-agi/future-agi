package guardrails_test

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/guardrails"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/blocklist"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/contentmod"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/expression"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/pii"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/secrets"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/toolperm"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/validation"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/webhook"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

func postInputContext(t *testing.T, prompt, answer string) *models.RequestContext {
	t.Helper()
	rc := models.AcquireRequestContext()
	t.Cleanup(rc.Release)
	rc.Model = "model"
	rc.Metadata["org_id"] = "org"
	request, _ := json.Marshal(prompt)
	response, _ := json.Marshal(answer)
	rc.Request = &models.ChatCompletionRequest{Model: rc.Model, Messages: []models.Message{{Role: "user", Content: request}}}
	rc.Response = &models.ChatCompletionResponse{Model: rc.Model, Choices: []models.Choice{{Index: 0, Message: models.Message{Role: "assistant", Content: response}, FinishReason: "stop"}}}
	return rc
}

func postInputPlugin(g guardrails.Guardrail) *guardrails.GuardrailPlugin {
	store := tenant.NewStore()
	store.Set("org", &tenant.OrgConfig{Guardrails: &tenant.GuardrailConfig{Checks: map[string]*tenant.GuardrailCheck{g.Name(): {Enabled: true, Action: "block", ConfidenceThreshold: .5, Config: map[string]interface{}{"stage": "post"}}}}})
	return guardrails.NewPlugin(nil, map[string]guardrails.Guardrail{g.Name(): g}, nil, nil, store)
}

func TestTenantPostNativeContentScannersExcludePrompt(t *testing.T) {
	for _, tc := range []struct {
		check guardrails.Guardrail
		bad   string
	}{
		{blocklist.New(map[string]interface{}{"words": []interface{}{"forbidden"}}), "forbidden"},
		{pii.New(map[string]interface{}{"types": []interface{}{"email"}}), "test@example.com"},
		{contentmod.New(map[string]interface{}{"categories": []interface{}{"toxicity"}, "category_threshold": 1}), "you are stupid"},
		{secrets.New(map[string]interface{}{"types": []interface{}{"password_assign"}}), "password=synthetic-test-value"},
	} {
		t.Run(tc.check.Name(), func(t *testing.T) {
			for _, inResponse := range []bool{false, true} {
				prompt, answer := tc.bad, "clean answer"
				if inResponse {
					prompt, answer = "clean prompt", tc.bad
				}
				rc := postInputContext(t, prompt, answer)
				before, _ := json.Marshal(rc.Request)
				result := postInputPlugin(tc.check).ProcessResponse(context.Background(), rc)
				if (result.Error != nil) != inResponse {
					t.Fatalf("inResponse=%t result=%+v", inResponse, result)
				}
				after, _ := json.Marshal(rc.Request)
				if string(before) != string(after) {
					t.Fatal("content scanner changed the original prompt")
				}
			}
		})
	}
}

func TestTenantPostValidationCannotUsePromptToSatisfyRequiredPattern(t *testing.T) {
	g := validation.New(map[string]interface{}{"require_patterns": []interface{}{"^OK"}})
	for _, tc := range []struct {
		prompt, answer string
		blocked        bool
	}{{"OK please answer", "INVALID", true}, {"other prompt", "OK answer", false}} {
		rc := postInputContext(t, tc.prompt, tc.answer)
		if result := postInputPlugin(g).ProcessResponse(context.Background(), rc); (result.Error != nil) != tc.blocked {
			t.Fatalf("prompt=%q answer=%q result=%+v", tc.prompt, tc.answer, result)
		}
	}
}

func TestTenantPostExpressionComparesRequestAndResponse(t *testing.T) {
	rc := postInputContext(t, "PRIVATE_SENTINEL", "PRIVATE_SENTINEL")
	rc.Request.Messages[0].Role = "system"
	g := expression.New("no-system-echo", map[string]interface{}{"expression": "response.choices[0].message.content == request.messages[0].content"})
	if result := postInputPlugin(g).ProcessResponse(context.Background(), rc); result.Error == nil || rc.Response != nil {
		t.Fatalf("input/output comparison failed to block: %+v", result)
	}
}

func TestTenantPostWebhookReceivesPrompt(t *testing.T) {
	seen := make(chan *guardrails.CheckInput, 1)
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var payload guardrails.CheckInput
		if json.NewDecoder(r.Body).Decode(&payload) != nil {
			w.WriteHeader(http.StatusBadRequest)
			return
		}
		seen <- &payload
		json.NewEncoder(w).Encode(map[string]interface{}{"pass": true})
	}))
	defer server.Close()
	rc := postInputContext(t, "original prompt", "candidate")
	g := webhook.New("compare-prompt", map[string]interface{}{"url": server.URL})
	if result := postInputPlugin(g).ProcessResponse(context.Background(), rc); result.Error != nil {
		t.Fatal(result.Error)
	}
	select {
	case input := <-seen:
		if input.Request == nil || len(input.Request.Messages) != 1 || string(input.Request.Messages[0].Content) != `"original prompt"` || input.Response == nil || len(input.Response.Choices) != 1 {
			t.Fatalf("webhook lost context: %+v", input)
		}
	default:
		t.Fatal("webhook did not receive a request")
	}
}

func TestTenantPostToolPermissionsBothRetainsRequestTools(t *testing.T) {
	rc := postInputContext(t, "prompt", "answer without tool calls")
	rc.Request.Tools = []models.Tool{{Type: "function", Function: models.ToolFunction{Name: "denied-tool"}}}
	g := toolperm.New("tool-permissions", map[string]interface{}{"provider": "tool_permission", "apply_to": "both", "mode": "denylist", "tools": "denied-tool"})
	if result := postInputPlugin(g).ProcessResponse(context.Background(), rc); result.Error == nil || rc.Response != nil {
		t.Fatalf("request tool restriction failed to block: %+v", result)
	}
}
