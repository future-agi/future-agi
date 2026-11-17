package credentials

import (
	"context"
	"errors"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/mcp"
)

var (
	keyA1 = Key{OrgID: "org-a", UserID: "u1", ServerID: "issues"}
	keyA2 = Key{OrgID: "org-a", UserID: "u2", ServerID: "issues"}
	keyB1 = Key{OrgID: "org-b", UserID: "u1", ServerID: "issues"}
	keyAW = Key{OrgID: "org-a", UserID: "u1", ServerID: "wiki"}
)

// A user's token is stored only sealed, under MC.6's
// mtok:{<org>}:<user>:<server>, for no longer than it may be used: until 60 s
// ahead of its expiry, and at most DefaultMaxCacheTTL.
func TestPerUserTokenCachedSealed(t *testing.T) {
	redis, prefix := testRedis(t)
	clock := newTestClock()
	cache := NewTokenCache(redis, testCodec(t), CacheOptions{Prefix: prefix, Now: clock.now})
	ctx := context.Background()

	const value = "m25-test-token-a1-not-real"
	tok := Token{AccessToken: value, ExpiresAt: clock.now().Add(time.Hour), CredentialVersion: 3}
	if err := cache.Put(ctx, keyA1, tok); err != nil {
		t.Fatal(err)
	}
	if got, want := cache.RedisKey(keyA1), prefix+"mtok:{org-a}:u1:issues"; got != want {
		t.Fatalf("cache key %q, want MC.6's %q", got, want)
	}
	raw, err := redis.Redis().Get(ctx, cache.RedisKey(keyA1)).Result()
	if err != nil || raw == "" {
		t.Fatalf("nothing stored: %v", err)
	}
	if strings.Contains(raw, value) {
		t.Fatal("the stored value holds the token in the clear")
	}
	if pttl := redis.Redis().PTTL(ctx, cache.RedisKey(keyA1)).Val(); pttl <= 0 || pttl > DefaultMaxCacheTTL {
		t.Fatalf("PTTL %v, want within (0, %v] for a token valid an hour", pttl, DefaultMaxCacheTTL)
	}
	got, ok, err := cache.Get(ctx, keyA1)
	if err != nil || !ok || got.AccessToken != value || got.CredentialVersion != 3 {
		t.Fatalf("Get = %v %v %v", got, ok, err)
	}

	// A token valid 3 minutes is cached 2: until 60 s ahead of its expiry.
	short := Token{AccessToken: "m25-test-token-short", ExpiresAt: clock.now().Add(3 * time.Minute), CredentialVersion: 1}
	if err := cache.Put(ctx, keyAW, short); err != nil {
		t.Fatal(err)
	}
	if pttl := redis.Redis().PTTL(ctx, cache.RedisKey(keyAW)).Val(); pttl <= time.Minute || pttl > 2*time.Minute {
		t.Fatalf("PTTL %v for a 3-minute token, want about 2 minutes", pttl)
	}
	// A token within 60 s of its expiry is not cached at all.
	if err := cache.Put(ctx, keyA2, Token{AccessToken: "m25-test-token-ending", ExpiresAt: clock.now().Add(30 * time.Second)}); err != nil {
		t.Fatal(err)
	}
	if n := redis.Redis().Exists(ctx, cache.RedisKey(keyA2)).Val(); n != 0 {
		t.Fatal("a token within 60 s of its expiry was cached")
	}
	// A cached token stops being served 60 s ahead of its expiry.
	clock.advance(2*time.Minute + time.Second)
	if _, ok, _ := cache.Get(ctx, keyAW); ok {
		t.Fatal("a token 59 s from its expiry was served from the cache")
	}
}

