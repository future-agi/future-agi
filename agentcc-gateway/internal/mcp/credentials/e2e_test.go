package credentials

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/cmd/agentcc-mcpmock/mcpmock"
	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/mcp"
	"github.com/futureagi/agentcc-gateway/internal/mcp/tenancy"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// The gateway end to end: the MCP server with its identity, the resolver as
// the wiring installs it (Django stand-in, Redis cache), the org pools of
// tenancy.Manager over two orgs' pushed configs, and the MCP mock upstream,
// which records a hash of the Authorization each request carried. Each org
// has a per_user server "issues" and a shared server "wiki" on the mock.
//
// The one stand-in on the gateway side: the pool lists a per_user server's
// tools only once a user's credential exists, so listingTenancy gives
// "issues" the mock's listing; calls go through the pool's own per-user
// clients.

const (
	callerA1 = "m25-test-caller-a-u1"
	callerA2 = "m25-test-caller-a-u2"
	callerB1 = "m25-test-caller-b-u1"
	// sharedKey is org-a's shared key; its Owner label is u1.
	sharedKey = "m25-test-shared-org-key"
)

type e2e struct {
	srv      *mcp.Server
	mock     *mcpmock.Mock
	django   *fakeDjango
	resolver *Resolver
	cache    *TokenCache
	clock    *testClock
}

func newE2E(t *testing.T) *e2e {
	t.Helper()
	return newE2EPool(t, true)
}

