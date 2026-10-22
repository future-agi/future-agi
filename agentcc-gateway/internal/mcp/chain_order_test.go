package mcp

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"
)

// events records, in order, what a test hook or finalizer marks and every
// hook the observer sees.
type events struct {
	mu   sync.Mutex
	list []string
}

func (e *events) add(s string) {
	e.mu.Lock()
	e.list = append(e.list, s)
	e.mu.Unlock()
}

func (e *events) all() []string {
	e.mu.Lock()
	defer e.mu.Unlock()
	return append([]string(nil), e.list...)
}

// hooksOnly returns the observed hook names, in order.
func hooksOnly(list []string) []string {
	var out []string
	for _, s := range list {
		if name, ok := strings.CutPrefix(s, "hook:"); ok {
			out = append(out, name)
		}
	}
	return out
}

// chainServer is an MCP server with one upstream connected through the
// built-in client ("mock": tool echo, exposed as mock_echo; resource
// file:///a; prompt greet), a session, and a hook observer. It uses only the
// client's public API.
type chainServer struct {
	*Server
	ev      *events
	session string
	up      *mockUpstream
}

func newChainServer(t *testing.T) *chainServer {
	t.Helper()
	up := startMockUpstream(t)
	s := NewServer(ServerConfig{MaxAgentDepth: 5, ToolCallTimeout: 5 * time.Second, SessionTTL: time.Minute, Separator: "_"})
	t.Cleanup(s.Close)
	client := NewClient(ClientConfig{ServerID: "mock", URL: up.URL, TransportType: "http"})
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	if err := client.Connect(ctx); err != nil {
		t.Fatalf("connect to the mock upstream: %v", err)
	}
	s.RegisterClient(client)

	ev := &events{}
	s.hookObserver = func(hook string, _ *CallContext) { ev.add("hook:" + hook) }
	return &chainServer{Server: s, ev: ev, session: initializeSession(t, s), up: up}
}

// call posts one JSON-RPC request on the server's session.
func (c *chainServer) call(t *testing.T, method string, params any) *httptest.ResponseRecorder {
	t.Helper()
	raw, err := json.Marshal(params)
	if err != nil {
		t.Fatal(err)
	}
	return postMCP(t, c.Server, &Message{JSONRPC: "2.0", ID: json.RawMessage(`7`), Method: method, Params: raw},
		map[string]string{"MCP-Session-Id": c.session})
}

func (c *chainServer) callTool(t *testing.T) *httptest.ResponseRecorder {
	t.Helper()
	return c.call(t, MethodToolsCall, ToolCallParams{Name: "mock_echo", Arguments: map[string]interface{}{"input": "hi"}})
}

// replaceStep swaps the run of the named step in chain for the test's
// duration.
func replaceStep(t *testing.T, chain []chainStep, name string, run hookFunc) {
	t.Helper()
	for i := range chain {
		if chain[i].name == name {
			old := chain[i].run
			chain[i].run = run
			t.Cleanup(func() { chain[i].run = old })
			return
		}
	}
	t.Fatalf("no step %q in the chain", name)
}

// wantToolsCallOrder is the tools/call order, step 0 included.
var wantToolsCallOrder = []string{
	"startSpan", "resolvePrincipal", "parseAgent", "registerRecord", "resolveTenant",
	"resolveVirtualServer", "resolveMetaTool", "authorizeTool", "translateRetry", "scopeMemory",
	"validateArgs", "checkPin", "checkApproval", "checkDepth", "checkRateLimit",
	"resolveEndUser", "resolveCredential", "reserveCredits", "reserveBudget", "scanArgs",
	"callUpstream", "scanResult", "applyTerms", "settleCredits", "settleBudget", "evidenceMeta",
}

