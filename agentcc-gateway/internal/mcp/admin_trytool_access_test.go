package mcp

import (
	"context"
	"encoding/json"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// The console's "Try a tool" (POST /-/mcp/test, AdminTestTool) on an org that
// turned MCP on and added a server, with no virtual server and no mcp.access
// block: the compatibility view's access.default is deny, and it governs keys
// bound to no virtual server, not the admin's own test. These tests run the
// real chain, authorizeTool included (usePoolHooks replaces it).

const tryToolKey = "trytool-test-key-org-a"

// newOrgConfig is what the console writes for a new org: MCP on and one
// server, nothing else.
const newOrgConfig = `{"mcp":{"enabled":true,"servers":{"github":{"url":"https://org.example/mcp"}}}}`

// tryToolFixture is a server with the gateway identity (one key of org-a), a
// tenancy whose org-a pool serves the fake upstream's echo as server github,
// and the pools as the virtual servers' member source, as the wiring installs
// them. No step is replaced.
type tryToolFixture struct {
	*Server
	ten  *w3bTenancy
	up   *fakeUpstream
	key  map[string]string // a network caller's headers: its key and session
	rule string            // metadata.rule of the last admin call's refusal
}

func newTryToolFixture(t *testing.T, cfg string) *tryToolFixture {
	t.Helper()
	up := newFakeUpstream(t, "/mcp", nil)
	client := connectFake(t, up, ClientConfig{ServerID: "github"})
	pool := &w3bPool{org: "org-a",
		servers: map[string]PoolServer{"github": {ID: "github", Source: "org", Healthy: true, Tools: client.Tools()}},
		clients: map[string]UpstreamClient{"github": AdaptClient(client)}}
	ten := &w3bTenancy{cfgs: map[string]*tenant.OrgConfig{"org-a": orgConfig(t, cfg)}, pools: map[string]TenantPool{"org-a": pool}}
	f := &tryToolFixture{Server: newTestServer(t), ten: ten, up: up}
	t.Cleanup(f.Close)
	f.SetIdentity(Identity{Keys: auth.NewKeyStore(config.AuthConfig{Keys: []config.AuthKeyConfig{
		{Name: "a", Key: tryToolKey, Metadata: map[string]string{"org_id": "org-a"}},
	}})})
	f.SetTenancy(ten)
	t.Cleanup(func() { f.SetTenancy(nil) })
	sep := f.Registry().Separator()
	f.Registry().SetOrgSource(func(org string) MemberSource {
		_, p, _ := ten.Resolve(org)
		return PoolMembers(p, sep)
	})
	f.hookObserver = func(hook string, cc *CallContext) {
		if hook != hookEvidenceMeta || cc.Principal.Kind != PrincipalAdmin {
			return
		}
		f.rule = ""
		if d := cc.Decision; d != nil {
			meta, _ := d.Metadata.(map[string]any)
			f.rule, _ = meta["rule"].(string)
		}
	}
	f.key = bearer(tryToolKey)
	f.key = withHeaders(f.key, "MCP-Session-Id", initializeSessionWith(t, f.Server, f.key))
	return f
}

// adminRun is the console's Try a tool on github__echo, with headers on the
// admin request.
func (f *tryToolFixture) adminRun(t *testing.T, headers map[string]string) TestToolResult {
	t.Helper()
	req := httptest.NewRequest("POST", "/-/mcp/test", nil)
	req.Header.Set("Authorization", "Bearer the-admin-token")
	for k, v := range headers {
		req.Header.Set(k, v)
	}
	res, err := f.AdminTestTool(req, "org-a", "", "github__echo", json.RawMessage(`{}`))
	if err != nil {
		t.Fatalf("admin test: %v", err)
	}
	return res
}

func (f *tryToolFixture) upstreamCalls() int { return len(f.up.requests(MethodToolsCall)) }

func TestAdminTryToolRunsOnANewOrgWithNoAccessBlock(t *testing.T) {
	f := newTryToolFixture(t, newOrgConfig)

	res := f.adminRun(t, nil)
	if res.Error != "" || res.Code != "" || res.IsError || res.ServerID != "github" {
		t.Fatalf("Try a tool on a new org: %+v (rule %q), want the tool's answer", res, f.rule)
	}
	if len(res.Content) != 1 || res.Content[0].Text != "called echo" || f.upstreamCalls() != 1 {
		t.Fatalf("content %+v, %d upstream calls; want the upstream's answer to one call", res.Content, f.upstreamCalls())
	}

	// A network key bound to no virtual server is still refused by
	// access.default=deny: it lists nothing and its call is tool_denied.
	resp := decodeResponse(t, postMCP(t, f.Server, toolsListMsg(), f.key))
	var list ListToolsResult
	if resp.Error != nil || json.Unmarshal(resp.Result, &list) != nil || len(list.Tools) != 0 {
		t.Fatalf("the key's tools/list = %s %+v, want no tools", resp.Result, resp.Error)
	}
	call := &Message{JSONRPC: "2.0", ID: json.RawMessage(`3`), Method: MethodToolsCall, Params: json.RawMessage(`{"name":"github__echo","arguments":{}}`)}
	requireDenied(t, decodeResponse(t, postMCP(t, f.Server, call, f.key)), "access.default", "")
	if f.upstreamCalls() != 1 {
		t.Fatalf("the refused key call reached the upstream (%d calls)", f.upstreamCalls())
	}
	// tools/list keeps access.default for every principal: the admin's
	// served read (what such a key is served) lists nothing either.
	served, err := f.AdminServedTools(context.Background(), "org-a", "", "")
	if err != nil || len(served.Tools) != 0 {
		t.Fatalf("served tools %+v %v, want none under access.default=deny", served, err)
	}
}

func TestAdminTryToolStillAppliesEveryOtherRule(t *testing.T) {
	f := newTryToolFixture(t, newOrgConfig)
	refused := func(t *testing.T, res TestToolResult, rule string) {
		t.Helper()
		if res.Code != "tool_denied" || f.rule != rule {
			t.Fatalf("Try a tool: %+v, rule %q; want tool_denied by %s", res, f.rule, rule)
		}
		if f.upstreamCalls() != 0 {
			t.Fatalf("a refused admin test reached the upstream (%d calls)", f.upstreamCalls())
		}
	}

	// The request's x-agentcc-mcp-allow-tools header still narrows.
	refused(t, f.adminRun(t, map[string]string{HeaderMCPAllowTools: "github__other"}), ruleHeader)

	// The tool policy still runs.
	f.SetDeps(Deps{ToolPolicy: &fakePolicy{decide: func(*CallContext) PolicyDecision {
		return PolicyDecision{Effect: PolicyDeny, RuleID: "no-echo"}
	}}})
	refused(t, f.adminRun(t, nil), "policy:no-echo")
	f.SetDeps(Deps{})

	// A virtual server's access rules still refuse.
	f.ten.cfgs["org-a"] = orgConfig(t, `{"mcp":{"enabled":true,"servers":{"github":{"url":"https://org.example/mcp"}},
		"virtual_servers":{"eng":{"id":"`+vsEng+`","members":["github"],"access":{"default_for_org":true,"rules":[{"deny":["github/echo"]}]}}}}}`)
	refused(t, f.adminRun(t, nil), "access.rules[0]")
}

// An org that stored mcp.access.default itself, through the org-config API:
// its own decision for keys bound to no virtual server, which a stored "deny"
// extends to the admin's Try a tool too (the org's kill switch for tool
// calls). The admin's exemption covers only an org with no stored default.
const (
	storedDenyConfig  = `{"mcp":{"enabled":true,"access":{"default":"deny"},"servers":{"github":{"url":"https://org.example/mcp"}}}}`
	storedAllowConfig = `{"mcp":{"enabled":true,"access":{"default":"allow"},"servers":{"github":{"url":"https://org.example/mcp"}}}}`
	// wantStoredDenyRefusal is what the admin's refused Try a tool says.
	wantStoredDenyRefusal = "This organization's MCP access default is Deny, so tools can't run here. Add this server to a virtual server, or change mcp.access.default in the organization config."
)

// MCPAccess tells a stored default from the deny it falls back to.
func TestMCPAccessSaysWhetherTheDefaultIsStored(t *testing.T) {
	for _, tc := range []struct {
		cfg    string
		def    string
		stored bool
	}{
		{newOrgConfig, "deny", false},
		{storedDenyConfig, "deny", true},
		{storedAllowConfig, "allow", true},
		{`{"mcp":{"enabled":true,"access":{},"servers":{"github":{"url":"https://org.example/mcp"}}}}`, "deny", false},
	} {
		a := orgConfig(t, tc.cfg).MCPAccess()
		if a.Default != tc.def || a.Stored != tc.stored {
			t.Errorf("%s: MCPAccess() = %+v, want default %q stored %v", tc.cfg, a, tc.def, tc.stored)
		}
	}
}

func TestAdminTryToolIsRefusedUnderAStoredDeny(t *testing.T) {
	f := newTryToolFixture(t, storedDenyConfig)

	res := f.adminRun(t, nil)
	if res.Code != "tool_denied" || f.rule != ruleAccessDefault || f.upstreamCalls() != 0 {
		t.Fatalf("Try a tool under a stored deny: %+v, rule %q, %d upstream calls; want tool_denied by access.default and no call", res, f.rule, f.upstreamCalls())
	}
	if !strings.Contains(res.Error, wantStoredDenyRefusal) {
		t.Errorf("the refusal says %q, want it to name the setting and how to change it: %q", res.Error, wantStoredDenyRefusal)
	}

	// A network key bound to no virtual server is refused as before, with the
	// refusal keys always got.
	resp := decodeResponse(t, postMCP(t, f.Server, toolsListMsg(), f.key))
	var list ListToolsResult
	if resp.Error != nil || json.Unmarshal(resp.Result, &list) != nil || len(list.Tools) != 0 {
		t.Fatalf("the key's tools/list = %s %+v, want no tools", resp.Result, resp.Error)
	}
	call := &Message{JSONRPC: "2.0", ID: json.RawMessage(`3`), Method: MethodToolsCall, Params: json.RawMessage(`{"name":"github__echo","arguments":{}}`)}
	keyResp := decodeResponse(t, postMCP(t, f.Server, call, f.key))
	requireDenied(t, keyResp, ruleAccessDefault, "")
	if strings.Contains(string(keyResp.Result), "organization config") {
		t.Errorf("the key's refusal changed: %s", keyResp.Result)
	}
	if f.upstreamCalls() != 0 {
		t.Fatalf("a refused call reached the upstream (%d calls)", f.upstreamCalls())
	}
	served, err := f.AdminServedTools(context.Background(), "org-a", "", "")
	if err != nil || len(served.Tools) != 0 {
		t.Fatalf("served tools %+v %v, want none under a stored deny", served, err)
	}
}

func TestAdminTryToolAndKeysRunUnderAStoredAllow(t *testing.T) {
	f := newTryToolFixture(t, storedAllowConfig)

	res := f.adminRun(t, nil)
	if res.Error != "" || res.Code != "" || res.IsError || len(res.Content) != 1 || res.Content[0].Text != "called echo" {
		t.Fatalf("Try a tool under a stored allow: %+v (rule %q), want the tool's answer", res, f.rule)
	}

	resp := decodeResponse(t, postMCP(t, f.Server, toolsListMsg(), f.key))
	var list ListToolsResult
	if resp.Error != nil || json.Unmarshal(resp.Result, &list) != nil || len(list.Tools) != 1 || list.Tools[0].Name != "github__echo" {
		t.Fatalf("the key's tools/list = %s %+v, want github__echo", resp.Result, resp.Error)
	}
	call := &Message{JSONRPC: "2.0", ID: json.RawMessage(`3`), Method: MethodToolsCall, Params: json.RawMessage(`{"name":"github__echo","arguments":{}}`)}
	keyResp := decodeResponse(t, postMCP(t, f.Server, call, f.key))
	if keyResp.Error != nil || denial(t, keyResp) != nil || f.upstreamCalls() != 2 {
		t.Fatalf("the key's call = %s %+v, %d upstream calls; want it run", keyResp.Result, keyResp.Error, f.upstreamCalls())
	}
	served, err := f.AdminServedTools(context.Background(), "org-a", "", "")
	if err != nil || len(served.Tools) != 1 {
		t.Fatalf("served tools %+v %v, want github__echo", served, err)
	}
}
