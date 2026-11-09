package mcp

// Integrator-owned tests for the W3b merge: same-wave seams between lanes that
// no single lane could test, each red before its integration commit.

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/contracts/schematest"
	"github.com/futureagi/agentcc-gateway/internal/mcp/protocol"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// M9 IR-7 (1) with M8: the admin playground (POST /-/mcp/test, M9's
// AdminTestTool) runs the whole tools/call chain as principal admin:<org> on a
// copy of the admin request with no credential. The gateway installs M8's
// identity on every MCP server (wiring_mcp_identity.go), whose hook 1 refuses
// a call whose request never passed authenticateHTTP; it must keep the admin
// principal AdminTestTool set, or every playground call is 401.
func TestAdminTestToolWithGatewayIdentityRunsChain(t *testing.T) {
	s := NewServer(ServerConfig{MaxAgentDepth: 2, ToolCallTimeout: 5 * time.Second, SessionTTL: time.Minute, Separator: "_"})
	defer s.Close()
	s.SetIdentity(Identity{Keys: auth.NewKeyStore(config.AuthConfig{Keys: []config.AuthKeyConfig{
		{Name: "a", Key: "w3b-test-key-org-a", Metadata: map[string]string{"org_id": "org-a"}},
	}})})
	ten, a, b := twoOrgs()
	s.SetTenancy(ten)
	usePoolHooks(t)
	var principals []Principal
	s.hookObserver = func(_ string, cc *CallContext) { principals = append(principals, cc.Principal) }

	req := httptest.NewRequest("POST", "/-/mcp/test", nil)
	req.Header.Set("Authorization", "Bearer the-admin-token")
	res, err := s.AdminTestTool(req, "org-a", "", "github__search", json.RawMessage(`{"q":"x"}`))
	if err != nil || res.Error != "" || res.IsError || res.ServerID != "github" {
		t.Fatalf("admin test with the gateway identity installed: %+v, %v", res, err)
	}
	if got, other := a.called(), b.called(); len(got) != 1 || len(other) != 0 {
		t.Fatalf("upstream calls: org-a %v, org-b %v", got, other)
	}
	for _, p := range principals {
		if p.Kind != PrincipalAdmin || p.OrgID != "org-a" {
			t.Fatalf("a hook saw principal %+v, want admin:org-a", p)
		}
	}

	// The exception is the admin principal only: a chain run on a request
	// that never passed authenticateHTTP, with no principal set, is still 401.
	cc := s.newCallContext(httptest.NewRequest("POST", "/mcp", nil), &Message{Method: MethodToolsCall}, nil)
	cc.Tool.ExposedName = "github_search"
	cc.Args = json.RawMessage(`{}`)
	s.runChain(cc, toolsCallChain)
	cc.runFinalizers()
	if cc.Decision == nil || cc.Decision.Code != AuthMissingCredentials {
		t.Fatalf("a chain call with no principal and no authenticated request: decision %+v", cc.Decision)
	}
	if len(a.called()) != 1 {
		t.Fatalf("the unauthenticated call reached org-a's upstream: %v", a.called())
	}
}

