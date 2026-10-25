package guardrails_test

import (
	"context"
	"encoding/json"
	"fmt"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/guardrails"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/blocklist"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/contentmod"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/pii"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/secrets"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/topic"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/validation"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// A check on the request reads the arguments of the tool calls the request
// carries (an assistant turn's tool_calls[].function.arguments) and the
// results of those calls (tool messages), as it reads a message's text.

// contentCheck is a check on the request that reads every message's text,
// with a text it triggers on.
type contentCheck struct {
	name  string
	check guardrails.Guardrail
	match string
}

func contentChecks() []contentCheck {
	return []contentCheck{
		{"pii-detection", pii.New(nil), "123-45-6789"},
		{"secret-detection", secrets.New(nil), "AKIAABCDEFGHIJKLMNOP"},
		{"keyword-blocklist", blocklist.New(map[string]interface{}{"words": []interface{}{"project bluebird"}}), "project bluebird"},
		{"content-moderation", contentmod.New(map[string]interface{}{"categories": []interface{}{"toxicity"}}),
			"You are stupid and a complete idiot"},
		{"topic-restriction", topic.New(map[string]interface{}{"forbidden_topics": []interface{}{
			map[string]interface{}{"name": "weapons", "keywords": []interface{}{"grenade"}}}}), "grenade"},
		{"input-validation", validation.New(map[string]interface{}{"deny_patterns": []interface{}{"DROP TABLE"}}), "DROP TABLE"},
	}
}

// escapeAll writes every character of s as a JSON \u escape, as an encoder
// may write a tool call's arguments: the check reads the text the provider
// decodes, not the escapes.
func escapeAll(s string) string {
	var b strings.Builder
	for _, r := range s {
		fmt.Fprintf(&b, `\u%04x`, r)
	}
	return b.String()
}

func jsonString(t *testing.T, s string) json.RawMessage {
	t.Helper()
	b, err := json.Marshal(s)
	if err != nil {
		t.Fatal(err)
	}
	return b
}

// toolCallTurns is a request whose only text a check matches is in arguments,
// the arguments of the assistant's tool call; the user's turn and the tool's
// result are plain.
func toolCallTurns(t *testing.T, arguments string) *models.ChatCompletionRequest {
	t.Helper()
	return &models.ChatCompletionRequest{Model: "gpt-4o", Messages: []models.Message{
		{Role: "user", Content: jsonString(t, "Look the record up.")},
		{Role: "assistant", ToolCalls: []models.ToolCall{{ID: "call_1", Type: "function",
			Function: models.FunctionCall{Name: "lookup", Arguments: arguments}}}},
		{Role: "tool", ToolCallID: "call_1", Content: jsonString(t, "Found it.")},
	}}
}

// toolResultTurns is a request whose only text a check matches is in the
// tool's result, given as content.
func toolResultTurns(t *testing.T, content json.RawMessage) *models.ChatCompletionRequest {
	t.Helper()
	return &models.ChatCompletionRequest{Model: "gpt-4o", Messages: []models.Message{
		{Role: "user", Content: jsonString(t, "Look the record up.")},
		{Role: "assistant", ToolCalls: []models.ToolCall{{ID: "call_1", Type: "function",
			Function: models.FunctionCall{Name: "lookup", Arguments: `{"id":"r-7"}`}}}},
		{Role: "tool", ToolCallID: "call_1", Content: content},
	}}
}

func checkRequest(c contentCheck, req *models.ChatCompletionRequest) *guardrails.CheckResult {
	return c.check.Check(context.Background(), &guardrails.CheckInput{Request: req, Metadata: map[string]string{}})
}

func TestContentChecks_ReadToolCallArguments(t *testing.T) {
	for _, c := range contentChecks() {
		match := c.match
		for _, tc := range []struct {
			name, arguments string
		}{
			{"a string value", fmt.Sprintf(`{"query":%q}`, match)},
			{"a value written with JSON escapes", `{"query":"` + escapeAll(match) + `"}`},
			{"a value deep in lists and objects", fmt.Sprintf(`{"filters":[{"field":"note","values":["x",%q]}],"limit":5}`, match)},
			{"arguments that are not JSON", "run the lookup for " + match},
		} {
			t.Run(c.name+" / "+tc.name, func(t *testing.T) {
				r := checkRequest(c, toolCallTurns(t, tc.arguments))
				if r == nil || r.Pass {
					t.Errorf("%s passed a request whose tool call's arguments %s hold %q: it did not read them", c.name, tc.arguments, match)
				}
			})
		}
		t.Run(c.name+" / arguments without a match pass", func(t *testing.T) {
			r := checkRequest(c, toolCallTurns(t, `{"query":"weather in Paris","days":3,"metric":true}`))
			if r == nil || !r.Pass {
				t.Errorf("%s refused a request with no match in its tool call's arguments: %+v", c.name, r)
			}
		})
	}
}

// The result of a tool call (a tool message) is read as every other
// message's text is: as a string, or as text parts.
func TestContentChecks_ReadToolResults(t *testing.T) {
	for _, c := range contentChecks() {
		parts, _ := json.Marshal([]map[string]string{{"type": "text", "text": "Result: " + c.match}})
		for _, tc := range []struct {
			name    string
			content json.RawMessage
		}{
			{"as a string", jsonString(t, "Result: "+c.match)},
			{"as text parts", parts},
		} {
			t.Run(c.name+" / "+tc.name, func(t *testing.T) {
				r := checkRequest(c, toolResultTurns(t, tc.content))
				if r == nil || r.Pass {
					t.Errorf("%s passed a request whose tool result holds %q: it did not read it", c.name, c.match)
				}
			})
		}
	}
}

// A password given as a member of a tool call's arguments is read with its
// key, as "password: <value>", which secret-detection's password pattern
// matches; the value alone would not.
func TestSecretDetection_ReadsAPasswordArgumentWithItsKey(t *testing.T) {
	c := contentCheck{name: "secret-detection", check: secrets.New(nil)}
	r := checkRequest(c, toolCallTurns(t, `{"username":"admin","password":"hunter2hunter2"}`))
	if r == nil || r.Pass {
		t.Errorf("secret-detection passed a password in a tool call's arguments: %+v", r)
	}
}