// newE2EPool is newE2E, with the stand-in listing of the per_user server
// (standIn) or with the pool as it is.
func newE2EPool(t *testing.T, standIn bool) *e2e {
	t.Helper()
	redis, prefix := testRedis(t)
	f := &e2e{django: newFakeDjango(t), clock: newTestClock()}
	f.resolver, f.cache = testResolver(t, f.django, redis, prefix, f.clock)

	fix, err := mcpmock.LoadFixtures("../../../scripts/scenarios/fixtures/mcp/common")
	if err != nil {
		t.Fatal(err)
	}
	era, err := mcpmock.ParseEra("2025-06-18")
	if err != nil {
		t.Fatal(err)
	}
	if f.mock, err = mcpmock.New(mcpmock.Config{Era: era, Auth: "none", Path: "/mcp", ServerName: "m25"}, fix); err != nil {
		t.Fatal(err)
	}
	up := httptest.NewServer(f.mock.Handler())
	t.Cleanup(up.Close)
	f.mock.SetPublicURL(up.URL)

	store := tenant.NewStore()
	for _, org := range []string{"org-a", "org-b"} {
		raw, _ := json.Marshal(map[string]any{"meta": map[string]any{"version": 1}, "mcp": map[string]any{
			"enabled": true, "access": map[string]any{"default": "allow"},
			"servers": map[string]any{
				"issues": map[string]any{"url": up.URL + "/mcp", "credential": map[string]any{"mode": "per_user"}},
				"wiki": map[string]any{"url": up.URL + "/mcp", "credential": map[string]any{"mode": "shared", "revision": 1,
					"secret": map[string]any{"type": "bearer", "header": "Authorization", "value": "m25-test-shared-wiki-" + org}}},
			}}})
		var cfg tenant.OrgConfig
		if err := json.Unmarshal(raw, &cfg); err != nil {
			t.Fatal(err)
		}
		store.Set(org, &cfg)
	}
	mgr := tenancy.New(tenancy.Options{Store: store, InitialWait: 10 * time.Second, Deps: func() *mcp.Deps {
		return &mcp.Deps{ClientFactory: mcp.DefaultClientFactory, Builtins: mcp.NewBuiltinRegistry(), ManagedServers: noManagedServers{}}
	}})
	t.Cleanup(func() {
		ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		_ = mgr.Close(ctx)
	})
	var listing []mcp.Tool
	for _, tl := range fix.Tools {
		var tool mcp.Tool
		if err := json.Unmarshal(tl.Def, &tool); err != nil {
			t.Fatal(err)
		}
		tool.Raw = tl.Def
		listing = append(listing, tool)
	}
	var ten mcp.Tenancy = mgr
	if standIn {
		ten = &listingTenancy{Tenancy: mgr, tools: listing}
	}

	f.srv = mcp.NewServer(mcp.ServerConfig{MaxAgentDepth: 10, ToolCallTimeout: 10 * time.Second, SessionTTL: 5 * time.Minute, Separator: "_"})
	t.Cleanup(f.srv.Close)
	f.srv.SetIdentity(mcp.Identity{Keys: auth.NewKeyStore(config.AuthConfig{Keys: []config.AuthKeyConfig{
		{Name: "shared", Key: sharedKey, Owner: "u1", Metadata: map[string]string{"org_id": "org-a"}},
	}})})
	f.srv.SetDeps(mcp.Deps{
		BearerVerifier: callers{
			callerA1: oauthUser("org-a", "u1"),
			callerA2: oauthUser("org-a", "u2"),
			callerB1: oauthUser("org-b", "u1"),
		},
		CredentialResolver: f.resolver,
	})
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

type noManagedServers struct{}

func (noManagedServers) ForOrg(string) []mcp.ClientConfig { return nil }

// callers is a BearerVerifier for the test's OAuth callers; any other token
// is left to key authentication.
type callers map[string]mcp.Principal

func (c callers) Verify(_ context.Context, token, _ string) (*mcp.Principal, error) {
	if p, ok := c[token]; ok {
		return &p, nil
	}
	return nil, nil
}

// listingTenancy serves the manager's pools with a per_user server's tools
// listed.
type listingTenancy struct {
	mcp.Tenancy
	tools []mcp.Tool
}

func (l *listingTenancy) Resolve(org string) (*tenant.OrgConfig, mcp.TenantPool, error) {
	cfg, pool, err := l.Tenancy.Resolve(org)
	if pool == nil || err != nil {
		return cfg, pool, err
	}
	return cfg, &listingPool{TenantPool: pool, tools: l.tools}, nil
}

type listingPool struct {
	mcp.TenantPool
	tools []mcp.Tool
}

func (p *listingPool) listed(s mcp.PoolServer) mcp.PoolServer {
	if s.PerUser && len(s.Tools) == 0 {
		s.Tools, s.Healthy = p.tools, true
	}
	return s
}

func (p *listingPool) Servers() []mcp.PoolServer {
	out := p.TenantPool.Servers()
	for i := range out {
		out[i] = p.listed(out[i])
	}
	return out
}

func (p *listingPool) Server(id string) (mcp.PoolServer, bool) {
	s, ok := p.TenantPool.Server(id)
	return p.listed(s), ok
}

// call opens a session as bearer and calls tool; it returns the result and
// the in-band error object, if the call was refused.
func (f *e2e) call(t *testing.T, bearer, tool string) (json.RawMessage, map[string]any) {
	t.Helper()
	post := func(session string, msg map[string]any) *httptest.ResponseRecorder {
		body, _ := json.Marshal(msg)
		req := httptest.NewRequest(http.MethodPost, "/mcp", bytes.NewReader(body))
		req.Header.Set("Content-Type", "application/json")
		req.Header.Set("Accept", "application/json, text/event-stream")
		req.Header.Set("Authorization", "Bearer "+bearer)
		if session != "" {
			req.Header.Set("MCP-Session-Id", session)
		}
		w := httptest.NewRecorder()
		f.srv.HandlePost(w, req)
		return w
	}
	w := post("", map[string]any{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": map[string]any{
		"protocolVersion": mcp.ProtocolVersion, "capabilities": map[string]any{}, "clientInfo": map[string]any{"name": "m25-test", "version": "1"}}})
	session := w.Header().Get("MCP-Session-Id")
	if session == "" {
		t.Fatalf("initialize as %s: %d %s", bearer, w.Code, w.Body.String())
	}
	w = post(session, map[string]any{"jsonrpc": "2.0", "id": 2, "method": "tools/call",
		"params": map[string]any{"name": tool, "arguments": map[string]any{"text": "m25"}}})
	var resp struct {
		Result json.RawMessage `json:"result"`
		Error  json.RawMessage `json:"error"`
	}
	if err := json.Unmarshal(w.Body.Bytes(), &resp); err != nil || len(resp.Error) > 0 {
		t.Fatalf("tools/call %s as %s: %d %s", tool, bearer, w.Code, w.Body.String())
	}
	var res struct {
		IsError bool                       `json:"isError"`
		Meta    map[string]json.RawMessage `json:"_meta"`
	}
	_ = json.Unmarshal(resp.Result, &res)
	if raw, ok := res.Meta[mcp.MetaKeyError]; ok && res.IsError {
		var obj map[string]any
		_ = json.Unmarshal(raw, &obj)
		return resp.Result, obj
	}
	return resp.Result, nil
}