func TestMCPHookOrderToolsCall(t *testing.T) {
	c := newChainServer(t)

	// Each step reports its own name as it starts: wrap every step to mark
	// its entry, and the observer's report must follow the mark directly.
	for i := range toolsCallChain {
		st := toolsCallChain[i]
		replaceStep(t, toolsCallChain, st.name, func(s *Server, cc *CallContext) *Denial {
			c.ev.add("enter:" + st.name)
			return st.run(s, cc)
		})
	}

	w := c.callTool(t)
	resp := decodeResponse(t, w)
	if resp.Error != nil || !strings.Contains(string(resp.Result), "echoed") {
		t.Fatalf("tools/call answered %s %v, want the upstream's result", resp.Result, resp.Error)
	}
	got := c.ev.all()
	if hooks := hooksOnly(got); strings.Join(hooks, ",") != strings.Join(wantToolsCallOrder, ",") {
		t.Fatalf("hooks ran\n %v\nwant\n %v", hooks, wantToolsCallOrder)
	}
	for i := 0; i+1 < len(got); i += 2 {
		if strings.TrimPrefix(got[i], "enter:") != strings.TrimPrefix(got[i+1], "hook:") {
			t.Fatalf("step %s did not report itself first (next event %s)", got[i], got[i+1])
		}
	}

	// Steps 7a, 12a and 18a sit between their neighbours.
	pos := map[string]int{}
	for i, h := range wantToolsCallOrder {
		pos[h] = i
	}
	for _, p := range [][3]string{
		{"translateRetry", "scopeMemory", "validateArgs"},
		{"checkRateLimit", "resolveEndUser", "resolveCredential"},
		{"scanResult", "applyTerms", "settleCredits"},
	} {
		if !(pos[p[0]]+1 == pos[p[1]] && pos[p[1]]+1 == pos[p[2]]) {
			t.Errorf("%s is not between %s and %s", p[1], p[0], p[2])
		}
	}
}

func TestMCPHookOrderToolsList(t *testing.T) {
	c := newChainServer(t)
	head := "startSpan,resolvePrincipal,parseAgent,resolveTenant,resolveVirtualServer"
	for _, tc := range []struct {
		method string
		params any
		want   string
		answer string
	}{
		{MethodToolsList, struct{}{}, head + ",assembleList,pinFilter,scanDescriptions,toolset,priceMeta", `"mock_echo"`},
		{MethodResourcesList, struct{}{}, head + ",filterResources", `"file:///a"`},
		{MethodResourcesRead, ReadResourceParams{URI: "file:///a"}, head + ",authorizeResource,readResource,scanResource", `"abc"`},
		{MethodPromptsList, struct{}{}, head + ",filterPrompts", `"mock_greet"`},
		{MethodPromptsGet, GetPromptParams{Name: "mock_greet"}, head + ",authorizePrompt,getPrompt,scanPrompt", `"hi"`},
	} {
		c.ev.list = nil
		resp := decodeResponse(t, c.call(t, tc.method, tc.params))
		if resp.Error != nil || !strings.Contains(string(resp.Result), tc.answer) {
			t.Fatalf("%s answered %s %v, want %s", tc.method, resp.Result, resp.Error, tc.answer)
		}
		if got := strings.Join(hooksOnly(c.ev.all()), ","); got != tc.want {
			t.Errorf("%s hooks ran %s, want %s", tc.method, got, tc.want)
		}
	}

	// A denial on a list is a JSON-RPC error carrying the error object, and
	// the steps after it do not run.
	c.ev.list = nil
	replaceStep(t, toolsListChain, hookResolveTenant, func(_ *Server, cc *CallContext) *Denial {
		observeHook(cc, hookResolveTenant)
		return &Denial{Type: "server_error", Code: "policy_unavailable", Message: "org policy could not be loaded"}
	})
	resp := decodeResponse(t, c.call(t, MethodToolsList, struct{}{}))
	if resp.Error == nil || resp.Error.Code != ErrCodeInvalidRequest || resp.Error.Message != "org policy could not be loaded [policy_unavailable]" ||
		!strings.Contains(string(resp.Error.Data), `"code":"policy_unavailable"`) {
		t.Fatalf("denied tools/list answered %+v, want -32600 with the error object", resp.Error)
	}
	if got := strings.Join(hooksOnly(c.ev.all()), ","); got != "startSpan,resolvePrincipal,parseAgent,resolveTenant" {
		t.Fatalf("after the denial hooks ran %s", got)
	}
}

// finalizerHook registers finalizers named after the step, then returns
// what then returns.
func finalizerHook(ev *events, name string, then func(cc *CallContext) *Denial, finals ...string) hookFunc {
	return func(_ *Server, cc *CallContext) *Denial {
		observeHook(cc, name)
		for _, f := range finals {
			cc.Defer(f, func() { ev.add("final:" + f) })
		}
		return then(cc)
	}
}

func finalsOnly(list []string) string {
	var out []string
	for _, s := range list {
		if f, ok := strings.CutPrefix(s, "final:"); ok {
			out = append(out, f)
		}
	}
	return strings.Join(out, ",")
}

