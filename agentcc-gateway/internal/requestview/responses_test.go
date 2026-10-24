package requestview

import (
	"context"
	"reflect"
	"strings"
	"testing"
)

// A Responses API body reads as a chat request: instructions, a prompt's
// variables, then each input item in order.
func TestResponses_ReadsTheChatShape(t *testing.T) {
	for _, tc := range []struct {
		name string
		body string
		want []string
	}{
		{"a string input",
			`{"model":"m","input":"hello"}`,
			[]string{"user: hello"}},
		{"instructions first",
			`{"model":"m","instructions":"be brief","input":"hello"}`,
			[]string{"system: be brief", "user: hello"}},
		{"message items, with and without a type, of every role",
			`{"input":[{"role":"developer","content":"rules"},{"type":"message","role":"user","content":[{"type":"input_text","text":"a"},{"type":"input_text","text":"b"}]},{"type":"message","role":"assistant","content":[{"type":"output_text","text":"c"},{"type":"refusal","refusal":"no"}]}]}`,
			[]string{"developer: rules", "user: a\nb", "assistant: c\nno"}},
		{"an image makes content parts; a file is not read",
			`{"input":[{"role":"user","content":[{"type":"input_text","text":"see"},{"type":"input_image","image_url":"https://x/i.png"},{"type":"input_file","file_id":"f1"}]}]}`,
			[]string{`user: [{"text":"see","type":"text"},{"image_url":{"url":"https://x/i.png"},"type":"image_url"}]`}},
		{"function and custom tool calls and their outputs",
			`{"input":[{"type":"function_call","call_id":"c1","name":"lookup","arguments":"{\"q\":1}"},{"type":"function_call_output","call_id":"c1","output":"r1"},{"type":"custom_tool_call","call_id":"c2","name":"grep","input":"x"},{"type":"custom_tool_call_output","call_id":"c2","output":[{"type":"input_text","text":"r2"}]}]}`,
			[]string{`assistant:  [call c1 lookup {"q":1}]`, "tool: r1 (for c1)", "assistant:  [call c2 grep x]", "tool: r2 (for c2)"}},
		{"an MCP call and its output",
			`{"input":[{"type":"mcp_call","id":"m1","name":"search","arguments":"{}","output":"found"}]}`,
			[]string{"assistant:  [call m1 search {}]", "tool: found (for m1)"}},
		// A tool's output is a tool message with the text the model reads
		// in it, under whatever field the item keeps it.
		{"shell and apply_patch calls and their outputs",
			`{"input":[{"type":"shell_call","call_id":"c1","action":{"commands":["ls"]}},{"type":"shell_call_output","call_id":"c1","output":[{"stdout":"OUT","stderr":"ERR","outcome":{"type":"exit","exit_code":0}}]},` +
				`{"type":"apply_patch_call","call_id":"c2","operation":{"type":"update_file","path":"a.py","diff":"-x"}},{"type":"apply_patch_call_output","call_id":"c2","output":"patched"}]}`,
			[]string{`assistant:  [call c1 shell {"commands":["ls"]}]`, "tool: OUT\nERR (for c1)",
				`assistant:  [call c2 apply_patch {"type":"update_file","path":"a.py","diff":"-x"}]`, "tool: patched (for c2)"}},
		{"a code interpreter call's logs",
			`{"input":[{"type":"code_interpreter_call","id":"ci1","code":"print(1)","container_id":"k","outputs":[{"type":"logs","logs":"LOGS"},{"type":"image","url":"https://x/p.png"}]}]}`,
			[]string{"assistant:  [call ci1 code_interpreter print(1)]", "tool: LOGS (for ci1)"}},
		{"reasoning and item references are not read",
			`{"input":[{"type":"reasoning","id":"r1","summary":[{"type":"summary_text","text":"plan"}]},{"type":"item_reference","id":"i1"},{"role":"user","content":"x"}]}`,
			[]string{"user: x"}},
		{"an item of an unknown type: its text, as the user's",
			`{"input":[{"type":"file_search_call","id":"fs","results":[{"text":"found text","filename":"a"}]}]}`,
			[]string{"user: found text"}},
		{"a stored prompt's variables, in name order",
			`{"prompt":{"id":"p1","variables":{"b":"second","a":{"type":"input_text","text":"first"},"c":{"type":"input_file","file_id":"f"}}},"input":"go"}`,
			[]string{"user: first\nsecond", "user: go"}},
	} {
		t.Run(tc.name, func(t *testing.T) {
			got := shape(t, readOK(t, Responses([]byte(tc.body))))
			if !reflect.DeepEqual(got, tc.want) {
				t.Errorf("chat shape\n got %q\nwant %q", got, tc.want)
			}
		})
	}
}

// An MCP tool is named mcp:<its server_label>, as an MCP server is on
// /v1/messages, so one allowlist entry names the server on both routes.
func TestResponses_NamesMCPToolsByServer(t *testing.T) {
	req := readOK(t, Responses([]byte(`{"model":"gpt-x","input":"x","tools":[{"type":"mcp","server_label":"crm","server_url":"https://mcp.example.invalid/sse"},{"type":"mcp","connector_id":"connector_gmail"}]}`)))
	var names []string
	for _, tool := range req.Tools {
		names = append(names, tool.Function.Name)
	}
	if want := []string{"mcp:crm", "mcp"}; !reflect.DeepEqual(names, want) {
		t.Errorf("tools %v, want %v", names, want)
	}
}

// Function and custom tools are read by name; a tool the provider runs, by
// its type.
func TestResponses_ReadsTools(t *testing.T) {
	req := readOK(t, Responses([]byte(`{"model":"gpt-x","stream":true,"input":"x","tools":[{"type":"function","name":"lookup","parameters":{}},{"type":"custom","name":"grep"},{"type":"web_search"}]}`)))
	var names []string
	for _, tool := range req.Tools {
		names = append(names, tool.Function.Name)
	}
	if !reflect.DeepEqual(names, []string{"lookup", "grep", "web_search"}) || req.Model != "gpt-x" || !req.Stream {
		t.Errorf("tools %v model %q stream %v", names, req.Model, req.Stream)
	}
}

func TestResponses_UnreadableBodies(t *testing.T) {
	for _, tc := range []struct{ name, body, where string }{
		{"input of another type", `{"input":42}`, "input is neither a string nor a list of items"},
		{"an item not an object", `{"input":["hi"]}`, "input[0] is not an object"},
		{"content of another type", `{"input":[{"role":"user","content":7}]}`, "input[0].content is neither"},
		{"a part not an object", `{"input":[{"role":"user","content":["x"]}]}`, "input[0].content[0] is not an object"},
		{"a text that is not a string", `{"input":[{"role":"user","content":[{"type":"input_text","text":7}]}]}`, "input[0].content[0].text is not a string"},
		{"an output of another type", `{"input":[{"type":"function_call_output","call_id":"c","output":{"x":1}}]}`, "input[0].output is neither"},
		{"instructions not a string", `{"instructions":["x"]}`, "instructions is not a string"},
		{"prompt variables not an object", `{"prompt":{"id":"p","variables":[1]}}`, "prompt.variables is not an object"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			req, reason := Responses([]byte(tc.body)).ChatRequest(context.Background())
			if req != nil || !strings.Contains(reason, tc.where) {
				t.Errorf("request %v, reason %q; want none, and a reason that says %q", req, reason, tc.where)
			}
		})
	}
}
