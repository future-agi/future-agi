package oauthrs

import (
	"context"
	"fmt"
	"os"
	"strconv"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/mcp/oauthrs/oauthrstest"
	"github.com/futureagi/agentcc-gateway/internal/redisstate"
)

// redisTestStore is a RedisKeyStore on the test Redis (TEST_REDIS_ADDR;
// TEST_REDIS_DB, else DB 5) under the prefix test:M23:<test>:<nonce>:, whose
// key is deleted when the test ends (never FLUSHDB). It skips when
// TEST_REDIS_ADDR is unset and fails when it is set but unreachable.
func redisTestStore(t *testing.T) *RedisKeyStore {
	t.Helper()
	addr := os.Getenv("TEST_REDIS_ADDR")
	if addr == "" {
		t.Skip("set TEST_REDIS_ADDR to run against a real Redis")
	}
	db := 5
	if v := os.Getenv("TEST_REDIS_DB"); v != "" {
		db, _ = strconv.Atoi(v)
	}
	client, err := redisstate.NewClient(redisstate.Config{Address: addr, DB: db, Timeout: time.Second})
	if err != nil {
		t.Fatalf("TEST_REDIS_ADDR=%s is set but Redis cannot be reached: %v", addr, err)
	}
	prefix := fmt.Sprintf("test:M23:%s:%d:", t.Name(), time.Now().UnixNano())
	store := NewRedisKeyStore(client, prefix)
	t.Cleanup(func() {
		client.Redis().Del(context.Background(), store.key)
		client.Close()
	})
	return store
}

// Pushed keys live in Redis under one key every replica reads, and the store
// never lets an older set replace a newer one, whichever replica writes.
func TestKeysPushStoredInRedis(t *testing.T) {
	ctx := context.Background()
	store := redisTestStore(t)
	if got, want := store.key[len(store.key)-len(RedisKey):], RedisKey; got != want {
		t.Fatalf("store key %q does not end in %q", store.key, want)
	}
	a := oauthrstest.NewSigner(t, "as-a")
	b := oauthrstest.NewSigner(t, "as-b")
	replica1 := NewKeySet(testIssuer, store, nil)
	replica2 := NewKeySet(testIssuer, store, nil)

	if res, err := replica1.Push(ctx, oauthrstest.PushBody(t, testIssuer, 5, a)); err != nil || !res.Applied {
		t.Fatalf("push to replica 1 = %+v, %v", res, err)
	}
	if _, ok := replica2.Lookup(ctx, "as-a"); !ok {
		t.Fatal("replica 2 does not accept the key replica 1 stored in Redis")
	}
	// Replica 3 has never seen a push; an older one sent to it is not
	// applied over the newer set in Redis, and it adopts the newer set.
	replica3 := NewKeySet(testIssuer, store, nil)
	res, err := replica3.Push(ctx, oauthrstest.PushBody(t, testIssuer, 4, b))
	if err != nil || res.Applied || res.Version != 5 {
		t.Fatalf("older push to a fresh replica = %+v, %v, want not applied, version 5", res, err)
	}
	if _, ok := replica3.Lookup(ctx, "as-b"); ok {
		t.Fatal("the older set's key is accepted")
	}
	raw, err := store.Load(ctx)
	if err != nil || raw == nil {
		t.Fatalf("Load = %s, %v", raw, err)
	}
	if doc, _, err := parsePush(raw, testIssuer); err != nil || doc.Version != 5 {
		t.Fatalf("Redis holds %+v, %v, want version 5", doc, err)
	}
	if saved, err := store.SaveIfNewer(ctx, 5, raw); err != nil || saved {
		t.Fatalf("SaveIfNewer of the same version = %v, %v, want not saved", saved, err)
	}
}
