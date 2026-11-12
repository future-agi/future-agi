package mcp

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/contracts/schematest"
	"github.com/futureagi/agentcc-gateway/internal/mcp/vserver"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// testKeyAuth is a mock KeyAuthenticator for testing.
type testKeyAuth struct {
	keys map[string]struct {
		allowed []string
		denied  []string
	}
}

func (a *testKeyAuth) AuthenticateKey(rawKey string) (KeyGrant, bool) {
	entry, ok := a.keys[rawKey]
	if !ok {
		return KeyGrant{}, false
	}
	return KeyGrant{KeyID: "id-" + rawKey, AllowedTools: entry.allowed, DeniedTools: entry.denied}, true
}

func TestPerKeyToolFiltering_AllowedList(t *testing.T) {
	s := newTestServer(t)
	defer s.Close()

	s.Registry().RegisterServer("github", []Tool{
		{Name: "create_issue", Description: "Create an issue", InputSchema: json.RawMessage(`{"type":"object"}`)},
		{Name: "list_repos", Description: "List repos", InputSchema: json.RawMessage(`{"type":"object"}`)},
		{Name: "delete_repo", Description: "Delete repo", InputSchema: json.RawMessage(`{"type":"object"}`)},
	})

	s.SetKeyAuth(&testKeyAuth{
		keys: map[string]struct {
			allowed []string
			denied  []string
		}{
			"key-readonly": {allowed: []string{"github_list_repos"}, denied: nil},
		},
	})

	sessionID := initializeSessionWith(t, s, map[string]string{"Authorization": "Bearer key-readonly"})

	// List tools with the readonly key — should only see list_repos.
	msg := &Message{JSONRPC: "2.0", ID: json.RawMessage(`2`), Method: MethodToolsList}
	w := postMCP(t, s, msg, map[string]string{
		"MCP-Session-Id": sessionID,
		"Authorization":  "Bearer key-readonly",
	})

	resp := decodeResponse(t, w)
	if resp.Error != nil {
		t.Fatalf("unexpected error: %s", resp.Error.Message)
	}

	var result ListToolsResult
	if err := json.Unmarshal(resp.Result, &result); err != nil {
		t.Fatal(err)
	}
	if len(result.Tools) != 1 {
		t.Fatalf("expected 1 tool for readonly key, got %d", len(result.Tools))
	}
	if result.Tools[0].Name != "github_list_repos" {
		t.Fatalf("expected github_list_repos, got %s", result.Tools[0].Name)
	}
}

func TestPerKeyToolFiltering_DeniedList(t *testing.T) {
	s := newTestServer(t)
	defer s.Close()

	s.Registry().RegisterServer("github", []Tool{
		{Name: "create_issue", Description: "Create", InputSchema: json.RawMessage(`{"type":"object"}`)},
		{Name: "delete_repo", Description: "Delete", InputSchema: json.RawMessage(`{"type":"object"}`)},
	})

	s.SetKeyAuth(&testKeyAuth{
		keys: map[string]struct {
			allowed []string
			denied  []string
		}{
			"key-safe": {allowed: nil, denied: []string{"github_delete_repo"}},
		},
	})

	sessionID := initializeSessionWith(t, s, map[string]string{"Authorization": "Bearer key-safe"})

	// List tools with denied key — should not see delete_repo.
	msg := &Message{JSONRPC: "2.0", ID: json.RawMessage(`2`), Method: MethodToolsList}
	w := postMCP(t, s, msg, map[string]string{
		"MCP-Session-Id": sessionID,
		"Authorization":  "Bearer key-safe",
	})

	resp := decodeResponse(t, w)
	if resp.Error != nil {
		t.Fatalf("unexpected error: %s", resp.Error.Message)
	}

	var result ListToolsResult
	json.Unmarshal(resp.Result, &result)
	if len(result.Tools) != 1 {
		t.Fatalf("expected 1 tool (delete_repo denied), got %d", len(result.Tools))
	}
	if result.Tools[0].Name != "github_create_issue" {
		t.Fatalf("expected github_create_issue, got %s", result.Tools[0].Name)
	}
}

func TestPerKeyToolFiltering_InvalidKeyRejected(t *testing.T) {
	s := newTestServer(t)
	defer s.Close()

	s.SetKeyAuth(&testKeyAuth{
		keys: map[string]struct {
			allowed []string
			denied  []string
		}{
			"valid-key": {allowed: nil, denied: nil},
		},
	})

	// Send with invalid key.
	params := InitializeParams{
		ProtocolVersion: ProtocolVersion,
		ClientInfo:      Implementation{Name: "test", Version: "1.0"},
	}
	paramsData, _ := json.Marshal(params)
	msg := &Message{JSONRPC: "2.0", ID: json.RawMessage(`1`), Method: MethodInitialize, Params: paramsData}

	w := postMCP(t, s, msg, map[string]string{
		"Authorization": "Bearer bad-key",
	})

	requireUnauthorized(t, w, "invalid key")
	resp := decodeResponse(t, w)
	if resp.Error == nil {
		t.Fatal("expected error for invalid API key")
	}
	// MCP auth's 401 body: JSON-RPC -32001 whose data.code says why.
	if resp.Error.Code != ErrCodeUnauthorized {
		t.Fatalf("expected code %d, got %d", ErrCodeUnauthorized, resp.Error.Code)
	}
	var data struct {
		Code string `json:"code"`
	}
	if err := json.Unmarshal(resp.Error.Data, &data); err != nil || data.Code != AuthInvalidAPIKey {
		t.Fatalf("data %s (%v), want code %q", resp.Error.Data, err, AuthInvalidAPIKey)
	}
	if s.SessionCount() != 0 {
		t.Fatalf("an invalid key opened %d session(s)", s.SessionCount())
	}
}

