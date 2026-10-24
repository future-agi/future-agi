package server

import (
	"encoding/json"
	"fmt"
	"net/http"
	"strings"
	"testing"
)

// A check on the request reads the arguments of the tool calls a request
// carries, on every route that carries them, and a check that rewrites the
// request (pii-detection's mask or redact) rewrites them where the provider
// reads them: in the chat request the gateway sends, and in the body it
// passes through to the provider's own API (an Anthropic tool_use's input, a
// Gemini functionCall's args, a Responses function_call's arguments). The
// arguments stay JSON, and the request log keeps the rewritten arguments.
//
// The probe is the request-check fixture's pii-detection, on a request whose
// only social security number is in the assistant's tool call.

const toolCallArgs = `{"ssn":"` + requestCheckSSN + `","note":"file it"}`

func chatToolCall() string {
	args, _ := json.Marshal(toolCallArgs)
	return `{"model":"gpt-4o","messages":[{"role":"user","content":"Look my record up."},` +
		`{"role":"assistant","content":null,"tool_calls":[{"id":"call_1","type":"function","function":{"name":"lookup","arguments":` + string(args) + `}}]},` +
		`{"role":"tool","tool_call_id":"call_1","content":"found"}]}`
}

func messagesToolCall(model string) string {
	return fmt.Sprintf(`{"model":%q,"max_tokens":64,"messages":[{"role":"user","content":"Look my record up."},`+
		`{"role":"assistant","content":[{"type":"tool_use","id":"tu_1","name":"lookup","input":%s}]},`+
		`{"role":"user","content":[{"type":"tool_result","tool_use_id":"tu_1","content":"found"}]}]}`, model, toolCallArgs)
}

func genaiToolCall() string {
	return `{"contents":[{"role":"user","parts":[{"text":"Look my record up."}]},` +
		`{"role":"model","parts":[{"functionCall":{"name":"lookup","args":` + toolCallArgs + `}}]},` +
		`{"role":"user","parts":[{"functionResponse":{"name":"lookup","response":{"r":"found"}}}]}]}`
}

func responsesToolCall() string {
	args, _ := json.Marshal(toolCallArgs)
	return `{"model":"gpt-4o","input":[{"role":"user","content":"Look my record up."},` +
		`{"type":"function_call","call_id":"c1","name":"lookup","arguments":` + string(args) + `},` +
		`{"type":"function_call_output","call_id":"c1","output":"found"}]}`
}

// toolCallSurfaces are the routes whose request carries a tool call: as
// the gateway's chat request (chat, and the routes it translates for a
// provider of another format) and as the body it passes through.
var toolCallSurfaces = []struct {
	name, path, body string
	format           wireFormat
	// arguments finds the tool call's arguments in the body the provider
	// got, as JSON.
	arguments func(t *testing.T, sent string) string
}{
	{"chat", "/v1/chat/completions", chatToolCall(), openaiWire, chatArguments},
	{"messages, translated", "/v1/messages", messagesToolCall("gpt-4o"), anthropicWire, chatArguments},
	{"messages, pass-through", "/v1/messages", messagesToolCall(postBlockClaude), anthropicWire, anthropicInput},
	{"generateContent, translated", "/v1beta/models/gpt-4o:generateContent", genaiToolCall(), genaiWire, chatArguments},
	{"generateContent, pass-through", "/v1beta/models/" + postBlockGemini + ":generateContent", genaiToolCall(), genaiWire, geminiArgs},
	{"responses", "/v1/responses", responsesToolCall(), openaiWire, responsesArguments},
}

// chatArguments is the arguments of the tool call in a chat request.
func chatArguments(t *testing.T, sent string) string {
	t.Helper()
	var req struct {
		Messages []struct {
			ToolCalls []struct {
				Function struct {
					Arguments string `json:"arguments"`
				} `json:"function"`
			} `json:"tool_calls"`
		} `json:"messages"`
	}
	if err := json.Unmarshal([]byte(sent), &req); err != nil {
		t.Fatalf("the provider got a chat request that is not JSON: %v: %s", err, sent)
	}
	for _, m := range req.Messages {
		for _, tc := range m.ToolCalls {
			return tc.Function.Arguments
		}
	}
	t.Fatalf("the provider got no tool call: %s", sent)
	return ""
}

