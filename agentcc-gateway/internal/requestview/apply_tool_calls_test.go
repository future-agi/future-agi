package requestview

import (
	"strings"
	"testing"
)

// A check that rewrites the request rewrites the arguments of its tool calls
// too (pii-detection with mask, redact or hash). ApplyChecks writes the
// rewritten arguments into the body at the place they were read from: into
// the JSON value of an Anthropic tool_use's input, a Gemini functionCall's
// args and a Responses call's action or operation, and into the string of a
// Responses call's arguments, input or code.

// rewriteCall reads body with v, sets the arguments of tool call call of
// message msg, and applies it.
func rewriteCall(t *testing.T, v *View, msg, call int, arguments string) error {
	t.Helper()
	req := readOK(t, v)
	if msg >= len(req.Messages) || call >= len(req.Messages[msg].ToolCalls) {
		t.Fatalf("message %d has no tool call %d: %+v", msg, call, req.Messages)
	}
	req.Messages[msg].ToolCalls[call].Function.Arguments = arguments
	return v.ApplyChecks()
}

func TestApplyChecks_WritesRewrittenToolCallArguments(t *testing.T) {
	for _, tc := range []struct {
		name      string
		view      *View
		msg, call int
		arguments string
		want      string
	}{
		{"an Anthropic tool_use input",
			Anthropic([]byte(`{"messages":[{"role":"assistant","content":[{"type":"text","text":"sending"},{"type":"tool_use","id":"t","name":"send","input":{"to":"a@b.co", "n":1}}]}]}`)),
			0, 0, `{"to":"***","n":1}`,
			`{"messages":[{"role":"assistant","content":[{"type":"text","text":"sending"},{"type":"tool_use","id":"t","name":"send","input":{"to":"***","n":1}}]}]}`},
		{"a Gemini functionCall's args",
			Gemini([]byte(`{"contents":[{"role":"model","parts":[{"functionCall":{"name":"send","args":{"to":"a@b.co"}}}]}]}`), "m", false),
			0, 0, `{"to":"***"}`,
			`{"contents":[{"role":"model","parts":[{"functionCall":{"name":"send","args":{"to":"***"}}}]}]}`},
		{"a Gemini function_call's args (snake_case)",
			Gemini([]byte(`{"contents":[{"role":"model","parts":[{"function_call":{"name":"send","args":{"to":"a@b.co"}}}]}]}`), "m", false),
			0, 0, `{"to":"***"}`,
			`{"contents":[{"role":"model","parts":[{"function_call":{"name":"send","args":{"to":"***"}}}]}]}`},
		{"a Responses function_call's arguments",
			Responses([]byte(`{"input":[{"type":"function_call","call_id":"c","name":"send","arguments":"{\"to\":\"a@b.co\"}"}]}`)),
			0, 0, `{"to":"***"}`,
			`{"input":[{"type":"function_call","call_id":"c","name":"send","arguments":"{\"to\":\"***\"}"}]}`},
		{"a Responses custom_tool_call's input",
			Responses([]byte(`{"input":[{"type":"custom_tool_call","call_id":"c","name":"mail","input":"email a@b.co now"}]}`)),
			0, 0, "email *** now",
			`{"input":[{"type":"custom_tool_call","call_id":"c","name":"mail","input":"email *** now"}]}`},
		{"a Responses mcp_call's arguments",
			Responses([]byte(`{"input":[{"type":"mcp_call","id":"m","server_label":"s","name":"send","arguments":"{\"to\":\"a@b.co\"}","output":"ok"}]}`)),
			0, 0, `{"to":"***"}`,
			`{"input":[{"type":"mcp_call","id":"m","server_label":"s","name":"send","arguments":"{\"to\":\"***\"}","output":"ok"}]}`},
		{"a Responses local_shell_call's action",
			Responses([]byte(`{"input":[{"type":"local_shell_call","call_id":"c","action":{"type":"exec","command":["mail","a@b.co"]}}]}`)),
			0, 0, `{"type":"exec","command":["mail","***"]}`,
			`{"input":[{"type":"local_shell_call","call_id":"c","action":{"type":"exec","command":["mail","***"]}}]}`},
		{"a Responses code_interpreter_call's code",
			Responses([]byte(`{"input":[{"type":"code_interpreter_call","id":"ci","code":"send('a@b.co')","container_id":"k"}]}`)),
			0, 0, "send('***')",
			`{"input":[{"type":"code_interpreter_call","id":"ci","code":"send('***')","container_id":"k"}]}`},
	} {
		t.Run(tc.name, func(t *testing.T) {
			if err := rewriteCall(t, tc.view, tc.msg, tc.call, tc.arguments); err != nil {
				t.Fatalf("ApplyChecks: %v", err)
			}
			if got := string(tc.view.Body()); got != tc.want {
				t.Errorf("body\n got %s\nwant %s", got, tc.want)
			}
			if err := tc.view.ApplyChecks(); err != nil || string(tc.view.Body()) != tc.want {
				t.Errorf("a second ApplyChecks changed the body: %v %s", err, tc.view.Body())
			}
		})
	}
}

// A tool call no check rewrote leaves the body's bytes as sent.
func TestApplyChecks_LeavesUnrewrittenToolCallsAsSent(t *testing.T) {
	const body = `{"messages":[{"role":"assistant","content":[{"type":"tool_use","id":"t","name":"f","input":{ "q" : "x" }}]},{"role":"user","content":"ssn 1"}]}`
	v := Anthropic([]byte(body))
	req := readOK(t, v)
	req.Messages[1].Content = []byte(`"ssn ***"`)
	if err := v.ApplyChecks(); err != nil {
		t.Fatal(err)
	}
	if got := string(v.Body()); !strings.Contains(got, `"input":{ "q" : "x" }`) || !strings.Contains(got, `"ssn ***"`) {
		t.Errorf("body %s, want the tool call as sent and the text rewritten", got)
	}
}

// Rewritten arguments that cannot be written are an error, and the body is
// left as it was: arguments that are not JSON where the body holds a JSON
// value, and a check that changed the number of a message's tool calls.
func TestApplyChecks_RefusesToolCallArgumentsItCannotWrite(t *testing.T) {
	t.Run("arguments that are not JSON for a JSON value", func(t *testing.T) {
		v := Anthropic([]byte(`{"messages":[{"role":"assistant","content":[{"type":"tool_use","id":"t","name":"f","input":{"to":"a@b.co"}}]}]}`))
		before := string(v.Body())
		err := rewriteCall(t, v, 0, 0, `to ***`)
		if err == nil || !strings.Contains(err.Error(), "not JSON of its type") {
			t.Errorf("ApplyChecks error %v, want one that says the rewrite is not JSON of its type", err)
		}
		if string(v.Body()) != before {
			t.Errorf("the body changed: %s", v.Body())
		}
	})
	t.Run("a check that changed the number of tool calls", func(t *testing.T) {
		v := Anthropic([]byte(`{"messages":[{"role":"assistant","content":[{"type":"tool_use","id":"t","name":"f","input":{}}]}]}`))
		req := readOK(t, v)
		req.Messages[0].ToolCalls = append(req.Messages[0].ToolCalls, req.Messages[0].ToolCalls[0])
		if err := v.ApplyChecks(); err == nil || !strings.Contains(err.Error(), "tool calls") {
			t.Errorf("ApplyChecks error %v, want one about the number of tool calls", err)
		}
	})
}
