package credentials

import (
	"context"
	"encoding/json"
	"net/url"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/contracts/schematest"
	"github.com/futureagi/agentcc-gateway/internal/mcp"
	"github.com/futureagi/agentcc-gateway/internal/redisstate"
)

// requireAuthRequired checks d is auth_required with reason, valid against
// mcp-denial-v1's $defs/Error, and returns its metadata.
func requireAuthRequired(t *testing.T, d *mcp.Denial, reason string) map[string]any {
	t.Helper()
	if d == nil || d.Code != mcp.CodeAuthRequired {
		t.Fatalf("got %v, want auth_required", d)
	}
	obj := errorObject(t, d)
	if errs := schematest.ValidateDef(t, "mcp-denial-v1.schema.json", "Error", obj); len(errs) > 0 {
		t.Fatalf("auth_required breaks mcp-denial-v1 $defs/Error: %v", errs)
	}
	meta, _ := obj["metadata"].(map[string]any)
	if meta["reason"] != reason {
		t.Fatalf("reason %v, want %s", meta["reason"], reason)
	}
	return meta
}

// openConnectLink checks link is base with a binding that opens, under the
// connect purpose only, to want.
func openConnectLink(t *testing.T, link, base string, want ConnectBinding) {
	t.Helper()
	u, err := url.Parse(link)
	if err != nil || !strings.HasPrefix(link, base+"?") {
		t.Fatalf("connect_url %q, want it on %q", link, base)
	}
	sealed := u.Query().Get(ConnectBindingParam)
	b, err := OpenConnectBinding(testCodec(t), sealed)
	if err != nil || b != want {
		t.Fatalf("binding = %+v, %v; want %+v", b, err, want)
	}
	if _, err := testCodec(t).Open(mcp.StatePurposeTokenCache, sealed); err == nil {
		t.Fatal("a connect binding opened as a cached token")
	}
}

// Every way Django says there is no usable token is auth_required with a
// connect link for that user: no_credential is no_token, needs_reauth and
// revoked are needs_reauth, expired is expired. The link is Django's start
// page, else the server's start page on the Django address.
func TestAuthRequiredTyped(t *testing.T) {
	d := newFakeDjango(t)
	r, _ := testResolver(t, d, nil, "", newTestClock())
	hint := testConnectHost + ConnectStartPath("issues")
	for _, c := range []struct{ refusal, reason, hint, base string }{
		{RefusalNoCredential, mcp.AuthReasonNoToken, hint, hint},
		{RefusalNeedsReauth, mcp.AuthReasonNeedsReauth, hint, hint},
		{RefusalRevoked, mcp.AuthReasonNeedsReauth, hint, hint},
		{RefusalExpired, mcp.AuthReasonExpired, "", d.URL + ConnectStartPath("issues")},
		{RefusalNeedsReauth, mcp.AuthReasonNeedsReauth, "not a url", d.URL + ConnectStartPath("issues")},
	} {
		d.refuse(keyA1, c.refusal, c.hint)
		auth, _, den := r.resolveUser(context.Background(), oauthUser("org-a", "u1"), "org-a", "issues")
		if auth != nil {
			t.Fatalf("%s: a refused credential produced an override", c.refusal)
		}
		meta := requireAuthRequired(t, den, c.reason)
		if meta["server_id"] != "issues" {
			t.Fatalf("%s: server_id %v", c.refusal, meta["server_id"])
		}
		link, _ := meta["connect_url"].(string)
		openConnectLink(t, link, c.base, ConnectBinding{V: 1, OrgID: "org-a", ServerID: "issues", UserID: "u1"})
	}
}

// A call with no user identity is auth_required no_user_identity, without a
// connect link and without asking Django: a key that is not personal, a
// runtime or callback token, the admin playground, a user of another org
// than the server's, or a user id with no valid shape.
func TestNoUserIdentityAuthRequired(t *testing.T) {
	d := newFakeDjango(t)
	r, _ := testResolver(t, d, nil, "", newTestClock())
	for name, p := range map[string]mcp.Principal{
		"shared key":     {Kind: mcp.PrincipalKey, KeyID: "k1", OrgID: "org-a"},
		"runtime token":  {Kind: mcp.PrincipalRuntime, OrgID: "org-a", UserID: "u1"},
		"callback token": {Kind: mcp.PrincipalCallback, OrgID: "org-a", UserID: "u1"},
		"admin":          {Kind: mcp.PrincipalAdmin, OrgID: "org-a", UserID: "u1"},
		"no principal":   {},
		"another org":    oauthUser("org-b", "u1"),
		"malformed user": oauthUser("org-a", "u1:issues"),
	} {
		auth, _, den := r.resolveUser(context.Background(), p, "org-a", "issues")
		if auth != nil {
			t.Fatalf("%s: got an override", name)
		}
		meta := requireAuthRequired(t, den, mcp.AuthReasonNoUserIdentity)
		if _, ok := meta["connect_url"]; ok {
			t.Fatalf("%s: a connect link bound to no user", name)
		}
		if !strings.Contains(den.Message, "OAuth") || !strings.Contains(den.Message, "personal key") {
			t.Fatalf("%s: the message %q names no remedy", name, den.Message)
		}
	}
	if len(d.sentHeaders()) != 0 {
		t.Fatalf("Django was asked %d times for a call with no user", len(d.sentHeaders()))
	}
}

