package server

import (
	"fmt"
	"net/http"
	"strings"
	"testing"
	"time"
)

// A body the gateway passes through to a provider's own API, or proxies as
// sent, is read up to 64 levels deep where a reader walks a value on its own
// (an item, part or block of a type it does not know, a Gemini function
// response, a block in a block). A body that holds a value nested deeper is
// input the gateway cannot read in full: under a check that would block or
// rewrite it, it is refused before the provider is called, 403
// request_unavailable naming the check, in the route's format, with
// fail_open on (the gateway's default) or off, within the check's timeout;
// under a check that only logs it goes on, with request_unavailable in the
// log.

// deepValue is a value nested depth objects deep with the probe's social
// security number at the innermost level.
func deepValue(depth int) string {
	return strings.Repeat(`{"a":`, depth) + `"` + requestCheckSSN + `"` + strings.Repeat("}", depth)
}

// deepBodySurface is a route with a body that holds, after a user's text,
// v where a reader walks it on its own.
type deepBodySurface struct {
	name, path string
	body       func(v string) string
	format     wireFormat
	logged     bool
}

func deepResponsesItems(n int) func(string) string {
	return func(v string) string {
		items := make([]string, n)
		for i := range items {
			items[i] = `{"type":"zz_unknown","x":` + v + `}`
		}
		return `{"model":"gpt-4o","input":[{"role":"user","content":"Look my record up."},` + strings.Join(items, ",") + `]}`
	}
}

var deepBodySurfaces = []deepBodySurface{
	{"responses, an item of a type the reader does not know", "/v1/responses", deepResponsesItems(1), openaiWire, true},
	{"responses, four such items", "/v1/responses", deepResponsesItems(4), openaiWire, true},
	{"threads, a content part of a type the reader does not know", "/v1/threads", func(v string) string {
		return `{"messages":[{"role":"user","content":"Look my record up."},{"role":"user","content":[{"type":"zz_unknown","x":` + v + `}]}]}`
	}, openaiWire, false},
	{"generateContent, passed through, a function response", "/v1beta/models/" + postBlockGemini + ":generateContent", func(v string) string {
		return `{"contents":[{"role":"user","parts":[{"text":"Look my record up."}]},{"role":"user","parts":[{"functionResponse":{"name":"lookup","response":` + v + `}}]}]}`
	}, genaiWire, true},
	{"messages, passed through, a block of a type the reader does not know", "/v1/messages", func(v string) string {
		return fmt.Sprintf(`{"model":%q,"max_tokens":64,"messages":[{"role":"user","content":[{"type":"text","text":"Look my record up."},{"type":"zz_unknown","x":%s}]}]}`, postBlockClaude, v)
	}, anthropicWire, true},
}

// deepToolResults is a /v1/messages body passed through whose tool result
// holds a tool result, n deep, the innermost holding text.
func deepToolResults(n int, text string) string {
	return fmt.Sprintf(`{"model":%q,"max_tokens":64,"messages":[{"role":"user","content":[`, postBlockClaude) +
		strings.Repeat(`{"type":"tool_result","tool_use_id":"t","content":[`, n) +
		fmt.Sprintf(`{"type":"text","text":%q}`, text) + strings.Repeat(`]}`, n) + `]}]}`
}

// assertDeepBodyRefused: 403 request_unavailable naming the check, in the
// route's format, before any provider call, and, on a logged route, the
// log's record of the block and of why.
func assertDeepBodyRefused(t *testing.T, g *otherAPIsGateway, sf deepBodySurface, code int, resp string, elapsed time.Duration) {
	t.Helper()
	if elapsed >= 2*time.Second {
		t.Errorf("answered after %v, want within the check's timeout (2s)", elapsed)
	}
	if code != http.StatusForbidden || g.providerCalls() != 0 {
		t.Fatalf("status %d, provider calls %d; want 403 and 0; body %.300s", code, g.providerCalls(), resp)
	}
	assertUnavailableError(t, sf.format, resp)
	if !strings.Contains(resp, "nested more than 64 levels deep") {
		t.Errorf("error %.300s, want it to say the body is nested more than 64 levels deep", resp)
	}
	if !sf.logged {
		return
	}
	entry := g.entry(t)
	assertBlockLogged(t, entry, requestCheck)
	if meta, _ := entry["metadata"].(map[string]any); meta["request_unavailable"] == nil {
		t.Errorf("log metadata %v, want request_unavailable", meta)
	}
}

