package mcp

import (
	"context"
	"encoding/json"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

// The M30 meta-tool tests: call_tool runs its target through the same chain
// as a direct call, so every rule that refuses a direct call refuses it the
// same way through call_tool.

// searchVS is a search-mode virtual server over members, with extra JSON
// members (access, tools, overrides) spliced into its object.
func searchVS(members, extra string) string {
	return `{"mcp":{"virtual_servers":{"eng":{"id":"` + vsEng + `","mode":"search","members":` + members + `,` + extra + `}}}}`
}

// viaCallTool posts tools/call of call_tool with {name: target, arguments}.
func (f *vsFixture) viaCallTool(t *testing.T, vsID, target string, args map[string]any, headers map[string]string) *httptest.ResponseRecorder {
	t.Helper()
	inner := map[string]any{"name": target}
	if args != nil {
		inner["arguments"] = args
	}
	return f.post(t, vsID, MethodToolsCall, map[string]any{"name": "call_tool", "arguments": inner}, headers)
}

// direct posts tools/call of name with args.
func (f *vsFixture) direct(t *testing.T, vsID, name string, args map[string]any, headers map[string]string) *httptest.ResponseRecorder {
	t.Helper()
	params := map[string]any{"name": name}
	if args != nil {
		params["arguments"] = args
	}
	return f.post(t, vsID, MethodToolsCall, params, headers)
}

// errorObject is the in-band io.futureagi/error of an answer, or nil.
func errorObject(t *testing.T, w *httptest.ResponseRecorder) *ErrorObject {
	t.Helper()
	return denial(t, decodeResponse(t, httptestCopy(w)))
}

// connectMock connects the chain tests' mock upstream (tool echo) as
// server "mock" and returns it.
func connectMock(t *testing.T, s *Server) *mockUpstream {
	t.Helper()
	up := startMockUpstream(t)
	client := NewClient(ClientConfig{ServerID: "mock", URL: up.URL, TransportType: "http"})
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	if err := client.Connect(ctx); err != nil {
		t.Fatalf("connect to the mock upstream: %v", err)
	}
	s.RegisterClient(client)
	return up
}

// TestCallToolEntersChain: call_tool {name, arguments} runs the named tool
// as a direct call would: the upstream gets the real tool's own name and
// the inner arguments, the client gets the upstream's result, and the
// call's record names the real tool.
func TestCallToolEntersChain(t *testing.T) {
	f := newVSFixture(t, searchVS(`["mock"]`, `"access":{"default_for_org":true}`))
	up := connectMock(t, f.Server)
	sink := &recordSink{}
	f.SetDeps(Deps{Events: sink.add})
	var atAccess string
	f.hookObserver = func(hook string, cc *CallContext) {
		if hook == hookAuthorizeTool {
			atAccess = cc.Tool.ExposedName + " " + string(cc.Args)
		}
	}

	w := f.viaCallTool(t, "", "mock__echo", map[string]any{"input": "hi"}, nil)
	refused := errorObject(t, w)
	resp := decodeResponse(t, w)
	if resp.Error != nil || !strings.Contains(string(resp.Result), `"echoed"`) || refused != nil {
		t.Fatalf("call_tool answered %s %+v, want the upstream's result", resp.Result, resp.Error)
	}
	if atAccess != `mock__echo {"input":"hi"}` {
		t.Fatalf("the access step judged %q, want the target with its own arguments", atAccess)
	}
	got := up.received()
	if len(got) != 1 {
		t.Fatalf("the upstream got %d calls, want 1", len(got))
	}
	var sent struct {
		Name      string         `json:"name"`
		Arguments map[string]any `json:"arguments"`
	}
	if err := json.Unmarshal(got[0], &sent); err != nil || sent.Name != "echo" || len(sent.Arguments) != 1 || sent.Arguments["input"] != "hi" {
		t.Fatalf("the upstream got %s, want echo with {input: hi}", got[0])
	}
	rec := sink.only(t)
	if rec.Tool != "mock__echo" || rec.UpstreamTool != "echo" || rec.ServerID != "mock" || rec.Outcome != OutcomeSuccess || rec.VirtualServerID != vsEng {
		t.Fatalf("record tool %q upstream %q server %q outcome %q vs %q, want the real tool's", rec.Tool, rec.UpstreamTool, rec.ServerID, rec.Outcome, rec.VirtualServerID)
	}

	// A direct call by the real name still works in search mode, and is
	// answered alike.
	direct := decodeResponse(t, f.direct(t, "", "mock__echo", map[string]any{"input": "hi"}, nil))
	if direct.Error != nil || string(stripCallMeta(t, direct.Result)) != string(stripCallMeta(t, resp.Result)) {
		t.Fatalf("a direct call answered %s %+v, unlike call_tool's %s", direct.Result, direct.Error, resp.Result)
	}
}

// stripCallMeta drops the per-call _meta["io.futureagi/tool_call"] (its id
// and latency differ between two calls).
func stripCallMeta(t *testing.T, raw json.RawMessage) json.RawMessage {
	t.Helper()
	var obj map[string]json.RawMessage
	if err := json.Unmarshal(raw, &obj); err != nil {
		t.Fatal(err)
	}
	var meta map[string]json.RawMessage
	_ = json.Unmarshal(obj["_meta"], &meta)
	delete(meta, MetaKeyToolCall)
	obj["_meta"], _ = json.Marshal(meta)
	out, _ := json.Marshal(obj)
	return out
}

// TestCallToolDeniedStaysDenied: a tool a direct call is refused is refused
// through call_tool with the same error object (code, message, rule and
// virtual server), whichever rule refuses it: an app's tool posture (an
// access rule's deny, or an allow list that leaves it out), the key's own
// MCP lists, the request's x-agentcc-mcp-allow-tools narrowing, or the
// org's tool policy. Nothing reaches the upstream.
func TestCallToolDeniedStaysDenied(t *testing.T) {
	f := newVSFixture(t, searchVS(`["mock","github"]`, `"access":{"default_for_org":true,"rules":[
		{"match":{"key_ids":["k1"]},"deny":["github/delete_repo"]},
		{"match":{"key_ids":["k2"]},"allow":["github/list_repos"]}]}`))
	up := connectMock(t, f.Server)
	registerTools(f.Server, "github", "list_repos", "delete_repo", "risky")
	f.SetDeps(Deps{ToolPolicy: &fakePolicy{decide: func(cc *CallContext) PolicyDecision {
		if cc.Tool.UpstreamName == "risky" {
			return PolicyDecision{Effect: PolicyDeny, RuleID: "no-risk"}
		}
		return PolicyDecision{Effect: PolicyAllow}
	}}})
	f.SetKeyAuth(&testKeyAuth{keys: map[string]struct {
		allowed []string
		denied  []string
	}{"key-plain": {}, "key-x": {denied: []string{"mock__echo"}}, "key-y": {allowed: []string{"github/list_repos"}}}})
	sessions := map[string]string{}
	for _, k := range []string{"key-plain", "key-x", "key-y"} {
		sessions[k] = initializeSessionWith(t, f.Server, map[string]string{"Authorization": "Bearer " + k})
	}

	for _, tc := range []struct {
		name, keyID, bearer, tool, header, rule string
	}{
		{"posture deny", "k1", "key-plain", "github__delete_repo", "", "access.rules[0]"},
		{"posture allow list", "k2", "key-plain", "github__delete_repo", "", "access.rules[1]"},
		{"key denied list", "k9", "key-x", "mock__echo", "", "key.mcp_denied_tools"},
		{"key allowed list", "k9", "key-y", "mock__echo", "", "key.mcp_allowed_tools"},
		{"header narrowing", "k9", "key-plain", "mock__echo", "github__*", "header"},
		{"tool policy", "k9", "key-plain", "github__risky", "", "policy:no-risk"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			f.principal.KeyID = tc.keyID
			f.session = sessions[tc.bearer]
			h := map[string]string{"Authorization": "Bearer " + tc.bearer}
			if tc.header != "" {
				h[HeaderMCPAllowTools] = tc.header
			}
			direct := f.direct(t, "", tc.tool, map[string]any{}, h)
			requireDenied(t, decodeResponse(t, httptestCopy(direct)), tc.rule, vsEng)
			via := f.viaCallTool(t, "", tc.tool, map[string]any{}, h)
			requireDenied(t, decodeResponse(t, httptestCopy(via)), tc.rule, vsEng)
			if d, v := errorObject(t, direct), errorObject(t, via); d.Code != v.Code || d.Message != v.Message || d.Type != v.Type {
				t.Fatalf("call_tool refused with %+v, a direct call with %+v", v, d)
			}
		})
	}
	if n := len(up.received()); n != 0 {
		t.Fatalf("the upstream got %d calls, want none: every call was refused", n)
	}
}

