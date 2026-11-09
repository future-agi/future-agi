package mcp

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"slices"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/mcp/vserver"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

func TestServerSessionCount(t *testing.T) {
	s := newTestServer(t)
	defer s.Close()

	if s.SessionCount() != 0 {
		t.Fatal("expected 0 sessions")
	}

	initializeSession(t, s)
	initializeSession(t, s)

	if s.SessionCount() != 2 {
		t.Fatalf("expected 2 sessions, got %d", s.SessionCount())
	}
}

func TestServerClientStatuses(t *testing.T) {
	s := newTestServer(t)
	defer s.Close()

	statuses := s.ClientStatuses()
	if len(statuses) != 0 {
		t.Fatal("expected 0 client statuses")
	}
}

func TestTestTool_Success(t *testing.T) {
	s := newTestServer(t)
	defer s.Close()

	tools := []Tool{{Name: "echo", Description: "Echo tool", InputSchema: json.RawMessage(`{}`)}}
	s.Registry().RegisterServer("mock", tools)

	mockUpstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var msg Message
		json.NewDecoder(r.Body).Decode(&msg)
		result := ToolCallResult{Content: []ContentPart{{Type: "text", Text: "echoed"}}}
		resp, _ := NewResponse(msg.ID, result)
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(resp)
	}))
	defer mockUpstream.Close()

	client := NewClient(ClientConfig{ServerID: "mock", URL: mockUpstream.URL, TransportType: "http"})
	client.transport = NewHTTPTransport(mockUpstream.URL, AuthConfig{})
	client.healthy.Store(true)
	client.tools.Store(&tools)
	s.clientsMu.Lock()
	s.clients["mock"] = client
	s.clientsMu.Unlock()

	ctx := context.Background()
	result := s.TestTool(ctx, "mock_echo", map[string]interface{}{"input": "hello"})

	if result.Error != "" {
		t.Fatalf("expected no error, got: %s", result.Error)
	}
	if result.IsError {
		t.Fatal("expected IsError=false")
	}
	if len(result.Content) != 1 || result.Content[0].Text != "echoed" {
		t.Fatalf("unexpected content: %+v", result.Content)
	}
	if result.DurationMs <= 0 {
		t.Fatal("expected positive duration")
	}
	if result.Server != "mock" {
		t.Fatalf("expected server 'mock', got %s", result.Server)
	}
	if result.GuardrailPre != "skipped" || result.GuardrailPost != "skipped" {
		t.Fatalf("expected guardrails skipped (no guard set), got pre=%s post=%s", result.GuardrailPre, result.GuardrailPost)
	}
}

func TestTestTool_NotFound(t *testing.T) {
	s := newTestServer(t)
	defer s.Close()

	result := s.TestTool(context.Background(), "nonexistent_tool", nil)
	if result.Error == "" {
		t.Fatal("expected error for non-existent tool")
	}
}

func TestTestTool_GuardrailBlocks(t *testing.T) {
	s := newTestServer(t)
	defer s.Close()

	s.SetGuard(&testGuard{blockedTools: map[string]bool{"mock_dangerous": true}})

	tools := []Tool{{Name: "dangerous", Description: "Dangerous"}}
	s.Registry().RegisterServer("mock", tools)

	// No upstream needed — guardrail blocks before reaching upstream.

	result := s.TestTool(context.Background(), "mock_dangerous", map[string]interface{}{})
	if result.Error == "" {
		t.Fatal("expected error from guardrail block")
	}
	if result.GuardrailPre != "blocked" {
		t.Fatalf("expected guardrail_pre='blocked', got %s", result.GuardrailPre)
	}
}

