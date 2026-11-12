package vendors_test

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net/http/httptest"
	"os"
	"reflect"
	"sync"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/cmd/agentcc-mcpmock/mcpmock"
	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/connectorcatalog/manifest"
	"github.com/futureagi/agentcc-gateway/internal/mcp"
	"github.com/futureagi/agentcc-gateway/internal/mcp/credentials"
	"github.com/futureagi/agentcc-gateway/internal/mcp/tenancy"
	"github.com/futureagi/agentcc-gateway/internal/mcp/vendors" // W4B_FACTORY_IMPORT
	"github.com/futureagi/agentcc-gateway/internal/redisstate"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

const stateSecret = "w4b-synthetic-state-key-not-a-real-secret"
const channelSecret = "w4b-synthetic-channel-key-not-a-real-secret"
const sharedCaller = "w4b-synthetic-shared-gateway-key"

// binding is deliberately test-local: the base receipt replaces ONLY the
// selected factory/import, leaving fixtures and behavioral assertions identical.
type binding struct {
	OrgID, ServerID, Platform, CatalogRef, URL, AuthKind, ToolMode string
	Delegated                                                      bool
}

// W4B_FACTORY_BEGIN
func selected(t *testing.T, f *fixture) mcp.VendorCredentials {
	t.Helper()
	r, err := vendors.New(vendors.Options{Resolver: f.resolver, Catalog: f.catalog,
		Bindings: vendors.BindingFunc(func(_ context.Context, org, server string) (vendors.Binding, error) {
			b, ok := f.bindings[org+"/"+server]
			if !ok {
				return vendors.Binding{}, fmt.Errorf("synthetic binding missing")
			}
			return vendors.Binding{OrgID: b.OrgID, ServerID: b.ServerID, Platform: b.Platform,
				CatalogRef: b.CatalogRef, URL: b.URL, AuthKind: b.AuthKind, ToolMode: b.ToolMode, Delegated: b.Delegated}, nil
		})})
	if err != nil {
		t.Fatal(err)
	}
	return r
}

// W4B_FACTORY_END

type fixture struct {
	srv        *mcp.Server
	mock       *mcpmock.Mock
	upstream   string
	cp         *controlPlane
	resolver   *credentials.Resolver
	cache      *credentials.TokenCache
	codec      *mcp.StateCodec
	catalog    *manifest.Catalog
	bindings   map[string]binding
	configs    map[string]*tenant.OrgConfig
	vendor     mcp.VendorCredentials
	platform   string
	bridge     *bridge
	ledger     *ledger
	runBudget  *ledger
	toolBudget *ledger
	seen       *observations
	// catalogOff builds the tenancy with mcp.catalog_enabled off.
	catalogOff bool
}

// Load and validate the repository catalog once, including its secret scan.
// Fixtures only read these records; change callbacks mutate per-fixture bindings
// and org configs, not the catalog. A catalog-mutating test must first copy the
// affected records (including any nested slices/maps it changes).
var fixtureCatalog = sync.OnceValues(func() (*manifest.Catalog, error) {
	return manifest.LoadDir(os.DirFS("../../../catalog/connectors"))
})