// M10 IR-1 on the live MCP auth path: the gateway installs M8's identity
// (wiring_mcp_identity.go), so the key lists the MCP path applies come from
// identityAuth, not from server.go's mcpKeyAuth. A key's mcp_allowed_tools /
// mcp_denied_tools filter its MCP tools; its /v1 function-tool lists
// (allowed_tools, denied_tools) do so only when both MCP lists are absent.
func TestIdentityAppliesTheKeysMCPToolLists(t *testing.T) {
	s := newTestServer(t)
	t.Cleanup(s.Close)
	s.Registry().RegisterServer("gh", []Tool{
		{Name: "t1", InputSchema: json.RawMessage(`{}`)},
		{Name: "t2", InputSchema: json.RawMessage(`{}`)},
	})
	s.Registry().SetStaticOrg("org-w3b")
	org := map[string]string{"org_id": "org-w3b"}
	ks := auth.NewKeyStore(config.AuthConfig{Keys: []config.AuthKeyConfig{
		// The /v1 list names web_search, the MCP list gh_t1.
		{Name: "split", Key: "w3b-test-key-split", Metadata: org, AllowedTools: []string{"web_search"}, MCPAllowedTools: []string{"gh_t1"}},
		// No MCP lists: the /v1 lists still filter MCP (deprecated fallback).
		{Name: "legacy", Key: "w3b-test-key-legacy", Metadata: org, DeniedTools: []string{"gh_t1"}},
	}})
	s.SetIdentity(Identity{Keys: ks})
	for _, tc := range []struct {
		key, want string
	}{
		{"w3b-test-key-split", "gh_t1"},
		{"w3b-test-key-legacy", "gh_t2"},
	} {
		h := bearer(tc.key)
		h = withHeaders(h, "MCP-Session-Id", initializeSessionWith(t, s, h))
		w := postMCP(t, s, toolsListMsg(), h)
		resp := decodeResponse(t, w)
		if resp.Error != nil {
			t.Fatalf("%s: tools/list error %+v", tc.key, resp.Error)
		}
		var res ListToolsResult
		if err := json.Unmarshal(resp.Result, &res); err != nil {
			t.Fatal(err)
		}
		var names []string
		for _, tool := range res.Tools {
			names = append(names, tool.Name)
		}
		if got := strings.Join(names, ","); got != tc.want {
			t.Fatalf("%s lists %q on /mcp, want %q", tc.key, got, tc.want)
		}
	}
	// The grant identityAuth hands a KeyAuthenticator caller carries the MCP lists too.
	grant, ok := s.keyAuth.AuthenticateKey("w3b-test-key-split")
	if !ok || strings.Join(grant.AllowedTools, ",") != "gh_t1" || len(grant.DeniedTools) != 0 {
		t.Fatalf("identity grant %+v %v, want the MCP allow list [gh_t1]", grant, ok)
	}
}

// M11 IR-2: CallContext.Era is the request's era (frozen chain.go's
// newCallContext), so M10's era-dependent answers (resource-not-found -32602
// on 2026-07-28, -32002 on a legacy era) tell the eras apart. A 2026 call is
// stateless (no stored session, no session id: ruling R1); a legacy one keeps
// its session.
func TestCallContextEraFromTheRequest(t *testing.T) {
	up := startM11Upstream(t, false)
	s, hooks := newM11Server(t, up)
	w := serveM11(s, m11Request(t, MethodToolsCall, map[string]any{"name": "up_weather", "arguments": map[string]any{}}, nil))
	if res := m11Result(t, w); res["structuredContent"] == nil {
		t.Fatalf("2026 call: %v", res)
	}
	if cc := hooks.at(hookCallUpstream); cc.Era != protocol.Version2026 || cc.SessionID != "" {
		t.Fatalf("2026 call: Era %q, SessionID %q; want %q and none", cc.Era, cc.SessionID, protocol.Version2026)
	}
	sid := initializeSession(t, s)
	postMCP(t, s, &Message{JSONRPC: "2.0", ID: []byte(`2`), Method: MethodToolsList}, map[string]string{"MCP-Session-Id": sid, "MCP-Protocol-Version": "2025-06-18"})
	if cc := hooks.at(hookAssembleList); cc.Era != "2025-06-18" || cc.SessionID != sid {
		t.Fatalf("legacy list: Era %q, SessionID %q; want 2025-06-18 and the session", cc.Era, cc.SessionID)
	}
}

