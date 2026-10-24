package server

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"
	"sync"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// A check on the request reads it on every route, whether the gateway
// builds a chat request or passes the request through to the provider's own API
// (/v1/messages to an Anthropic-format provider, /v1beta to a Google-format
// one, /v1/responses), and reads it in the chat shape. A request whose text a
// check in block mode refuses is refused before the provider is called, with
// the guardrail's 403 in the route's own error format, and the request log
// records 403 and the block.
//
// The probe is pii-detection in block mode, as an organization's check and
// as a rule in the gateway's config file at the pre stage, on a request whose
// text carries a social security number. The fixture is newPostBlockGateway's.

const (
	requestCheck       = "pii-detection"
	requestCheckSSN    = "123-45-6789"
	requestCheckPrompt = "Please file my tax return. My SSN is " + requestCheckSSN + "."
)

// upstreamCalls records the API calls the upstream gets (its POSTs; the
// gateway's start-up connectivity check is a GET): each one's path and body.
// Its record method is a postBlockSetup.upstream handler that writes
// nothing, so startLeakUpstream still answers.
type upstreamCalls struct {
	mu     sync.Mutex
	paths  []string
	bodies []string
}

func (u *upstreamCalls) record(_ http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		return
	}
	b, _ := io.ReadAll(r.Body)
	r.Body = io.NopCloser(bytes.NewReader(b))
	u.mu.Lock()
	defer u.mu.Unlock()
	u.paths = append(u.paths, r.URL.Path)
	u.bodies = append(u.bodies, string(b))
}

func (u *upstreamCalls) count() int {
	u.mu.Lock()
	defer u.mu.Unlock()
	return len(u.paths)
}

// requestCheckShapes are the probe's two shapes of a check on the request in
// block mode.
var requestCheckShapes = []struct {
	name  string
	setup postBlockSetup
}{
	{"org check", postBlockSetup{orgRequestCheck: requestCheck}},
	{"config rule", postBlockSetup{requestRule: requestCheck}},
}

// newRequestCheckGateway is newPostBlockGateway for setup, with its upstream
// calls recorded.
func newRequestCheckGateway(t *testing.T, setup postBlockSetup) (*postBlockGateway, *upstreamCalls) {
	t.Helper()
	calls := &upstreamCalls{}
	setup.upstream = calls.record
	return newPostBlockGateway(t, setup), calls
}

// requestSurface is one route a request takes, with a body that carries text.
type requestSurface struct {
	name   string
	path   string
	body   func(text string) string
	format wireFormat
}

func messagesWith(model string, stream bool) func(string) string {
	return func(text string) string {
		return fmt.Sprintf(`{"model":%q,"max_tokens":64,"stream":%t,"messages":[{"role":"user","content":%q}]}`, model, stream, text)
	}
}

func genaiWith(text string) string {
	return fmt.Sprintf(`{"contents":[{"role":"user","parts":[{"text":%q}]}]}`, text)
}

func responsesWith(stream, background bool) func(string) string {
	return func(text string) string {
		return fmt.Sprintf(`{"model":"gpt-4o","stream":%t,"background":%t,"input":%q}`, stream, background, text)
	}
}

func chatWith(stream bool) func(string) string {
	return func(text string) string {
		return fmt.Sprintf(`{"model":"gpt-4o","stream":%t,"messages":[{"role":"user","content":%q}]}`, stream, text)
	}
}

// requestSurfaces are the routes a check on the request reads. The chat and
// translated routes are the parity controls.
var requestSurfaces = []requestSurface{
	{"chat", "/v1/chat/completions", chatWith(false), openaiWire},
	{"chat stream", "/v1/chat/completions", chatWith(true), openaiWire},
	{"messages, translated", "/v1/messages", messagesWith("gpt-4o", false), anthropicWire},
	{"messages, pass-through", "/v1/messages", messagesWith(postBlockClaude, false), anthropicWire},
	{"messages stream, pass-through", "/v1/messages", messagesWith(postBlockClaude, true), anthropicWire},
	{"generateContent, translated", "/v1beta/models/gpt-4o:generateContent", genaiWith, genaiWire},
	{"generateContent, pass-through", "/v1beta/models/" + postBlockGemini + ":generateContent", genaiWith, genaiWire},
	{"streamGenerateContent, pass-through", "/v1beta/models/" + postBlockGemini + ":streamGenerateContent", genaiWith, genaiWire},
	{"responses", "/v1/responses", responsesWith(false, false), openaiWire},
	{"responses stream", "/v1/responses", responsesWith(true, false), openaiWire},
	{"responses background", "/v1/responses", responsesWith(false, true), openaiWire},
}

// assertRequestRefused: the client got the guardrail's 403 for check in the
// surface's format, the provider was never called, and the request log
// records 403 and the block. The gateway read the request: the check
// refused its text, not a body it could not read (request_unavailable).
func assertRequestRefused(t *testing.T, g *postBlockGateway, calls *upstreamCalls, format wireFormat, code int, body, check string) {
	t.Helper()
	if code != http.StatusForbidden {
		t.Errorf("status %d, want the guardrail's 403; body %s", code, body)
	}
	assertWireError(t, format, strings.TrimSpace(body), check)
	if n := calls.count(); n != 0 {
		t.Errorf("the provider was called %d times (%v), want 0: the request went out", n, calls.paths)
	}
	e := g.entry(t)
	if e["status_code"] != float64(code) {
		t.Errorf("the log records status %v, the client got %d", e["status_code"], code)
	}
	assertBlockLogged(t, e, check)
	if meta, _ := e["metadata"].(map[string]any); meta["request_unavailable"] != nil {
		t.Errorf("refused unread (request_unavailable %v), want the request read and its text refused", meta["request_unavailable"])
	}
}

// Every surface refuses a request whose text the check blocks, for both
// shapes of the check, before the provider is called.
func TestRequestCheck_EverySurfaceRefusesABlockedRequest(t *testing.T) {
	for _, shape := range requestCheckShapes {
		for _, sf := range requestSurfaces {
			t.Run(sf.name+" / "+shape.name, func(t *testing.T) {
				g, calls := newRequestCheckGateway(t, shape.setup)
				code, body := g.do(t, sf.path, sf.body(requestCheckPrompt))
				assertRequestRefused(t, g, calls, sf.format, code, body, requestCheck)
			})
		}
	}
}

