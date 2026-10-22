package server

import (
	"encoding/json"
	"fmt"
	"strings"
	"testing"
)

// Beyond the routes of TestRequestCheck_DeeplyNestedToolCallArgumentsAreRefused:
// /v1/messages translated for a provider of another format, and
// generateContent, translated and passed through. On each, tool call
// arguments nested 9000 levels deep are refused under a blocking or
// rewriting check before the provider is called (403 request_unavailable),
// and read at the depth limit.

func deepMessagesTranslatedToolUses(depth int) string {
	block := `{"type":"tool_use","id":"tu_1","name":"lookup","input":` + deepToolArguments(depth) + `}`
	return fmt.Sprintf(`{"model":"gpt-4o","max_tokens":64,"messages":[{"role":"user","content":"Look my record up."},`+
		`{"role":"assistant","content":[%s,%s,%s]}]}`, block, block, block)
}

func deepGenaiFunctionCalls(depth int) string {
	part := `{"functionCall":{"name":"lookup","args":` + deepToolArguments(depth) + `}}`
	return `{"contents":[{"role":"user","parts":[{"text":"Look my record up."}]},` +
		`{"role":"model","parts":[` + part + `,` + part + `,` + part + `]}]}`
}

var deepArgumentTranslatedSurfaces = []struct {
	name, path string
	body       func(depth int) string
	format     wireFormat
}{
	{"messages, translated", "/v1/messages", deepMessagesTranslatedToolUses, anthropicWire},
	{"generateContent, translated", "/v1beta/models/gpt-4o:generateContent", deepGenaiFunctionCalls, genaiWire},
	{"generateContent, pass-through", "/v1beta/models/" + postBlockGemini + ":generateContent", deepGenaiFunctionCalls, genaiWire},
}

func TestDeeplyNestedToolCallArgumentsAreRefusedOnEveryRoute(t *testing.T) {
	for _, sf := range deepArgumentTranslatedSurfaces {
		for _, shape := range requestCheckShapes {
			t.Run("block / "+sf.name+" / "+shape.name, func(t *testing.T) {
				setup := shape.setup
				setup.failOpen = true
				g, calls := newRequestCheckGateway(t, setup)
				code, resp := g.do(t, sf.path, sf.body(9000))
				assertUnreadableRefused(t, g, calls, sf.format, code, resp)
			})
			t.Run("at the limit / "+sf.name+" / "+shape.name, func(t *testing.T) {
				setup := shape.setup
				setup.failOpen = true
				g, calls := newRequestCheckGateway(t, setup)
				code, resp := g.do(t, sf.path, sf.body(64))
				assertRequestRefused(t, g, calls, sf.format, code, resp, requestCheck)
			})
		}
		for _, shape := range remediationShapes("mask")[:2] {
			t.Run("mask / "+sf.name+" / "+shape.name, func(t *testing.T) {
				setup := shape.setup
				setup.failOpen = true
				g, calls := newRequestCheckGateway(t, setup)
				code, resp := g.do(t, sf.path, sf.body(9000))
				assertUnreadableRefused(t, g, calls, sf.format, code, resp)
			})
		}
	}
}

// Free text that begins with more than 64 brackets cannot be told from JSON
// nested too deep before it is read, so it is refused the same way under a
// blocking check, and the refusal says so.
func TestArgumentsBeginningWithMoreThan64BracketsAreRefusedAsSuch(t *testing.T) {
	args, _ := json.Marshal(strings.Repeat("[", 65) + " my ssn is " + requestCheckSSN)
	body := `{"model":"gpt-4o","messages":[{"role":"user","content":"Look my record up."},` +
		`{"role":"assistant","content":null,"tool_calls":[{"id":"call_1","type":"function","function":{"name":"lookup","arguments":` +
		string(args) + `}}]}]}`
	for _, shape := range requestCheckShapes {
		t.Run(shape.name, func(t *testing.T) {
			setup := shape.setup
			setup.failOpen = true
			g, calls := newRequestCheckGateway(t, setup)
			code, resp := g.do(t, "/v1/chat/completions", body)
			assertUnreadableRefused(t, g, calls, openaiWire, code, resp)
			if !strings.Contains(resp, "nested more than 64 levels deep or begin with more than 64 [ or { brackets") {
				t.Errorf("refusal %s does not say that arguments beginning with more than 64 brackets are refused", resp)
			}
		})
	}
}