// M11 IR-8 (merge-blocking with M10): the 2026-07-28 header check reads a
// tool's inputSchema (which arguments are mirrored as Mcp-Param-*) through
// the caller's own view, the one authorizeTool uses. A tool the caller may
// not use answers like a tool that does not exist, never 400 -32020 on a
// mismatched header, which would tell any key that the tool exists and
// mirrors a parameter. A tool the caller may use is still checked.
func TestHeaderCheckSeesTheCallersView(t *testing.T) {
	f := newVSFixture(t, `{"mcp":{"virtual_servers":{"eng":{"id":"`+vsEng+`","members":["github"],"access":{"default_for_org":true,
		"rules":[{"match":{"key_ids":["k1"]},"deny":["github/denied_geo"]}]},
		"tools":{"exclude":["github/hidden_geo"]}}}}}`)
	geo := json.RawMessage(`{"type":"object","properties":{"region":{"type":"string","x-mcp-header":"Region"}}}`)
	f.Registry().RegisterServer("github", []Tool{
		{Name: "geo", InputSchema: geo},
		{Name: "hidden_geo", InputSchema: geo},
		{Name: "denied_geo", InputSchema: geo},
	})
	call := func(name string) *httptest.ResponseRecorder {
		// The body says region eu, the header us: a mismatch for a tool whose
		// schema marks region.
		return serveM11(f.Server, m11Request(t, MethodToolsCall,
			map[string]any{"name": name, "arguments": map[string]any{"region": "eu"}},
			map[string]string{"Mcp-Param-Region": "us"}))
	}
	// A tool the caller may use: the mismatch is refused before the chain.
	m11Refused(t, call("github__geo"), http.StatusBadRequest, protocol.CodeHeaderMismatch)
	// A tool its view hides answers exactly like one that does not exist.
	missing := call("github__nope")
	if missing.Code == http.StatusBadRequest {
		t.Fatalf("a missing tool: HTTP %d %s", missing.Code, missing.Body.String())
	}
	hidden := call("github__hidden_geo")
	if got, want := strings.ReplaceAll(hidden.Body.String(), "github__hidden_geo", "github__nope"), missing.Body.String(); hidden.Code != missing.Code || got != want {
		t.Fatalf("a hidden tool with a mismatched header answered HTTP %d\n%s\nunlike a missing tool (HTTP %d)\n%s", hidden.Code, hidden.Body.String(), missing.Code, want)
	}
	// A tool an access rule refuses this key: the chain answers its own
	// refusal (tool_denied, in band), not the header check.
	denied := call("github__denied_geo")
	if denied.Code == http.StatusBadRequest || !strings.Contains(denied.Body.String(), "tool_denied") {
		t.Fatalf("a denied tool with a mismatched header answered HTTP %d %s, want the chain's tool_denied", denied.Code, denied.Body.String())
	}
}

// M11 IR-3 (23:00 table): GET and DELETE on the MCP endpoint without an
// MCP-Session-Id answer 405 with Allow: POST (a 2026-07-28 client uses POST
// alone, and only a legacy session has a GET stream or an end); with an
// unknown session they keep M12's 404.
func TestStreamMethodsWithoutASession405(t *testing.T) {
	s := newTestServer(t)
	defer s.Close()
	for _, m := range []string{http.MethodGet, http.MethodDelete} {
		rec := httptest.NewRecorder()
		req := httptest.NewRequest(m, "/mcp", nil)
		if m == http.MethodGet {
			s.HandleSSE(rec, req)
		} else {
			s.HandleDelete(rec, req)
		}
		if rec.Code != http.StatusMethodNotAllowed || rec.Header().Get("Allow") != http.MethodPost {
			t.Fatalf("%s without a session = %d, Allow %q; want 405 with Allow: POST", m, rec.Code, rec.Header().Get("Allow"))
		}
		rec = httptest.NewRecorder()
		req = httptest.NewRequest(m, "/mcp", nil)
		req.Header.Set("MCP-Session-Id", "unknown")
		if m == http.MethodGet {
			s.HandleSSE(rec, req)
		} else {
			s.HandleDelete(rec, req)
		}
		if rec.Code != http.StatusNotFound {
			t.Fatalf("%s on an unknown session = %d, want M12's 404", m, rec.Code)
		}
	}
}

// w3bPool is a TenantPool of fixed servers, each served by one client (nil
// for a server whose client cannot be built).
type w3bPool struct {
	org     string
	servers map[string]PoolServer
	clients map[string]UpstreamClient
}