func TestMCPFinalizersOnceOnPanicAndDenial(t *testing.T) {
	ok := func(*CallContext) *Denial { return nil }

	t.Run("success", func(t *testing.T) {
		c := newChainServer(t)
		replaceStep(t, toolsCallChain, hookRegisterRecord, finalizerHook(c.ev, hookRegisterRecord, ok, "record"))
		replaceStep(t, toolsCallChain, hookReserveCredits, finalizerHook(c.ev, hookReserveCredits, ok, "void-credits"))
		c.callTool(t)
		if got := finalsOnly(c.ev.all()); got != "void-credits,record" {
			t.Fatalf("finalizers ran %q, want void-credits,record once each, last in first out", got)
		}
	})

	t.Run("denial", func(t *testing.T) {
		c := newChainServer(t)
		replaceStep(t, toolsCallChain, hookRegisterRecord, finalizerHook(c.ev, hookRegisterRecord, ok, "record"))
		replaceStep(t, toolsCallChain, hookCheckDepth, finalizerHook(c.ev, hookCheckDepth, func(*CallContext) *Denial {
			return &Denial{Type: "invalid_request_error", Code: "tool_call_depth_exceeded", Message: "too deep", Metadata: map[string]any{"max_depth": 5}}
		}, "a", "b"))
		w := c.callTool(t)

		events := c.ev.all()
		if got := finalsOnly(events); got != "b,a,record" {
			t.Fatalf("finalizers ran %q, want b,a,record once each", got)
		}
		hooks := strings.Join(hooksOnly(events), ",")
		if !strings.HasSuffix(hooks, "checkDepth,evidenceMeta") || strings.Contains(hooks, "callUpstream") {
			t.Fatalf("hooks after the denial: %s; want only evidenceMeta", hooks)
		}
		if n := len(c.up.received()); n != 0 {
			t.Fatalf("a denied call reached the upstream (%d calls)", n)
		}
		// In band: HTTP 200, an isError result with the error object.
		resp := decodeResponse(t, w)
		var res struct {
			Content []ContentPart              `json:"content"`
			IsError bool                       `json:"isError"`
			Meta    map[string]json.RawMessage `json:"_meta"`
		}
		if w.Code != 200 || resp.Error != nil || json.Unmarshal(resp.Result, &res) != nil || !res.IsError ||
			len(res.Content) != 1 || res.Content[0].Text != "too deep [tool_call_depth_exceeded]" {
			t.Fatalf("denial answered %d %s %v, want an in-band isError result", w.Code, resp.Result, resp.Error)
		}
		if got, want := string(res.Meta[MetaKeyError]), `{"type":"invalid_request_error","code":"tool_call_depth_exceeded","message":"too deep","param":null,"metadata":{"max_depth":5}}`; got != want {
			t.Fatalf("error object = %s, want %s", got, want)
		}
	})

	t.Run("panic", func(t *testing.T) {
		c := newChainServer(t)
		replaceStep(t, toolsCallChain, hookRegisterRecord, finalizerHook(c.ev, hookRegisterRecord, ok, "record"))
		replaceStep(t, toolsCallChain, hookScanArgs, finalizerHook(c.ev, hookScanArgs, func(*CallContext) *Denial {
			panic("scanner exploded")
		}, "void-hold"))
		func() {
			defer func() {
				if r := recover(); r != "scanner exploded" {
					t.Fatalf("recovered %v, want the hook's panic to propagate", r)
				}
			}()
			c.callTool(t)
		}()
		if got := finalsOnly(c.ev.all()); got != "void-hold,record" {
			t.Fatalf("finalizers ran %q on a panic, want void-hold,record once each", got)
		}
		if n := len(c.up.received()); n != 0 {
			t.Fatalf("the upstream was called after the panic (%d calls)", n)
		}
	})

	t.Run("a panicking finalizer does not stop the others", func(t *testing.T) {
		c := newChainServer(t)
		replaceStep(t, toolsCallChain, hookRegisterRecord, finalizerHook(c.ev, hookRegisterRecord, ok, "record"))
		replaceStep(t, toolsCallChain, hookReserveBudget, func(_ *Server, cc *CallContext) *Denial {
			observeHook(cc, hookReserveBudget)
			cc.Defer("boom", func() { panic("finalizer exploded") })
			return nil
		})
		c.callTool(t)
		if got := finalsOnly(c.ev.all()); got != "record" {
			t.Fatalf("finalizers ran %q, want record to run after the panicking one", got)
		}
	})
}