// Control: a request the check passes reaches the provider on every surface
// and is answered with 200.
func TestRequestCheck_EverySurfaceSendsAPassingRequest(t *testing.T) {
	for _, shape := range requestCheckShapes {
		for _, sf := range requestSurfaces {
			t.Run(sf.name+" / "+shape.name, func(t *testing.T) {
				g, calls := newRequestCheckGateway(t, shape.setup)
				code, body := g.do(t, sf.path, sf.body(postBlockPrompt))
				if code != http.StatusOK || calls.count() != 1 {
					t.Fatalf("status %d, provider calls %d; want 200 and 1; body %s", code, calls.count(), body)
				}
				if e := g.entry(t); e["status_code"] != float64(http.StatusOK) {
					t.Errorf("log status_code %v, want 200", e["status_code"])
				}
			})
		}
	}
}

// The check reads every place a Messages API request carries text the model
// reads, on a request passed through to an Anthropic-format provider: the
// system prompt as a string and as blocks, a text block after another, a
// tool result as a string and as blocks, a document's text and a search
// result. Each is refused.
func TestRequestCheck_MessagesPassThroughReadsEveryText(t *testing.T) {
	ssn, _ := json.Marshal(requestCheckPrompt)
	for _, tc := range []struct{ name, fields string }{
		{"system string", fmt.Sprintf(`"system":%s,"messages":[{"role":"user","content":"hi"}]`, ssn)},
		{"system blocks", fmt.Sprintf(`"system":[{"type":"text","text":"You help."},{"type":"text","text":%s}],"messages":[{"role":"user","content":"hi"}]`, ssn)},
		{"second text block", fmt.Sprintf(`"messages":[{"role":"user","content":[{"type":"text","text":"hi"},{"type":"text","text":%s}]}]`, ssn)},
		{"an earlier turn", fmt.Sprintf(`"messages":[{"role":"user","content":%s},{"role":"assistant","content":"ok"},{"role":"user","content":"thanks"}]`, ssn)},
		{"tool result string", fmt.Sprintf(`"messages":[{"role":"user","content":"look it up"},{"role":"assistant","content":[{"type":"tool_use","id":"tu_1","name":"lookup","input":{}}]},{"role":"user","content":[{"type":"tool_result","tool_use_id":"tu_1","content":%s}]}]`, ssn)},
		{"tool result blocks", fmt.Sprintf(`"messages":[{"role":"user","content":"look it up"},{"role":"assistant","content":[{"type":"tool_use","id":"tu_1","name":"lookup","input":{}}]},{"role":"user","content":[{"type":"tool_result","tool_use_id":"tu_1","content":[{"type":"text","text":%s}]}]}]`, ssn)},
		{"document text", fmt.Sprintf(`"messages":[{"role":"user","content":[{"type":"document","source":{"type":"text","media_type":"text/plain","data":%s}},{"type":"text","text":"summarize"}]}]`, ssn)},
		{"search result", fmt.Sprintf(`"messages":[{"role":"user","content":[{"type":"search_result","source":"https://example.com","title":"t","content":[{"type":"text","text":%s}]},{"type":"text","text":"summarize"}]}]`, ssn)},
		// A server tool's result the model reads, in the assistant's turn.
		{"bash code execution stdout", fmt.Sprintf(`"messages":[{"role":"user","content":"read notes.txt"},{"role":"assistant","content":[`+
			`{"type":"server_tool_use","id":"srvtoolu_1","name":"bash_code_execution","input":{"command":"cat notes.txt"}},`+
			`{"type":"bash_code_execution_tool_result","tool_use_id":"srvtoolu_1","content":{"type":"bash_code_execution_result","stdout":%s,"stderr":"","return_code":0}}]},`+
			`{"role":"user","content":"summarize"}]`, ssn)},
		{"code execution stderr", fmt.Sprintf(`"messages":[{"role":"user","content":"run it"},{"role":"assistant","content":[`+
			`{"type":"code_execution_tool_result","tool_use_id":"srvtoolu_1","content":{"type":"code_execution_result","stdout":"","stderr":%s,"return_code":1}}]},`+
			`{"role":"user","content":"why?"}]`, ssn)},
		{"text editor view", fmt.Sprintf(`"messages":[{"role":"user","content":"show notes.txt"},{"role":"assistant","content":[`+
			`{"type":"text_editor_code_execution_tool_result","tool_use_id":"srvtoolu_1","content":{"type":"text_editor_code_execution_view_result","file_type":"text","content":%s}}]},`+
			`{"role":"user","content":"summarize"}]`, ssn)},
		{"a fetched page", fmt.Sprintf(`"messages":[{"role":"user","content":"fetch it"},{"role":"assistant","content":[`+
			`{"type":"web_fetch_tool_result","tool_use_id":"srvtoolu_1","content":{"type":"web_fetch_result","url":"https://example.com","content":{"type":"document","source":{"type":"text","media_type":"text/plain","data":%s}}}}]},`+
			`{"role":"user","content":"summarize"}]`, ssn)},
	} {
		for _, shape := range requestCheckShapes {
			t.Run(tc.name+" / "+shape.name, func(t *testing.T) {
				g, calls := newRequestCheckGateway(t, shape.setup)
				body := fmt.Sprintf(`{"model":%q,"max_tokens":64,%s}`, postBlockClaude, tc.fields)
				code, resp := g.do(t, "/v1/messages", body)
				assertRequestRefused(t, g, calls, anthropicWire, code, resp, requestCheck)
			})
		}
	}
}