func (p *w3bPool) OrgID() string { return p.org }
func (p *w3bPool) Servers() []PoolServer {
	out := make([]PoolServer, 0, len(p.servers))
	for _, s := range p.servers {
		out = append(out, s)
	}
	return out
}
func (p *w3bPool) Server(id string) (PoolServer, bool) { s, ok := p.servers[id]; return s, ok }
func (p *w3bPool) Client(id, _ string, _ AuthOverride) (UpstreamClient, error) {
	if c := p.clients[id]; c != nil {
		return c, nil
	}
	return nil, errors.New("connect: connection refused")
}
func (p *w3bPool) CachedList(_ ListCacheKey, build func() []Tool) []Tool { return build() }

// w3bTenancy resolves each org to its config and pool; "" to the static one.
type w3bTenancy struct {
	cfgs  map[string]*tenant.OrgConfig
	pools map[string]TenantPool
}

func (t *w3bTenancy) Resolve(org string) (*tenant.OrgConfig, TenantPool, error) {
	return t.cfgs[org], t.pools[org], nil
}
func (t *w3bTenancy) Known(org string) bool         { _, ok := t.pools[org]; return ok }
func (t *w3bTenancy) ReloadStatic(config.MCPConfig) {}

// w3bOrgFixture is a server with the operator's YAML server "github" on the
// static upstream, an installed tenancy and the org pools as the virtual
// servers' member source, as the wiring installs them. The real step 3
// (resolveTenant) runs, on the principal the fixture sets first.
type w3bOrgFixture struct {
	*Server
	session   string
	principal Principal
}

func newW3BOrgFixture(t *testing.T, ten *w3bTenancy, static *fakeUpstream) *w3bOrgFixture {
	t.Helper()
	f := &w3bOrgFixture{Server: newTestServer(t)}
	t.Cleanup(f.Close)
	f.RegisterClient(connectFake(t, static, ClientConfig{ServerID: "github"}))
	f.SetTenancy(ten)
	t.Cleanup(func() { f.SetTenancy(nil) })
	sep := f.Registry().Separator()
	f.Registry().SetOrgSource(func(org string) MemberSource {
		_, pool, err := ten.Resolve(org)
		if err != nil {
			return nil
		}
		return PoolMembers(pool, sep)
	})
	for _, chain := range vsChains {
		replaceStep(t, chain, hookResolveTenant, func(s *Server, cc *CallContext) *Denial {
			cc.Principal = f.principal
			return resolveTenant(s, cc)
		})
	}
	f.session = initializeSession(t, f.Server)
	return f
}

func (f *w3bOrgFixture) call(t *testing.T, name string) *Message {
	t.Helper()
	vf := &vsFixture{Server: f.Server, session: f.session}
	return vf.callResult(t, "", name, nil)
}

func (f *w3bOrgFixture) list(t *testing.T) []string {
	t.Helper()
	vf := &vsFixture{Server: f.Server, session: f.session}
	return vf.toolNames(t, "", nil)
}

func w3bOrgConfig(t *testing.T) *tenant.OrgConfig {
	t.Helper()
	return orgConfig(t, `{"mcp":{"enabled":true,"access":{"default":"allow"},"servers":{"github":{"url":"https://org.example/mcp"}}}}`)
}