func TestPerKeyToolFiltering_ToolCallBlocked(t *testing.T) {
	s := newTestServer(t)
	defer s.Close()

	tools := []Tool{{Name: "secret_tool", InputSchema: json.RawMessage(`{}`)}}
	s.Registry().RegisterServer("srv", tools)

	mockUpstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var msg Message
		json.NewDecoder(r.Body).Decode(&msg)
		result := ToolCallResult{Content: []ContentPart{{Type: "text", Text: "secret data"}}}
		resp, _ := NewResponse(msg.ID, result)
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(resp)
	}))
	defer mockUpstream.Close()

	client := NewClient(ClientConfig{ServerID: "srv", URL: mockUpstream.URL, TransportType: "http"})
	client.transport = NewHTTPTransport(mockUpstream.URL, AuthConfig{})
	client.healthy.Store(true)
	client.tools.Store(&tools)
	s.clientsMu.Lock()
	s.clients["srv"] = client
	s.clientsMu.Unlock()

	s.SetKeyAuth(&testKeyAuth{
		keys: map[string]struct {
			allowed []string
			denied  []string
		}{
			"restricted": {allowed: nil, denied: []string{"srv_secret_tool"}},
		},
	})

	sessionID := initializeSessionWith(t, s, map[string]string{"Authorization": "Bearer restricted"})

	// Try calling the denied tool.
	params := ToolCallParams{Name: "srv_secret_tool", Arguments: map[string]interface{}{}}
	paramsData, _ := json.Marshal(params)
	msg := &Message{JSONRPC: "2.0", ID: json.RawMessage(`3`), Method: MethodToolsCall, Params: paramsData}

	w := postMCP(t, s, msg, map[string]string{
		"MCP-Session-Id": sessionID,
		"Authorization":  "Bearer restricted",
	})

	resp := decodeResponse(t, w)
	if resp.Error == nil {
		t.Fatal("expected error for denied tool call")
	}
}

func TestCheckToolKeyAccess(t *testing.T) {
	// No restrictions.
	ctx := context.Background()
	if msg := checkToolKeyAccess(ctx, "any_tool"); msg != "" {
		t.Errorf("expected empty, got: %s", msg)
	}

	// Allowed list — tool in list.
	ctx = context.WithValue(context.Background(), ctxKeyAllowedTools, []string{"a_tool"})
	if msg := checkToolKeyAccess(ctx, "a_tool"); msg != "" {
		t.Errorf("expected empty for allowed tool, got: %s", msg)
	}

	// Allowed list — tool NOT in list.
	if msg := checkToolKeyAccess(ctx, "other_tool"); msg == "" {
		t.Error("expected error for tool not in allowed list")
	}

	// Denied list — tool in list.
	ctx = context.WithValue(context.Background(), ctxKeyDeniedTools, []string{"bad_tool"})
	if msg := checkToolKeyAccess(ctx, "bad_tool"); msg == "" {
		t.Error("expected error for denied tool")
	}

	// Denied list — tool NOT in list.
	if msg := checkToolKeyAccess(ctx, "good_tool"); msg != "" {
		t.Errorf("expected empty for non-denied tool, got: %s", msg)
	}
}

// --- Virtual-server fixture ---

// vsFixture is a server whose steps 1 and 3 are stood in for: the principal,
// the agent id (parseAgent), the era and the org config
// are set on every call from the fixture's fields, so this lane's
// hooks run on them as they will once those lanes are merged.
type vsFixture struct {
	*Server
	session   string
	principal Principal
	agentID   string
	runID     string
	era       string
	cfg       *tenant.OrgConfig
}

// vsChains are every chain whose head step 3 the fixture replaces.
var vsChains = [][]chainStep{toolsCallChain, toolsListChain, resourcesListChain, resourcesReadChain, promptsListChain, promptsGetChain, resultHandleReadChain}

func newVSFixture(t *testing.T, cfgJSON string) *vsFixture {
	t.Helper()
	f := &vsFixture{Server: newTestServer(t), principal: Principal{Kind: PrincipalKey, KeyID: "k1", OrgID: "org-a"}}
	t.Cleanup(f.Close)
	// Every org's members come from the registry, as the tenancy's pools will
	// serve them (SetOrgSource); without it a virtual server has none.
	f.Registry().SetOrgSource(func(string) MemberSource { return f.Registry() })
	if cfgJSON != "" {
		f.cfg = orgConfig(t, cfgJSON)
	}
	for _, chain := range vsChains {
		replaceStep(t, chain, hookResolveTenant, func(_ *Server, cc *CallContext) *Denial {
			observeHook(cc, hookResolveTenant)
			cc.Principal, cc.Era, cc.OrgConfig = f.principal, f.era, f.cfg
			cc.Agent.AgentID, cc.Agent.RunID = f.agentID, f.runID
			return nil
		})
	}
	f.session = initializeSession(t, f.Server)
	return f
}