// The check reads every place a generateContent request carries text the
// model reads, on a request passed through to a Google-format provider: the
// system instruction, under either of the names Google's parser takes, a
// text part after another, an earlier turn, the model's own turn, and a
// function response, under either name. Each is refused, on the stream too.
func TestRequestCheck_GenAIPassThroughReadsEveryText(t *testing.T) {
	ssn, _ := json.Marshal(requestCheckPrompt)
	for _, tc := range []struct{ name, body string }{
		{"systemInstruction", fmt.Sprintf(`{"systemInstruction":{"parts":[{"text":%s}]},"contents":[{"role":"user","parts":[{"text":"hi"}]}]}`, ssn)},
		{"system_instruction", fmt.Sprintf(`{"system_instruction":{"parts":[{"text":%s}]},"contents":[{"role":"user","parts":[{"text":"hi"}]}]}`, ssn)},
		{"second text part", fmt.Sprintf(`{"contents":[{"role":"user","parts":[{"text":"hi"},{"text":%s}]}]}`, ssn)},
		{"an earlier turn", fmt.Sprintf(`{"contents":[{"role":"user","parts":[{"text":%s}]},{"role":"model","parts":[{"text":"ok"}]},{"role":"user","parts":[{"text":"thanks"}]}]}`, ssn)},
		{"the model's turn", fmt.Sprintf(`{"contents":[{"role":"user","parts":[{"text":"hi"}]},{"role":"model","parts":[{"text":%s}]},{"role":"user","parts":[{"text":"thanks"}]}]}`, ssn)},
		{"functionResponse", fmt.Sprintf(`{"contents":[{"role":"user","parts":[{"text":"look it up"}]},{"role":"model","parts":[{"functionCall":{"name":"lookup","args":{}}}]},{"role":"user","parts":[{"functionResponse":{"name":"lookup","response":{"result":%s}}}]}]}`, ssn)},
		{"function_response", fmt.Sprintf(`{"contents":[{"role":"user","parts":[{"text":"look it up"}]},{"role":"model","parts":[{"function_call":{"name":"lookup","args":{}}}]},{"role":"user","parts":[{"function_response":{"name":"lookup","response":{"result":%s}}}]}]}`, ssn)},
		// Google decodes a function response before the model reads it:
		// the number written with JSON escapes, as an encoder that escapes
		// characters writes it, is the number.
		{"functionResponse with escapes", `{"contents":[{"role":"user","parts":[{"text":"look it up"}]},{"role":"model","parts":[{"functionCall":{"name":"lookup","args":{}}}]},` +
			`{"role":"user","parts":[{"functionResponse":{"name":"lookup","response":{"result":"Please file my tax return. My SSN is 123\u002d45\u002d6789."}}}]}]}`},
		{"function_response with escaped digits", `{"contents":[{"role":"user","parts":[{"function_response":{"name":"lookup","response":{"r\u0065sult":"My SSN is \u0031\u00323-45-6789."}}}]}]}`},
		// Google's parser takes one value for a repeated field; its own
		// examples send contents and parts as one object.
		{"contents as one object", fmt.Sprintf(`{"contents":{"role":"user","parts":[{"text":%s}]}}`, ssn)},
		{"contents and parts as one object", fmt.Sprintf(`{"contents":{"role":"user","parts":{"text":%s}}}`, ssn)},
		{"a system instruction's parts as one object", fmt.Sprintf(`{"systemInstruction":{"parts":{"text":%s}},"contents":[{"role":"user","parts":[{"text":"hi"}]}]}`, ssn)},
	} {
		for _, action := range []string{"generateContent", "streamGenerateContent"} {
			for _, shape := range requestCheckShapes {
				t.Run(tc.name+" / "+action+" / "+shape.name, func(t *testing.T) {
					g, calls := newRequestCheckGateway(t, shape.setup)
					code, resp := g.do(t, "/v1beta/models/"+postBlockGemini+":"+action, tc.body)
					assertRequestRefused(t, g, calls, genaiWire, code, resp, requestCheck)
				})
			}
		}
	}
}

// The check reads every place a Responses API request carries text the
// model reads: the instructions, a message item's input_text parts (the
// second of two) and one with no type, an earlier assistant turn, a tool
// call's output as a string and as parts, a custom tool's output, a stored
// prompt's variables, and the text of an item of a type the gateway does not
// know (here a file search's results). Each is refused, on the stream and
// in the background too.
func TestRequestCheck_ResponsesReadsEveryText(t *testing.T) {
	ssn, _ := json.Marshal(requestCheckPrompt)
	for _, tc := range []struct{ name, fields string }{
		{"instructions", fmt.Sprintf(`"instructions":%s,"input":"hi"`, ssn)},
		{"input_text parts", fmt.Sprintf(`"input":[{"type":"message","role":"user","content":[{"type":"input_text","text":"hi"},{"type":"input_text","text":%s}]}]`, ssn)},
		{"a message with no type", fmt.Sprintf(`"input":[{"role":"user","content":%s}]`, ssn)},
		{"an earlier assistant turn", fmt.Sprintf(`"input":[{"role":"user","content":"hi"},{"type":"message","role":"assistant","content":[{"type":"output_text","text":%s}]},{"role":"user","content":"go on"}]`, ssn)},
		{"function_call_output string", fmt.Sprintf(`"input":[{"role":"user","content":"look it up"},{"type":"function_call","call_id":"c1","name":"lookup","arguments":"{}"},{"type":"function_call_output","call_id":"c1","output":%s}]`, ssn)},
		{"function_call_output parts", fmt.Sprintf(`"input":[{"type":"function_call","call_id":"c1","name":"lookup","arguments":"{}"},{"type":"function_call_output","call_id":"c1","output":[{"type":"input_text","text":%s}]}]`, ssn)},
		{"custom_tool_call_output", fmt.Sprintf(`"input":[{"type":"custom_tool_call","call_id":"c1","name":"grep","input":"x"},{"type":"custom_tool_call_output","call_id":"c1","output":%s}]`, ssn)},
		{"prompt variable string", fmt.Sprintf(`"prompt":{"id":"pmpt_1","variables":{"customer":%s}},"input":"hi"`, ssn)},
		{"prompt variable input_text", fmt.Sprintf(`"prompt":{"id":"pmpt_1","variables":{"customer":{"type":"input_text","text":%s}}}`, ssn)},
		{"an item of an unknown type", fmt.Sprintf(`"input":[{"type":"file_search_call","id":"fs_1","status":"completed","queries":["q"],"results":[{"file_id":"f1","filename":"a.txt","text":%s}]},{"role":"user","content":"summarize"}]`, ssn)},
		{"shell_call_output stdout", fmt.Sprintf(`"input":[{"type":"shell_call","call_id":"c1","action":{"commands":["cat notes.txt"]},"status":"completed"},`+
			`{"type":"shell_call_output","call_id":"c1","output":[{"stdout":%s,"stderr":"","outcome":{"type":"exit","exit_code":0}}]},{"role":"user","content":"summarize"}]`, ssn)},
		{"shell_call_output stderr", fmt.Sprintf(`"input":[{"type":"shell_call_output","call_id":"c1","output":[{"stdout":"","stderr":%s,"outcome":{"type":"exit","exit_code":1}}]}]`, ssn)},
		{"apply_patch_call_output", fmt.Sprintf(`"input":[{"type":"apply_patch_call","call_id":"c1","operation":{"type":"create_file","path":"a.txt","diff":"+x"}},`+
			`{"type":"apply_patch_call_output","call_id":"c1","status":"completed","output":%s}]`, ssn)},
		{"code_interpreter_call logs", fmt.Sprintf(`"input":[{"type":"code_interpreter_call","id":"ci_1","status":"completed","code":"print(open('n').read())","container_id":"cntr_1",`+
			`"outputs":[{"type":"logs","logs":%s}]},{"role":"user","content":"summarize"}]`, ssn)},
	} {
		for _, mode := range []string{`"stream":false`, `"stream":true`, `"background":true`} {
			for _, shape := range requestCheckShapes {
				t.Run(tc.name+" / "+mode+" / "+shape.name, func(t *testing.T) {
					g, calls := newRequestCheckGateway(t, shape.setup)
					body := fmt.Sprintf(`{"model":"gpt-4o",%s,%s}`, mode, tc.fields)
					code, resp := g.do(t, "/v1/responses", body)
					assertRequestRefused(t, g, calls, openaiWire, code, resp, requestCheck)
				})
			}
		}
	}
}