func TestTestTool_GuardrailPasses(t *testing.T) {
	s := newTestServer(t)
	defer s.Close()

	s.SetGuard(&testGuard{}) // Guard that blocks nothing

	tools := []Tool{{Name: "safe", Description: "Safe tool", InputSchema: json.RawMessage(`{}`)}}
	s.Registry().RegisterServer("mock", tools)

	mockUpstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var msg Message
		json.NewDecoder(r.Body).Decode(&msg)
		result := ToolCallResult{Content: []ContentPart{{Type: "text", Text: "ok"}}}
		resp, _ := NewResponse(msg.ID, result)
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(resp)
	}))
	defer mockUpstream.Close()

	client := NewClient(ClientConfig{ServerID: "mock", URL: mockUpstream.URL, TransportType: "http"})
	client.transport = NewHTTPTransport(mockUpstream.URL, AuthConfig{})
	client.healthy.Store(true)
	client.tools.Store(&tools)
	s.clientsMu.Lock()
	s.clients["mock"] = client
	s.clientsMu.Unlock()

	result := s.TestTool(context.Background(), "mock_safe", map[string]interface{}{})
	if result.Error != "" {
		t.Fatalf("expected success, got: %s", result.Error)
	}
	if result.GuardrailPre != "pass" {
		t.Fatalf("expected guardrail_pre='pass', got %s", result.GuardrailPre)
	}
	if result.GuardrailPost != "pass" {
		t.Fatalf("expected guardrail_post='pass', got %s", result.GuardrailPost)
	}
}

func TestTestTool_GuardrailBlocksOutput(t *testing.T) {
	s := newTestServer(t)
	defer s.Close()

	s.SetGuard(&testGuard{blockOutput: true})

	tools := []Tool{{Name: "echo", Description: "Echo", InputSchema: json.RawMessage(`{}`)}}
	s.Registry().RegisterServer("mock", tools)

	mockUpstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var msg Message
		json.NewDecoder(r.Body).Decode(&msg)
		result := ToolCallResult{Content: []ContentPart{{Type: "text", Text: "bad content"}}}
		resp, _ := NewResponse(msg.ID, result)
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(resp)
	}))
	defer mockUpstream.Close()

	client := NewClient(ClientConfig{ServerID: "mock", URL: mockUpstream.URL, TransportType: "http"})
	client.transport = NewHTTPTransport(mockUpstream.URL, AuthConfig{})
	client.healthy.Store(true)
	client.tools.Store(&tools)
	s.clientsMu.Lock()
	s.clients["mock"] = client
	s.clientsMu.Unlock()

	result := s.TestTool(context.Background(), "mock_echo", map[string]interface{}{})
	if result.Error == "" {
		t.Fatal("expected error from post-guardrail block")
	}
	if result.GuardrailPost != "blocked" {
		t.Fatalf("expected guardrail_post='blocked', got %s", result.GuardrailPost)
	}
}

// --- M9: org tenancy through the chain and the admin API ---

// recordingClient is one org's upstream: it records the tools it was called
// with and answers with the org's name.
type recordingClient struct {
	org   string
	mu    sync.Mutex
	calls []string
}

func (c *recordingClient) called() []string {
	c.mu.Lock()
	defer c.mu.Unlock()
	return append([]string(nil), c.calls...)
}

func (c *recordingClient) ListTools(context.Context) ([]json.RawMessage, error) { return nil, nil }
func (c *recordingClient) CallTool(_ context.Context, name string, _ json.RawMessage, _ CallOptions) (json.RawMessage, error) {
	c.mu.Lock()
	c.calls = append(c.calls, name)
	c.mu.Unlock()
	return json.RawMessage(`{"content":[{"type":"text","text":"` + c.org + ":" + name + `"}]}`), nil
}
func (c *recordingClient) ListResources(context.Context) ([]json.RawMessage, error) { return nil, nil }
func (c *recordingClient) ReadResource(context.Context, string, CallOptions) (json.RawMessage, error) {
	return nil, nil
}
func (c *recordingClient) ListPrompts(context.Context) ([]json.RawMessage, error) { return nil, nil }
func (c *recordingClient) GetPrompt(context.Context, string, json.RawMessage, CallOptions) (json.RawMessage, error) {
	return nil, nil
}
func (c *recordingClient) Healthy() bool { return true }
func (c *recordingClient) Close() error  { return nil }