// A personal key carries its owner: the identity step sets the user on a
// key principal only for a key its owner marked personal, and that user's
// own token is used.
func TestPersonalKeyCarriesOwner(t *testing.T) {
	d := newFakeDjango(t)
	clock := newTestClock()
	r, _ := testResolver(t, d, nil, "", clock)
	d.give(keyA1, "m25-test-token-a1", clock.now().Add(time.Hour), 2)
	p := mcp.Principal{Kind: mcp.PrincipalKey, KeyID: "k-personal", OrgID: "org-a", UserID: "u1"}
	if c, ok := CallerOf(p); !ok || c != (Caller{OrgID: "org-a", UserID: "u1"}) {
		t.Fatalf("CallerOf(personal key) = %+v, %v", c, ok)
	}
	auth, ref, den := r.resolveUser(context.Background(), p, "org-a", "issues")
	if den != nil || bearerOf(auth) != "m25-test-token-a1" {
		t.Fatalf("personal key: %q, %v", bearerOf(auth), den)
	}
	if ref.Source != mcp.CredentialUser || ref.Ref != "user:u1" {
		t.Fatalf("credential ref %+v", ref)
	}
}

// Each resolution takes Django's current state even while transport storage
// holds a fresh token. An already expired answer is refused and never cached.
func TestExpiredTokenRefreshes(t *testing.T) {
	redis, prefix := testRedis(t)
	d := newFakeDjango(t)
	clock := newTestClock()
	r, cache := testResolver(t, d, redis, prefix, clock)
	ctx := context.Background()
	u1 := oauthUser("org-a", "u1")

	d.give(keyA1, "m25-test-token-first", clock.now().Add(10*time.Minute), 1)
	for i := 0; i < 2; i++ {
		auth, _, den := r.resolveUser(ctx, u1, "org-a", "issues")
		if den != nil || bearerOf(auth) != "m25-test-token-first" {
			t.Fatalf("call %d: %q, %v", i, bearerOf(auth), den)
		}
	}
	if n := d.count(keyA1); n != 2 {
		t.Fatalf("Django checked %d times for two calls on a fresh token, want 2", n)
	}

	clock.advance(9*time.Minute + 30*time.Second) // 30 s from expiry
	d.give(keyA1, "m25-test-token-second", clock.now().Add(time.Hour), 2)
	auth, _, den := r.resolveUser(ctx, u1, "org-a", "issues")
	if den != nil || bearerOf(auth) != "m25-test-token-second" {
		t.Fatalf("near expiry: %q, %v; want the refreshed token", bearerOf(auth), den)
	}
	if n := d.count(keyA1); n != 3 {
		t.Fatalf("Django fetched %d times, want 3", n)
	}
	if tok, ok, _ := cache.Get(ctx, keyA1); !ok || tok.AccessToken != "m25-test-token-second" {
		t.Fatal("the refreshed token was not cached")
	}

	d.give(keyA2, "m25-test-token-stale", clock.now().Add(-time.Second), 1)
	_, _, den = r.resolveUser(ctx, oauthUser("org-a", "u2"), "org-a", "issues")
	requireAuthRequired(t, den, mcp.AuthReasonExpired)
	if _, ok, _ := cache.Get(ctx, keyA2); ok {
		t.Fatal("an expired token was cached")
	}
}

// Concurrent calls of one user on one server with nothing cached ask Django
// once, and each gets that user's token.
func TestConcurrentMissesFetchOnce(t *testing.T) {
	redis, prefix := testRedis(t)
	d := newFakeDjango(t)
	clock := newTestClock()
	r, _ := testResolver(t, d, redis, prefix, clock)
	d.give(keyA1, "m25-test-token-a1", clock.now().Add(time.Hour), 1)
	d.mu.Lock() // hold Django until every call is waiting on it
	var wg sync.WaitGroup
	got := make([]string, 8)
	for i := range got {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			auth, _, _ := r.resolveUser(context.Background(), oauthUser("org-a", "u1"), "org-a", "issues")
			got[i] = bearerOf(auth)
		}(i)
	}
	time.Sleep(200 * time.Millisecond)
	d.mu.Unlock()
	wg.Wait()
	for i, g := range got {
		if g != "m25-test-token-a1" {
			t.Fatalf("call %d got %q", i, g)
		}
	}
	if n := d.count(keyA1); n > 2 {
		t.Fatalf("Django fetched %d times for 8 concurrent calls", n)
	}
}