func TestMCPRecordRegisteredBeforeTenant(t *testing.T) {
	order := strings.Join(wantToolsCallOrder, ",")
	if !strings.Contains(order, "parseAgent,registerRecord,resolveTenant") {
		t.Fatal("registerRecord is not step 2a, between parseAgent and resolveTenant")
	}
	c := newChainServer(t)
	var seen []string
	replaceStep(t, toolsCallChain, hookRegisterRecord, func(_ *Server, cc *CallContext) *Denial {
		observeHook(cc, hookRegisterRecord)
		cc.Defer(FinalizerRecord, func() {
			code := "none"
			if cc.Decision != nil {
				code = cc.Decision.Code
			}
			seen = append(seen, code)
		})
		return nil
	})
	replaceStep(t, toolsCallChain, hookResolveTenant, func(_ *Server, cc *CallContext) *Denial {
		observeHook(cc, hookResolveTenant)
		return &Denial{Type: "server_error", Code: "policy_unavailable", Message: "org policy could not be loaded"}
	})
	c.callTool(t)
	hooks := hooksOnly(c.ev.all())
	if strings.Join(hooks, ",") != "startSpan,resolvePrincipal,parseAgent,registerRecord,resolveTenant,evidenceMeta" {
		t.Fatalf("hooks ran %v", hooks)
	}
	if len(seen) != 1 || seen[0] != "policy_unavailable" {
		t.Fatalf("record finalizer saw %v, want one record of the policy_unavailable denial", seen)
	}
}

func TestMCPFinalizersDetachOnPark(t *testing.T) {
	ok := func(*CallContext) *Denial { return nil }

	// parkingServer parks every call at the upstream step, after registering a
	// credit hold, and hands the handle to the test.
	parkingServer := func(t *testing.T) (*chainServer, chan *Parked) {
		c := newChainServer(t)
		handles := make(chan *Parked, 4)
		replaceStep(t, toolsCallChain, hookRegisterRecord, finalizerHook(c.ev, hookRegisterRecord, ok, "record"))
		replaceStep(t, toolsCallChain, hookReserveCredits, finalizerHook(c.ev, hookReserveCredits, ok, "credit-hold"))
		replaceStep(t, toolsCallChain, hookCallUpstream, func(_ *Server, cc *CallContext) *Denial {
			observeHook(cc, hookCallUpstream)
			cc.ToolCallID = "tc_parked"
			cc.Result = json.RawMessage(`{"resultType":"input_required","content":[]}`)
			handles <- cc.Park()
			return nil
		})
		return c, handles
	}

	t.Run("completion", func(t *testing.T) {
		c, handles := parkingServer(t)
		resp := decodeResponse(t, c.callTool(t))
		if resp.Error != nil || !strings.Contains(string(resp.Result), "input_required") {
			t.Fatalf("the first call answered %s %v", resp.Result, resp.Error)
		}
		p := <-handles
		if got := finalsOnly(c.ev.all()); got != "" {
			t.Fatalf("finalizers ran at the end of the first call: %s", got)
		}
		if p.ToolCallID != "tc_parked" {
			t.Fatalf("parked tool_call_id = %q", p.ToolCallID)
		}
		p.Complete()
		p.Complete()
		p.Expire()
		if got := finalsOnly(c.ev.all()); got != "credit-hold,record" {
			t.Fatalf("finalizers ran %q, want credit-hold,record exactly once at completion", got)
		}
	})

	t.Run("expiry", func(t *testing.T) {
		c, handles := parkingServer(t)
		c.callTool(t)
		p := <-handles
		if got := finalsOnly(c.ev.all()); got != "" {
			t.Fatalf("finalizers ran at the end of the first call: %s", got)
		}
		p.Expire()
		p.Expire()
		p.Complete()
		if got := finalsOnly(c.ev.all()); got != "credit-hold,record" {
			t.Fatalf("finalizers ran %q, want credit-hold,record exactly once at expiry", got)
		}
	})

	t.Run("a resumed call adopts the parked call", func(t *testing.T) {
		c, handles := parkingServer(t)
		c.callTool(t)
		p := <-handles

		// The second call resumes it: only the retry steps run, translateRetry adopts
		// the parked call and drops the retry's own record finalizer, and the
		// parked finalizers run once when the resumed call completes.
		c.ev.list = nil
		replaceStep(t, toolsCallChain, hookTranslateRetry, func(_ *Server, cc *CallContext) *Denial {
			observeHook(cc, hookTranslateRetry)
			if !cc.Adopt(p) {
				return &Denial{Code: "request_state_invalid", Message: "expired", RPCCode: ErrCodeInvalidParams}
			}
			cc.Result = json.RawMessage(`{"content":[{"type":"text","text":"resumed"}]}`)
			return nil
		})
		cc := c.newCallContext(httptest.NewRequest("POST", "/mcp", nil), &Message{Method: MethodToolsCall}, nil)
		cc.retry = true
		c.runChain(cc, toolsCallChain)
		if cc.Decision != nil || cc.ToolCallID != "tc_parked" {
			t.Fatalf("resume: decision %v, tool_call_id %q", cc.Decision, cc.ToolCallID)
		}
		hooks := strings.Join(hooksOnly(c.ev.all()), ",")
		if hooks != "startSpan,resolvePrincipal,parseAgent,registerRecord,resolveTenant,resolveVirtualServer,translateRetry,scanResult,applyTerms,settleCredits,settleBudget,evidenceMeta" {
			t.Fatalf("a resumed call ran %s; want 0-4 with 2a, translateRetry, then 18 onwards", hooks)
		}
		cc.runFinalizers()
		if got := finalsOnly(c.ev.all()); got != "credit-hold,record" {
			t.Fatalf("finalizers ran %q, want the parked call's credit-hold and record once, and no second record", got)
		}
		p.Expire()
		if got := finalsOnly(c.ev.all()); got != "credit-hold,record" {
			t.Fatalf("expiry after adoption ran finalizers again: %q", got)
		}

		// A second resume of the same handle is refused and keeps its own
		// record finalizer, so it is recorded as a denied attempt.
		c.ev.list = nil
		cc2 := c.newCallContext(httptest.NewRequest("POST", "/mcp", nil), &Message{Method: MethodToolsCall}, nil)
		cc2.retry = true
		c.runChain(cc2, toolsCallChain)
		cc2.runFinalizers()
		if cc2.Decision == nil || cc2.Decision.Code != "request_state_invalid" || finalsOnly(c.ev.all()) != "record" {
			t.Fatalf("a replayed handle: decision %v, finalizers %q", cc2.Decision, finalsOnly(c.ev.all()))
		}
	})
}