// TestVirtualServerCallGoesThroughTheOrgPool (M9 IR-4 and M10 IR-4): org A
// pushes a server named github, the id of the operator's YAML server. A's key
// lists A's github (from A's pool, through PoolMembers) and its call lands on
// A's upstream, never on the operator's static client of the same id. A
// server A's pool cannot serve is unavailable, with no fallback to the static
// client. The legacy view (a principal with no org, no org config) keeps the
// static client. Before the merge the call went to the static client.
func TestVirtualServerCallGoesThroughTheOrgPool(t *testing.T) {
	static := newFakeUpstream(t, "/mcp", nil)
	orgUp := newFakeUpstream(t, "/mcp", nil)
	orgClient := connectFake(t, orgUp, ClientConfig{ServerID: "github"})
	poolA := &w3bPool{org: "org-a",
		servers: map[string]PoolServer{"github": {ID: "github", Source: "org", Healthy: true, Tools: orgClient.Tools()}},
		clients: map[string]UpstreamClient{"github": AdaptClient(orgClient)}}
	poolB := &w3bPool{org: "org-b", // github listed, but its client cannot be built
		servers: map[string]PoolServer{"github": {ID: "github", Source: "org", Tools: orgClient.Tools()}}}
	ten := &w3bTenancy{
		cfgs:  map[string]*tenant.OrgConfig{"org-a": w3bOrgConfig(t), "org-b": w3bOrgConfig(t)},
		pools: map[string]TenantPool{"org-a": poolA, "org-b": poolB, "": &w3bPool{}},
	}
	f := newW3BOrgFixture(t, ten, static)

	f.principal = Principal{Kind: PrincipalKey, KeyID: "ka", OrgID: "org-a"}
	if got := f.list(t); len(got) != 1 || got[0] != "github__echo" {
		t.Fatalf("org A lists %v, want [github__echo] from its pool", got)
	}
	resp := f.call(t, "github__echo")
	if resp.Error != nil || denial(t, resp) != nil {
		t.Fatalf("org A's call failed: %+v %s", resp.Error, resp.Result)
	}
	if got, op := len(orgUp.requests(MethodToolsCall)), len(static.requests(MethodToolsCall)); got != 1 || op != 0 {
		t.Fatalf("org A's call: %d on A's upstream, %d on the operator's static client; want 1 and 0", got, op)
	}

	f.principal = Principal{Kind: PrincipalKey, KeyID: "kb", OrgID: "org-b"}
	// Today's answer for a server with no usable client: JSON-RPC -32603
	// "upstream server ... is unavailable" (upstreamUnavailable).
	if resp := f.call(t, "github__echo"); resp.Error == nil || resp.Error.Code != ErrCodeInternal {
		t.Fatalf("org B's call with no pool client: %+v %s, want -32603 unavailable", resp.Error, resp.Result)
	}
	if op := len(static.requests(MethodToolsCall)); op != 0 {
		t.Fatalf("org B's call fell back to the operator's static client (%d calls)", op)
	}

	f.principal = Principal{} // no org, no org config: the legacy view
	if resp := f.call(t, "github_echo"); resp.Error != nil || denial(t, resp) != nil {
		t.Fatalf("the legacy view's call failed: %+v %s", resp.Error, resp.Result)
	}
	if op := len(static.requests(MethodToolsCall)); op != 1 {
		t.Fatalf("the legacy view's call: %d on the static client, want 1", op)
	}
}

// TestPoolMembersServeThePoolServersLists (M10 IR-4): the member source over
// a pool serves a server's tools under RegisterServer's legacy names (first of
// a duplicated name kept), its resources and prompts, and nothing for a
// server the pool does not hold; a nil pool is no source.
func TestPoolMembersServeThePoolServersLists(t *testing.T) {
	tools := []Tool{{Name: "search", Raw: json.RawMessage(`{"name":"search","title":"Search"}`)}, {Name: "search"}, {Name: "other"}}
	pool := &w3bPool{servers: map[string]PoolServer{"github": {ID: "github", Tools: tools,
		Resources: []Resource{{URI: "file:///a"}}, Prompts: []Prompt{{Name: "p"}}}}}
	src := PoolMembers(pool, "_")
	got := src.MemberTools("github")
	if len(got) != 2 || got[0].Name != "github_search" || got[0].OrigName != "search" || got[0].Title != "Search" || got[1].Name != "github_other" {
		t.Fatalf("MemberTools = %+v", got)
	}
	if len(src.MemberResources("github")) != 1 || len(src.MemberPrompts("github")) != 1 {
		t.Fatal("resources or prompts missing")
	}
	if src.MemberTools("absent") != nil || src.MemberResources("absent") != nil || src.MemberPrompts("absent") != nil {
		t.Fatal("a server the pool does not hold has members")
	}
	if PoolMembers(nil, "_") != nil {
		t.Fatal("a nil pool must be no source (the registry's fail-closed default)")
	}
}

