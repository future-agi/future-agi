package requestview

import (
	"context"
	"encoding/json"
	"reflect"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// shape is a chat request's messages as role and content text, for
// comparison: a string content as itself, a parts content as its parts.
func shape(t *testing.T, req *models.ChatCompletionRequest) []string {
	t.Helper()
	var out []string
	for _, m := range req.Messages {
		s := m.Role + ": "
		var text string
		if json.Unmarshal(m.Content, &text) == nil {
			s += text
		} else if len(m.Content) > 0 {
			s += string(m.Content)
		}
		for _, tc := range m.ToolCalls {
			s += " [call " + tc.ID + " " + tc.Function.Name + " " + tc.Function.Arguments + "]"
		}
		if m.ToolCallID != "" {
			s += " (for " + m.ToolCallID + ")"
		}
		out = append(out, s)
	}
	return out
}

func readOK(t *testing.T, v *View) *models.ChatCompletionRequest {
	t.Helper()
	req, reason := v.ChatRequest(context.Background())
	if req == nil {
		t.Fatalf("the body was not read: %s", reason)
	}
	return req
}

// A Messages API body reads as the chat request the gateway's translator
// gives such a request: the system prompt first, a turn's tool results
// before its other text, the text of a turn as one message, tool calls, and
// every text the model reads.
func TestAnthropic_ReadsTheChatShape(t *testing.T) {
	for _, tc := range []struct {
		name string
		body string
		want []string
	}{
		{"a string turn",
			`{"model":"m","max_tokens":5,"messages":[{"role":"user","content":"hello"}]}`,
			[]string{"user: hello"}},
		{"the system prompt, a string",
			`{"model":"m","system":"be brief","messages":[{"role":"user","content":"hi"}]}`,
			[]string{"system: be brief", "user: hi"}},
		{"the system prompt, blocks",
			`{"model":"m","system":[{"type":"text","text":"one","cache_control":{"type":"ephemeral"}},{"type":"text","text":"two"}],"messages":[]}`,
			[]string{"system: one\ntwo"}},
		{"text blocks of a turn",
			`{"model":"m","messages":[{"role":"user","content":[{"type":"text","text":"a"},{"type":"text","text":"b"}]}]}`,
			[]string{"user: a\nb"}},
		{"tool results first, then the turn's text",
			`{"model":"m","messages":[{"role":"user","content":[{"type":"text","text":"after"},{"type":"tool_result","tool_use_id":"t1","content":"r1"},{"type":"tool_result","tool_use_id":"t2","content":[{"type":"text","text":"r2"}]}]}]}`,
			[]string{"tool: r1 (for t1)", "tool: r2 (for t2)", "user: after"}},
		{"an assistant turn's text and tool calls; thinking is not read",
			`{"model":"m","messages":[{"role":"assistant","content":[{"type":"thinking","thinking":"secret plan","signature":"s"},{"type":"text","text":"calling"},{"type":"tool_use","id":"t1","name":"lookup","input":{"q":"x"}}]}]}`,
			[]string{`assistant: calling [call t1 lookup {"q":"x"}]`}},
		{"an image makes content parts",
			`{"model":"m","messages":[{"role":"user","content":[{"type":"text","text":"see"},{"type":"image","source":{"type":"base64","media_type":"image/png","data":"AAAA"}}]}]}`,
			[]string{`user: [{"text":"see","type":"text"},{"image_url":{"url":"data:image/png;base64,AAAA"},"type":"image_url"}]`}},
		{"an image by file id is not read",
			`{"model":"m","messages":[{"role":"user","content":[{"type":"text","text":"see"},{"type":"image","source":{"type":"file","file_id":"f1"}}]}]}`,
			[]string{"user: see"}},
		{"a document's title, context and text",
			`{"model":"m","messages":[{"role":"user","content":[{"type":"document","title":"T","context":"C","source":{"type":"text","media_type":"text/plain","data":"D"}}]}]}`,
			[]string{"user: T\nC\nD"}},
		{"a document of content blocks",
			`{"model":"m","messages":[{"role":"user","content":[{"type":"document","source":{"type":"content","content":[{"type":"text","text":"D1"},{"type":"text","text":"D2"}]}}]}]}`,
			[]string{"user: D1\nD2"}},
		{"a PDF document is not read",
			`{"model":"m","messages":[{"role":"user","content":[{"type":"document","source":{"type":"base64","media_type":"application/pdf","data":"JVBER"}},{"type":"text","text":"q"}]}]}`,
			[]string{"user: q"}},
		{"a search result",
			`{"model":"m","messages":[{"role":"user","content":[{"type":"search_result","source":"https://x","title":"T","content":[{"type":"text","text":"S"}]}]}]}`,
			[]string{"user: T\nS"}},
		{"a block of a type the reader does not know: its text",
			`{"model":"m","messages":[{"role":"user","content":[{"type":"future_block","inner":{"text":"F"},"input":{"text":"not a prompt"}}]}]}`,
			[]string{"user: F"}},
		{"an empty turn is no message",
			`{"model":"m","messages":[{"role":"user","content":[]},{"role":"user","content":"x"}]}`,
			[]string{"user: x"}},
		// A server tool's result, in the assistant's turn, is a tool message
		// with the text the model reads in it, under whatever field.
		{"code execution results: stdout and stderr",
			`{"model":"m","messages":[{"role":"assistant","content":[{"type":"server_tool_use","id":"s1","name":"bash_code_execution","input":{"command":"ls"}},` +
				`{"type":"bash_code_execution_tool_result","tool_use_id":"s1","content":{"type":"bash_code_execution_result","stdout":"OUT","stderr":"ERR","return_code":0,"content":[]}},` +
				`{"type":"code_execution_tool_result","tool_use_id":"s2","content":{"type":"code_execution_result","stdout":"","stderr":"E2","return_code":1}}]}]}`,
			[]string{"tool: OUT\nERR (for s1)", "tool: E2 (for s2)", `assistant:  [call s1 bash_code_execution {"command":"ls"}]`}},
		{"text editor results: a file's text and changed lines",
			`{"model":"m","messages":[{"role":"assistant","content":[{"type":"text_editor_code_execution_tool_result","tool_use_id":"s1","content":{"type":"text_editor_code_execution_view_result","file_type":"text","content":"FILE","num_lines":1}},` +
				`{"type":"text_editor_code_execution_tool_result","tool_use_id":"s2","content":{"type":"text_editor_code_execution_str_replace_result","old_start":1,"lines":["-a","+b"]}}]}]}`,
			[]string{"tool: FILE (for s1)", "tool: -a\n+b (for s2)"}},
		{"a fetched page's text and a search result's title",
			`{"model":"m","messages":[{"role":"assistant","content":[{"type":"web_fetch_tool_result","tool_use_id":"s1","content":{"type":"web_fetch_result","url":"https://x","content":{"type":"document","title":"PAGE","source":{"type":"text","media_type":"text/plain","data":"BODY"}}}},` +
				`{"type":"web_search_tool_result","tool_use_id":"s2","content":[{"type":"web_search_result","url":"https://y","title":"HIT","encrypted_content":"Eq0x","page_age":"2025-04-30"}]}]}]}`,
			[]string{"tool: PAGE\nBODY (for s1)", "tool: HIT (for s2)"}},
	} {
		t.Run(tc.name, func(t *testing.T) {
			got := shape(t, readOK(t, Anthropic([]byte(tc.body))))
			if !reflect.DeepEqual(got, tc.want) {
				t.Errorf("chat shape\n got %q\nwant %q", got, tc.want)
			}
		})
	}
}

// The tools are read by name, with their description and input schema; the
// model, the stream flag and the sampling settings are kept.
func TestAnthropic_ReadsToolsAndSettings(t *testing.T) {
	req := readOK(t, Anthropic([]byte(`{"model":"claude-x","stream":true,"max_tokens":9,"temperature":0.5,
		"messages":[{"role":"user","content":"hi"}],
		"tools":[{"name":"lookup","description":"finds","input_schema":{"type":"object"}},{"type":"web_search_20250305","name":"web_search"}]}`)))
	if req.Model != "claude-x" || !req.Stream || req.MaxTokens == nil || *req.MaxTokens != 9 || req.Temperature == nil {
		t.Errorf("settings model %q stream %v max_tokens %v temperature %v", req.Model, req.Stream, req.MaxTokens, req.Temperature)
	}
	var names []string
	for _, tool := range req.Tools {
		names = append(names, tool.Function.Name)
	}
	if !reflect.DeepEqual(names, []string{"lookup", "web_search"}) || req.Tools[0].Function.Description != "finds" {
		t.Errorf("tools %+v", req.Tools)
	}
}

// An MCP server the request connects (mcp_servers, Anthropic's MCP
// connector) is a tool it offers, named mcp:<the server's name>, and so is
// an mcp_toolset tool, by the server it names, once: a server named by both
// is one tool. A tool with no name is named by its type. A check on the
// tools (tool permissions) reads each.
func TestAnthropic_NamesMCPServersAndUnnamedTools(t *testing.T) {
	req := readOK(t, Anthropic([]byte(`{"model":"claude-x","max_tokens":9,"messages":[{"role":"user","content":"hi"}],
		"mcp_servers":[{"type":"url","url":"https://mcp.example.invalid/sse","name":"crm"},{"type":"url","url":"https://x.invalid"}],
		"tools":[{"name":"lookup"},{"type":"mcp_toolset","mcp_server_name":"crm"},{"type":"some_future_tool_20990101"}]}`)))
	var names []string
	for _, tool := range req.Tools {
		names = append(names, tool.Function.Name)
	}
	want := []string{"lookup", "mcp:crm", "some_future_tool_20990101", "mcp"}
	if !reflect.DeepEqual(names, want) {
		t.Errorf("tools %v, want %v", names, want)
	}
	if _, reason := Anthropic([]byte(`{"messages":[],"mcp_servers":{"name":"crm"}}`)).ChatRequest(context.Background()); !strings.Contains(reason, "mcp_servers is not a list") {
		t.Errorf("reason %q, want mcp_servers is not a list", reason)
	}
}

// A body that is not the Messages API's shape cannot be read, and the
// reason names where.
func TestAnthropic_UnreadableBodies(t *testing.T) {
	for _, tc := range []struct{ name, body, where string }{
		{"not an object", `[1]`, "not a JSON object"},
		{"messages not a list", `{"messages":{"role":"user"}}`, "messages is not a list"},
		{"a turn not an object", `{"messages":["hi"]}`, "messages[0] is not an object"},
		{"content of another type", `{"messages":[{"role":"user","content":42}]}`, "messages[0].content is neither"},
		{"a block not an object", `{"messages":[{"role":"user","content":["hi"]}]}`, "messages[0].content[0] is not an object"},
		{"a text that is not a string", `{"messages":[{"role":"user","content":[{"type":"text","text":7}]}]}`, "messages[0].content[0].text is not a string"},
		{"system of another type", `{"system":3,"messages":[]}`, "system is neither"},
		{"tools not a list", `{"messages":[],"tools":{}}`, "tools is not a list"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			req, reason := Anthropic([]byte(tc.body)).ChatRequest(context.Background())
			if req != nil || !strings.Contains(reason, tc.where) || !strings.HasPrefix(reason, "the gateway cannot read this request: ") {
				t.Errorf("request %v, reason %q; want none, and a reason that says %q", req, reason, tc.where)
			}
		})
	}
}

// The body is read once, on the first call, and Body is the body as given.
func TestView_ReadsOnce(t *testing.T) {
	reads := 0
	v := &View{body: []byte(`{}`), read: func(d *doc) (*chat, error) {
		reads++
		return readAnthropic(d)
	}}
	v.ChatRequest(context.Background())
	v.ChatRequest(context.Background())
	if reads != 1 || string(v.Body()) != `{}` {
		t.Errorf("read %d times, body %s", reads, v.Body())
	}
}
