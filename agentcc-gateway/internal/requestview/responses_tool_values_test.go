package requestview

import (
	"strings"
	"testing"
)

// A Responses tool call whose arguments (a function_call's or an mcp_call's
// arguments, a custom_tool_call's input, a code_interpreter_call's code) are
// a JSON value other than a string are read as JSON, as a shell call's action
// is, and a check's rewrite of them is written back as JSON.

var nonStringToolValues = []struct {
	name, item, args string
	// rewritten is a check's rewrite of args, and body the input item the
	// provider then gets.
	rewritten, body string
}{
	{"function_call arguments as an object",
		`{"type":"function_call","call_id":"c1","name":"lookup","arguments":{"ssn":"123-45-6789"}}`, `{"ssn":"123-45-6789"}`,
		`{"ssn":"***"}`, `{"type":"function_call","call_id":"c1","name":"lookup","arguments":{"ssn":"***"}}`},
	{"custom_tool_call input as an object",
		`{"type":"custom_tool_call","call_id":"c1","name":"lookup","input":{"ssn":"123-45-6789"}}`, `{"ssn":"123-45-6789"}`,
		`{"ssn":"***"}`, `{"type":"custom_tool_call","call_id":"c1","name":"lookup","input":{"ssn":"***"}}`},
	{"code_interpreter_call code as a list",
		`{"type":"code_interpreter_call","id":"ci1","code":["print('123-45-6789')"]}`, `["print('123-45-6789')"]`,
		`["print('***')"]`, `{"type":"code_interpreter_call","id":"ci1","code":["print('***')"]}`},
	{"mcp_call arguments as an object",
		`{"type":"mcp_call","id":"m1","server_label":"s","name":"lookup","arguments":{"ssn":"123-45-6789"},"output":"ok"}`, `{"ssn":"123-45-6789"}`,
		`{"ssn":"***"}`, `{"type":"mcp_call","id":"m1","server_label":"s","name":"lookup","arguments":{"ssn":"***"},"output":"ok"}`},
}

func responsesWithItem(item string) []byte {
	return []byte(`{"model":"gpt-4o","input":[` + item + `]}`)
}

func TestResponses_NonStringToolArgumentsAreReadAsJSON(t *testing.T) {
	for _, tc := range append(nonStringToolValues, struct{ name, item, args, rewritten, body string }{
		name: "function_call arguments as a number", item: `{"type":"function_call","call_id":"c1","name":"lookup","arguments":123456789}`,
		args: `123456789`,
	}) {
		t.Run(tc.name, func(t *testing.T) {
			req := readOK(t, Responses(responsesWithItem(tc.item)))
			if len(req.Messages) == 0 || len(req.Messages[0].ToolCalls) != 1 {
				t.Fatalf("read %+v, want the tool call", req.Messages)
			}
			if got := req.Messages[0].ToolCalls[0].Function.Arguments; got != tc.args {
				t.Errorf("arguments read as %q, want the value as JSON, %s", got, tc.args)
			}
		})
	}
}

func TestApplyChecks_WritesRewrittenNonStringToolArgumentsAsJSON(t *testing.T) {
	for _, tc := range nonStringToolValues {
		t.Run(tc.name, func(t *testing.T) {
			v := Responses(responsesWithItem(tc.item))
			if err := rewriteCall(t, v, 0, 0, tc.rewritten); err != nil {
				t.Fatalf("ApplyChecks: %v", err)
			}
			if got, want := string(v.Body()), string(responsesWithItem(tc.body)); got != want {
				t.Errorf("body\n got %s\nwant %s", got, want)
			}
		})
	}
}

// A rewrite that changes the value's JSON type (a number the check masks as
// a string) cannot be written where the body holds a number: an error, so the
// request is refused (remediation_unavailable), not sent unrewritten.
func TestApplyChecks_RefusesANonStringScalarRewrittenAsAString(t *testing.T) {
	v := Responses(responsesWithItem(`{"type":"function_call","call_id":"c1","name":"lookup","arguments":4111111111111111}`))
	before := string(v.Body())
	err := rewriteCall(t, v, 0, 0, `"[REDACTED:credit_card]"`)
	if err == nil || !strings.Contains(err.Error(), "not JSON of its type") {
		t.Errorf("ApplyChecks error %v, want one that says the rewrite is not JSON of its type", err)
	}
	if string(v.Body()) != before {
		t.Errorf("the body changed: %s", v.Body())
	}
}