// TestLegacyClientGetsNo2026EnvelopeFromA2026Upstream (M11 with M13): M13's
// client reaches a dual-era upstream as 2026-07-28, whose results carry the
// 2026 envelope (resultType, ttlMs, cacheScope, _meta serverInfo). A legacy
// client must get "the same raw result and no 2026 fields" (scenario 67 step
// 6): the legacy answer drops those top-level members and keeps every other
// one, the upstream's other _meta keys and structuredContent included; a
// 2026 client still gets the gateway's own envelope. Before the merge's fix
// the upstream's resultType reached the legacy client.
func TestLegacyClientGetsNo2026EnvelopeFromA2026Upstream(t *testing.T) {
	up := newFakeUpstream(t, "/mcp", func(f *fakeUpstream) {
		f.era2026 = true
		f.onCall = func(fakeRequest, func(any)) (any, *RPCError) {
			return map[string]any{
				"content":           []any{map[string]any{"type": "text", "text": "ok"}},
				"structuredContent": map[string]any{"ttlMs": 5, "resultType": "nested stays"},
				"resultType":        "complete", "ttlMs": 1000, "cacheScope": "public",
				"_meta": map[string]any{"io.modelcontextprotocol/serverInfo": map[string]any{"name": "upstream"}, "vendor/x": 1},
			}, nil
		}
	})
	s := newTestServer(t)
	defer s.Close()
	s.RegisterClient(connectFake(t, up, ClientConfig{ServerID: "up"}))
	session := initializeSession(t, s)
	resp := decodeResponse(t, postMCP(t, s, &Message{JSONRPC: "2.0", ID: json.RawMessage(`2`), Method: MethodToolsCall,
		Params: json.RawMessage(`{"name":"up_echo","arguments":{}}`)}, map[string]string{"MCP-Session-Id": session}))
	if resp.Error != nil {
		t.Fatalf("legacy tools/call: %+v", resp.Error)
	}
	var res map[string]json.RawMessage
	if err := json.Unmarshal(resp.Result, &res); err != nil {
		t.Fatal(err)
	}
	for _, k := range []string{"resultType", "ttlMs", "cacheScope"} {
		if _, ok := res[k]; ok {
			t.Errorf("the legacy answer carries the 2026 member %s: %s", k, resp.Result)
		}
	}
	if !strings.Contains(string(res["_meta"]), `"vendor/x":1`) || strings.Contains(string(res["_meta"]), "serverInfo") {
		t.Errorf("_meta = %s, want the upstream's vendor key without its serverInfo", res["_meta"])
	}
	if !strings.Contains(string(res["structuredContent"]), `"nested stays"`) {
		t.Errorf("structuredContent changed: %s", res["structuredContent"])
	}
}

// bootStaticPool is how the route setup (routes_mcp.go) connects the YAML
// servers at boot.
func bootStaticPool(ctx context.Context, cfg *config.Config) []*Client {
	return BuildStaticPool(ctx, cfg.MCP, OperatorRedactor(cfg.Privacy))
}

