package credentials

import (
	"context"
	"encoding/json"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/cmd/agentcc-mcpmock/mcpmock"
	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/contracts/schematest"
	"github.com/futureagi/agentcc-gateway/internal/mcp"
	"github.com/futureagi/agentcc-gateway/internal/mcp/tenancy"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// The organization's one shared account signed in over OAuth: a
// shared server whose credential kind is oauth. The gateway fetches that
// account's token from Django just in time, on the credential channel, for every
// request to the server (its listing included), keeps it sealed under a key
// of the organization's own scope, and never serves it as a user's token or
// a user's token as it. Each org has the shared OAuth server "crm" on the
// MCP mock, which records a hash of the Authorization of each request.

const (
	orgKeyA = "cn24-test-org-a-key"
	orgKeyB = "cn24-test-org-b-key"
)

var (
	sharedA = SharedKey("org-a", "crm")
	sharedB = SharedKey("org-b", "crm")
)

// sharedConfig is org's pushed config with the shared OAuth server "crm" at
// url, its credential as given.
func sharedConfig(t *testing.T, url string, credential map[string]any) *tenant.OrgConfig {
	t.Helper()
	raw, _ := json.Marshal(map[string]any{"meta": map[string]any{"version": 1}, "mcp": map[string]any{
		"enabled": true, "access": map[string]any{"default": "allow"},
		"servers": map[string]any{"crm": map[string]any{"url": url, "credential": credential}},
	}})
	var cfg tenant.OrgConfig
	if err := json.Unmarshal(raw, &cfg); err != nil {
		t.Fatal(err)
	}
	return &cfg
}

// signedIn is a shared OAuth credential whose account an admin signed in.
func signedIn(id string) map[string]any {
	return map[string]any{"mode": "shared", "kind": "oauth", "credential_id": id, "state": "active"}
}

type sharedE2E struct {
	*e2e
	store *tenant.Store
	mgr   *tenancy.Manager
	url   string
}

// newSharedE2E is the gateway end to end with the two orgs' shared OAuth
// servers, the resolver installed both as step 13's resolver and as the
// pools' source of shared credentials, as the wiring installs it. creds
// gives each org's crm credential (default: signed in).
func newSharedE2E(t *testing.T, creds map[string]map[string]any) *sharedE2E {
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
	if f.mock, err = mcpmock.New(mcpmock.Config{Era: era, Auth: "none", Path: "/mcp", ServerName: "cn24"}, fix); err != nil {
		t.Fatal(err)
	}
	up := httptest.NewServer(f.mock.Handler())
	t.Cleanup(up.Close)
	f.mock.SetPublicURL(up.URL)

	s := &sharedE2E{e2e: f, store: tenant.NewStore(), url: up.URL + "/mcp"}
	for _, org := range []string{"org-a", "org-b"} {
		cred, ok := creds[org]
		if !ok {
			cred = signedIn("cred-" + org)
		}
		s.store.Set(org, sharedConfig(t, s.url, cred))
	}
	s.mgr = tenancy.New(tenancy.Options{Store: s.store, InitialWait: 10 * time.Second, Deps: func() *mcp.Deps {
		return &mcp.Deps{ClientFactory: mcp.DefaultClientFactory, Builtins: mcp.NewBuiltinRegistry(),
			ManagedServers: noManagedServers{}, SharedCredentials: f.resolver}
	}})
	t.Cleanup(func() {
		ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		_ = s.mgr.Close(ctx)
	})
	f.srv = mcp.NewServer(mcp.ServerConfig{MaxAgentDepth: 10, ToolCallTimeout: 10 * time.Second, SessionTTL: 5 * time.Minute, Separator: "_"})
	t.Cleanup(f.srv.Close)
	f.srv.SetIdentity(mcp.Identity{Keys: auth.NewKeyStore(config.AuthConfig{Keys: []config.AuthKeyConfig{
		{Name: "org-a", Key: orgKeyA, Owner: "u1", Metadata: map[string]string{"org_id": "org-a"}},
		{Name: "org-b", Key: orgKeyB, Owner: "u1", Metadata: map[string]string{"org_id": "org-b"}},
	}})})
	f.srv.SetDeps(mcp.Deps{
		BearerVerifier:     callers{callerA1: oauthUser("org-a", "u1"), callerA2: oauthUser("org-a", "u2")},
		CredentialResolver: f.resolver,
		SharedCredentials:  f.resolver,
	})
	f.srv.SetTenancy(s.mgr)
	t.Cleanup(func() { f.srv.SetTenancy(nil) })
	sep := f.srv.Registry().Separator()
	f.srv.Registry().SetOrgSource(func(org string) mcp.MemberSource {
		_, pool, err := s.mgr.Resolve(org)
		if err != nil {
			return nil
		}
		return mcp.PoolMembers(pool, sep)
	})
	return s
}

// crm is org's crm server as its pool's status reports it (a server the
// config refused included).
func (s *sharedE2E) crm(t *testing.T, org string) mcp.PoolServer {
	t.Helper()
	_, pool, err := s.mgr.ResolveStatus(org)
	if pool == nil {
		t.Fatalf("org %s has no pool: %v", org, err)
	}
	for _, ps := range pool.Servers() {
		if ps.ID == "crm" {
			return ps
		}
	}
	t.Fatalf("org %s's pool has no crm", org)
	return mcp.PoolServer{}
}

// waitListed waits until org's crm server has listed its tools.
func (s *sharedE2E) waitListed(t *testing.T, org string) {
	t.Helper()
	deadline := time.Now().Add(10 * time.Second)
	for time.Now().Before(deadline) {
		if ps := s.crm(t, org); ps.Healthy && len(ps.Tools) > 0 {
			return
		}
		time.Sleep(20 * time.Millisecond)
	}
	t.Fatalf("org %s's crm never listed its tools: %+v", org, s.crm(t, org))
}

// upstreamAuthAll is the hash of the Authorization of every request the
// mock received (its listings included), in order.
func (s *sharedE2E) upstreamAuthAll() []string {
	var out []string
	for _, r := range s.mock.State().ReceivedLast(0, "") {
		out = append(out, r.Auth.SHA256)
	}
	return out
}

// Every request to a shared OAuth server, its listing and each call, carries
// its organization's signed-in account's token, fetched just in time from
// Django and cached: for an agent-mode org key and for a person of the org
// alike, and never another org's.
func TestSharedOAuthJustInTime(t *testing.T) {
	exp := time.Now().Add(time.Hour)
	s := newSharedE2E(t, nil)
	s.django.give(sharedA, "cn24-test-shared-token-a", exp, 1)
	s.django.give(sharedB, "cn24-test-shared-token-b", exp, 1)
	s.waitListed(t, "org-a")
	s.waitListed(t, "org-b")
	if ps := s.crm(t, "org-a"); !ps.SharedOAuth || ps.PerUser {
		t.Fatalf("crm is reported as %+v, want a shared OAuth server", ps)
	}

	for _, bearer := range []string{orgKeyA, callerA1, orgKeyB} {
		if _, den := s.call(t, bearer, "crm__echo"); den != nil {
			t.Fatalf("%s on crm: %v", bearer, den)
		}
	}
	want := []string{bearerHash("cn24-test-shared-token-a"), bearerHash("cn24-test-shared-token-a"), bearerHash("cn24-test-shared-token-b")}
	got := s.upstreamAuth()
	if len(got) != len(want) {
		t.Fatalf("the upstream got %d calls, want %d", len(got), len(want))
	}
	for i := range want {
		if got[i] != want[i] {
			t.Fatalf("call %d carried another credential than its organization's account", i)
		}
	}
	for _, h := range s.upstreamAuthAll() {
		if h != bearerHash("cn24-test-shared-token-a") && h != bearerHash("cn24-test-shared-token-b") {
			t.Fatal("a request to the upstream carried no organization account's token")
		}
	}
	if n := s.django.count(sharedA); n != 1 {
		t.Fatalf("org-a's account token was fetched %d times, want 1 (cached)", n)
	}
	s.django.mu.Lock()
	defer s.django.mu.Unlock()
	for _, body := range s.django.bodies {
		if _, named := body["user_id"]; named || body["scope"] != "shared" {
			t.Fatalf("Django was asked %v for a shared server, want the organization's request", body)
		}
	}
}

// A shared account's token close to its expiry is fetched again before it
// is used, and the next request carries the new one.
func TestSharedOAuthRefresh(t *testing.T) {
	s := newSharedE2E(t, nil)
	s.django.give(sharedA, "cn24-test-shared-token-a1", s.clock.now().Add(2*time.Minute), 1)
	s.waitListed(t, "org-a")
	if _, den := s.call(t, orgKeyA, "crm__echo"); den != nil {
		t.Fatalf("first call: %v", den)
	}
	s.django.give(sharedA, "cn24-test-shared-token-a2", s.clock.now().Add(time.Hour), 2)
	s.clock.advance(61 * time.Second) // inside RefreshBefore of the first token's expiry
	if _, den := s.call(t, orgKeyA, "crm__echo"); den != nil {
		t.Fatalf("second call: %v", den)
	}
	got := s.upstreamAuth()
	if len(got) != 2 || got[0] != bearerHash("cn24-test-shared-token-a1") || got[1] != bearerHash("cn24-test-shared-token-a2") {
		t.Fatal("the call after the refresh window did not carry the refreshed token")
	}
	if n := s.django.count(sharedA); n != 2 {
		t.Fatalf("the account token was fetched %d times, want 2", n)
	}
}

// The organization's token and a person's token never stand in for each
// other: each is cached under a key of its own scope and opens only for it,
// a per_user server never gets the organization's token, and a shared
// server never asks Django for a person's.
func TestSharedNeverServedAsAnotherUsersToken(t *testing.T) {
	redis, prefix := testRedis(t)
	clock := newTestClock()
	d := newFakeDjango(t)
	r, cache := testResolver(t, d, redis, prefix, clock)
	ctx := context.Background()
	exp := clock.now().Add(time.Hour)

	userKey := Key{OrgID: "org-a", UserID: "u1", ServerID: "crm"}
	if cache.RedisKey(sharedA) == cache.RedisKey(userKey) || !strings.Contains(cache.RedisKey(sharedA), "#") {
		t.Fatalf("the organization's key %q is not in a family of its own", cache.RedisKey(sharedA))
	}
	if err := cache.Put(ctx, sharedA, Token{AccessToken: "cn24-test-shared", ExpiresAt: exp, CredentialVersion: 1}); err != nil {
		t.Fatal(err)
	}
	if err := cache.Put(ctx, userKey, Token{AccessToken: "cn24-test-user", ExpiresAt: exp, CredentialVersion: 1}); err != nil {
		t.Fatal(err)
	}
	rdb := redis.Redis()
	sharedSealed, _ := rdb.Get(ctx, cache.RedisKey(sharedA)).Result()
	userSealed, _ := rdb.Get(ctx, cache.RedisKey(userKey)).Result()
	// Each sealed value copied under the other scope's key opens for nobody.
	rdb.Set(ctx, cache.RedisKey(userKey), sharedSealed, time.Minute)
	if tok, ok, _ := cache.Get(ctx, userKey); ok {
		t.Fatalf("the organization's token opened as a person's: %v", tok)
	}
	rdb.Set(ctx, cache.RedisKey(sharedA), userSealed, time.Minute)
	if tok, ok, _ := cache.Get(ctx, sharedA); ok {
		t.Fatalf("a person's token opened as the organization's: %v", tok)
	}
	if (Key{OrgID: "org-a", UserID: "u1", ServerID: "crm", Shared: true}).valid() {
		t.Fatal("a key of the organization's scope that names a user is valid")
	}

	// Django has both tokens; each request is answered for its own scope.
	d.give(sharedA, "cn24-test-shared-django", exp, 1)
	if auth, _, den := r.resolveUser(ctx, oauthUser("org-a", "u2"), "org-a", "crm"); den == nil || auth != nil {
		t.Fatalf("a person with no own token got %v (%v), want auth_required", bearerOf(auth), den)
	}
	d.give(Key{OrgID: "org-a", UserID: "u2", ServerID: "crm"}, "cn24-test-user-u2", exp, 1)
	tok, err := r.token(ctx, SharedKey("org-a", "crm"))
	if err != nil || tok.AccessToken != "cn24-test-shared-django" {
		t.Fatalf("the organization's token is %v, %v", tok, err)
	}
	d.mu.Lock()
	last := d.bodies[len(d.bodies)-1]
	d.mu.Unlock()
	if _, named := last["user_id"]; named || last["scope"] != "shared" {
		t.Fatalf("the organization's token was asked for as %v", last)
	}
}

// A shared OAuth server whose account no one has signed in yet is not
// connected: it has no tools, its status says so, and Django is not asked.
// Once the account is signed in (the pushed config names it), it connects
// and lists at once.
func TestSharedOAuthWaitsForItsAccount(t *testing.T) {
	s := newSharedE2E(t, map[string]map[string]any{"org-a": {"mode": "shared", "kind": "oauth"}})
	s.django.give(sharedA, "cn24-test-shared-token-a", time.Now().Add(time.Hour), 1)
	time.Sleep(200 * time.Millisecond)
	ps := s.crm(t, "org-a")
	if ps.Healthy || len(ps.Tools) != 0 || !strings.Contains(ps.LastError, "signed in") {
		t.Fatalf("an account no one signed in: %+v", ps)
	}
	if n := s.django.count(sharedA); n != 0 {
		t.Fatalf("Django was asked %d times for an account no one signed in", n)
	}
	s.store.Set("org-a", sharedConfig(t, s.url, signedIn("cred-a")))
	s.waitListed(t, "org-a")
	if _, den := s.call(t, orgKeyA, "crm__echo"); den != nil {
		t.Fatalf("after the sign-in: %v", den)
	}
}

// A shared OAuth server takes no pushed secret: its token is fetched when
// it is used, so a config that carries one is refused, and so is the oauth
// kind on any other mode.
func TestSharedOAuthRefusesAPushedSecret(t *testing.T) {
	withSecret := signedIn("cred-a")
	withSecret["secret"] = map[string]any{"type": "bearer", "header": "Authorization", "value": "cn24-test-pushed-not-real"}
	s := newSharedE2E(t, map[string]map[string]any{
		"org-a": withSecret,
		"org-b": {"mode": "per_user", "kind": "oauth"},
	})
	for _, org := range []string{"org-a", "org-b"} {
		ps := s.crm(t, org)
		if ps.Healthy || len(ps.Tools) != 0 || ps.LastError == "" {
			t.Fatalf("org %s's crm loaded: %+v", org, ps)
		}
		if strings.Contains(ps.LastError, "cn24-test-pushed-not-real") {
			t.Fatal("the refusal names the pushed secret")
		}
	}
}

// Once Django revokes the organization's account and purges the gateway's
// copy, a call is auth_required with a link to the server's connect page,
// which an Owner or Admin signs in through (no person's binding), and the
// call is not sent.
func TestSharedOAuthAuthRequiredAfterRevoke(t *testing.T) {
	s := newSharedE2E(t, nil)
	s.django.give(sharedA, "cn24-test-shared-token-a", time.Now().Add(time.Hour), 1)
	s.waitListed(t, "org-a")
	if _, den := s.call(t, orgKeyA, "crm__echo"); den != nil {
		t.Fatalf("before the revoke: %v", den)
	}
	s.django.refuse(sharedA, RefusalRevoked, testConnectHost+ConnectStartPath("crm"))
	if n, err := s.resolver.PurgeCredentials(context.Background(), "org-a", "", "crm"); err != nil || n != 1 {
		t.Fatalf("purge = %d, %v", n, err)
	}
	_, den := s.call(t, orgKeyA, "crm__echo")
	if den == nil || den["code"] != mcp.CodeAuthRequired || metaOf(den)["reason"] != mcp.AuthReasonNeedsReauth {
		t.Fatalf("after the revoke: %v, want auth_required needs_reauth", den)
	}
	if link, _ := metaOf(den)["connect_url"].(string); link != testConnectHost+ConnectStartPath("crm") {
		t.Fatalf("connect_url %q, want the server's connect page with no binding", link)
	}
	if got := s.upstreamAuth(); len(got) != 1 {
		t.Fatalf("the upstream got %d calls, want only the one before the revoke", len(got))
	}
}

// A purge of a server's every token drops the organization's too; a purge
// of one person's leaves it.
func TestPurgeDropsTheSharedToken(t *testing.T) {
	redis, prefix := testRedis(t)
	clock := newTestClock()
	cache := NewTokenCache(redis, testCodec(t), CacheOptions{Prefix: prefix, Now: clock.now})
	ctx := context.Background()
	exp := clock.now().Add(time.Hour)
	if err := cache.Put(ctx, sharedA, Token{AccessToken: "cn24-test-shared", ExpiresAt: exp, CredentialVersion: 1}); err != nil {
		t.Fatal(err)
	}
	if _, err := cache.Purge(ctx, "org-a", "u1", "crm"); err != nil {
		t.Fatal(err)
	}
	if _, ok, _ := cache.Get(ctx, sharedA); !ok {
		t.Fatal("a purge of one person's token dropped the organization's")
	}
	if n, err := cache.Purge(ctx, "org-a", "", "crm"); err != nil || n != 1 {
		t.Fatalf("purge of every token = %d, %v, want 1", n, err)
	}
	if _, ok, _ := cache.Get(ctx, sharedA); ok {
		t.Fatal("the organization's token survived a purge of every token")
	}
}

// The organization's request is mcp-upstream-token-v1's SharedRequest, as
// its golden states it.
func TestUpstreamTokenClientSharedRequestMatchesGolden(t *testing.T) {
	golden := schematest.ReadExample(t, "mcp-upstream-token-v1.json")
	req := golden["shared_request"].(map[string]any)
	d := newFakeDjango(t)
	c := tokenClientFor(t, d)
	k := SharedKey(req["org_id"].(string), req["server_id"].(string))
	d.give(k, "cn24-test-shared-golden", time.Now().Add(time.Hour), 1)
	if tok, err := c.Fetch(context.Background(), k); err != nil || tok.AccessToken != "cn24-test-shared-golden" {
		t.Fatalf("the organization's token: %v, %v", tok, err)
	}
	d.mu.Lock()
	sent := d.bodies[len(d.bodies)-1]
	d.mu.Unlock()
	if errs := schematest.ValidateDef(t, "mcp-upstream-token-v1.schema.json", "SharedRequest", sent); len(errs) > 0 {
		t.Fatalf("the request breaks $defs/SharedRequest: %v", errs)
	}
	if len(sent) != len(req) || sent["scope"] != req["scope"] || sent["org_id"] != req["org_id"] || sent["server_id"] != req["server_id"] {
		t.Fatalf("sent %v, want the golden %v", sent, req)
	}
}