// A Django that cannot be reached, or that refuses the gateway's secret, is
// never a reason to use another credential: the call is refused, not sent.
func TestCredentialServiceDownRefusesTheCall(t *testing.T) {
	d := newFakeDjango(t)
	r, _ := testResolver(t, d, nil, "", newTestClock())
	for _, status := range []int{500, 401, 302} {
		d.failWith(status)
		auth, _, den := r.resolveUser(context.Background(), oauthUser("org-a", "u1"), "org-a", "issues")
		if auth != nil || den == nil || den.Code != mcp.CodePolicyUnavailable {
			t.Fatalf("Django answering %d: override %v, denial %v; want policy_unavailable", status, auth != nil, den)
		}
		if errs := schematest.ValidateDef(t, "mcp-denial-v1.schema.json", "Error", errorObject(t, den)); len(errs) > 0 {
			t.Fatalf("the refusal breaks mcp-denial-v1: %v", errs)
		}
	}
}

// A credential revoked in Django stops being used within the cache's cap
// even when the purge that follows a revoke never reaches the gateway.
func TestRevokedTokenStopsWithinCacheCap(t *testing.T) {
	redis, prefix := testRedis(t)
	d := newFakeDjango(t)
	clock := newTestClock()
	codec := testCodec(t)
	cache := NewTokenCache(redis, codec, CacheOptions{Prefix: prefix, Now: clock.now, MaxTTL: time.Second})
	r, err := NewResolver(ResolverOptions{Tokens: tokenClientFor(t, d), Cache: cache, Codec: codec, DjangoBaseURL: d.URL, Now: clock.now})
	if err != nil {
		t.Fatal(err)
	}
	ctx := context.Background()
	d.give(keyA1, "m25-test-token-a1", clock.now().Add(time.Hour), 1)
	if auth, _, den := r.resolveUser(ctx, oauthUser("org-a", "u1"), "org-a", "issues"); den != nil || bearerOf(auth) != "m25-test-token-a1" {
		t.Fatalf("first call: %v %v", bearerOf(auth), den)
	}
	d.refuse(keyA1, RefusalRevoked, "")
	time.Sleep(1200 * time.Millisecond)
	_, _, den := r.resolveUser(ctx, oauthUser("org-a", "u1"), "org-a", "issues")
	if den == nil || den.Code != "auth_required" {
		t.Fatalf("a revoked credential past the cache cap: %v, want auth_required", den)
	}
}

// testResolver is a resolver on d with the Redis cache (nil cache when
// redis is nil), judged by clock.
func testResolver(t *testing.T, d *fakeDjango, redis *redisstate.Client, prefix string, clock *testClock) (*Resolver, *TokenCache) {
	t.Helper()
	codec := testCodec(t)
	var cache *TokenCache
	if redis != nil {
		cache = NewTokenCache(redis, codec, CacheOptions{Prefix: prefix, Now: clock.now})
	}
	r, err := NewResolver(ResolverOptions{Tokens: tokenClientFor(t, d), Cache: cache, Codec: codec, DjangoBaseURL: d.URL, Now: clock.now})
	if err != nil {
		t.Fatal(err)
	}
	return r, cache
}

// oauthUser is the OAuth principal of user in org, with the scopes the
// authorization server's access tokens carry (mcp-access-token-claims-v1), so
// the gateway's scope check (M23) admits its tools calls.
func oauthUser(org, user string) mcp.Principal {
	return mcp.Principal{
		Kind: mcp.PrincipalOAuth, OrgID: org, UserID: user, ClientID: "client-m25",
		Scopes: []string{"mcp:tools", "mcp:resources", "mcp:prompts"},
	}
}

// bearerOf is the token an override sends, or "".
func bearerOf(a *mcp.AuthOverride) string {
	if a == nil {
		return ""
	}
	v := a.Header.Get("Authorization")
	if len(v) > len("Bearer ") && v[:len("Bearer ")] == "Bearer " {
		return v[len("Bearer "):]
	}
	return ""
}

// errorObject is d's io.futureagi/error object as clients decode it.
func errorObject(t *testing.T, d *mcp.Denial) map[string]any {
	t.Helper()
	raw, err := json.Marshal(d.Object())
	if err != nil {
		t.Fatal(err)
	}
	var obj map[string]any
	if err := json.Unmarshal(raw, &obj); err != nil {
		t.Fatal(err)
	}
	return obj
}