func newFixture(t *testing.T, platform, app string, change func(*fixture)) *fixture {
	t.Helper()
	rdb, prefix := testRedis(t)
	f := &fixture{platform: platform, bindings: map[string]binding{}, configs: map[string]*tenant.OrgConfig{},
		ledger: &ledger{}, runBudget: &ledger{}, toolBudget: &ledger{}, seen: &observations{}}
	var err error
	f.codec, err = credentials.ConnectCodec(channelSecret)
	if err != nil {
		t.Fatal(err)
	}
	cacheCodec, err := mcp.NewStateCodec([]string{stateSecret}, false)
	if err != nil {
		t.Fatal(err)
	}
	f.cache = credentials.NewTokenCache(rdb, cacheCodec, credentials.CacheOptions{Prefix: prefix})
	f.cp = newControlPlane(t)
	tc, err := credentials.NewTokenClient(credentials.TokenClientOptions{BaseURL: f.cp.url, Secret: channelSecret})
	if err != nil {
		t.Fatal(err)
	}
	f.resolver, err = credentials.NewResolver(credentials.ResolverOptions{Tokens: tc, Cache: f.cache, Codec: cacheCodec, ConnectCodec: f.codec, DjangoBaseURL: f.cp.url})
	if err != nil {
		t.Fatal(err)
	}
	f.catalog, err = fixtureCatalog()
	if err != nil {
		t.Fatal(err)
	}
	fix, err := mcpmock.LoadFixtures("../../../scripts/scenarios/fixtures/mcp/common")
	if err != nil {
		t.Fatal(err)
	}
	era, err := mcpmock.ParseEra("2025-06-18")
	if err != nil {
		t.Fatal(err)
	}
	// The mock refuses an unregistered OAuth client (3b6fb79119), so the
	// client ids the Arcade contract's grants stand in for (oauthGrant) are
	// registered with it, as public PKCE clients. The CIMD-shaped id is a URL
	// on the mock: the listener is open before the mock is built.
	up := httptest.NewUnstartedServer(nil)
	var clients map[string]mcpmock.OAuthClient
	if platform == "arcade" {
		base := "http://" + up.Listener.Addr().String()
		clients = map[string]mcpmock.OAuthClient{}
		for _, id := range []string{arcadeDCRClient, base + arcadeCIMDPath} {
			clients[id] = mcpmock.OAuthClient{RedirectURIs: []string{arcadeRedirect}, AuthMethod: "none",
				GrantTypes: []string{"authorization_code", "refresh_token"}, ResponseTypes: []string{"code"}}
		}
	}
	f.mock, err = mcpmock.New(mcpmock.Config{Era: era, Auth: "oauth", Path: "/mcp", ServerName: "w4b", OAuthClients: clients}, fix)
	if err != nil {
		up.Listener.Close()
		t.Fatal(err)
	}
	up.Config.Handler = f.mock.Handler()
	up.Start()
	t.Cleanup(up.Close)
	f.upstream = up.URL
	f.mock.SetPublicURL(up.URL)
	for _, org := range []string{"org-a", "org-b"} {
		ref, kind := app+"/futureagi-rest@1.0.0", manifest.AuthOAuth2
		if platform == "arcade" {
			ref, kind = "", manifest.AuthMCPOAuth
		}
		f.bindings[org+"/service"] = binding{OrgID: org, ServerID: "service", Platform: platform, CatalogRef: ref, URL: up.URL + "/mcp", AuthKind: kind, ToolMode: "fixed", Delegated: true}
		raw, _ := json.Marshal(map[string]any{"meta": map[string]any{"version": 1}, "mcp": map[string]any{
			"enabled": true, "access": map[string]any{"default": "allow"}, "servers": map[string]any{
				"service": map[string]any{"url": up.URL + "/mcp", "catalog_ref": ref, "credential": map[string]any{"mode": "per_user"}},
			}}})
		var cfg tenant.OrgConfig
		if err := json.Unmarshal(raw, &cfg); err != nil {
			t.Fatal(err)
		}
		f.configs[org] = &cfg
	}
	f.catalog = withClientSources(t, f.catalog, []string{"org"})
	if change != nil {
		change(f)
	}
	f.vendor = selected(t, f)
	f.bridge = &bridge{vendor: f.vendor, platform: f.platform}
	store := tenant.NewStore()
	for org, cfg := range f.configs {
		store.Set(org, cfg)
	}
	// The release builds catalog_ref servers only with mcp.catalog_enabled
	// on (tenancy/spec.go orgSpecsForRelease, 28c5d0ecd4), its default
	// (config.DefaultConfig). catalogOff is the off state.
	mgr := tenancy.New(tenancy.Options{Store: store, MCP: config.MCPConfig{CatalogEnabled: !f.catalogOff}, InitialWait: time.Second, Deps: func() *mcp.Deps {
		return &mcp.Deps{ClientFactory: mcp.DefaultClientFactory, Builtins: mcp.NewBuiltinRegistry(), ManagedServers: noManaged{}}
	}})
	t.Cleanup(func() {
		ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		_ = mgr.Close(ctx)
	})
	var tools []mcp.Tool
	for _, item := range fix.Tools {
		var tool mcp.Tool
		if err := json.Unmarshal(item.Def, &tool); err != nil {
			t.Fatal(err)
		}
		tool.Raw = item.Def
		tools = append(tools, tool)
	}
	ten := &listedTenancy{Tenancy: mgr, tools: tools}
	f.srv = mcp.NewServer(mcp.ServerConfig{MaxAgentDepth: 10, ToolCallTimeout: 3 * time.Second, SessionTTL: time.Minute, Separator: "_"})
	t.Cleanup(f.srv.Close)
	f.srv.SetIdentity(mcp.Identity{Keys: auth.NewKeyStore(config.AuthConfig{Keys: []config.AuthKeyConfig{{Name: "shared", Key: sharedCaller, Owner: "u1", Metadata: map[string]string{"org_id": "org-a"}}}})})
	f.srv.SetDeps(mcp.Deps{BearerVerifier: callers{}, CredentialResolver: f.bridge, Events: f.seen.event, ToolStats: f.seen,
		Credits: f.ledger, SpendGuard: f.ledger, KeyLimits: f.ledger, RunBudget: f.runBudget, ToolBudget: f.toolBudget})
	f.srv.SetTenancy(ten)
	t.Cleanup(func() { f.srv.SetTenancy(nil) })
	sep := f.srv.Registry().Separator()
	f.srv.Registry().SetOrgSource(func(org string) mcp.MemberSource {
		_, pool, err := ten.Resolve(org)
		if err != nil {
			return nil
		}
		return mcp.PoolMembers(pool, sep)
	})
	return f
}