// upstreamAuth is the hash of the Authorization of each tools/call the mock
// received, in order.
func (f *e2e) upstreamAuth() []string {
	var out []string
	for _, r := range f.mock.State().ReceivedLast(0, "tools/call") {
		out = append(out, r.Auth.SHA256)
	}
	return out
}

func bearerHash(token string) string {
	sum := sha256.Sum256([]byte("Bearer " + token))
	return hex.EncodeToString(sum[:])
}

func metaOf(obj map[string]any) map[string]any {
	m, _ := obj["metadata"].(map[string]any)
	return m
}

// Each user's call reaches the upstream with that user's own token: two
// users of one org, and the same user id in another org, never get each
// other's token or upstream client; a user with no connection is
// auth_required with a link bound to that user, and nothing is sent.
func TestPerUserClientIsolation(t *testing.T) {
	f := newE2E(t)
	exp := f.clock.now().Add(time.Hour)
	f.django.give(keyA1, "m25-test-token-a-u1", exp, 1)
	f.django.give(keyB1, "m25-test-token-b-u1", exp, 1)

	if _, den := f.call(t, callerA1, "issues__echo"); den != nil {
		t.Fatalf("org-a u1: %v", den)
	}
	_, den := f.call(t, callerA2, "issues__echo")
	if den == nil || den["code"] != mcp.CodeAuthRequired || metaOf(den)["reason"] != mcp.AuthReasonNoToken {
		t.Fatalf("org-a u2 with no connection: %v, want auth_required no_token", den)
	}
	link, _ := metaOf(den)["connect_url"].(string)
	openConnectLink(t, link, testConnectHost+ConnectStartPath("issues"), ConnectBinding{V: 1, OrgID: "org-a", ServerID: "issues", UserID: "u2"})
	if _, den := f.call(t, callerB1, "issues__echo"); den != nil {
		t.Fatalf("org-b u1: %v", den)
	}
	if _, den := f.call(t, callerA1, "issues__echo"); den != nil {
		t.Fatalf("org-a u1 again: %v", den)
	}

	want := []string{bearerHash("m25-test-token-a-u1"), bearerHash("m25-test-token-b-u1"), bearerHash("m25-test-token-a-u1")}
	got := f.upstreamAuth()
	if len(got) != len(want) {
		t.Fatalf("the upstream got %d calls, want %d (u2's call must not be sent)", len(got), len(want))
	}
	for i := range want {
		if got[i] != want[i] {
			t.Fatalf("upstream call %d carried another credential than its caller's", i)
		}
	}
	if n := f.django.count(keyA1); n != 2 {
		t.Fatalf("org-a u1's credential state was checked %d times for two calls, want 2", n)
	}
}