func orgConfig(t *testing.T, raw string) *tenant.OrgConfig {
	t.Helper()
	var cfg tenant.OrgConfig
	if err := json.Unmarshal([]byte(raw), &cfg); err != nil {
		t.Fatalf("org config: %v", err)
	}
	return &cfg
}

// post sends one JSON-RPC request on the fixture's session, addressed to the
// virtual server vsID when it is set (as the /mcp/v/{vs_id} route does).
func (f *vsFixture) post(t *testing.T, vsID, method string, params any, headers map[string]string) *httptest.ResponseRecorder {
	t.Helper()
	raw, err := json.Marshal(params)
	if err != nil {
		t.Fatal(err)
	}
	body, _ := json.Marshal(&Message{JSONRPC: "2.0", ID: json.RawMessage(`7`), Method: method, Params: raw})
	req := httptest.NewRequest("POST", "/mcp", bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("MCP-Session-Id", f.session)
	for k, v := range headers {
		req.Header.Set(k, v)
	}
	if vsID != "" {
		req = WithVirtualServer(req, vsID)
	}
	w := httptest.NewRecorder()
	f.HandlePost(w, req)
	return w
}

// toolNames lists the tools a tools/list answers with.
func (f *vsFixture) toolNames(t *testing.T, vsID string, headers map[string]string) []string {
	t.Helper()
	resp := decodeResponse(t, f.post(t, vsID, MethodToolsList, struct{}{}, headers))
	if resp.Error != nil {
		t.Fatalf("tools/list: %+v", resp.Error)
	}
	var res ListToolsResult
	if err := json.Unmarshal(resp.Result, &res); err != nil {
		t.Fatal(err)
	}
	names := []string{}
	for _, tl := range res.Tools {
		names = append(names, tl.Name)
	}
	return names
}

// callResult posts tools/call and returns the decoded response.
func (f *vsFixture) callResult(t *testing.T, vsID, name string, headers map[string]string) *Message {
	t.Helper()
	return decodeResponse(t, f.post(t, vsID, MethodToolsCall, ToolCallParams{Name: name, Arguments: map[string]interface{}{}}, headers))
}

// denial is the in-band error object of a refused tools/call, or nil.
func denial(t *testing.T, resp *Message) *ErrorObject {
	t.Helper()
	if resp.Error != nil {
		t.Fatalf("a protocol error %+v where an in-band denial was expected", resp.Error)
	}
	var res struct {
		IsError bool                       `json:"isError"`
		Meta    map[string]json.RawMessage `json:"_meta"`
	}
	if err := json.Unmarshal(resp.Result, &res); err != nil {
		t.Fatal(err)
	}
	raw, ok := res.Meta[MetaKeyError]
	if !res.IsError || !ok {
		return nil
	}
	var obj ErrorObject
	if err := json.Unmarshal(raw, &obj); err != nil {
		t.Fatal(err)
	}
	return &obj
}

// requireDenialSchema checks a refusal's error object against mcp-denial-v1's
// $defs/Error, the contract clients validate it with, and returns it.
// The whole $defs/Denial also needs _meta["io.futureagi/tool_call"], which
// evidenceMeta writes (a no-op in this lane).
func requireDenialSchema(t *testing.T, resp *Message) map[string]any {
	t.Helper()
	var res struct {
		Meta map[string]json.RawMessage `json:"_meta"`
	}
	if err := json.Unmarshal(resp.Result, &res); err != nil {
		t.Fatal(err)
	}
	var obj map[string]any
	if err := json.Unmarshal(res.Meta[MetaKeyError], &obj); err != nil {
		t.Fatalf("no error object in %s: %v", resp.Result, err)
	}
	if errs := schematest.ValidateDef(t, "mcp-denial-v1.schema.json", "Error", obj); len(errs) > 0 {
		t.Fatalf("the refusal %s breaks mcp-denial-v1 $defs/Error: %v", res.Meta[MetaKeyError], errs)
	}
	return obj
}

// requireDenied checks an in-band tool_denied: its rule, its virtual server
// (absent, never null, when vs is ""), and mcp-denial-v1.
func requireDenied(t *testing.T, resp *Message, rule, vs string) {
	t.Helper()
	obj := denial(t, resp)
	if obj == nil || obj.Code != "tool_denied" {
		t.Fatalf("answer %s %+v, want an in-band tool_denied", resp.Result, obj)
	}
	meta, _ := requireDenialSchema(t, resp)["metadata"].(map[string]any)
	if meta["rule"] != rule {
		t.Fatalf("tool_denied rule = %v, want %q", meta["rule"], rule)
	}
	if got, present := meta["virtual_server"]; vs == "" && present || vs != "" && got != vs {
		t.Fatalf("tool_denied virtual_server = %v (present: %v), want %q", got, present, vs)
	}
}

func requireUnknownTool(t *testing.T, resp *Message, name string) {
	t.Helper()
	if resp.Error == nil || resp.Error.Code != ErrCodeInvalidParams || resp.Error.Message != "Unknown tool: "+name || resp.Error.Data != nil {
		t.Fatalf("calling %s answered %s %+v, want -32602 Unknown tool", name, resp.Result, resp.Error)
	}
}

func registerTools(s *Server, server string, names ...string) {
	var tools []Tool
	for _, n := range names {
		tools = append(tools, Tool{Name: n, Description: "upstream " + n, InputSchema: json.RawMessage(`{"type":"object"}`)})
	}
	s.Registry().RegisterServer(server, tools)
}

const vsEng = "vs_01M4ZEB9E0J3AADR85Z4T4428X"

func TestAccessRulesKeyAgentUser(t *testing.T) {
	f := newVSFixture(t, `{"mcp":{"virtual_servers":{"eng":{"id":"`+vsEng+`","members":["github"],"access":{"default_for_org":true,"rules":[
		{"match":{"key_ids":["k1"]},"allow":["github/list_*"]},
		{"match":{"agent_ids":["planner"]},"deny":["github/delete_*"]},
		{"match":{"user_ids":["u1"]},"deny":["github/list_*"]}]}}}}}`)
	registerTools(f.Server, "github", "list_repos", "create_issue", "delete_repo")

	if got := strings.Join(f.toolNames(t, "", nil), ","); got != "github__list_repos" {
		t.Fatalf("key k1 lists %s, want only github__list_repos", got)
	}
	requireDenied(t, f.callResult(t, "", "github__create_issue", nil), "access.rules[0]", vsEng)

	f.principal.KeyID = "k2"
	f.agentID = "planner"
	if got := strings.Join(f.toolNames(t, "", nil), ","); got != "github__create_issue,github__list_repos" {
		t.Fatalf("agent planner lists %s", got)
	}
	requireDenied(t, f.callResult(t, "", "github__delete_repo", nil), "access.rules[1]", vsEng)

	f.agentID = ""
	f.principal.UserID = "u1"
	if got := strings.Join(f.toolNames(t, "", nil), ","); got != "github__create_issue,github__delete_repo" {
		t.Fatalf("user u1 lists %s", got)
	}
	requireDenied(t, f.callResult(t, "", "github__list_repos", nil), "access.rules[2]", vsEng)
}

func TestHeaderNarrowsOnly(t *testing.T) {
	f := newVSFixture(t, `{"mcp":{"virtual_servers":{"eng":{"id":"`+vsEng+`","members":["github"],"tools":{"exclude":["github/secret"]},"access":{"default_for_org":true}}}}}`)
	registerTools(f.Server, "github", "a", "b", "secret")
	registerTools(f.Server, "other", "x")

	narrow := map[string]string{HeaderMCPAllowTools: "github__a, github/zzz"}
	if got := strings.Join(f.toolNames(t, "", narrow), ","); got != "github__a" {
		t.Fatalf("narrowed list = %s, want github__a", got)
	}
	requireDenied(t, f.callResult(t, "", "github__b", narrow), "header", vsEng)
	// A header can never widen: an excluded tool and a non-member's stay out.
	wide := map[string]string{HeaderMCPAllowTools: "*"}
	if got := strings.Join(f.toolNames(t, "", wide), ","); got != "github__a,github__b" {
		t.Fatalf("a * header lists %s", got)
	}
	requireUnknownTool(t, f.callResult(t, "", "github__secret", wide), "github__secret")
	requireUnknownTool(t, f.callResult(t, "", "other__x", wide), "other__x")

	// The legacy view (the static org's principal, no org config) narrows
	// the same way.
	f.Registry().SetStaticOrg(f.principal.OrgID)
	f.cfg = nil
	if got := strings.Join(f.toolNames(t, "", map[string]string{HeaderMCPAllowTools: "github_a"}), ","); got != "github_a" {
		t.Fatalf("legacy narrowed list = %s, want github_a", got)
	}
	requireDenied(t, f.callResult(t, "", "github_b", map[string]string{HeaderMCPAllowTools: "github_a"}), "header", "")
}

func TestHiddenToolIsUnknown(t *testing.T) {
	f := newVSFixture(t, `{"mcp":{"virtual_servers":{"eng":{"id":"`+vsEng+`","members":["github"],"access":{"default_for_org":true},
		"tools":{"exclude":["github/excluded"]},"overrides":{"github/hidden":{"hidden":true}}}}}}`)
	registerTools(f.Server, "github", "visible", "excluded", "hidden")
	registerTools(f.Server, "nonmember", "x")

	if got := strings.Join(f.toolNames(t, "", nil), ","); got != "github__visible" {
		t.Fatalf("list = %s", got)
	}
	missing := f.post(t, "", MethodToolsCall, ToolCallParams{Name: "nope"}, nil).Body.String()
	for _, name := range []string{"github__excluded", "github__hidden", "nonmember__x", "github_visible"} {
		w := f.post(t, "", MethodToolsCall, ToolCallParams{Name: name}, nil)
		requireUnknownTool(t, decodeResponse(t, httptestCopy(w)), name)
		if got := strings.ReplaceAll(w.Body.String(), name, "nope"); got != missing {
			t.Fatalf("hidden %s answered\n%s\nunlike a missing tool\n%s", name, got, missing)
		}
	}
}

// httptestCopy lets a recorded body be decoded and still compared.
func httptestCopy(w *httptest.ResponseRecorder) *httptest.ResponseRecorder {
	c := httptest.NewRecorder()
	c.Body.Write(w.Body.Bytes())
	return c
}

func TestDeniedVisibleToolIsError(t *testing.T) {
	f := newVSFixture(t, `{"mcp":{"virtual_servers":{"eng":{"id":"`+vsEng+`","members":["github"],"access":{"default_for_org":true,
		"rules":[{"match":{"key_ids":["k1"]},"deny":["github/delete_repo"]}]}}}}}`)
	registerTools(f.Server, "github", "list_repos", "delete_repo")

	if got := strings.Join(f.toolNames(t, "", nil), ","); got != "github__list_repos" {
		t.Fatalf("list = %s, want the denied tool hidden from the list", got)
	}
	w := f.post(t, "", MethodToolsCall, ToolCallParams{Name: "github__delete_repo"}, nil)
	if w.Code != http.StatusOK {
		t.Fatalf("a denied call answered HTTP %d, want 200 (in band)", w.Code)
	}
	requireDenied(t, decodeResponse(t, w), "access.rules[0]", vsEng)

	// The key's MCP lists refuse in band through a virtual server too.
	f.SetKeyAuth(&testKeyAuth{keys: map[string]struct {
		allowed []string
		denied  []string
	}{"key-x": {denied: []string{"github__list_repos"}}}})
	f.session = initializeSessionWith(t, f.Server, map[string]string{"Authorization": "Bearer key-x"})
	auth := map[string]string{"Authorization": "Bearer key-x"}
	f.principal.KeyID = "k9"
	if got := strings.Join(f.toolNames(t, "", auth), ","); got != "github__delete_repo" {
		t.Fatalf("key-x lists %s", got)
	}
	requireDenied(t, f.callResult(t, "", "github__list_repos", auth), "key.mcp_denied_tools", vsEng)
}

func TestDefaultAccessDenyForNewOrgs(t *testing.T) {
	// No virtual server, no access block: a new org's compatibility view is
	// denied.
	f := newVSFixture(t, `{"mcp":{"servers":{"github":{"url":"https://example.com/mcp"}}}}`)
	registerTools(f.Server, "github", "list_repos")
	if got := f.toolNames(t, "", nil); len(got) != 0 {
		t.Fatalf("a new org lists %v, want nothing", got)
	}
	requireDenied(t, f.callResult(t, "", "github__list_repos", nil), "access.default", "")
	// Resources and prompts are refused the same way: nothing listed, nothing read.
	f.Registry().RegisterResources("github", []Resource{{URI: "file:///r", Name: "r"}})
	f.Registry().RegisterPrompts("github", []Prompt{{Name: "p"}})
	for _, m := range []string{MethodResourcesList, MethodPromptsList} {
		resp := decodeResponse(t, f.post(t, "", m, struct{}{}, nil))
		if got := string(resp.Result); resp.Error != nil || strings.Contains(got, "github") {
			t.Fatalf("%s under access.default=deny = %s %+v, want nothing", m, got, resp.Error)
		}
	}
	if resp := decodeResponse(t, f.post(t, "", MethodResourcesRead, ReadResourceParams{URI: "mcp://github/file:///r"}, nil)); resp.Error == nil || resp.Error.Code != -32002 {
		t.Fatalf("resources/read under access.default=deny = %+v", resp.Error)
	}
	if resp := decodeResponse(t, f.post(t, "", MethodPromptsGet, GetPromptParams{Name: "github__p"}, nil)); resp.Error == nil || resp.Error.Code != ErrCodeInvalidParams {
		t.Fatalf("prompts/get under access.default=deny = %+v", resp.Error)
	}

	// An org that had MCP before virtual servers existed is migrated to allow: every org
	// server is served, under virtual-server names.
	f.cfg = orgConfig(t, `{"mcp":{"access":{"default":"allow"},"servers":{"github":{"url":"https://example.com/mcp"}}}}`)
	if got := strings.Join(f.toolNames(t, "", nil), ","); got != "github__list_repos" {
		t.Fatalf("access.default=allow lists %s", got)
	}
	// A key bound to a virtual server is not affected by the default.
	f.cfg = orgConfig(t, `{"mcp":{"servers":{"github":{"url":"https://example.com/mcp"}},"virtual_servers":{"eng":{"id":"`+vsEng+`","members":["github"],"access":{"keys":["k1"]}}}}}`)
	if got := strings.Join(f.toolNames(t, "", nil), ","); got != "github__list_repos" {
		t.Fatalf("a bound key lists %s", got)
	}
}

func TestCallbackClaimsNarrowTools(t *testing.T) {
	f := newVSFixture(t, `{"mcp":{"virtual_servers":{"eng":{"id":"`+vsEng+`","members":["github"],"access":{"default_for_org":true}}}}}`)
	registerTools(f.Server, "github", "a", "b")
	f.principal = Principal{Kind: PrincipalCallback, KeyID: "k1", OrgID: "org-a", AllowedTools: []string{"github__a", "github/zzz"}}
	if got := strings.Join(f.toolNames(t, "", nil), ","); got != "github__a" {
		t.Fatalf("callback lists %s, want github__a", got)
	}
	requireDenied(t, f.callResult(t, "", "github__b", nil), "callback", vsEng)
	// A header cannot widen past the claim.
	if got := strings.Join(f.toolNames(t, "", map[string]string{HeaderMCPAllowTools: "*"}), ","); got != "github__a" {
		t.Fatalf("callback with a * header lists %s", got)
	}
	// A callback token that names no tools gets none.
	f.principal.AllowedTools = nil
	if got := f.toolNames(t, "", nil); len(got) != 0 {
		t.Fatalf("a callback token with no tools lists %v", got)
	}
}

// fakePolicy records each evaluation and answers with its decision.
type fakePolicy struct {
	decide func(cc *CallContext) PolicyDecision
	seen   []string // server/tool of each evaluated call
}

func (p *fakePolicy) Evaluate(cc *CallContext) PolicyDecision {
	p.seen = append(p.seen, cc.Upstream.ServerID+"/"+cc.Tool.UpstreamName)
	return p.decide(cc)
}

func TestToolPolicyEvaluatedAfterAccessRules(t *testing.T) {
	f := newVSFixture(t, `{"mcp":{"virtual_servers":{"eng":{"id":"`+vsEng+`","members":["github"],"access":{"default_for_org":true,
		"rules":[{"deny":["github/blocked"]}]}}}}}`)
	registerTools(f.Server, "github", "risky", "approved", "blocked")
	policy := &fakePolicy{decide: func(cc *CallContext) PolicyDecision {
		switch cc.Tool.UpstreamName {
		case "risky":
			return PolicyDecision{Effect: PolicyDeny, RuleID: "no-risk"}
		case "approved":
			return PolicyDecision{Effect: PolicyApprove, RuleID: "pre-ok"}
		}
		return PolicyDecision{Effect: PolicyAllow}
	}}
	f.SetDeps(Deps{ToolPolicy: policy})
	var approval string
	f.hookObserver = func(hook string, cc *CallContext) {
		if hook == hookTranslateRetry {
			approval = cc.PolicyApproval
		}
	}

	requireDenied(t, f.callResult(t, "", "github__risky", nil), "policy:no-risk", vsEng)
	requireDenied(t, f.callResult(t, "", "github__blocked", nil), "access.rules[0]", vsEng)
	if strings.Join(policy.seen, ",") != "github/risky" {
		t.Fatalf("the policy saw %v: it must see the resolved tool, and never one the access rules refused", policy.seen)
	}
	f.callResult(t, "", "github__approved", nil) // fails upstream (no client); the approval is set before
	if approval != "pre-ok" {
		t.Fatalf("cc.PolicyApproval = %q, want pre-ok", approval)
	}
}

// blockingGuard blocks one exposed tool of one server, as a configured
// blocked_tools guard does.
type blockingGuard struct{ tool, server string }

func (g blockingGuard) CheckPre(toolName, serverID string, _ map[string]interface{}) string {
	if toolName == g.tool && serverID == g.server {
		return "tool is blocked"
	}
	return ""
}

func (blockingGuard) CheckPost(string, string, *ToolCallResult) string { return "" }

func TestScannerSeesResolvedTool(t *testing.T) {
	f := newVSFixture(t, `{"mcp":{"virtual_servers":{"eng":{"id":"`+vsEng+`","members":["git_hub"],"access":{"default_for_org":true}}}}}`)
	registerTools(f.Server, "git_hub", "search")
	f.SetGuard(blockingGuard{tool: "git_hub__search", server: "git_hub"})
	resp := f.callResult(t, "", "git_hub__search", nil)
	if resp.Error == nil || !strings.Contains(resp.Error.Message, "guardrail: tool is blocked") {
		t.Fatalf("a call resolved through a virtual server answered %s %+v, want the guard's block", resp.Result, resp.Error)
	}

	// The scanner step also sees the upstream tool every call resolved to,
	// for the exposed names a guard cannot read back to it (an override's, an
	// account member's, a hash-capped and a normalized one), so the guard can
	// judge the call by it.
	f.SetGuard(nil)
	long := strings.Repeat("d", 62)
	registerTools(f.Server, "github", "delete_repo", "create_issue", long, "list repos")
	var seen string
	f.hookObserver = func(hook string, cc *CallContext) {
		if hook == hookScanArgs {
			seen = cc.Upstream.ServerID + "/" + cc.Tool.UpstreamName
		}
	}
	prefixed := `{"mcp":{"virtual_servers":{"eng":{"id":"` + vsEng + `","members":["github","github@work"],"access":{"default_for_org":true},
		"overrides":{"github/delete_repo":{"name":"remove_repo"}}}}}}`
	flat := `{"mcp":{"virtual_servers":{"eng":{"id":"` + vsEng + `","members":["github"],"naming":"flat","access":{"default_for_org":true}}}}}`
	for _, tc := range []struct{ cfg, exposed, upstream string }{
		{prefixed, "remove_repo", "github/delete_repo"},
		{prefixed, "github_work__create_issue", "github/create_issue"},
		{prefixed, vserver.EncodePrefixed("github", long), "github/" + long},
		{prefixed, "github__list_repos", "github/list repos"},
		{flat, "list_repos", "github/list repos"},
	} {
		f.cfg = orgConfig(t, tc.cfg)
		seen = ""
		f.callResult(t, "", tc.exposed, nil) // fails upstream (no client), after the scanner step
		if seen != tc.upstream {
			t.Errorf("calling %s: the scanner step saw upstream %q, want %q", tc.exposed, seen, tc.upstream)
		}
	}
}

func TestAccountQualifiedMemberNames(t *testing.T) {
	f := newVSFixture(t, `{"mcp":{"virtual_servers":{"eng":{"id":"`+vsEng+`","members":["github@work","github"],"access":{"default_for_org":true}}}}}`)
	registerTools(f.Server, "github", "create_issue")
	if got := strings.Join(f.toolNames(t, "", nil), ","); got != "github__create_issue,github_work__create_issue" {
		t.Fatalf("list = %s", got)
	}
	var cred, server, upstream string
	f.hookObserver = func(hook string, cc *CallContext) {
		if hook == hookTranslateRetry {
			cred, server, upstream = cc.Credential.Ref, cc.Upstream.ServerID, cc.Tool.UpstreamName
		}
	}
	f.callResult(t, "", "github_work__create_issue", nil)
	if cred != "work" || server != "github" || upstream != "create_issue" {
		t.Fatalf("account call: credential ref %q, server %q, tool %q; want work, github, create_issue", cred, server, upstream)
	}
	f.callResult(t, "", "github__create_issue", nil)
	if cred != "" {
		t.Fatalf("the plain member set credential ref %q", cred)
	}

	// A label whose names could not be read back to their tool (a "__" in
	// it, or "_" at either end, as normalized) exposes nothing: no tool,
	// prompt or resource, and its would-be names are unknown. Status sees
	// each refused tool and prompt (account_label). A "_" inside is fine.
	f.cfg = orgConfig(t, `{"mcp":{"virtual_servers":{"eng":{"id":"`+vsEng+`","access":{"default_for_org":true},
		"members":["github@a_b","github@my__acct","github@_x","github@x_","github@a  b"]}}}}`)
	f.Registry().RegisterPrompts("github", []Prompt{{Name: "p"}})
	f.Registry().RegisterResources("github", []Resource{{URI: "file:///r", Name: "r"}})
	if got := strings.Join(f.toolNames(t, "", nil), ","); got != "github_a_b__create_issue" {
		t.Fatalf("list = %s, want only the a_b account's tool", got)
	}
	for _, name := range []string{"github_my__acct__create_issue", "github__x__create_issue", "github_x___create_issue", "github_a__b__create_issue"} {
		requireUnknownTool(t, f.callResult(t, "", name, nil), name)
	}
	if got := string(decodeResponse(t, f.post(t, "", MethodPromptsList, struct{}{}, nil)).Result); !strings.Contains(got, `"github_a_b__p"`) || strings.Count(got, `"name"`) != 1 {
		t.Fatalf("prompts/list = %s, want only github_a_b__p", got)
	}
	if got := string(decodeResponse(t, f.post(t, "", MethodResourcesList, struct{}{}, nil)).Result); !strings.Contains(got, `"mcp://github_a_b/file:///r"`) || strings.Count(got, `"uri"`) != 1 {
		t.Fatalf("resources/list = %s, want only mcp://github_a_b/file:///r", got)
	}
	if resp := decodeResponse(t, f.post(t, "", MethodResourcesRead, ReadResourceParams{URI: "mcp://github_my__acct/file:///r"}, nil)); resp.Error == nil || resp.Error.Code != -32002 {
		t.Fatalf("reading a refused member's resource = %+v, want not found", resp.Error)
	}
	tools, prompts := f.Registry().Rejections("org-a", mustVS(t, f.cfg, "eng"), true)
	if len(tools) != 4 || len(prompts) != 4 {
		t.Fatalf("rejections: %d tools %+v, %d prompts, want 4 of each", len(tools), tools, len(prompts))
	}
	for _, r := range append(tools, prompts...) {
		if r.Reason != vserver.ReasonAccountLabel || r.Tool.Member.Account == "a_b" {
			t.Fatalf("rejection %+v, want reason account_label for a refused label only", r)
		}
	}
}

// TestOverridesCannotWidenAccess: an override renames or relabels a tool,
// but the lists an operator or admin wrote still judge it by the names an
// override cannot change, and an exclude still hides it.
func TestOverridesCannotWidenAccess(t *testing.T) {
	f := newVSFixture(t, `{"mcp":{"virtual_servers":{"eng":{"id":"`+vsEng+`","members":["github"],"access":{"default_for_org":true,
		"rules":[{"match":{"key_ids":["k1"]},"allow":["github_list_repos","github/list_*"]}]},
		"tools":{"exclude":["github/wipe"]},
		"overrides":{"github/delete_repo":{"name":"github_list_repos"},"github/wipe":{"name":"safe_wipe","hidden":false}}}}}}`)
	registerTools(f.Server, "github", "list_repos", "delete_repo", "wipe")

	// The access rule allows list_repos by its legacy name; delete_repo,
	// renamed to that very name, is not let through by it.
	if got := strings.Join(f.toolNames(t, "", nil), ","); got != "github__list_repos" {
		t.Fatalf("k1 lists %s, want only github__list_repos", got)
	}
	requireDenied(t, f.callResult(t, "", "github_list_repos", nil), "access.rules[0]", vsEng)
	// An override does not bring back an excluded tool.
	requireUnknownTool(t, f.callResult(t, "", "safe_wipe", nil), "safe_wipe")

	// A key's MCP allow list, written in legacy names, is not widened either.
	f.principal.KeyID = "k9" // no access rule matches k9
	f.SetKeyAuth(&testKeyAuth{keys: map[string]struct {
		allowed []string
		denied  []string
	}{"key-x": {allowed: []string{"github_list_repos"}}}})
	auth := map[string]string{"Authorization": "Bearer key-x"}
	f.session = initializeSessionWith(t, f.Server, auth)
	if got := strings.Join(f.toolNames(t, "", auth), ","); got != "github__list_repos" {
		t.Fatalf("key-x lists %s, want only github__list_repos", got)
	}
	requireDenied(t, f.callResult(t, "", "github_list_repos", auth), "key.mcp_allowed_tools", vsEng)
	// A deny matches every name: the legacy name denies list_repos, and the
	// same string, now delete_repo's exposed name, denies it too.
	f.SetKeyAuth(&testKeyAuth{keys: map[string]struct {
		allowed []string
		denied  []string
	}{"key-x": {denied: []string{"github_list_repos"}}}})
	if got := f.toolNames(t, "", auth); len(got) != 0 {
		t.Fatalf("key-x with a deny lists %v, want nothing", got)
	}
	requireDenied(t, f.callResult(t, "", "github_list_repos", auth), "key.mcp_denied_tools", vsEng)
	requireDenied(t, f.callResult(t, "", "github__list_repos", auth), "key.mcp_denied_tools", vsEng)
}

// TestListAndCallAgree: for every principal and request narrowing, a tool is
// listed exactly when a call to it passes the access step. A tool hidden from
// a caller can never be called by name, and nothing listed is refused.
func TestListAndCallAgree(t *testing.T) {
	f := newVSFixture(t, `{"mcp":{"virtual_servers":{"eng":{"id":"`+vsEng+`","members":["github","git@work"],"access":{"default_for_org":true,
		"rules":[{"match":{"key_ids":["k1"]},"allow":["github/*","git@work/x"]},{"match":{"agent_ids":["a1"]},"deny":["*/delete_*"]},{"match":{"user_ids":["u1"]},"deny":["git/*"]}]},
		"tools":{"exclude":["github/secret"]},"overrides":{"github/old":{"name":"renamed"}}}}}}`)
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
			listed := map[string]bool{}
			for _, n := range f.toolNames(t, "", h) {
				listed[n] = true
			}
			for _, name := range all {
				passed = false
				f.callResult(t, "", name, h)
				if passed != listed[name] {
					t.Errorf("%+v header %q: %s listed=%v but call passed access=%v", p, header, name, listed[name], passed)
				}
			}
		}
	}
}

