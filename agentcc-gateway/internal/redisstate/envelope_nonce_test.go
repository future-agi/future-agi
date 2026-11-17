package redisstate

import (
	"context"
	"fmt"
	"os"
	"strconv"
	"testing"
	"time"
)

func envelopeNonceTestStore(t *testing.T) (*EnvelopeNonceStore, *Client, string) {
	t.Helper()
	addr := os.Getenv("TEST_REDIS_ADDR")
	if addr == "" {
		t.Skip("skipping: TEST_REDIS_ADDR is not set")
	}
	db := 0
	if v := os.Getenv("TEST_REDIS_DB"); v != "" {
		var err error
		if db, err = strconv.Atoi(v); err != nil || db < 0 {
			t.Fatalf("TEST_REDIS_DB=%q is not a database number", v)
		}
	}
	client, err := NewClient(Config{Address: addr, DB: db, Timeout: time.Second})
	if err != nil {
		t.Skipf("skipping: Redis not available at %s: %v", addr, err)
	}
	prefix := fmt.Sprintf("test:envelope-nonce:%s:%d:", t.Name(), time.Now().UnixNano())
	t.Cleanup(func() {
		ctx := context.Background()
		iter := client.Redis().Scan(ctx, 0, prefix+"*", 100).Iterator()
		for iter.Next(ctx) {
			client.Redis().Del(ctx, iter.Val())
		}
		client.Close()
	})
	return NewEnvelopeNonceStore(client, prefix), client, prefix
}

// An envelope id is consumed once, and kept one envelope life past its expiry,
// so a replica whose clock runs behind still finds it.
func TestEnvelopeNonceStoreConsumesOnce(t *testing.T) {
	store, client, prefix := envelopeNonceTestStore(t)
	ctx := context.Background()
	now := time.Now().Unix()
	id := "0123456789abcdef0123456789abcdef"
	ok, err := store.Consume(ctx, id, now+60, now)
	if err != nil || !ok {
		t.Fatalf("first consume = %v, %v; want true, nil", ok, err)
	}
	ok, err = store.Consume(ctx, id, now+60, now)
	if err != nil || ok {
		t.Fatalf("second consume = %v, %v; want false, nil", ok, err)
	}
	ttl, err := client.Redis().TTL(ctx, prefix+id).Result()
	if err != nil || ttl <= 60*time.Second || ttl > 121*time.Second {
		t.Fatalf("nonce TTL = %v, %v; want (60s, 121s]", ttl, err)
	}
	// Another id is independent.
	if ok, err := store.Consume(ctx, "fedcba9876543210fedcba9876543210", now+60, now); err != nil || !ok {
		t.Fatalf("another id = %v, %v; want true, nil", ok, err)
	}
}