// Tokens are per user and never shared across users or orgs, on every path
// that could carry one over: the cache, the per-user upstream client and
// the same user id in two orgs.
func TestTokensPerUserNeverSharedAcrossUsersOrOrgs(t *testing.T) {
	f := newE2E(t)
	exp := f.clock.now().Add(time.Hour)
	f.django.give(keyA1, "m25-test-token-a-u1", exp, 1)
	if _, den := f.call(t, callerA1, "issues__echo"); den != nil {
		t.Fatalf("org-a u1: %v", den)
	}
	// u1 of org-a is connected and cached; u1 of org-b and u2 of org-a are not.
	for _, c := range []struct {
		bearer string
		k      Key
	}{{callerB1, keyB1}, {callerA2, keyA2}} {
		_, den := f.call(t, c.bearer, "issues__echo")
		if den == nil || den["code"] != mcp.CodeAuthRequired {
			t.Fatalf("%s got through on another user's token: %v", c.bearer, den)
		}
		if n := f.django.count(c.k); n != 1 {
			t.Fatalf("%s: Django asked %d times, want once for its own key", c.bearer, n)
		}
	}
	if got := f.upstreamAuth(); len(got) != 1 || got[0] != bearerHash("m25-test-token-a-u1") {
		t.Fatalf("the upstream got %d calls, want only org-a u1's", len(got))
	}
}

// A shared org key is never the user it was made by: on a per_user server
// it is auth_required no_user_identity, and the creator's token is not
// fetched.
func TestSharedKeyNeverActsAsCreator(t *testing.T) {
	f := newE2E(t)
	f.django.give(keyA1, "m25-test-token-a-u1", f.clock.now().Add(time.Hour), 1)
	_, den := f.call(t, sharedKey, "issues__echo")
	if den == nil || den["code"] != mcp.CodeAuthRequired || metaOf(den)["reason"] != mcp.AuthReasonNoUserIdentity {
		t.Fatalf("a shared key whose owner is u1: %v, want auth_required no_user_identity", den)
	}
	if n := f.django.count(keyA1); n != 0 || len(f.upstreamAuth()) != 0 {
		t.Fatalf("the creator's token was fetched (%d) or the call was sent (%d)", n, len(f.upstreamAuth()))
	}
}

// A shared server keeps the credential the org pushed, for every caller,
// and Django is never asked.
func TestSharedUsesPushedCredential(t *testing.T) {
	f := newE2E(t)
	for _, bearer := range []string{callerA1, sharedKey} {
		if _, den := f.call(t, bearer, "wiki__echo"); den != nil {
			t.Fatalf("%s on the shared server: %v", bearer, den)
		}
	}
	for i, h := range f.upstreamAuth() {
		if h != bearerHash("m25-test-shared-wiki-org-a") {
			t.Fatalf("shared call %d did not carry org-a's pushed credential", i)
		}
	}
	if len(f.django.sentHeaders()) != 0 {
		t.Fatal("Django was asked for a shared server's credential")
	}
}

// A revoked credential answers auth_required needs_reauth with a link to
// connect again, once Django revokes it and purges the gateway's copy, and
// the call is not sent.
func TestRevokedTokenAnswersAuthRequired(t *testing.T) {
	f := newE2E(t)
	f.django.give(keyA1, "m25-test-token-a-u1", f.clock.now().Add(time.Hour), 1)
	if _, den := f.call(t, callerA1, "issues__echo"); den != nil {
		t.Fatalf("before the revoke: %v", den)
	}
	f.django.refuse(keyA1, RefusalRevoked, testConnectHost+ConnectStartPath("issues"))
	if n, err := f.resolver.PurgeCredentials(context.Background(), "org-a", "u1", "issues"); err != nil || n != 1 {
		t.Fatalf("purge = %d, %v", n, err)
	}
	_, den := f.call(t, callerA1, "issues__echo")
	if den == nil || den["code"] != mcp.CodeAuthRequired || metaOf(den)["reason"] != mcp.AuthReasonNeedsReauth {
		t.Fatalf("after the revoke: %v, want auth_required needs_reauth", den)
	}
	link, _ := metaOf(den)["connect_url"].(string)
	openConnectLink(t, link, testConnectHost+ConnectStartPath("issues"), ConnectBinding{V: 1, OrgID: "org-a", ServerID: "issues", UserID: "u1"})
	if got := f.upstreamAuth(); len(got) != 1 {
		t.Fatalf("the upstream got %d calls, want only the one before the revoke", len(got))
	}
}