// unreadableBody is a body passed through to the provider's own API that the
// gateway cannot read into the chat shape.
type unreadableBody struct {
	name, path, body string
	format           wireFormat
}

// unreadablePassThroughBodies are such bodies, on every pass-through route.
var unreadablePassThroughBodies = []unreadableBody{
	{"messages", "/v1/messages", fmt.Sprintf(`{"model":%q,"max_tokens":64,"messages":[{"role":"user","content":42}]}`, postBlockClaude), anthropicWire},
	{"generateContent", "/v1beta/models/" + postBlockGemini + ":generateContent", `{"contents":[{"role":"user","parts":"hi"}]}`, genaiWire},
	{"streamGenerateContent", "/v1beta/models/" + postBlockGemini + ":streamGenerateContent", `{"contents":[{"role":"user","parts":[{"text":7}]}]}`, genaiWire},
	{"responses", "/v1/responses", `{"model":"gpt-4o","input":42}`, openaiWire},
	{"responses stream", "/v1/responses", `{"model":"gpt-4o","stream":true,"input":["hi"]}`, openaiWire},
	{"responses background", "/v1/responses", `{"model":"gpt-4o","background":true,"input":[{"role":"user","content":[{"type":"input_text","text":7}]}]}`, openaiWire},
}

// assertUnreadableRefused: the client got 403 request_unavailable in format,
// naming the check and saying the request cannot be read; the provider was
// never called; and the log records the block and the reason.
func assertUnreadableRefused(t *testing.T, g *postBlockGateway, calls *upstreamCalls, format wireFormat, code int, resp string) {
	t.Helper()
	if code != http.StatusForbidden || calls.count() != 0 {
		t.Fatalf("status %d, provider calls %d; want 403 and 0; body %s; the provider got %v", code, calls.count(), resp, calls.bodies)
	}
	assertUnavailableError(t, format, resp)
	entry := g.entry(t)
	assertBlockLogged(t, entry, requestCheck)
	if meta, _ := entry["metadata"].(map[string]any); meta["request_unavailable"] == nil {
		t.Errorf("log metadata %v, want request_unavailable", meta)
	}
}

// toolPermissions is an organization's Tool Permissions check as the
// console sets it: an allowlist of one tool, lookup, read on the request,
// in block mode.
const toolPermissions = "tool-permissions"

func toolPermissionsSetup() postBlockSetup {
	return postBlockSetup{orgChecks: map[string]*tenant.GuardrailCheck{toolPermissions: {Enabled: true, Action: "block",
		ConfidenceThreshold: 0.5, Config: map[string]any{"provider": "tool_permission", "mode": "allowlist",
			"apply_to": "request", "tools": "lookup"}}}}
}

// A check on the tools a request offers (an organization's Tool Permissions
// check) reads every tool of a request passed through to the provider's own
// API: a Gemini built-in tool (Google Search, code execution, URL context),
// under either spelling Google's parser takes, and an MCP server an
// Anthropic request connects (mcp_servers, or an mcp_toolset tool). A tool
// the allowlist leaves out refuses the request before the provider is
// called, and the refusal names it. A request that offers only the
// allowed tool goes on.
func TestRequestCheck_ToolPermissionsReadEveryTool(t *testing.T) {
	gemini := func(action, tools string) (string, string) {
		return "/v1beta/models/" + postBlockGemini + ":" + action,
			`{"contents":[{"role":"user","parts":[{"text":"what is new today?"}]}],"tools":` + tools + `}`
	}
	messages := func(fields string) (string, string) {
		return "/v1/messages", fmt.Sprintf(`{"model":%q,"max_tokens":64,"messages":[{"role":"user","content":"what is new today?"}],%s}`,
			postBlockClaude, fields)
	}
	for _, tc := range []struct {
		name       string
		path, body string
		format     wireFormat
		tool       string // the tool the refusal names; "" when the request goes on
	}{
		{"googleSearch", "", "", genaiWire, "google_search"},
		{"google_search", "", "", genaiWire, "google_search"},
		{"codeExecution", "", "", genaiWire, "code_execution"},
		{"urlContext, streamed", "", "", genaiWire, "url_context"},
		{"a built-in tool beside the allowed function", "", "", genaiWire, "google_search"},
		{"mcp_servers", "", "", anthropicWire, "mcp:crm"},
		{"an mcp_toolset tool", "", "", anthropicWire, "mcp:crm"},
		{"a Responses MCP tool", "", "", openaiWire, "mcp:crm"},
		{"control: the allowed function", "", "", genaiWire, ""},
		{"control: the allowed tool", "", "", anthropicWire, ""},
	} {
		switch tc.name {
		case "googleSearch":
			tc.path, tc.body = gemini("generateContent", `[{"googleSearch":{}}]`)
		case "google_search":
			tc.path, tc.body = gemini("generateContent", `[{"google_search":{}}]`)
		case "codeExecution":
			tc.path, tc.body = gemini("generateContent", `[{"codeExecution":{}}]`)
		case "urlContext, streamed":
			tc.path, tc.body = gemini("streamGenerateContent", `[{"urlContext":{}}]`)
		case "a built-in tool beside the allowed function":
			tc.path, tc.body = gemini("generateContent", `[{"functionDeclarations":[{"name":"lookup"}]},{"googleSearch":{}}]`)
		case "mcp_servers":
			tc.path, tc.body = messages(`"mcp_servers":[{"type":"url","url":"https://mcp.example.invalid/sse","name":"crm"}]`)
		case "an mcp_toolset tool":
			tc.path, tc.body = messages(`"mcp_servers":[{"type":"url","url":"https://mcp.example.invalid/sse","name":"crm"}],` +
				`"tools":[{"name":"lookup","input_schema":{"type":"object"}},{"type":"mcp_toolset","mcp_server_name":"crm"}]`)
		case "a Responses MCP tool":
			tc.path, tc.body = "/v1/responses", `{"model":"gpt-4o","input":"what is new today?","tools":[`+
				`{"type":"function","name":"lookup","parameters":{"type":"object"}},{"type":"mcp","server_label":"crm","server_url":"https://mcp.example.invalid/sse"}]}`
		case "control: the allowed function":
			tc.path, tc.body = gemini("generateContent", `[{"functionDeclarations":[{"name":"lookup"}]}]`)
		case "control: the allowed tool":
			tc.path, tc.body = messages(`"tools":[{"name":"lookup","input_schema":{"type":"object"}}]`)
		}
		t.Run(tc.name, func(t *testing.T) {
			g, calls := newRequestCheckGateway(t, toolPermissionsSetup())
			code, resp := g.do(t, tc.path, tc.body)
			if tc.tool == "" {
				if code != http.StatusOK || calls.count() != 1 {
					t.Fatalf("status %d, provider calls %d; want 200 and 1; body %s", code, calls.count(), resp)
				}
				return
			}
			assertRequestRefused(t, g, calls, tc.format, code, resp, toolPermissions)
			if n := strings.Count(resp, tc.tool); n != 1 {
				t.Errorf("the refusal %s names %s %d times, want once", resp, tc.tool, n)
			}
		})
	}
}