// bridge is test-only injection into the dispatched CredentialResolver seam.
// The shipped VendorCredentials seam is not dispatched by this source stage.
type bridge struct {
	vendor   mcp.VendorCredentials
	platform string
	inspect  func(*mcp.CallContext, *mcp.AuthOverride, *mcp.Denial)
}

func (b *bridge) Resolve(cc *mcp.CallContext) (*mcp.AuthOverride, *mcp.Denial) {
	a, d := b.vendor.Resolve(cc, b.platform)
	if b.inspect != nil {
		b.inspect(cc, a, d)
	}
	return a, d
}

type callers struct{}

func (callers) Verify(_ context.Context, bearer, _ string) (*mcp.Principal, error) {
	// Synthetic authenticated identities, including personal-key identities
	// handed to CallerOf. This does not claim shipped key sync is activated.
	for _, org := range []string{"org-a", "org-b"} {
		for _, user := range []string{"u1", "u2"} {
			for _, kind := range []mcp.PrincipalKind{mcp.PrincipalOAuth, mcp.PrincipalKey, mcp.PrincipalRuntime, mcp.PrincipalCallback, mcp.PrincipalAdmin} {
				if bearer == caller(org, user, kind) {
					return &mcp.Principal{Kind: kind, OrgID: org, UserID: user, KeyID: "synthetic-key", Scopes: []string{"mcp:tools"}, AllowedTools: []string{"*"}}, nil
				}
			}
		}
	}
	if bearer == "w4b-no-user" {
		return &mcp.Principal{Kind: mcp.PrincipalOAuth, OrgID: "org-a", Scopes: []string{"mcp:tools"}}, nil
	}
	return nil, nil
}
func caller(org, user string, kind mcp.PrincipalKind) string {
	return "w4b-gateway-" + org + "-" + user + "-" + string(kind)
}
func userCaller(org, user string) string { return caller(org, user, mcp.PrincipalOAuth) }

type noManaged struct{}

func (noManaged) ForOrg(string) []mcp.ClientConfig { return nil }

type listedTenancy struct {
	mcp.Tenancy
	tools []mcp.Tool
}

func (l *listedTenancy) Resolve(org string) (*tenant.OrgConfig, mcp.TenantPool, error) {
	c, p, e := l.Tenancy.Resolve(org)
	if e != nil || p == nil {
		return c, p, e
	}
	return c, &listedPool{TenantPool: p, tools: l.tools}, nil
}

type listedPool struct {
	mcp.TenantPool
	tools []mcp.Tool
}

func (p *listedPool) listed(s mcp.PoolServer) mcp.PoolServer {
	if len(s.Tools) == 0 {
		s.Tools, s.Healthy = p.tools, true
	}
	return s
}
func (p *listedPool) Server(id string) (mcp.PoolServer, bool) {
	s, ok := p.TenantPool.Server(id)
	return p.listed(s), ok
}
func (p *listedPool) Servers() []mcp.PoolServer {
	s := p.TenantPool.Servers()
	for i := range s {
		s[i] = p.listed(s[i])
	}
	return s
}

type answer struct {
	IsError bool                       `json:"isError"`
	Meta    map[string]json.RawMessage `json:"_meta"`
	Raw     json.RawMessage            `json:"-"`
	Denial  mcp.ErrorObject            `json:"-"`
}

func (f *fixture) call(t *testing.T, bearer string) answer {
	t.Helper()
	status, result, rpcErr := f.callEnvelope(t, bearer)
	if len(rpcErr) > 0 {
		t.Fatalf("tools/call: status=%d, protocol failure=%s", status, rpcErr)
	}
	var out answer
	if err := json.Unmarshal(result, &out); err != nil {
		t.Fatal(err)
	}
	out.Raw = result
	if raw := out.Meta[mcp.MetaKeyError]; len(raw) > 0 {
		if err := json.Unmarshal(raw, &out.Denial); err != nil {
			t.Fatal(err)
		}
	}
	return out
}