// TestCallToolHiddenTargetUnknown: call_tool on a tool the caller cannot see
// (excluded, hidden by an override, not a member's, refused by an access
// rule's allow list for this key, or no such tool) answers byte for byte as
// a direct call of that name does: -32602 Unknown tool, nothing that tells
// them apart.
func TestCallToolHiddenTargetUnknown(t *testing.T) {
	f := newVSFixture(t, searchVS(`["github"]`, `"access":{"default_for_org":true},
		"tools":{"exclude":["github/excluded"]},"overrides":{"github/hidden":{"hidden":true}}`))
	registerTools(f.Server, "github", "visible", "excluded", "hidden")
	registerTools(f.Server, "nonmember", "x")
	for _, name := range []string{"github__excluded", "github__hidden", "nonmember__x", "nope", "github_visible", "Call_Tool", "SEARCH_TOOLS"} {
		direct := f.direct(t, "", name, map[string]any{}, nil).Body.String()
		w := f.viaCallTool(t, "", name, map[string]any{}, nil)
		requireUnknownTool(t, decodeResponse(t, httptestCopy(w)), name)
		if got := w.Body.String(); got != direct {
			t.Fatalf("call_tool on %s answered\n%s\nunlike a direct call\n%s", name, got, direct)
		}
	}
}