// A body passed through to the provider's own API that the gateway cannot
// read into the chat shape is refused before the provider is called when a
// check on the request would block it: 403 request_unavailable in the
// route's format, which names the check and says why, and the log records
// the block and the reason.
func TestRequestCheck_UnreadablePassThroughIsRefusedWhenACheckWouldBlock(t *testing.T) {
	for _, tc := range unreadablePassThroughBodies {
		for _, shape := range requestCheckShapes {
			t.Run(tc.name+" / "+shape.name, func(t *testing.T) {
				g, calls := newRequestCheckGateway(t, shape.setup)
				code, resp := g.do(t, tc.path, tc.body)
				assertUnreadableRefused(t, g, calls, tc.format, code, resp)
			})
		}
	}
}

// Such a body is refused, the same way, when a check on the request would
// rewrite it (pii-detection's mask or redact, here in log mode, so a body
// the gateway reads goes on with the rewrite): the rewrite could never be
// applied. Two of the bodies carry the number in a field the gateway reads
// beside one it cannot.
func TestRequestCheck_UnreadablePassThroughWithARewriteIsRefused(t *testing.T) {
	bodies := append([]unreadableBody{
		{"messages, the number in the system prompt", "/v1/messages", fmt.Sprintf(
			`{"model":%q,"max_tokens":64,"system":%q,"messages":[{"role":"user","content":42}]}`, postBlockClaude, requestCheckPrompt), anthropicWire},
		{"responses, the number in the instructions", "/v1/responses", fmt.Sprintf(
			`{"model":"gpt-4o","instructions":%q,"input":42}`, requestCheckPrompt), openaiWire},
	}, unreadablePassThroughBodies...)
	for _, rem := range remediations {
		for _, tc := range bodies {
			for _, shape := range remediationShapes(rem.mode) {
				t.Run(rem.mode+" / "+tc.name+" / "+shape.name, func(t *testing.T) {
					g, calls := newRequestCheckGateway(t, shape.setup)
					code, resp := g.do(t, tc.path, tc.body)
					assertUnreadableRefused(t, g, calls, tc.format, code, resp)
				})
			}
		}
	}
}

// assertUnavailableError: body is a refusal in format that names the check
// and says the request cannot be read; on the OpenAI-format routes its code
// is request_unavailable.
func assertUnavailableError(t *testing.T, format wireFormat, body string) {
	t.Helper()
	var e wireError
	if err := json.Unmarshal([]byte(body), &e); err != nil {
		t.Fatalf("not an error: %v; %s", err, body)
	}
	if !strings.Contains(e.Error.Message, requestCheck) || !strings.Contains(e.Error.Message, "cannot read this request") {
		t.Errorf("message %q, want it to name %s and say the request cannot be read", e.Error.Message, requestCheck)
	}
	switch format {
	case anthropicWire:
		if e.Type != "error" || e.Error.Type != "permission_error" {
			t.Errorf("error %s, want Anthropic's permission_error", body)
		}
	case genaiWire:
		if e.Error.Code != float64(http.StatusForbidden) || e.Error.Status != "PERMISSION_DENIED" {
			t.Errorf("error %s, want Google's 403 PERMISSION_DENIED", body)
		}
	case openaiWire:
		if e.Error.Type != "guardrail_error" || e.Error.Code != "request_unavailable" {
			t.Errorf("error %s, want guardrail_error request_unavailable", body)
		}
	}
}

// remediations are pii-detection's rewrites of the request, with what each
// writes in place of the social security number.
var remediations = []struct{ mode, token string }{
	{"mask", "***"},
	{"redact", "[REDACTED:ssn]"},
}

// remediationShapes are the probe's shapes of a check on the request that
// rewrites it, in log mode so the request goes on: pii-detection with
// remediation mode, as an organization's check and as a rule in the config
// file. For mask there is a third: an organization's pii-detection with the
// action "mask" and no config, as the console saves it, which the
// gateway runs as a mask of the request, so every rule for a check that
// rewrites the request (write-back, refusals) holds for it too.
func remediationShapes(mode string) []struct {
	name  string
	setup postBlockSetup
} {
	cfg := map[string]any{"remediation": mode}
	shapes := []struct {
		name  string
		setup postBlockSetup
	}{
		{"org check", postBlockSetup{orgRequestCheck: requestCheck, requestAction: "log", requestConfig: cfg}},
		{"config rule", postBlockSetup{requestRule: requestCheck, requestAction: "log", requestConfig: cfg}},
	}
	if mode == "mask" {
		shapes = append(shapes, struct {
			name  string
			setup postBlockSetup
		}{"org check, action mask", postBlockSetup{orgRequestCheck: requestCheck, requestAction: "mask"}})
	}
	return shapes
}

// assertRewriteSent: the request went on (200) and reached the provider
// once, with token where the social security number was, want times, and
// the number nowhere, as sent or as the provider decodes the body (a JSON
// escape the provider decodes is the character it stands for).
func assertRewriteSent(t *testing.T, calls *upstreamCalls, code int, resp, token string, want int) string {
	t.Helper()
	if code != http.StatusOK || calls.count() != 1 {
		t.Fatalf("status %d, provider calls %d; want 200 and 1; body %s", code, calls.count(), resp)
	}
	sent := calls.bodies[0]
	if strings.Contains(sent, requestCheckSSN) || strings.Contains(decodedJSON(t, sent), requestCheckSSN) {
		t.Errorf("the provider got the social security number: %s", sent)
	}
	if n := strings.Count(sent, token); n != want {
		t.Errorf("the provider got %q %d times, want %d: %s", token, n, want, sent)
	}
	return sent
}