func TestRequestCheck_DeeplyNestedBodyIsRefused(t *testing.T) {
	surfaces := append(append([]deepBodySurface{}, deepBodySurfaces...), deepBodySurface{
		"messages, passed through, tool results in tool results", "/v1/messages",
		func(string) string { return deepToolResults(4400, requestCheckPrompt) }, anthropicWire, true})
	for _, sf := range surfaces {
		for _, shape := range requestCheckShapes {
			for _, failOpen := range []bool{true, false} {
				if !failOpen && sf.name != deepBodySurfaces[0].name {
					continue
				}
				name := sf.name + " / " + shape.name + map[bool]string{true: " / fail_open", false: " / fail closed"}[failOpen]
				t.Run("block / "+name, func(t *testing.T) {
					setup := shape.setup
					setup.failOpen = failOpen
					g := newOtherAPIsGateway(t, setup)
					start := time.Now()
					code, resp := g.doAs(t, postBlockKey, sf.path, sf.body(deepValue(9000)), nil)
					assertDeepBodyRefused(t, g, sf, code, resp, time.Since(start))
				})
			}
		}
		for _, shape := range remediationShapes("mask") {
			t.Run("mask / "+sf.name+" / "+shape.name, func(t *testing.T) {
				setup := shape.setup
				setup.failOpen = true
				g := newOtherAPIsGateway(t, setup)
				start := time.Now()
				code, resp := g.doAs(t, postBlockKey, sf.path, sf.body(deepValue(9000)), nil)
				assertDeepBodyRefused(t, g, sf, code, resp, time.Since(start))
			})
		}
	}
}

// The controls: under a check that only logs, the request goes on, with
// request_unavailable in the log; a value nested as deep as the checks read
// is read, and a check in block mode refuses its text.
func TestRequestCheck_DeeplyNestedBodyGoesOnUnderACheckThatOnlyLogs(t *testing.T) {
	for _, sf := range deepBodySurfaces {
		t.Run("log / "+sf.name, func(t *testing.T) {
			g := newOtherAPIsGateway(t, postBlockSetup{orgRequestCheck: requestCheck, requestAction: "log", failOpen: true})
			code, resp := g.doAs(t, postBlockKey, sf.path, sf.body(deepValue(9000)), nil)
			if code == http.StatusForbidden || g.providerCalls() != 1 {
				t.Fatalf("status %d, provider calls %d; want the request sent once; body %.300s", code, g.providerCalls(), resp)
			}
			if !sf.logged {
				return
			}
			if meta, _ := g.entry(t)["metadata"].(map[string]any); meta["request_unavailable"] == nil {
				t.Errorf("log metadata %v, want request_unavailable", meta)
			}
		})
	}
	// At the limit: the innermost object holding the number is inside 63
	// others (the walked value starts inside 3 on Responses), and is read.
	atLimit := deepBodySurface{"responses, at the limit", "/v1/responses", func(string) string {
		return deepResponsesItems(1)(strings.Repeat(`{"a":`, 60) + `{"text":"` + requestCheckPrompt + `"}` + strings.Repeat("}", 60))
	}, openaiWire, true}
	for _, shape := range requestCheckShapes {
		t.Run("at the limit / "+shape.name, func(t *testing.T) {
			setup := shape.setup
			setup.failOpen = true
			g, calls := newRequestCheckGateway(t, setup)
			code, resp := g.do(t, atLimit.path, atLimit.body(""))
			assertRequestRefused(t, g, calls, atLimit.format, code, resp, requestCheck)
		})
	}
	// Blocks in blocks at the limit: the text block is inside 62.
	for _, shape := range requestCheckShapes {
		t.Run("tool results at the limit / "+shape.name, func(t *testing.T) {
			setup := shape.setup
			setup.failOpen = true
			g, calls := newRequestCheckGateway(t, setup)
			code, resp := g.do(t, "/v1/messages", deepToolResults(29, requestCheckPrompt))
			assertRequestRefused(t, g, calls, anthropicWire, code, resp, requestCheck)
		})
	}
}

// The depth limit applies where a reader walks a value: a body nested 100
// deep elsewhere goes to the provider once, with no request_unavailable in
// the log.
func TestRequestCheck_DepthLimitIsWhereAReaderWalks(t *testing.T) {
	deep := strings.Repeat(`{"a":`, 100) + `"x"` + strings.Repeat("}", 100)
	bodies := []struct{ name, path, body string }{
		{"responses", "/v1/responses", `{"model":"gpt-4o","metadata":` + deep + `,"input":"Hello there.","tools":[{"type":"function","name":"lookup","parameters":` + deep + `}]}`},
		{"messages, passed through", "/v1/messages", fmt.Sprintf(`{"model":%q,"max_tokens":64,"metadata":%s,"messages":[{"role":"user","content":"Hello there."}],"tools":[{"name":"lookup","input_schema":%s}]}`, postBlockClaude, deep, deep)},
		{"generateContent, passed through", "/v1beta/models/" + postBlockGemini + ":generateContent", `{"contents":[{"role":"user","parts":[{"text":"Hello there."}]}],"tools":[{"functionDeclarations":[{"name":"lookup","parameters":` + deep + `}]}]}`},
	}
	for _, b := range bodies {
		for _, shape := range requestCheckShapes {
			t.Run(b.name+" / "+shape.name, func(t *testing.T) {
				g, calls := newRequestCheckGateway(t, shape.setup)
				code, resp := g.do(t, b.path, b.body)
				if code != http.StatusOK || calls.count() != 1 {
					t.Fatalf("status %d, provider calls %d; want 200 and 1; body %.300s", code, calls.count(), resp)
				}
				if meta, _ := g.entry(t)["metadata"].(map[string]any); meta["request_unavailable"] != nil {
					t.Errorf("log request_unavailable %v, want none: nothing the checks read is too deep", meta["request_unavailable"])
				}
			})
		}
	}
}