// TestCallToolListAgree: for every principal and request narrowing, a tool
// passes the access step through call_tool exactly when the same server in
// direct mode lists it. call_tool never reaches a tool the caller cannot
// see, and never refuses one it can.
func TestCallToolListAgree(t *testing.T) {
	access := `"access":{"default_for_org":true,
		"rules":[{"match":{"key_ids":["k1"]},"allow":["github/*","git@work/x"]},{"match":{"agent_ids":["a1"]},"deny":["*/delete_*"]},{"match":{"user_ids":["u1"]},"deny":["git/*"]}]},
		"tools":{"exclude":["github/secret"]},"overrides":{"github/old":{"name":"renamed"}}`
	directCfg := orgConfig(t, `{"mcp":{"virtual_servers":{"eng":{"id":"`+vsEng+`","members":["github","git@work"],`+access+`}}}}`)
	searchCfg := orgConfig(t, searchVS(`["github","git@work"]`, access))
	f := newVSFixture(t, "")
	registerTools(f.Server, "github", "list", "delete_repo", "secret", "old")
	registerTools(f.Server, "git", "x", "delete_y")
	all := []string{"github__list", "github__delete_repo", "github__secret", "renamed", "github__old", "git_work__x", "git_work__delete_y", "nope"}
	passed := false
	f.hookObserver = func(hook string, _ *CallContext) {
		if hook == hookTranslateRetry {
			passed = true
		}
	}
	for _, p := range []struct{ key, agent, user string }{{"k1", "", ""}, {"k2", "", ""}, {"k2", "a1", ""}, {"k1", "a1", ""}, {"k2", "", "u1"}} {
		for _, header := range []string{"", "github__*", "renamed,git_work__x"} {
			f.principal.KeyID, f.agentID, f.principal.UserID = p.key, p.agent, p.user
			h := map[string]string{}
			if header != "" {
				h[HeaderMCPAllowTools] = header
			}
			f.cfg = directCfg
			listed := map[string]bool{}
			for _, n := range f.toolNames(t, "", h) {
				listed[n] = true
			}
			f.cfg = searchCfg
			for _, name := range all {
				passed = false
				f.viaCallTool(t, "", name, map[string]any{}, h)
				if passed != listed[name] {
					t.Errorf("%+v header %q: %s listed=%v but call_tool passed access=%v", p, header, name, listed[name], passed)
				}
			}
		}
	}
}