// decodedJSON is body decoded and written again without escapes other than
// those JSON requires: the text a provider reads in it. A body that is not
// JSON is itself.
func decodedJSON(t *testing.T, body string) string {
	t.Helper()
	var v any
	if json.Unmarshal([]byte(body), &v) != nil {
		return body
	}
	var buf bytes.Buffer
	enc := json.NewEncoder(&buf)
	enc.SetEscapeHTML(false)
	if err := enc.Encode(v); err != nil {
		t.Fatal(err)
	}
	return buf.String()
}

// A check that rewrites the request (pii-detection's mask or redact) has its
// rewrite reach the provider on every surface, as on /v1/chat/completions:
// on a request passed through to the provider's own API, the gateway writes
// it into the body it sends.
func TestRequestCheck_EverySurfaceSendsTheRewrite(t *testing.T) {
	for _, rem := range remediations {
		for _, shape := range remediationShapes(rem.mode) {
			for _, sf := range requestSurfaces {
				t.Run(rem.mode+" / "+sf.name+" / "+shape.name, func(t *testing.T) {
					g, calls := newRequestCheckGateway(t, shape.setup)
					code, resp := g.do(t, sf.path, sf.body(requestCheckPrompt))
					assertRewriteSent(t, calls, code, resp, rem.token, 1)
				})
			}
		}
	}
}

// An organization's pii-detection with the action "mask", as the console
// saves it (no config), masks the request on every surface, and the request
// log records 200 and the check with the action mask.
func TestRequestCheck_OrgMaskActionMasksEverySurface(t *testing.T) {
	for _, sf := range requestSurfaces {
		t.Run(sf.name, func(t *testing.T) {
			g, calls := newRequestCheckGateway(t, postBlockSetup{orgRequestCheck: requestCheck, requestAction: "mask"})
			code, resp := g.do(t, sf.path, sf.body(requestCheckPrompt))
			assertRewriteSent(t, calls, code, resp, "***", 1)
			e := g.entry(t)
			if e["status_code"] != float64(http.StatusOK) || e["guardrail_triggered"] != true {
				t.Errorf("log status_code %v guardrail_triggered %v, want 200 and true", e["status_code"], e["guardrail_triggered"])
			}
			results, _ := e["guardrail_results"].([]any)
			if len(results) != 1 {
				t.Fatalf("log guardrail_results %v, want the one check", e["guardrail_results"])
			}
			if m, _ := results[0].(map[string]any); m["name"] != requestCheck || m["action"] != "mask" {
				t.Errorf("log guardrail_results %v, want %s with the action mask", e["guardrail_results"], requestCheck)
			}
		})
	}
}

// The rewrite reaches every place of a pass-through body that carries the
// number, and the rest of the body is sent as the client sent it.
func TestRequestCheck_PassThroughRewritesEveryText(t *testing.T) {
	ssn, _ := json.Marshal(requestCheckPrompt)
	for _, tc := range []struct {
		name, path, body string
		places           int
		kept             []string // parts of the body that must reach the provider unchanged
	}{
		{"messages", "/v1/messages", fmt.Sprintf(`{"model":%q,"max_tokens":64,`+
			`"system":[{"type":"text","text":"You help.","cache_control":{"type":"ephemeral"}},{"type":"text","text":%s}],`+
			`"messages":[{"role":"user","content":[{"type":"text","text":"hi"},{"type":"text","text":%s,"cache_control":{"type":"ephemeral"}}]},`+
			`{"role":"assistant","content":[{"type":"tool_use","id":"tu_1","name":"lookup","input":{"q":"x"}}]},`+
			`{"role":"user","content":[{"type":"tool_result","tool_use_id":"tu_1","content":%s},{"type":"document","source":{"type":"text","media_type":"text/plain","data":%s}}]}]}`,
			postBlockClaude, ssn, ssn, ssn, ssn), 4,
			[]string{`"cache_control":{"type":"ephemeral"}`, `"input":{"q":"x"}`, `"text":"You help."`, `"text":"hi"`}},
		{"generateContent", "/v1beta/models/" + postBlockGemini + ":generateContent", fmt.Sprintf(
			`{"systemInstruction":{"parts":[{"text":%s}]},"contents":[{"role":"user","parts":[{"text":"hi"},{"text":%s}]},`+
				`{"role":"model","parts":[{"functionCall":{"name":"lookup","args":{"q":"x"}}}]},`+
				`{"role":"user","parts":[{"functionResponse":{"name":"lookup","response":{"result":%s,"n":7}}}]}],`+
				`"generationConfig":{"temperature":0.2,"seed":12345678901234567890}}`, ssn, ssn, ssn), 3,
			[]string{`"args":{"q":"x"}`, `"n":7`, `"generationConfig":{"temperature":0.2,"seed":12345678901234567890}`}},
		{"messages, server tool results", "/v1/messages", fmt.Sprintf(`{"model":%q,"max_tokens":64,"messages":[{"role":"user","content":"read it"},{"role":"assistant","content":[`+
			`{"type":"server_tool_use","id":"s1","name":"bash_code_execution","input":{"command":"cat n"}},`+
			`{"type":"bash_code_execution_tool_result","tool_use_id":"s1","content":{"type":"bash_code_execution_result","stdout":%s,"stderr":"","return_code":0}},`+
			`{"type":"web_fetch_tool_result","tool_use_id":"s2","content":{"type":"web_fetch_result","url":"https://example.com","content":{"type":"document","source":{"type":"text","media_type":"text/plain","data":%s}}}}]},`+
			`{"role":"user","content":"summarize"}]}`, postBlockClaude, ssn, ssn), 2,
			[]string{`"input":{"command":"cat n"}`, `"return_code":0`, `"url":"https://example.com"`}},
		{"responses, tool outputs", "/v1/responses", fmt.Sprintf(`{"model":"gpt-4o","input":[`+
			`{"type":"shell_call_output","call_id":"c1","output":[{"stdout":%s,"stderr":"","outcome":{"type":"exit","exit_code":0}}]},`+
			`{"type":"code_interpreter_call","id":"ci_1","code":"x","container_id":"k","outputs":[{"type":"logs","logs":%s}]},{"role":"user","content":"go on"}]}`, ssn, ssn), 2,
			[]string{`"outcome":{"type":"exit","exit_code":0}`, `"container_id":"k"`}},
		{"generateContent, a function response with escapes", "/v1beta/models/" + postBlockGemini + ":generateContent",
			`{"contents":[{"role":"model","parts":[{"functionCall":{"name":"lookup","args":{"q":"x"}}}]},` +
				`{"role":"user","parts":[{"functionResponse":{"name":"lookup","response":{"result":"Please file my tax return. My SSN is 123\u002d45\u002d6789.","n":7}}}]}]}`, 1,
			[]string{`"args":{"q":"x"}`, `"response":{"result":"Please file my tax return. My SSN is ***.","n":7}`}},
		{"generateContent, contents and parts as one object", "/v1beta/models/" + postBlockGemini + ":generateContent", fmt.Sprintf(
			`{"system_instruction":{"parts":{"text":%s}},"contents":{"role":"user","parts":{"text":%s}},"generationConfig":{"temperature":0.2}}`, ssn, ssn), 2,
			[]string{`"system_instruction":{"parts":{"text":"Please file my tax return. My SSN is ***."}}`,
				`"contents":{"role":"user","parts":{"text":"Please file my tax return. My SSN is ***."}}`, `"generationConfig":{"temperature":0.2}`}},
		{"responses", "/v1/responses", fmt.Sprintf(`{"model":"gpt-4o","instructions":%s,`+
			`"input":[{"type":"message","role":"user","content":[{"type":"input_text","text":"hi"},{"type":"input_text","text":%s}]},`+
			`{"type":"function_call","call_id":"c1","name":"lookup","arguments":"{\"q\":\"x\"}"},{"type":"function_call_output","call_id":"c1","output":%s}],`+
			`"prompt":{"id":"pmpt_1","variables":{"customer":%s}},"metadata":{"k":"v"}}`, ssn, ssn, ssn, ssn), 4,
			[]string{`"arguments":"{\"q\":\"x\"}"`, `"metadata":{"k":"v"}`, `"id":"pmpt_1"`}},
	} {
		for _, shape := range remediationShapes("mask") {
			t.Run(tc.name+" / "+shape.name, func(t *testing.T) {
				g, calls := newRequestCheckGateway(t, shape.setup)
				code, resp := g.do(t, tc.path, tc.body)
				sent := assertRewriteSent(t, calls, code, resp, "***", tc.places)
				for _, kept := range tc.kept {
					if !strings.Contains(sent, kept) {
						t.Errorf("the provider did not get %s as sent: %s", kept, sent)
					}
				}
				if !json.Valid([]byte(sent)) {
					t.Errorf("the provider got a body that is not JSON: %s", sent)
				}
			})
		}
	}
}