// fakePool is a TenantPool with one server per entry of clients.
type fakePool struct {
	org     string
	servers []PoolServer
	clients map[string]*recordingClient
}

func (p *fakePool) OrgID() string         { return p.org }
func (p *fakePool) Servers() []PoolServer { return p.servers }
func (p *fakePool) Server(id string) (PoolServer, bool) {
	for _, s := range p.servers {
		if s.ID == id {
			return s, true
		}
	}
	return PoolServer{}, false
}
func (p *fakePool) Client(id, _ string, _ AuthOverride) (UpstreamClient, error) {
	if c, ok := p.clients[id]; ok {
		return c, nil
	}
	return nil, errors.New("not in this pool")
}
func (p *fakePool) CachedList(_ ListCacheKey, build func() []Tool) []Tool { return build() }

// fakeTenancy resolves each org to its fake pool, or to an error or panic.
type fakeTenancy struct {
	pools     map[string]*fakePool
	snapshots map[string]*tenant.OrgConfig
	fail      map[string]error
	panicOrg  string
}

func (f *fakeTenancy) Known(org string) bool         { _, ok := f.pools[org]; return ok || f.fail[org] != nil }
func (f *fakeTenancy) ReloadStatic(config.MCPConfig) {}
func (f *fakeTenancy) Resolve(org string) (*tenant.OrgConfig, TenantPool, error) {
	if org == f.panicOrg {
		panic("resolver bug")
	}
	if err := f.fail[org]; err != nil {
		return nil, nil, err
	}
	p, ok := f.pools[org]
	if !ok {
		return nil, &fakePool{org: org}, nil
	}
	return f.snapshots[org], p, nil
}

// twoOrgs is a tenancy in which org-a and org-b each have a server named
// github with their own tool and upstream.
func twoOrgs() (*fakeTenancy, *recordingClient, *recordingClient) {
	a, b := &recordingClient{org: "org-a"}, &recordingClient{org: "org-b"}
	pool := func(org, tool string, c *recordingClient) *fakePool {
		return &fakePool{org: org, clients: map[string]*recordingClient{"github": c},
			servers: []PoolServer{{ID: "github", Source: "org", Healthy: true, Tools: []Tool{{Name: tool, Raw: json.RawMessage(`{"name":"` + tool + `","annotations":{"readOnlyHint":true}}`)}}}}}
	}
	snapA := &tenant.OrgConfig{Meta: &tenant.ConfigMeta{Version: 7}, MCP: &tenant.MCPOrgConfig{Enabled: true,
		VirtualServers: map[string]*tenant.MCPVirtualServerConfig{"eng": {ID: "vs_eng", Members: []string{"github@work"}}}}}
	return &fakeTenancy{
		pools:     map[string]*fakePool{"org-a": pool("org-a", "search", a), "org-b": pool("org-b", "delete_repo", b)},
		snapshots: map[string]*tenant.OrgConfig{"org-a": snapA, "org-b": {MCP: &tenant.MCPOrgConfig{Enabled: true}}},
		fail:      map[string]error{},
	}, a, b
}

// poolAuthorize and poolCallUpstream stand in for M10's and M13's hooks,
// which read the principal's pool since the W3b merge (PoolMembers and
// callClient): they resolve and call only through TenantPoolOf(cc), by the
// admin API's virtual-server name (adminToolName), so these tests need no
// composition and no mcp.Client. The real hooks are proved on a pool in
// TestVirtualServerCallGoesThroughTheOrgPool and by scenario 65 step 7.
func poolAuthorize(_ *Server, cc *CallContext) *Denial {
	observeHook(cc, hookAuthorizeTool)
	if pool := TenantPoolOf(cc); pool != nil {
		for _, s := range pool.Servers() {
			for _, tool := range s.Tools {
				if vserver.EncodePrefixed(s.ID, tool.Name) == cc.Tool.ExposedName {
					cc.Upstream.ServerID, cc.Tool.UpstreamName = s.ID, tool.Name
					return nil
				}
			}
		}
	}
	return rpcDenial(ErrCodeMethodNotFound, "tool_not_found", "tool not found: "+cc.Tool.ExposedName)
}

