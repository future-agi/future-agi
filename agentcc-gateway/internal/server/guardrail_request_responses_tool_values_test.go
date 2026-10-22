package server

import (
	"encoding/json"
	"testing"
)

// On /v1/responses a tool call whose arguments are a JSON value other than a
// string (a function_call's or an mcp_call's arguments, a custom_tool_call's
// input, a code_interpreter_call's code) is read: a check in block mode
// refuses the request before the provider is called, and a rewrite
// (pii-detection's mask or redact) reaches the provider written as JSON
// where the value was.

var responsesNonStringItems = []struct {
	name, item, field string
}{
	{"function_call arguments as an object", `{"type":"function_call","call_id":"c1","name":"lookup","arguments":{"ssn":"` + requestCheckSSN + `"}}`, "arguments"},
	{"custom_tool_call input as an object", `{"type":"custom_tool_call","call_id":"c1","name":"lookup","input":{"ssn":"` + requestCheckSSN + `"}}`, "input"},
	{"code_interpreter_call code as a list", `{"type":"code_interpreter_call","id":"ci1","code":["print('` + requestCheckSSN + `')"]}`, "code"},
	{"mcp_call arguments as an object", `{"type":"mcp_call","id":"m1","server_label":"s","name":"lookup","arguments":{"ssn":"` + requestCheckSSN + `"},"output":"ok"}`, "arguments"},
}

func responsesNonStringBody(item string) string {
	return `{"model":"gpt-4o","input":[{"role":"user","content":"Look my record up."},` + item + `]}`
}

func TestRequestCheck_ResponsesNonStringToolArgumentsAreRead(t *testing.T) {
	for _, it := range responsesNonStringItems {
		for _, shape := range requestCheckShapes {
			t.Run("block / "+it.name+" / "+shape.name, func(t *testing.T) {
				g, calls := newRequestCheckGateway(t, shape.setup)
				code, resp := g.do(t, "/v1/responses", responsesNonStringBody(it.item))
				assertRequestRefused(t, g, calls, openaiWire, code, resp, requestCheck)
			})
		}
		for _, rem := range remediations {
			for _, shape := range remediationShapes(rem.mode) {
				t.Run(rem.mode+" / "+it.name+" / "+shape.name, func(t *testing.T) {
					g, calls := newRequestCheckGateway(t, shape.setup)
					code, resp := g.do(t, "/v1/responses", responsesNonStringBody(it.item))
					sent := assertRewriteSent(t, calls, code, resp, rem.token, 1)
					var req struct {
						Input []map[string]json.RawMessage `json:"input"`
					}
					if err := json.Unmarshal([]byte(sent), &req); err != nil || len(req.Input) != 2 {
						t.Fatalf("the provider got %s (%v)", sent, err)
					}
					if v := req.Input[1][it.field]; len(v) == 0 || (v[0] != '{' && v[0] != '[') {
						t.Errorf("the provider got %s %s, want it written back as JSON of its type", it.field, v)
					}
				})
			}
		}
	}
}