// A rewrite the gateway cannot write into a pass-through body is refused
// before the provider is called, rather than the request sent without it:
// 403 remediation_unavailable naming the check. Here the number the check
// masks runs from a document's title into its text, so the masked text has
// no place in either.
func TestRequestCheck_AnUnwritableRewriteIsRefused(t *testing.T) {
	for _, shape := range remediationShapes("mask") {
		t.Run(shape.name, func(t *testing.T) {
			g, calls := newRequestCheckGateway(t, shape.setup)
			body := fmt.Sprintf(`{"model":%q,"max_tokens":64,"messages":[{"role":"user","content":[`+
				`{"type":"document","title":"call 555-123","source":{"type":"text","media_type":"text/plain","data":"4567 tomorrow"}}]}]}`, postBlockClaude)
			code, resp := g.do(t, "/v1/messages", body)
			if code != http.StatusForbidden || calls.count() != 0 {
				t.Fatalf("status %d, provider calls %d; want 403 and 0; body %s", code, calls.count(), resp)
			}
			var e wireError
			_ = json.Unmarshal([]byte(resp), &e)
			if e.Type != "error" || e.Error.Type != "permission_error" || !strings.Contains(e.Error.Message, requestCheck) ||
				!strings.Contains(e.Error.Message, "cannot write the rewrite") {
				t.Errorf("error %s, want Anthropic's permission_error naming %s and saying the rewrite cannot be written", resp, requestCheck)
			}
			assertBlockLogged(t, g.entry(t), requestCheck)
		})
	}
}

// /v1/completions to an OpenAI-compatible provider forwards the prompt as
// the client sent it, so a check's rewrite of the request (a remediation)
// is written into that prompt too: a string, or each prompt of a list. A
// provider of another format gets the chat request the checks rewrote (the
// control). A rewrite that runs across two prompts of a list
// cannot be written into either, and refuses the request before the
// provider is called.
func TestRequestCheck_CompletionsSendsTheRewrite(t *testing.T) {
	for _, tc := range []struct {
		name, body, want string
	}{
		{"native, a prompt", fmt.Sprintf(`{"model":"gpt-4o","prompt":%q}`, requestCheckPrompt),
			`"prompt":"Please file my tax return. My SSN is ***."`},
		{"native, a list of prompts", fmt.Sprintf(`{"model":"gpt-4o","prompt":["hi",%q]}`, requestCheckPrompt),
			`"prompt":["hi","Please file my tax return. My SSN is ***."]`},
		{"translated", fmt.Sprintf(`{"model":%q,"prompt":%q}`, postBlockClaude, requestCheckPrompt),
			`Please file my tax return. My SSN is ***.`},
	} {
		for _, shape := range remediationShapes("mask") {
			t.Run(tc.name+" / "+shape.name, func(t *testing.T) {
				g, calls := newRequestCheckGateway(t, shape.setup)
				code, resp := g.do(t, "/v1/completions", tc.body)
				sent := assertRewriteSent(t, calls, code, resp, "***", 1)
				if !strings.Contains(sent, tc.want) {
					t.Errorf("the provider got %s, want it to hold %s", sent, tc.want)
				}
			})
		}
	}
	for _, shape := range remediationShapes("mask") {
		t.Run("native, a rewrite across two prompts / "+shape.name, func(t *testing.T) {
			g, calls := newRequestCheckGateway(t, shape.setup)
			code, resp := g.do(t, "/v1/completions", `{"model":"gpt-4o","prompt":["call 555-123","4567 now"]}`)
			if code != http.StatusForbidden || calls.count() != 0 {
				t.Fatalf("status %d, provider calls %d; want 403 and 0; body %s", code, calls.count(), resp)
			}
			var e wireError
			_ = json.Unmarshal([]byte(resp), &e)
			if e.Error.Type != "guardrail_error" || e.Error.Code != "remediation_unavailable" || !strings.Contains(e.Error.Message, requestCheck) {
				t.Errorf("error %s, want guardrail_error remediation_unavailable naming %s", resp, requestCheck)
			}
			assertBlockLogged(t, g.entry(t), requestCheck)
		})
	}
}