func poolCallUpstream(_ *Server, cc *CallContext) *Denial {
	observeHook(cc, hookCallUpstream)
	c, err := TenantPoolOf(cc).Client(cc.Upstream.ServerID, "", AuthOverride{})
	if err != nil {
		return upstreamUnavailable(cc.Upstream.ServerID)
	}
	cc.Dispatched = true
	raw, err := c.CallTool(cc.Context(), cc.Tool.UpstreamName, cc.Args, CallOptions{})
	if err != nil {
		return upstreamError(err.Error(), "upstream")
	}
	cc.Executed, cc.Result = true, raw
	return nil
}

// usePoolHooks swaps the tools/call chain's resolution and upstream steps
// for the pool-reading stand-ins for the test's duration.
func usePoolHooks(t *testing.T) {
	t.Helper()
	replaceStep(t, toolsCallChain, hookAuthorizeTool, poolAuthorize)
	replaceStep(t, toolsCallChain, hookCallUpstream, poolCallUpstream)
}

// runCall runs a tools/call chain for a principal of org.
func runCall(s *Server, org, name string, headers map[string]string) *CallContext {
	r := httptest.NewRequest("POST", "/mcp", nil)
	for k, v := range headers {
		r.Header.Set(k, v)
	}
	cc := s.newCallContext(r, &Message{Method: MethodToolsCall}, nil)
	cc.Principal = Principal{Kind: PrincipalKey, KeyID: "k-" + org, OrgID: org}
	cc.Tool.ExposedName = name
	cc.Args = json.RawMessage(`{}`)
	s.runChain(cc, toolsCallChain)
	cc.runFinalizers()
	return cc
}

// resolveTenant gives each principal its own org's snapshot and pool, so a
// call reaches only its own org's upstream even when both orgs name their
// server github.
func TestOrgPoolsIsolated(t *testing.T) {
	s := newTestServer(t)
	defer s.Close()
	ten, a, b := twoOrgs()
	s.SetTenancy(ten)
	usePoolHooks(t)

	cc := runCall(s, "org-a", "github__search", nil)
	if cc.Decision != nil || !cc.Executed || string(cc.Result) != `{"content":[{"type":"text","text":"org-a:search"}]}` {
		t.Fatalf("org-a call: decision %v, result %s", cc.Decision, cc.Result)
	}
	if cc.OrgConfig != ten.snapshots["org-a"] || TenantPoolOf(cc).OrgID() != "org-a" {
		t.Fatal("the call does not carry org-a's snapshot and pool")
	}
	if cc := runCall(s, "org-a", "github__delete_repo", nil); cc.Decision == nil || cc.Executed {
		t.Fatal("org-a resolved org-b's tool")
	}
	if got := b.called(); len(got) != 0 {
		t.Fatalf("org-b's upstream got org-a's calls: %v", got)
	}
	if cc := runCall(s, "org-b", "github__delete_repo", nil); cc.Decision != nil || !cc.Executed {
		t.Fatalf("org-b call: %v", cc.Decision)
	}
	if got := a.called(); len(got) != 1 || got[0] != "search" {
		t.Fatalf("org-a's upstream got %v", got)
	}
}

