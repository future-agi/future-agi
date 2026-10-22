package toolperm

import (
	"context"
	"encoding/json"
	"fmt"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/guardrails"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

func requestWithTools(t *testing.T, toolsJSON string) *models.ChatCompletionRequest {
	t.Helper()
	var req models.ChatCompletionRequest
	body := `{"model":"m","messages":[{"role":"user","content":"hi"}],"tools":` + toolsJSON + `}`
	if err := json.Unmarshal([]byte(body), &req); err != nil {
		t.Fatalf("reading the request: %v", err)
	}
	return &req
}

// This guardrail is configured independently of the tool-policy plugin, and
// read tool.Function.Name — so a provider-executed capability, which names no
// function, was invisible to it. A denylist for code_execution permitted both
// a bare and a typed entry, and the provider forwarded the capability.
func TestGuardrail_DeniesACapabilityInEitherSpelling(t *testing.T) {
	pairs := [][2]string{
		{"google_search", "googleSearch"},
		{"google_search_retrieval", "googleSearchRetrieval"},
		{"url_context", "urlContext"},
		{"code_execution", "codeExecution"},
	}

	for _, pair := range pairs {
		for _, configured := range pair {
			for _, requested := range pair {
				for _, shape := range []string{"bare", "typed"} {
					t.Run(fmt.Sprintf("deny %s / send %s / %s", configured, requested, shape), func(t *testing.T) {
						tools := `[{"` + requested + `":{}}]`
						if shape == "typed" {
							tools = `[{"type":"` + requested + `"}]`
						}

						g := New("toolperm", map[string]interface{}{
							"mode":     "denylist",
							"apply_to": "request",
							"tools":    configured,
						})

						res := g.Check(context.Background(), &guardrails.CheckInput{
							Request: requestWithTools(t, tools),
						})
						if res.Pass {
							t.Errorf("the denied capability passed the guardrail")
						}
					})
				}
			}
		}
	}
}

// An allowlist for an ordinary function must not be satisfied by a capability
// the caller smuggled in beside it.
func TestGuardrail_AllowlistDoesNotAdmitAContradictoryEntry(t *testing.T) {
	g := New("toolperm", map[string]interface{}{
		"mode":     "allowlist",
		"apply_to": "request",
		"tools":    "get_weather",
	})

	res := g.Check(context.Background(), &guardrails.CheckInput{
		Request: requestWithTools(t, `[{"type":"code_execution","function":{"name":"get_weather"}}]`),
	})
	if res.Pass {
		t.Error("the entry passed on its function name while code_execution is what travels")
	}
}

// An ordinary function name is untouched by the canonicalization.
func TestGuardrail_OrdinaryFunctionSemanticsAreUnchanged(t *testing.T) {
	g := New("toolperm", map[string]interface{}{
		"mode":     "allowlist",
		"apply_to": "request",
		"tools":    "get_weather,get_time",
	})

	res := g.Check(context.Background(), &guardrails.CheckInput{
		Request: requestWithTools(t, `[{"type":"function","function":{"name":"get_weather"}}]`),
	})
	if !res.Pass {
		t.Errorf("an allowed function was refused: %+v", res)
	}

	denied := g.Check(context.Background(), &guardrails.CheckInput{
		Request: requestWithTools(t, `[{"type":"function","function":{"name":"delete_everything"}}]`),
	})
	if denied.Pass {
		t.Error("a function outside the allowlist passed")
	}
}

// Alias equivalence must not reach an application's own functions on this path
// either, in either direction.
func TestGuardrail_AliasEquivalenceDoesNotReachCustomFunctions(t *testing.T) {
	custom := func(name string) string {
		return `[{"type":"function","function":{"name":"` + name + `"}}]`
	}

	tests := []struct {
		name     string
		mode     string
		patterns string
		tools    string
		wantPass bool
	}{
		{"a denylist for the built-in lets through a custom function spelled the other way",
			"denylist", "code_execution", custom("codeExecution"), true},
		{"and the other direction",
			"denylist", "codeExecution", custom("code_execution"), true},
		{"an allowlist for the built-in does not admit a custom function spelled the other way",
			"allowlist", "code_execution", custom("codeExecution"), false},
		{"and the other direction",
			"allowlist", "codeExecution", custom("code_execution"), false},
		{"a custom function named exactly as configured is still denied",
			"denylist", "codeExecution", custom("codeExecution"), false},
		{"the built-in is still denied under either spelling",
			"denylist", "code_execution", `[{"codeExecution":{}}]`, false},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			g := New("toolperm", map[string]interface{}{
				"mode":     tc.mode,
				"apply_to": "request",
				"tools":    tc.patterns,
			})
			res := g.Check(context.Background(), &guardrails.CheckInput{
				Request: requestWithTools(t, tc.tools),
			})
			if res.Pass != tc.wantPass {
				t.Errorf("pass = %v, want %v", res.Pass, tc.wantPass)
			}
		})
	}
}

// A tool call in a response is a function the model chose to call, matched by
// its exact name. Alias equivalence there would let a rule about a built-in
// catch an application function of a similar name.
func TestGuardrail_ResponseToolCallsAreMatchedExactly(t *testing.T) {
	response := func(name string) *models.ChatCompletionResponse {
		return &models.ChatCompletionResponse{
			Choices: []models.Choice{{
				Message: models.Message{
					Role:      "assistant",
					ToolCalls: []models.ToolCall{{Type: "function", Function: models.FunctionCall{Name: name}}},
				},
			}},
		}
	}

	tests := []struct {
		name     string
		patterns string
		called   string
		wantPass bool
	}{
		{"an exact name is denied", "codeExecution", "codeExecution", false},
		{"the other spelling is not", "code_execution", "codeExecution", true},
		{"nor the reverse", "codeExecution", "code_execution", true},
		{"a glob still applies", "code_*", "code_execution", false},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			g := New("toolperm", map[string]interface{}{
				"mode":     "denylist",
				"apply_to": "response",
				"tools":    tc.patterns,
			})
			res := g.Check(context.Background(), &guardrails.CheckInput{Response: response(tc.called)})
			if res.Pass != tc.wantPass {
				t.Errorf("pass = %v, want %v", res.Pass, tc.wantPass)
			}
		})
	}
}