// A sealed token opens only for the org, user and server it was sealed
// for: the same value placed under another user's or another org's key is
// served to nobody, and is removed.
func TestSealedTokenOpensOnlyForItsKey(t *testing.T) {
	redis, prefix := testRedis(t)
	clock := newTestClock()
	cache := NewTokenCache(redis, testCodec(t), CacheOptions{Prefix: prefix, Now: clock.now})
	ctx := context.Background()
	if err := cache.Put(ctx, keyA1, Token{AccessToken: "m25-test-token-a1", ExpiresAt: clock.now().Add(time.Hour)}); err != nil {
		t.Fatal(err)
	}
	for _, k := range []Key{keyA2, keyB1, keyAW} {
		if _, ok, err := cache.Get(ctx, k); ok || err != nil {
			t.Fatalf("%+v got user a1's token (%v, %v)", k, ok, err)
		}
	}
	sealed := redis.Redis().Get(ctx, cache.RedisKey(keyA1)).Val()
	for _, k := range []Key{keyA2, keyB1, keyAW} {
		if err := redis.Redis().Set(ctx, cache.RedisKey(k), sealed, time.Minute).Err(); err != nil {
			t.Fatal(err)
		}
		if _, ok, err := cache.Get(ctx, k); ok || err != nil {
			t.Fatalf("a1's sealed token, copied under %+v, opened for it (%v, %v)", k, ok, err)
		}
		if n := redis.Redis().Exists(ctx, cache.RedisKey(k)).Val(); n != 0 {
			t.Fatalf("the misplaced value under %+v was kept", k)
		}
	}
	if tok, ok, _ := cache.Get(ctx, keyA1); !ok || tok.AccessToken != "m25-test-token-a1" {
		t.Fatal("a1's own token no longer opens")
	}
	// An identifier that could be read as a separator, a hash tag or a
	// pattern names no key at all.
	for _, k := range []Key{
		{OrgID: "org-a", UserID: "u1:issues", ServerID: "x"},
		{OrgID: "org}-a", UserID: "u1", ServerID: "issues"},
		{OrgID: "org-a", UserID: "u*", ServerID: "issues"},
		{OrgID: "org-a", UserID: "", ServerID: "issues"},
	} {
		if err := cache.Put(ctx, k, Token{AccessToken: "m25-test-token-x", ExpiresAt: clock.now().Add(time.Hour)}); err == nil {
			t.Fatalf("Put accepted the key %+v", k)
		}
		if _, ok, err := cache.Get(ctx, k); ok || err == nil {
			t.Fatalf("Get accepted the key %+v", k)
		}
	}
}

// Purge drops one user's token on a server, or every user's on that server
// in that org, and nothing else.
func TestPurgeDropsCache(t *testing.T) {
	redis, prefix := testRedis(t)
	clock := newTestClock()
	cache := NewTokenCache(redis, testCodec(t), CacheOptions{Prefix: prefix, Now: clock.now})
	ctx := context.Background()
	for _, k := range []Key{keyA1, keyA2, keyB1, keyAW} {
		if err := cache.Put(ctx, k, Token{AccessToken: "m25-test-token-" + k.OrgID + k.UserID + k.ServerID, ExpiresAt: clock.now().Add(time.Hour)}); err != nil {
			t.Fatal(err)
		}
	}
	cached := func(k Key) bool { _, ok, _ := cache.Get(ctx, k); return ok }

	if got := redis.Redis().SMembers(ctx, prefix+"mtok:{org-a}:#users:issues").Val(); len(got) != 2 {
		t.Fatalf("org-a's issues index names %v, want u1 and u2", got)
	}
	if n, err := cache.Purge(ctx, "org-a", "u1", "issues"); err != nil || n != 1 {
		t.Fatalf("purging a1 on issues = %d, %v", n, err)
	}
	if cached(keyA1) || !cached(keyA2) || !cached(keyB1) || !cached(keyAW) {
		t.Fatalf("after purging a1 on issues: a1 %v a2 %v b1 %v a1-wiki %v", cached(keyA1), cached(keyA2), cached(keyB1), cached(keyAW))
	}
	if n, err := cache.Purge(ctx, "org-a", "", "issues"); err != nil || n != 1 {
		t.Fatalf("purging org-a's issues = %d, %v", n, err)
	}
	if cached(keyA2) || !cached(keyB1) || !cached(keyAW) {
		t.Fatalf("after purging org-a's issues: a2 %v b1 %v a1-wiki %v", cached(keyA2), cached(keyB1), cached(keyAW))
	}
	for _, bad := range [][3]string{{"org-*", "", "issues"}, {"org-a", "", "*"}, {"org-a", "u?", "wiki"}} {
		if _, err := cache.Purge(ctx, bad[0], bad[1], bad[2]); !errors.Is(err, mcp.ErrInvalidCredentialKey) {
			t.Fatalf("Purge of the pattern-like ids %q: %v", bad, err)
		}
	}
	if n, err := cache.Purge(ctx, "org-a", "", "issues"); err != nil || n != 0 {
		t.Fatalf("purging an empty index = %d, %v", n, err)
	}
	if !cached(keyB1) || !cached(keyAW) {
		t.Fatal("a refused purge dropped a token")
	}
}