// TestBootStaticPoolRedactsStdioStderr (M9 IR-12, privacy): the static stdio
// servers the route setup connects at boot, before the tenancy wiring, log
// their stderr through the operator's redactor (privacy.redact_patterns),
// like every client built later. Before the merge's fix the route setup built
// them with no redactor and the operator's pattern was logged in clear.
func TestBootStaticPoolRedactsStdioStderr(t *testing.T) {
	logs := captureSlog(t)
	command, args := stdioHelperCommand()
	cfg := &config.Config{
		MCP: config.MCPConfig{Stdio: config.MCPStdioConfig{Enabled: true}, Servers: map[string]config.MCPServerConfig{
			"boot-stdio": {Transport: "stdio", Command: command, Args: args},
		}},
		Privacy: config.PrivacyConfig{Enabled: true, Mode: "patterns",
			Patterns: []config.RedactPatternConfig{{Name: "email", Pattern: `[a-z]+@example\.com`}}},
	}
	clients := bootStaticPool(context.Background(), cfg)
	if len(clients) != 1 {
		t.Fatalf("boot connected %d static clients, want 1", len(clients))
	}
	c := clients[0]
	t.Cleanup(func() { _ = c.Close() })
	if _, err := callText(t, c, "stderr", map[string]interface{}{"lines": []string{"user ana@example.com logged in"}}); err != nil {
		t.Fatalf("stderr tool: %v", err)
	}
	var recs []logRecord
	for deadline := time.Now().Add(5 * time.Second); time.Now().Before(deadline); time.Sleep(10 * time.Millisecond) {
		recs = logs()
		done := false
		for _, r := range recs {
			done = done || r.attrs["line"] == "stderr-done"
		}
		if done {
			break
		}
	}
	saw := false
	for _, r := range recs {
		if r.msg != "mcp stdio stderr" {
			continue
		}
		if strings.Contains(r.attrs["line"], "ana@example.com") {
			t.Errorf("a boot-time static stdio server logged the operator's pattern in clear: %s", r.attrs["line"])
		}
		saw = saw || strings.Contains(r.attrs["line"], "[REDACTED:email]")
	}
	if !saw {
		t.Error("no redacted stderr line logged")
	}
}

// TestHiddenToolRecordMatchesTheSchema (M15 IR-1): the record of a call to a
// hidden or unknown tool (hook 6's -32602, outcome denied, error_code
// unknown_tool) has no server and no upstream tool, as M15's contract says,
// and validates against mcp-tool-call-v1, whose server_id and upstream_tool
// may now be empty. Before the merge's schema change it did not validate.
func TestHiddenToolRecordMatchesTheSchema(t *testing.T) {
	h := newMeterHarness(t)
	h.onHook(attribute("full"))
	h.call(t, "up_does_not_exist", map[string]any{"probe": true})
	rec := h.sink.only(t)
	if rec.ServerID != "" || rec.UpstreamTool != "" || rec.ErrorCode != "unknown_tool" {
		t.Fatalf("hidden-tool record: server %q, upstream tool %q, code %q", rec.ServerID, rec.UpstreamTool, rec.ErrorCode)
	}
	assertGolden(t, rec)
}

// TestToolCallRecordTagsMatchTheSchema (M15 IR-2): record.go's own JSON tags,
// with no MarshalJSON of M15's, write the shape mcp-tool-call-v1 asks for: an
// empty error_class and error_origin, and every empty agent field, turn
// included, are absent, not "" or null. Before the merge's tag change the
// plain encoding broke the schema (error_class "", agent.step "", agent.turn
// null) and M15's MarshalJSON rewrote it.
func TestToolCallRecordTagsMatchTheSchema(t *testing.T) {
	h := newMeterHarness(t)
	h.onHook(func(hook string, cc *CallContext) { // org-a's key and the era, no agent context
		switch hook {
		case hookResolvePrincipal:
			cc.Principal = Principal{Kind: PrincipalKey, KeyID: "key_7f3a", OrgID: "org-a"}
		case hookParseAgent:
			cc.Era, cc.ProtocolVersion = "2025-11-25", "2025-11-25"
		}
	})
	h.call(t, "up_ok", nil)
	rec := h.sink.only(t)
	type plain ToolCallRecord
	p := plain(rec)
	for _, list := range []*[]string{&p.Guardrail.Checked, &p.Guardrail.Blocked, &p.Guardrail.Masked, &p.Guardrail.Shadow} {
		if *list == nil {
			*list = []string{}
		}
	}
	raw, err := json.Marshal(p)
	if err != nil {
		t.Fatal(err)
	}
	var m map[string]any
	if err := json.Unmarshal(raw, &m); err != nil {
		t.Fatal(err)
	}
	m["kind"] = EventKindToolCall
	if errs := schematest.Validate(t, "mcp-tool-call-v1.schema.json", m); len(errs) > 0 {
		t.Fatalf("the plain record %s does not match mcp-tool-call-v1:\n%s", raw, strings.Join(errs, "\n"))
	}
}
