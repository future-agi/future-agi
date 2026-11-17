package redisstate

import (
	"context"
	"os"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/oklog/ulid/v2"
)

func TestW5ApprovalRedisAtomicTransitions(t *testing.T) {
	addr := os.Getenv("TEST_REDIS_ADDR")
	if addr == "" {
		t.Skip("TEST_REDIS_ADDR required")
	}
	c, err := NewClient(Config{Address: addr, DB: 15})
	if err != nil {
		t.Fatal(err)
	}
	defer c.Close()
	ctx := context.Background()
	tag := "mapr:{w5-store-" + ulid.Make().String() + "}:"
	key, index := tag+"row", tag+"index"
	defer c.Redis().Del(ctx, key, index)
	a, b := NewMCPApprovalStore(c), NewMCPApprovalStore(c)
	if err := a.Create(ctx, key, index, []byte("pending"), time.Minute); err != nil {
		t.Fatal(err)
	}
	if err := b.Create(ctx, key, index, []byte("other"), time.Minute); err == nil {
		t.Fatal("create overwrote row")
	}
	ok, err := b.CompareSwap(ctx, key, []byte("pending"), []byte("approved"), time.Now().Add(time.Minute))
	if err != nil || !ok {
		t.Fatal(ok, err)
	}
	before := c.Redis().PTTL(ctx, key).Val()
	var claims atomic.Int32
	var wg sync.WaitGroup
	for i := 0; i < 64; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			ok, err := a.CompareSwap(ctx, key, []byte("approved"), []byte("used"), time.Now().Add(time.Minute))
			if err != nil {
				t.Error(err)
			}
			if ok {
				claims.Add(1)
			}
		}()
	}
	wg.Wait()
	if claims.Load() != 1 {
		t.Fatalf("claims=%d", claims.Load())
	}
	if c.Redis().PTTL(ctx, key).Val() > before {
		t.Fatal("claim refreshed TTL")
	}
	rows, err := b.List(ctx, index)
	if err != nil || len(rows) != 1 || string(rows[0]) != "used" {
		t.Fatalf("list=%q %v", rows, err)
	}
	c.Redis().Del(ctx, key)
	if ok, err := a.CompareSwap(ctx, key, []byte("used"), []byte("approved"), time.Now().Add(time.Minute)); err != nil || ok {
		t.Fatal("CAS resurrected row", err)
	}
	rows, err = b.List(ctx, index)
	if err != nil || len(rows) != 0 {
		t.Fatal("expired row listed", err)
	}
}
