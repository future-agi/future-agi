package mcp

import (
	"encoding/json"
	"strings"
	"sync"
	"testing"
)

// TestMetaToolAnsweredByGateway: on a search-mode virtual server the client
// gets the gateway's own answer to search_tools and describe_tool as a
// tool result, with its evidence block, and none of the steps that judge,
// bill or run a member's tool runs for them; each call is recorded once, as
// a success with no upstream. A call made through call_tool is recorded
// under the real tool with via call_tool; a direct call has no via.
func TestMetaToolAnsweredByGateway(t *testing.T) {
	f := newVSFixture(t, searchVS(`["github","mock"]`, `"access":{"default_for_org":true}`))
	registerMany(f.Server, "github", 120)
	up := connectMock(t, f.Server)
	sink := &recordSink{}
	f.SetDeps(Deps{Events: sink.add})
	var mu sync.Mutex
	var hooks []string
	f.hookObserver = func(hook string, cc *CallContext) {
		if hook == hookResolveVirtualServer {
			// The protocol version M11 reads, as the meter tests set it.
			cc.Era, cc.ProtocolVersion = ProtocolVersion, ProtocolVersion
		}
		mu.Lock()
		hooks = append(hooks, hook)
		mu.Unlock()
	}
	call := func(name string, args map[string]any) (map[string]json.RawMessage, []string) {
		t.Helper()
		mu.Lock()
		hooks = nil
		mu.Unlock()
		sink.reset()
		resp := decodeResponse(t, f.post(t, "", MethodToolsCall, map[string]any{"name": name, "arguments": args}, nil))
		if resp.Error != nil {
			t.Fatalf("%s answered %+v, want a result", name, resp.Error)
		}
		var res map[string]json.RawMessage
		if err := json.Unmarshal(resp.Result, &res); err != nil {
			t.Fatal(err)
		}
		if string(res["isError"]) == "true" {
			t.Fatalf("%s answered an error result %s", name, resp.Result)
		}
		mu.Lock()
		defer mu.Unlock()
		return res, append([]string(nil), hooks...)
	}
	memberSteps := []string{hookAuthorizeTool, hookTranslateRetry, hookValidateArgs, hookCheckPin, hookCheckApproval, hookCheckDepth,
		hookCheckRateLimit, hookResolveCredential, hookReserveCredits, hookReserveBudget, hookScanArgs, hookCallUpstream,
		hookScanResult, hookSettleCredits, hookSettleBudget}

	for _, tc := range []struct {
		tool string
		args map[string]any
		want string
	}{
		{"search_tools", map[string]any{"query": "issue"}, `"github__create_issue"`},
		{"describe_tool", map[string]any{"name": "github__list_issues"}, `"name":"github__list_issues"`},
	} {
		res, ran := call(tc.tool, tc.args)
		if !strings.Contains(string(res["structuredContent"]), tc.want) {
			t.Fatalf("%s structuredContent %s, want %s", tc.tool, res["structuredContent"], tc.want)
		}
		var meta map[string]json.RawMessage
		if err := json.Unmarshal(res["_meta"], &meta); err != nil || len(meta[MetaKeyToolCall]) == 0 {
			t.Fatalf("%s _meta %s, want the io.futureagi/tool_call block", tc.tool, res["_meta"])
		}
		joined := "," + strings.Join(ran, ",") + ","
		for _, step := range memberSteps {
			if strings.Contains(joined, ","+step+",") {
				t.Fatalf("%s ran %s (steps %v): a gateway-answered call runs no member-tool step", tc.tool, step, ran)
			}
		}
		if !strings.Contains(joined, ","+hookResolveMetaTool+",") || !strings.HasSuffix(joined, ","+hookEvidenceMeta+",") {
			t.Fatalf("%s ran %v, want resolveMetaTool and, last, evidenceMeta", tc.tool, ran)
		}
		rec := sink.only(t)
		if rec.Tool != tc.tool || rec.Outcome != OutcomeSuccess || rec.ServerID != "" || rec.Via != "" || rec.Dispatched {
			t.Fatalf("%s record tool %q outcome %q server %q via %q dispatched %v", tc.tool, rec.Tool, rec.Outcome, rec.ServerID, rec.Via, rec.Dispatched)
		}
		assertGolden(t, rec)
	}

	call("call_tool", map[string]any{"name": "mock__echo", "arguments": map[string]any{"input": "hi"}})
	if rec := sink.only(t); rec.Tool != "mock__echo" || rec.Via != ViaCallTool || rec.Outcome != OutcomeSuccess {
		t.Fatalf("call_tool record tool %q via %q outcome %q, want mock__echo via call_tool", rec.Tool, rec.Via, rec.Outcome)
	} else {
		assertGolden(t, rec)
	}
	call("mock__echo", map[string]any{"input": "hi"})
	if rec := sink.only(t); rec.Tool != "mock__echo" || rec.Via != "" {
		t.Fatalf("direct record tool %q via %q, want mock__echo with no via", rec.Tool, rec.Via)
	}
	if n := len(up.received()); n != 2 {
		t.Fatalf("the upstream got %d calls, want 2 (call_tool's and the direct one)", n)
	}
}