// TestDirectModeMetaToolsUnknown: a virtual server in direct mode, an org's
// compatibility view and the legacy view have no meta-tools: calling one
// answers exactly as calling a tool that does not exist.
func TestDirectModeMetaToolsUnknown(t *testing.T) {
	f := newVSFixture(t, `{"mcp":{"access":{"default":"allow"},"virtual_servers":{"eng":{"id":"`+vsEng+`","members":["github"],"access":{"default_for_org":true}}}}}`)
	registerTools(f.Server, "github", "list_repos")
	check := func(view string) {
		t.Helper()
		missing := f.direct(t, "", "nope", map[string]any{}, nil).Body.String()
		for _, name := range []string{"call_tool", "search_tools", "describe_tool"} {
			got := f.direct(t, "", name, map[string]any{"name": "github__list_repos", "query": "repos"}, nil).Body.String()
			if strings.ReplaceAll(got, name, "nope") != missing {
				t.Fatalf("%s: %s answered\n%s\nunlike a missing tool\n%s", view, name, got, missing)
			}
		}
	}
	check("direct mode")
	f.cfg = orgConfig(t, `{"mcp":{"access":{"default":"allow"},"servers":{"github":{"url":"https://org.example/mcp"}}}}`)
	check("compatibility view")
	f.Registry().SetStaticOrg(f.principal.OrgID)
	f.cfg = nil
	check("legacy view")
}

// TestCallToolMalformed: a call_tool whose arguments are not {name,
// arguments} with a tool name and an object (or no) arguments is refused as
// invalid arguments, -32602, before any tool is resolved; nothing reaches
// the upstream, and the refusal is recorded.
func TestCallToolMalformed(t *testing.T) {
	f := newVSFixture(t, searchVS(`["mock"]`, `"access":{"default_for_org":true}`))
	up := connectMock(t, f.Server)
	sink := &recordSink{}
	f.SetDeps(Deps{Events: sink.add})
	for _, args := range []any{
		nil,
		map[string]any{},
		map[string]any{"name": ""},
		map[string]any{"name": 5},
		map[string]any{"name": "mock__echo", "arguments": []any{"hi"}},
		map[string]any{"name": "mock__echo", "arguments": "hi"},
	} {
		params := map[string]any{"name": "call_tool"}
		if args != nil {
			params["arguments"] = args
		}
		resp := decodeResponse(t, f.post(t, "", MethodToolsCall, params, nil))
		if resp.Error == nil || resp.Error.Code != ErrCodeInvalidParams {
			t.Fatalf("call_tool with %v answered %s %+v, want -32602", args, resp.Result, resp.Error)
		}
	}
	if n := len(up.received()); n != 0 {
		t.Fatalf("the upstream got %d calls, want none", n)
	}
	recs := sink.records(t)
	if len(recs) != 6 {
		t.Fatalf("%d records, want one per refused call", len(recs))
	}
	for _, rec := range recs {
		if rec.Outcome != OutcomeDenied || rec.ErrorCode != "invalid_arguments" {
			t.Fatalf("record outcome %q code %q, want denied invalid_arguments", rec.Outcome, rec.ErrorCode)
		}
	}

	// An absent or null inner arguments is a call with no arguments: it is
	// judged as a direct call without them is (echo requires input, so both
	// are refused by the argument check, alike).
	without := f.direct(t, "", "mock__echo", nil, nil).Body.String()
	if !strings.Contains(without, `missing required argument`) {
		t.Fatalf("a direct call of echo without arguments answered %s, want the argument check's refusal", without)
	}
	for _, inner := range []map[string]any{{"name": "mock__echo"}, {"name": "mock__echo", "arguments": nil}} {
		if got := f.post(t, "", MethodToolsCall, map[string]any{"name": "call_tool", "arguments": inner}, nil).Body.String(); got != without {
			t.Fatalf("call_tool with %v answered\n%s\nunlike a direct call without arguments\n%s", inner, got, without)
		}
	}
}

// metaAnswer calls the meta-tool name with args and returns the result the
// gateway computed for it, as the call's always-run evidence step sees it
// (cc.Result), with the call's record. The answer reaches the client once
// the chain lets a hook answer a call itself (answeredByGateway).
func (f *vsFixture) metaAnswer(t *testing.T, sink *recordSink, name string, args any, headers map[string]string) (json.RawMessage, ToolCallRecord) {
	t.Helper()
	var result json.RawMessage
	f.hookObserver = func(hook string, cc *CallContext) {
		if hook == hookEvidenceMeta {
			result = append(json.RawMessage(nil), cc.Result...)
		}
	}
	defer func() { f.hookObserver = nil }()
	sink.reset()
	params := map[string]any{"name": name}
	if args != nil {
		params["arguments"] = args
	}
	f.post(t, "", MethodToolsCall, params, headers)
	return result, sink.only(t)
}