// MCA I10: a policy evaluation error denies the call, never allows it: the
// chain stops at resolveTenant with an in-band policy_unavailable.
func TestPolicyErrorDenies(t *testing.T) {
	s := newTestServer(t)
	defer s.Close()
	ten, a, _ := twoOrgs()
	ten.fail["org-bad"] = errors.New("config cannot be loaded and there is no last-known-good version")
	ten.panicOrg = "org-panic"
	ten.pools["org-panic"] = ten.pools["org-a"]
	ten.pools["org-bad"] = ten.pools["org-a"]
	s.SetTenancy(ten)
	usePoolHooks(t)
	var hooks []string
	s.hookObserver = func(hook string, _ *CallContext) { hooks = append(hooks, hook) }

	for _, org := range []string{"org-bad", "org-panic"} {
		hooks = nil
		cc := runCall(s, org, "github__search", nil)
		if cc.Decision == nil || cc.Decision.Code != CodePolicyUnavailable || !cc.Decision.InBand() {
			t.Fatalf("%s: decision %+v, want an in-band policy_unavailable", org, cc.Decision)
		}
		if cc.Executed || cc.Dispatched || slices.Contains(hooks, hookAuthorizeTool) || slices.Contains(hooks, hookCallUpstream) {
			t.Fatalf("%s: the chain went on after the policy error: %v", org, hooks)
		}
		if cc.OrgConfig != nil || TenantPoolOf(cc) != nil {
			t.Fatalf("%s: a denied call carries a snapshot or a pool", org)
		}
		w := httptest.NewRecorder()
		cc.msg = &Message{JSONRPC: "2.0", ID: json.RawMessage(`1`)}
		s.writeToolCall(w, cc)
		if body := w.Body.String(); !strings.Contains(body, `"isError":true`) || !strings.Contains(body, `"code":"policy_unavailable"`) {
			t.Fatalf("%s: answer %s, want an isError result with io.futureagi/error policy_unavailable", org, body)
		}
	}
	if len(a.called()) != 0 {
		t.Fatal("a denied call reached the upstream")
	}

	// Without a tenancy the step does nothing (today's behaviour).
	s2 := newTestServer(t)
	defer s2.Close()
	cc := s2.newCallContext(httptest.NewRequest("POST", "/mcp", nil), &Message{Method: MethodToolsList}, nil)
	if d := resolveTenant(s2, cc); d != nil || cc.OrgConfig != nil || TenantPoolOf(cc) != nil {
		t.Fatalf("no tenancy: %v", d)
	}
}

