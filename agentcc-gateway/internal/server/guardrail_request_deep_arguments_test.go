package server

import (
	"encoding/json"
	"fmt"
	"net/http"
	"strings"
	"testing"
	"time"
)

// A request whose tool calls' arguments are nested deeper than the checks on
// the request read them (64 levels) is input the gateway cannot read: under
// a check that would block it or rewrite it, it is refused before the
// provider is called, 403 request_unavailable, in the route's format, with
// fail_open on (the gateway's default) or off, within the check's timeout;
// under a check that only logs it goes on, with request_unavailable in the
// log.

// deepToolArguments is arguments depth objects deep with the probe's social
// security number at the innermost level.
func deepToolArguments(depth int) string {
	return strings.Repeat(`{"a":`, depth) + `"` + requestCheckSSN + `"` + strings.Repeat("}", depth)
}

func deepChatToolCalls(depth int) string {
	args, _ := json.Marshal(deepToolArguments(depth))
	call := `{"id":"call_1","type":"function","function":{"name":"lookup","arguments":` + string(args) + `}}`
	return `{"model":"gpt-4o","messages":[{"role":"user","content":"Look my record up."},` +
		`{"role":"assistant","content":null,"tool_calls":[` + call + `,` + call + `,` + call + `]}]}`
}

func deepMessagesToolUses(depth int) string {
	block := `{"type":"tool_use","id":"tu_1","name":"lookup","input":` + deepToolArguments(depth) + `}`
	return fmt.Sprintf(`{"model":%q,"max_tokens":64,"messages":[{"role":"user","content":"Look my record up."},`+
		`{"role":"assistant","content":[%s,%s,%s]}]}`, postBlockClaude, block, block, block)
}

func deepResponsesCalls(depth int) string {
	args, _ := json.Marshal(deepToolArguments(depth))
	item := `{"type":"function_call","call_id":"c1","name":"lookup","arguments":` + string(args) + `}`
	return `{"model":"gpt-4o","input":[{"role":"user","content":"Look my record up."},` + item + `,` + item + `,` + item + `]}`
}

var deepArgumentSurfaces = []struct {
	name, path string
	body       func(depth int) string
	format     wireFormat
}{
	{"chat", "/v1/chat/completions", deepChatToolCalls, openaiWire},
	{"messages, pass-through", "/v1/messages", deepMessagesToolUses, anthropicWire},
	{"responses", "/v1/responses", deepResponsesCalls, openaiWire},
}

func TestRequestCheck_DeeplyNestedToolCallArgumentsAreRefused(t *testing.T) {
	for _, sf := range deepArgumentSurfaces {
		for _, shape := range requestCheckShapes {
			for _, failOpen := range []bool{true, false} {
				if !failOpen && sf.name != "chat" {
					continue
				}
				name := sf.name + " / " + shape.name + map[bool]string{true: " / fail_open", false: " / fail closed"}[failOpen]
				t.Run("block / "+name, func(t *testing.T) {
					setup := shape.setup
					setup.failOpen = failOpen
					g, calls := newRequestCheckGateway(t, setup)
					start := time.Now()
					code, resp := g.do(t, sf.path, sf.body(9000))
					if elapsed := time.Since(start); elapsed >= 2*time.Second {
						t.Errorf("answered after %v, want within the check's timeout (2s)", elapsed)
					}
					assertUnreadableRefused(t, g, calls, sf.format, code, resp)
				})
			}
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

// The controls: under a check that only logs the request goes on, with
// request_unavailable in the log; arguments nested as deep as the checks
// read are read, and a check in block mode refuses their text.
func TestRequestCheck_DeeplyNestedToolCallArgumentsGoOnUnderACheckThatOnlyLogs(t *testing.T) {
	for _, sf := range deepArgumentSurfaces {
		t.Run("log / "+sf.name, func(t *testing.T) {
			g, calls := newRequestCheckGateway(t, postBlockSetup{orgRequestCheck: requestCheck, requestAction: "log", failOpen: true})
			code, resp := g.do(t, sf.path, sf.body(9000))
			if code != http.StatusOK || calls.count() != 1 {
				t.Fatalf("status %d, provider calls %d; want 200 and 1; body %s", code, calls.count(), resp)
			}
			if meta, _ := g.entry(t)["metadata"].(map[string]any); meta["request_unavailable"] == nil {
				t.Errorf("log metadata %v, want request_unavailable", meta)
			}
		})
		for _, shape := range requestCheckShapes {
			t.Run("at the limit / "+sf.name+" / "+shape.name, func(t *testing.T) {
				setup := shape.setup
				setup.failOpen = true
				g, calls := newRequestCheckGateway(t, setup)
				code, resp := g.do(t, sf.path, sf.body(64))
				assertRequestRefused(t, g, calls, sf.format, code, resp, requestCheck)
			})
		}
	}
}