// searchHits decodes a search_tools result's structured tools.
func searchHits(t *testing.T, result json.RawMessage) []string {
	t.Helper()
	var res struct {
		Content           []ContentPart `json:"content"`
		StructuredContent struct {
			Tools []struct {
				Name        string `json:"name"`
				Description string `json:"description"`
			} `json:"tools"`
		} `json:"structuredContent"`
		IsError bool `json:"isError"`
	}
	if err := json.Unmarshal(result, &res); err != nil || res.IsError || len(res.Content) != 1 || res.Content[0].Type != "text" {
		t.Fatalf("search_tools result %s: want one text part and structured tools (%v)", result, err)
	}
	var mirrored any
	if err := json.Unmarshal([]byte(res.Content[0].Text), &mirrored); err != nil {
		t.Fatalf("search_tools text %q is not the structured result as JSON", res.Content[0].Text)
	}
	names := []string{}
	for _, h := range res.StructuredContent.Tools {
		names = append(names, h.Name)
	}
	return names
}

// TestSearchDeterministic: search_tools ranks the caller's tools by the
// query's words in their names and descriptions, a name match above a
// description match, ties by name in byte order; returns at most limit
// (default 10, at most 20); and answers the same query, in any word order
// or case, with the same bytes every time.
func TestSearchDeterministic(t *testing.T) {
	f := newVSFixture(t, searchVS(`["github"]`, `"access":{"default_for_org":true}`))
	registerMany(f.Server, "github", 120)
	sink := &recordSink{}
	f.SetDeps(Deps{Events: sink.add})

	first, rec := f.metaAnswer(t, sink, "search_tools", map[string]any{"query": "issue"}, nil)
	if got := strings.Join(searchHits(t, first), ","); got != "github__create_issue,github__list_issues" {
		t.Fatalf("search issue = %s, want create_issue then list_issues", got)
	}
	if rec.Tool != "search_tools" || rec.ServerID != "" {
		t.Fatalf("record tool %q server %q, want search_tools with no upstream", rec.Tool, rec.ServerID)
	}
	for i := 0; i < 20; i++ {
		if again, _ := f.metaAnswer(t, sink, "search_tools", map[string]any{"query": "issue"}, nil); string(again) != string(first) {
			t.Fatalf("search %d differs:\n%s\n%s", i, again, first)
		}
	}
	a, _ := f.metaAnswer(t, sink, "search_tools", map[string]any{"query": "Repository ISSUES"}, nil)
	b, _ := f.metaAnswer(t, sink, "search_tools", map[string]any{"query": "issues repository"}, nil)
	if string(a) != string(b) || strings.Join(searchHits(t, a), ",") != "github__list_issues,github__create_issue" {
		t.Fatalf("issues repository = %s and %s, want list_issues (both words) then create_issue", searchHits(t, a), searchHits(t, b))
	}
	// A name match ranks above a description-only match, whatever the
	// names' order: list_issues has "list" in its name, close_ticket
	// "support" only in its description.
	c, _ := f.metaAnswer(t, sink, "search_tools", map[string]any{"query": "list support"}, nil)
	if got := strings.Join(searchHits(t, c), ","); got != "github__list_issues,github__close_ticket" {
		t.Fatalf("list support = %s, want list_issues then close_ticket", got)
	}
	// Every tool matches "task" (descriptions) or none: limit applies.
	for _, tc := range []struct {
		limit any
		want  int
	}{{nil, 10}, {1, 1}, {20, 20}} {
		args := map[string]any{"query": "task team"}
		if tc.limit != nil {
			args["limit"] = tc.limit
		}
		res, _ := f.metaAnswer(t, sink, "search_tools", args, nil)
		hits := searchHits(t, res)
		if len(hits) != tc.want || hits[0] != "github__tool_003" {
			t.Fatalf("limit %v: %d hits from %v, want %d in name order", tc.limit, len(hits), hits, tc.want)
		}
	}
	if res, _ := f.metaAnswer(t, sink, "search_tools", map[string]any{"query": "zebra"}, nil); len(searchHits(t, res)) != 0 {
		t.Fatalf("zebra found %v, want nothing", searchHits(t, res))
	}
}