func anthropicInput(t *testing.T, sent string) string {
	t.Helper()
	var req struct {
		Messages []struct {
			Content json.RawMessage `json:"content"`
		} `json:"messages"`
	}
	if err := json.Unmarshal([]byte(sent), &req); err != nil {
		t.Fatalf("the provider got a body that is not JSON: %v: %s", err, sent)
	}
	for _, m := range req.Messages {
		var blocks []struct {
			Type  string          `json:"type"`
			Input json.RawMessage `json:"input"`
		}
		if json.Unmarshal(m.Content, &blocks) != nil {
			continue
		}
		for _, b := range blocks {
			if b.Type == "tool_use" {
				return string(b.Input)
			}
		}
	}
	t.Fatalf("the provider got no tool_use: %s", sent)
	return ""
}

func geminiArgs(t *testing.T, sent string) string {
	t.Helper()
	var req struct {
		Contents []struct {
			Parts []struct {
				FunctionCall *struct {
					Args json.RawMessage `json:"args"`
				} `json:"functionCall"`
			} `json:"parts"`
		} `json:"contents"`
	}
	if err := json.Unmarshal([]byte(sent), &req); err != nil {
		t.Fatalf("the provider got a body that is not JSON: %v: %s", err, sent)
	}
	for _, c := range req.Contents {
		for _, p := range c.Parts {
			if p.FunctionCall != nil {
				return string(p.FunctionCall.Args)
			}
		}
	}
	t.Fatalf("the provider got no functionCall: %s", sent)
	return ""
}

func responsesArguments(t *testing.T, sent string) string {
	t.Helper()
	var req struct {
		Input []struct {
			Type      string `json:"type"`
			Arguments string `json:"arguments"`
		} `json:"input"`
	}
	if err := json.Unmarshal([]byte(sent), &req); err != nil {
		t.Fatalf("the provider got a body that is not JSON: %v: %s", err, sent)
	}
	for _, it := range req.Input {
		if it.Type == "function_call" {
			return it.Arguments
		}
	}
	t.Fatalf("the provider got no function_call: %s", sent)
	return ""
}

func TestRequestCheck_ToolCallArgumentsAreRead(t *testing.T) {
	for _, shape := range requestCheckShapes {
		for _, sf := range toolCallSurfaces {
			t.Run(sf.name+" / "+shape.name, func(t *testing.T) {
				g, calls := newRequestCheckGateway(t, shape.setup)
				code, body := g.do(t, sf.path, sf.body)
				assertRequestRefused(t, g, calls, sf.format, code, body, requestCheck)
			})
		}
	}
}

func TestRequestCheck_ToolCallArgumentsAreRewritten(t *testing.T) {
	for _, rem := range remediations {
		for _, shape := range remediationShapes(rem.mode) {
			for _, sf := range toolCallSurfaces {
				t.Run(rem.mode+" / "+sf.name+" / "+shape.name, func(t *testing.T) {
					g, calls := newRequestCheckGateway(t, shape.setup)
					code, resp := g.do(t, sf.path, sf.body)
					sent := assertRewriteSent(t, calls, code, resp, rem.token, 1)
					args := sf.arguments(t, sent)
					var v struct {
						SSN  string `json:"ssn"`
						Note string `json:"note"`
					}
					if err := json.Unmarshal([]byte(args), &v); err != nil {
						t.Fatalf("the tool call's arguments the provider got are not JSON: %v: %s", err, args)
					}
					if v.SSN != rem.token || v.Note != "file it" {
						t.Errorf("arguments %s, want ssn %q and note as sent", args, rem.token)
					}
				})
			}
		}
	}
}

// The request log records the request the checks rewrote: its entry keeps
// the rewritten arguments, not the number.
func TestRequestCheck_ToolCallArgumentsAreRewrittenInTheLog(t *testing.T) {
	for _, shape := range remediationShapes("mask") {
		t.Run(shape.name, func(t *testing.T) {
			setup := shape.setup
			setup.capture = true
			g, calls := newRequestCheckGateway(t, setup)
			code, resp := g.do(t, "/v1/chat/completions", chatToolCall())
			assertRewriteSent(t, calls, code, resp, "***", 1)
			e := g.entry(t)
			logged, _ := json.Marshal(e["request_body"])
			if strings.Contains(string(logged), requestCheckSSN) || !strings.Contains(string(logged), `\"ssn\":\"***\"`) {
				t.Errorf("the log's request body %s, want the tool call's arguments masked", logged)
			}
			if e["status_code"] != float64(http.StatusOK) {
				t.Errorf("log status_code %v, want 200", e["status_code"])
			}
		})
	}
}