// TestMCPDefaultChainAnswers pins the protocol-level answers the default
// hooks give.
func TestMCPDefaultChainAnswers(t *testing.T) {
	c := newChainServer(t)
	for _, tc := range []struct {
		name   string
		params ToolCallParams
		header map[string]string
		code   int
		msg    string
	}{
		{"unknown tool", ToolCallParams{Name: "nope"}, nil, ErrCodeMethodNotFound, "tool not found: nope"},
		{"schema", ToolCallParams{Name: "mock_echo"}, nil, ErrCodeInvalidParams, `missing required argument: "input"`},
		{"depth", ToolCallParams{Name: "mock_echo", Arguments: map[string]interface{}{"input": "x"}}, map[string]string{HeaderAgentDepth: "9"}, ErrCodeInvalidRequest, "maximum agent depth (5) exceeded"},
	} {
		raw, _ := json.Marshal(tc.params)
		headers := map[string]string{"MCP-Session-Id": c.session}
		for k, v := range tc.header {
			headers[k] = v
		}
		w := postMCP(t, c.Server, &Message{JSONRPC: "2.0", ID: json.RawMessage(`1`), Method: MethodToolsCall, Params: raw}, headers)
		resp := decodeResponse(t, w)
		if resp.Error == nil || resp.Error.Code != tc.code || !strings.Contains(resp.Error.Message, tc.msg) || resp.Error.Data != nil {
			t.Errorf("%s: answered %+v, want %d %q and no data", tc.name, resp.Error, tc.code, tc.msg)
		}
	}
	c.UnregisterClient("mock")
	c.registry.RegisterServer("mock", []Tool{{Name: "echo"}})
	resp := decodeResponse(t, c.callTool(t))
	if resp.Error == nil || resp.Error.Code != ErrCodeInternal || resp.Error.Message != fmt.Sprintf("upstream server %q is unavailable", "mock") {
		t.Fatalf("unavailable upstream answered %+v", resp.Error)
	}
}