// TestSearchFindsExactlyWhatListOffers: for every principal, request
// narrowing and step profile, the tools search_tools can return, and
// describe_tool can describe, are exactly the tools the same server lists
// in direct mode for the same request (at step tool, the step of every
// tools/call). Search never reveals a tool the caller cannot see.
func TestSearchFindsExactlyWhatListOffers(t *testing.T) {
	access := `"access":{"default_for_org":true,
		"rules":[{"match":{"key_ids":["k1"]},"allow":["github/*","git@work/x"]},{"match":{"agent_ids":["a1"]},"deny":["*/delete_*"]},{"match":{"user_ids":["u1"]},"deny":["git/*"]}]},
		"tools":{"exclude":["github/secret"]},"overrides":{"github/old":{"name":"renamed"}},
		"step_profiles":{"tool":{"include":["github/*"]}}`
	directCfg := orgConfig(t, `{"mcp":{"virtual_servers":{"eng":{"id":"`+vsEng+`","members":["github","git@work"],`+access+`}}}}`)
	searchCfg := orgConfig(t, searchVS(`["github","git@work"]`, access))
	f := newVSFixture(t, "")
	registerTools(f.Server, "github", "list", "delete_repo", "secret", "old")
	registerTools(f.Server, "git", "x", "delete_y")
	sink := &recordSink{}
	f.SetDeps(Deps{Events: sink.add})
	all := []string{"github__list", "github__delete_repo", "github__secret", "renamed", "github__old", "git_work__x", "git_work__delete_y"}
	for _, p := range []struct{ key, agent, user string }{{"k1", "", ""}, {"k2", "", ""}, {"k2", "a1", ""}, {"k2", "", "u1"}} {
		for _, header := range []string{"", "github__*", "renamed,git_work__x"} {
			for _, step := range []string{"", "tool"} {
				f.principal.KeyID, f.agentID, f.principal.UserID = p.key, p.agent, p.user
				h := map[string]string{}
				if header != "" {
					h[HeaderMCPAllowTools] = header
				}
				if step != "" {
					h[headerStep] = step
				}
				f.cfg = directCfg
				listed := f.toolNames(t, "", h)
				// A tools/call is always at step tool (M8): the profile applies.
				delete(h, headerStep)
				f.cfg = searchCfg
				res, _ := f.metaAnswer(t, sink, "search_tools", map[string]any{"query": "upstream", "limit": 20}, h)
				found := searchHits(t, res)
				if step == "tool" && strings.Join(found, ",") != strings.Join(listed, ",") {
					t.Errorf("%+v header %q: search found %v, direct mode at step tool lists %v", p, header, found, listed)
				}
				described := map[string]bool{}
				for _, name := range all {
					if res, _ := f.metaAnswer(t, sink, "describe_tool", map[string]any{"name": name}, h); len(res) > 0 {
						described[name] = true
					}
				}
				for _, n := range found {
					if !described[n] {
						t.Errorf("%+v header %q: search found %s but describe_tool does not describe it", p, header, n)
					}
				}
				if len(described) != len(found) {
					t.Errorf("%+v header %q: describe_tool describes %v, search finds %v", p, header, described, found)
				}
			}
		}
	}
}