// Bug 4 (gateway half): /-/mcp/test runs the whole tools/call chain as
// admin:<org>, resolves the name in that org's pool only, and applies the
// depth check.
func TestAdminTestToolOrgScopedRunsChain(t *testing.T) {
	s := NewServer(ServerConfig{MaxAgentDepth: 2, ToolCallTimeout: 5 * time.Second, SessionTTL: time.Minute, Separator: "_"})
	defer s.Close()
	ten, a, b := twoOrgs()
	s.SetTenancy(ten)
	usePoolHooks(t)
	var hooks []string
	var principals []Principal
	var credentials []string
	s.hookObserver = func(hook string, cc *CallContext) {
		hooks = append(hooks, hook)
		principals = append(principals, cc.Principal)
		credentials = append(credentials, cc.Request().Header.Get("Authorization")+cc.Request().Header.Get("X-Api-Key"))
	}

	req := httptest.NewRequest("POST", "/-/mcp/test", nil)
	req.Header.Set("Authorization", "Bearer the-admin-token")
	req.Header.Set("X-Api-Key", "the-admin-token")
	res, err := s.AdminTestTool(req, "org-a", "", "github__search", json.RawMessage(`{"q":"x"}`))
	if err != nil || res.Error != "" || res.IsError || res.ServerID != "github" {
		t.Fatalf("org-a test: %+v, %v", res, err)
	}
	if len(res.Content) != 1 || res.Content[0].Text != "org-a:search" {
		t.Fatalf("content %+v, want org-a's upstream answer", res.Content)
	}
	var want []string
	for _, st := range toolsCallChain {
		want = append(want, st.name)
	}
	if strings.Join(hooks, ",") != strings.Join(want, ",") {
		t.Fatalf("hooks ran %v, want the whole tools/call chain %v", hooks, want)
	}
	for i, p := range principals {
		if p.Kind != PrincipalAdmin || p.OrgID != "org-a" {
			t.Fatalf("a hook saw principal %+v, want admin:org-a", p)
		}
		if credentials[i] != "" {
			t.Fatalf("hook %s saw the admin request's credential", hooks[i])
		}
	}
	if req.Header.Get("Authorization") == "" {
		t.Fatal("the caller's request was changed")
	}
	if got, other := a.called(), b.called(); len(got) != 1 || len(other) != 0 {
		t.Fatalf("upstream calls: org-a %v, org-b %v", got, other)
	}

	// Another org's tool is not found in this org, and nothing runs.
	hooks = nil
	res, err = s.AdminTestTool(req, "org-a", "", "github__delete_repo", nil)
	if err != nil || res.Error != "tool not found: github__delete_repo" || len(hooks) != 0 || len(b.called()) != 0 {
		t.Fatalf("org-b's tool from org-a: %+v %v, hooks %v", res, err, hooks)
	}
	// The depth check applies to the admin call.
	deep := httptest.NewRequest("POST", "/-/mcp/test", nil)
	deep.Header.Set(HeaderAgentDepth, "5")
	res, _ = s.AdminTestTool(deep, "org-a", "", "github__search", nil)
	if res.Code != "tool_call_depth_exceeded" || len(a.called()) != 1 {
		t.Fatalf("depth 5 of 2: %+v, upstream calls %v", res, a.called())
	}
	// A virtual server narrows to its members; an unknown one is 404.
	if res, err := s.AdminTestTool(req, "org-a", "vs_eng", "github__search", nil); err != nil || res.Error != "" {
		t.Fatalf("vs_eng: %+v %v", res, err)
	}
	if _, err := s.AdminTestTool(req, "org-a", "vs_nope", "github__search", nil); !errors.Is(err, ErrUnknownVirtualServer) {
		t.Fatalf("unknown vs: %v", err)
	}
	// An unknown org is ErrUnknownOrg; an org whose policy fails says so.
	if _, err := s.AdminTestTool(req, "org-zzz", "", "github__search", nil); !errors.Is(err, ErrUnknownOrg) {
		t.Fatalf("unknown org: %v", err)
	}
	ten.fail["org-bad"] = errors.New("no last-known-good")
	if _, err := s.AdminTestTool(req, "org-bad", "", "x", nil); !errors.Is(err, ErrPolicyUnavailable) {
		t.Fatalf("bad org: %v", err)
	}
}

// The admin views answer from one org's pool, in the mcp-admin-*-v1 shapes.
func TestAdminViewsOrgScoped(t *testing.T) {
	s := newTestServer(t)
	defer s.Close()
	ten, _, _ := twoOrgs()
	s.SetTenancy(ten)

	st, err := s.AdminStatus("org-a")
	if err != nil || !st.Enabled || !st.OrgEnabled || len(st.Servers) != 1 || st.Servers[0].ServerID != "github" || st.Tools != 1 {
		t.Fatalf("status %+v %v", st, err)
	}
	tools, err := s.AdminTools("org-b", "")
	if err != nil || len(tools.Tools) != 1 || tools.Tools[0].Name != "github__delete_repo" || tools.Tools[0].UpstreamName != "delete_repo" {
		t.Fatalf("org-b tools %+v %v", tools, err)
	}
	if tools, _ := s.AdminTools("org-a", ""); tools.Tools[0].Annotations["readOnlyHint"] != true {
		t.Fatalf("annotations not passed through: %+v", tools.Tools[0])
	}
	if _, err := s.AdminStatus("org-zzz"); !errors.Is(err, ErrUnknownOrg) {
		t.Fatalf("unknown org: %v", err)
	}
	if _, err := s.AdminTools("org-a", "vs_nope"); !errors.Is(err, ErrUnknownVirtualServer) {
		t.Fatalf("unknown vs: %v", err)
	}
	s2 := newTestServer(t)
	defer s2.Close()
	if _, err := s2.AdminStatus("org-a"); !errors.Is(err, ErrTenancyUnavailable) {
		t.Fatalf("no tenancy: %v", err)
	}
}