// callEnvelope calls service__echo as bearer in a new session and returns
// the HTTP status and the answer's JSON-RPC result and error members.
func (f *fixture) callEnvelope(t *testing.T, bearer string) (int, json.RawMessage, json.RawMessage) {
	t.Helper()
	post := func(session, method string, params any) *httptest.ResponseRecorder {
		raw, _ := json.Marshal(map[string]any{"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
		r := httptest.NewRequest("POST", "/mcp?user_id=u2&end_user=u2&platform=microsoft", bytes.NewReader(raw))
		r.Header.Set("Content-Type", "application/json")
		r.Header.Set("Accept", "application/json, text/event-stream")
		r.Header.Set("Authorization", "Bearer "+bearer)
		r.Header.Set("X-FA-End-User", "u2")
		if session != "" {
			r.Header.Set("MCP-Session-Id", session)
		}
		w := httptest.NewRecorder()
		f.srv.HandlePost(w, r)
		return w
	}
	w := post("", "initialize", map[string]any{"protocolVersion": mcp.ProtocolVersion, "capabilities": map[string]any{}, "clientInfo": map[string]any{"name": "w4b", "version": "1"}})
	session := w.Header().Get("MCP-Session-Id")
	if session == "" {
		t.Fatalf("initialize: status=%d", w.Code)
	}
	w = post(session, "tools/call", map[string]any{"name": "service__echo", "arguments": map[string]any{"text": "synthetic", "user_id": "u2", "end_user": "u2", "platform": "microsoft"}})
	var envelope struct {
		Result json.RawMessage `json:"result"`
		Error  json.RawMessage `json:"error"`
	}
	if err := json.Unmarshal(w.Body.Bytes(), &envelope); err != nil {
		t.Fatalf("tools/call: status=%d, protocol failure=%s", w.Code, envelope.Error)
	}
	return w.Code, envelope.Result, envelope.Error
}
func connected(t *testing.T, a answer) {
	t.Helper()
	if a.IsError || a.Denial.Code != "" {
		t.Fatalf("connected call refused: code=%s", a.Denial.Code)
	}
}
func denied(t *testing.T, a answer, code string) {
	t.Helper()
	if !a.IsError || a.Denial.Code != code || a.Denial.Type == "" {
		t.Fatalf("typed refusal: isError=%v code=%s; want %s", a.IsError, a.Denial.Code, code)
	}
}
func metadata(t *testing.T, a answer) map[string]any {
	t.Helper()
	m, ok := a.Denial.Metadata.(map[string]any)
	if !ok {
		t.Fatal("missing denial metadata")
	}
	return m
}
func equal(t *testing.T, got, want any) {
	t.Helper()
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("got %v; want %v", got, want)
	}
}

func testRedis(t *testing.T) (*redisstate.Client, string) {
	t.Helper()
	addr := os.Getenv("TEST_REDIS_ADDR")
	if addr == "" {
		t.Fatal("TEST_REDIS_ADDR required: cache/identity tests must not skip")
	}
	c, err := redisstate.NewClient(redisstate.Config{Address: addr, DB: 13, Timeout: time.Second})
	if err != nil {
		t.Fatal(err)
	}
	if err := c.Redis().Ping(context.Background()).Err(); err != nil {
		c.Close()
		t.Fatal(err)
	}
	prefix := fmt.Sprintf("test:W4b:%d:", time.Now().UnixNano())
	t.Cleanup(func() {
		ctx := context.Background()
		it := c.Redis().Scan(ctx, 0, prefix+"*", 100).Iterator()
		for it.Next(ctx) {
			c.Redis().Del(ctx, it.Val())
		}
		c.Close()
	})
	return c, prefix
}

type observations struct {
	mu      sync.Mutex
	records []mcp.ToolCallRecord
	billing []mcp.CallBilling
}

func (o *observations) event(kind string, payload any) {
	if kind == mcp.EventKindToolCall {
		o.mu.Lock()
		defer o.mu.Unlock()
		o.records = append(o.records, payload.(mcp.ToolCallRecord))
	}
}
func (o *observations) Observe(cc *mcp.CallContext) {
	o.mu.Lock()
	defer o.mu.Unlock()
	o.billing = append(o.billing, cc.Billing)
}