// TestDescribeTool: describe_tool returns a tool's full definition, exactly
// the item the same server lists for it in direct mode. For a tool the
// caller cannot see (excluded, hidden, not a member's, or no such tool) it
// answers byte for byte as a direct call of that name does, and for one an
// access rule refuses it answers exactly as for a tool that does not exist:
// describe_tool never tells a refused tool from a missing one.
func TestDescribeTool(t *testing.T) {
	access := `"access":{"default_for_org":true,"rules":[{"match":{"key_ids":["k1"]},"deny":["github/denied"]}]},
		"tools":{"exclude":["github/excluded"]},"overrides":{"github/hidden":{"hidden":true}}`
	f := newVSFixture(t, `{"mcp":{"virtual_servers":{"eng":{"id":"`+vsEng+`","members":["github"],`+access+`}}}}`)
	registerTools(f.Server, "github", "visible", "excluded", "hidden", "denied")
	registerTools(f.Server, "nonmember", "x")
	items, _ := f.listItems(t, nil)
	if len(items) != 1 {
		t.Fatalf("direct mode lists %d tools, want only visible", len(items))
	}
	f.cfg = orgConfig(t, searchVS(`["github"]`, access))
	sink := &recordSink{}
	f.SetDeps(Deps{Events: sink.add})

	res, rec := f.metaAnswer(t, sink, "describe_tool", map[string]any{"name": "github__visible"}, nil)
	var got struct {
		Content           []ContentPart `json:"content"`
		StructuredContent struct {
			Tool json.RawMessage `json:"tool"`
		} `json:"structuredContent"`
	}
	if err := json.Unmarshal(res, &got); err != nil || len(got.Content) != 1 {
		t.Fatalf("describe_tool result %s: %v", res, err)
	}
	if !jsonEqual(t, got.StructuredContent.Tool, items[0]) || !jsonEqual(t, json.RawMessage(got.Content[0].Text), items[0]) {
		t.Fatalf("describe_tool gave %s / %s, want the direct list's item %s", got.StructuredContent.Tool, got.Content[0].Text, items[0])
	}
	if rec.Tool != "describe_tool" || rec.ServerID != "" {
		t.Fatalf("record tool %q server %q, want describe_tool with no upstream", rec.Tool, rec.ServerID)
	}

	f.hookObserver = nil
	describe := func(name string) *httptest.ResponseRecorder {
		return f.post(t, "", MethodToolsCall, map[string]any{"name": "describe_tool", "arguments": map[string]any{"name": name}}, nil)
	}
	for _, name := range []string{"github__excluded", "github__hidden", "nonmember__x", "nope"} {
		direct := f.direct(t, "", name, map[string]any{}, nil).Body.String()
		w := describe(name)
		requireUnknownTool(t, decodeResponse(t, httptestCopy(w)), name)
		if body := w.Body.String(); body != direct {
			t.Fatalf("describe_tool %s answered\n%s\nunlike a direct call\n%s", name, body, direct)
		}
	}
	missing := describe("nope").Body.String()
	if got := strings.ReplaceAll(describe("github__denied").Body.String(), "github__denied", "nope"); got != missing {
		t.Fatalf("describe_tool of a refused tool answered\n%s\nunlike a missing one\n%s", got, missing)
	}
}

func jsonEqual(t *testing.T, a, b json.RawMessage) bool {
	t.Helper()
	var x, y any
	if json.Unmarshal(a, &x) != nil || json.Unmarshal(b, &y) != nil {
		return false
	}
	ja, _ := json.Marshal(x)
	jb, _ := json.Marshal(y)
	return string(ja) == string(jb)
}

// TestSearchArgumentsInvalid: search_tools without a query of words, with
// a query over 1,024 bytes or a limit that is not a whole number from 1 to
// 20, and describe_tool without a tool name, are refused as invalid
// arguments, -32602, and recorded so.
func TestSearchArgumentsInvalid(t *testing.T) {
	f := newVSFixture(t, searchVS(`["github"]`, `"access":{"default_for_org":true}`))
	registerTools(f.Server, "github", "list")
	sink := &recordSink{}
	f.SetDeps(Deps{Events: sink.add})
	for _, tc := range []struct {
		tool string
		args any
	}{
		{"search_tools", nil},
		{"search_tools", map[string]any{}},
		{"search_tools", map[string]any{"query": "  "}},
		{"search_tools", map[string]any{"query": "--- ..."}},
		{"search_tools", map[string]any{"query": 5}},
		{"search_tools", map[string]any{"query": strings.Repeat("a ", 513)}},
		{"search_tools", map[string]any{"query": "list", "limit": 0}},
		{"search_tools", map[string]any{"query": "list", "limit": 21}},
		{"search_tools", map[string]any{"query": "list", "limit": 2.5}},
		{"search_tools", map[string]any{"query": "list", "limit": "5"}},
		{"describe_tool", nil},
		{"describe_tool", map[string]any{"name": ""}},
		{"describe_tool", map[string]any{"name": []any{"x"}}},
	} {
		sink.reset()
		params := map[string]any{"name": tc.tool}
		if tc.args != nil {
			params["arguments"] = tc.args
		}
		resp := decodeResponse(t, f.post(t, "", MethodToolsCall, params, nil))
		if resp.Error == nil || resp.Error.Code != ErrCodeInvalidParams {
			t.Fatalf("%s %v answered %s %+v, want -32602", tc.tool, tc.args, resp.Result, resp.Error)
		}
		if rec := sink.only(t); rec.ErrorCode != "invalid_arguments" || rec.Tool != tc.tool {
			t.Fatalf("%s %v: record tool %q code %q, want invalid_arguments", tc.tool, tc.args, rec.Tool, rec.ErrorCode)
		}
	}
}