// TestVirtualServerSourceFailsClosed: with no org source installed, or none
// for the org, an org's virtual server lists nothing, so an org server id
// never resolves to the operator's server of the same id.
func TestVirtualServerSourceFailsClosed(t *testing.T) {
	f := newVSFixture(t, `{"mcp":{"access":{"default":"allow"},"servers":{"github":{"url":"https://org.example/mcp"}},
		"virtual_servers":{"eng":{"id":"`+vsEng+`","members":["github","github"],"access":{"default_for_org":true}}}}}`)
	registerTools(f.Server, "github", "operator_tool")
	if got := strings.Join(f.toolNames(t, "", nil), ","); got != "github__operator_tool" {
		t.Fatalf("with the registry as the org source: %s (a member listed twice is one member)", got)
	}
	f.Registry().SetOrgSource(func(org string) MemberSource { return nil })
	if got := f.toolNames(t, "", nil); len(got) != 0 {
		t.Fatalf("an org with no source lists %v", got)
	}
	requireUnknownTool(t, f.callResult(t, "", "github__operator_tool", nil), "github__operator_tool")
	fresh := newTestServer(t)
	defer fresh.Close()
	registerTools(fresh, "github", "operator_tool")
	vs, _ := f.cfg.MCPVirtualServer("eng")
	if v := fresh.Registry().compose("org-a", vs, true); len(v.tools.Entries) != 0 {
		t.Fatalf("with no org source installed the view has %d tools", len(v.tools.Entries))
	}
}