// countTokensUpstream records the upstream calls, as calls.record does, and
// answers a token count on the two count routes (the fixture's upstream
// answers neither).
func countTokensUpstream(calls *upstreamCalls) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		calls.record(w, r)
		switch {
		case r.URL.Path == "/v1/messages/count_tokens":
			w.Header().Set("Content-Type", "application/json")
			_, _ = w.Write([]byte(`{"input_tokens":5}`))
		case strings.HasSuffix(r.URL.Path, ":countTokens"):
			w.Header().Set("Content-Type", "application/json")
			_, _ = w.Write([]byte(`{"totalTokens":5}`))
		}
	}
}

// countTokensSurfaces are the routes that count a request's tokens at the
// provider, which gets the whole prompt: Anthropic's count_tokens (the
// route Claude Code calls with its prompt) and Google's countTokens, its
// prompt as contents or as a generateContentRequest.
var countTokensSurfaces = []requestSurface{
	{"messages count_tokens", "/v1/messages/count_tokens", func(text string) string {
		return fmt.Sprintf(`{"model":%q,"system":"You help.","messages":[{"role":"user","content":%q}]}`, postBlockClaude, text)
	}, anthropicWire},
	{"countTokens, contents", "/v1beta/models/" + postBlockGemini + ":countTokens", genaiWith, genaiWire},
	{"countTokens, generateContentRequest", "/v1beta/models/" + postBlockGemini + ":countTokens", func(text string) string {
		return fmt.Sprintf(`{"generateContentRequest":{"model":"models/%s","contents":[{"role":"user","parts":[{"text":%q}]}]}}`, postBlockGemini, text)
	}, genaiWire},
	{"countTokens, generate_content_request", "/v1beta/models/" + postBlockGemini + ":countTokens", func(text string) string {
		return fmt.Sprintf(`{"generate_content_request":{"model":"models/%s","system_instruction":{"parts":[{"text":%q}]},"contents":[{"parts":[{"text":"hi"}]}]}}`, postBlockGemini, text)
	}, genaiWire},
}

// The checks on the request read a request whose tokens the provider
// counts, as they read the request itself: a request whose text a check in
// block mode refuses is refused before the provider is called, in the
// route's format, and the log records 403 and the block; a check's rewrite
// reaches the provider. A request the check passes is counted (the control).
func TestRequestCheck_CountTokensIsChecked(t *testing.T) {
	newGateway := func(t *testing.T, setup postBlockSetup) (*postBlockGateway, *upstreamCalls) {
		calls := &upstreamCalls{}
		setup.upstream = countTokensUpstream(calls)
		return newPostBlockGateway(t, setup), calls
	}
	for _, sf := range countTokensSurfaces {
		for _, shape := range requestCheckShapes {
			t.Run("block / "+sf.name+" / "+shape.name, func(t *testing.T) {
				g, calls := newGateway(t, shape.setup)
				code, resp := g.do(t, sf.path, sf.body(requestCheckPrompt))
				assertRequestRefused(t, g, calls, sf.format, code, resp, requestCheck)
			})
			t.Run("control / "+sf.name+" / "+shape.name, func(t *testing.T) {
				g, calls := newGateway(t, shape.setup)
				code, resp := g.do(t, sf.path, sf.body(postBlockPrompt))
				if code != http.StatusOK || calls.count() != 1 || !strings.Contains(resp, "5") {
					t.Fatalf("status %d, provider calls %d; want 200, 1 and the count; body %s", code, calls.count(), resp)
				}
			})
		}
		for _, rem := range remediations {
			for _, shape := range remediationShapes(rem.mode) {
				t.Run(rem.mode+" / "+sf.name+" / "+shape.name, func(t *testing.T) {
					g, calls := newGateway(t, shape.setup)
					code, resp := g.do(t, sf.path, sf.body(requestCheckPrompt))
					assertRewriteSent(t, calls, code, resp, rem.token, 1)
				})
			}
		}
	}
}

// A native /v1/completions prompt of token ids is forwarded to the provider,
// which decodes the ids into the text the model reads; the gateway reads
// only their JSON text. Such a request is refused before the provider is
// called, 403 request_unavailable, when a check on the request would block
// or rewrite it, as a pass-through body the gateway cannot read is. With a
// check that neither blocks nor rewrites it goes on, with
// request_unavailable in the log, and to a provider of another format the
// ids are the chat request's text, which the checks read as the provider
// gets it (the controls).
func TestRequestCheck_CompletionsTokenIDsAreRefusedUnread(t *testing.T) {
	prompts := []string{`[5618,1052,856,3827,471]`, `[[5618,1052],[856]]`, `["hi",471]`}
	shapes := append(append([]struct {
		name  string
		setup postBlockSetup
	}{}, requestCheckShapes...), remediationShapes("mask")...)
	for i := range shapes[len(requestCheckShapes):] {
		shapes[len(requestCheckShapes)+i].name = "mask " + shapes[len(requestCheckShapes)+i].name
	}
	for _, prompt := range prompts {
		for _, shape := range shapes {
			t.Run(prompt+" / "+shape.name, func(t *testing.T) {
				g, calls := newRequestCheckGateway(t, shape.setup)
				code, resp := g.do(t, "/v1/completions", `{"model":"gpt-4o","prompt":`+prompt+`}`)
				assertUnreadableRefused(t, g, calls, openaiWire, code, resp)
			})
		}
	}
	t.Run("control: a check that neither blocks nor rewrites", func(t *testing.T) {
		g, calls := newRequestCheckGateway(t, postBlockSetup{orgRequestCheck: requestCheck, requestAction: "log"})
		code, resp := g.do(t, "/v1/completions", `{"model":"gpt-4o","prompt":[5618,1052]}`)
		if code != http.StatusOK || calls.count() != 1 {
			t.Fatalf("status %d, provider calls %d; want 200 and 1; body %s", code, calls.count(), resp)
		}
		if meta, _ := g.entry(t)["metadata"].(map[string]any); meta["request_unavailable"] == nil {
			t.Errorf("log metadata %v, want request_unavailable", meta)
		}
	})
	for _, shape := range requestCheckShapes {
		t.Run("control: a provider of another format / "+shape.name, func(t *testing.T) {
			g, calls := newRequestCheckGateway(t, shape.setup)
			code, resp := g.do(t, "/v1/completions", fmt.Sprintf(`{"model":%q,"max_tokens":8,"prompt":[5618,1052]}`, postBlockClaude))
			if code != http.StatusOK || calls.count() != 1 {
				t.Fatalf("status %d, provider calls %d; want 200 and 1; body %s", code, calls.count(), resp)
			}
		})
	}
}
