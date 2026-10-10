package redisstate

import (
	"context"
	"fmt"
	"os"
	"testing"
	"time"
)

func TestVideoSettlementReceipt(t *testing.T) {
	addr := os.Getenv("TEST_REDIS_ADDR")
	if addr == "" {
		t.Skip("TEST_REDIS_ADDR unset")
	}
	c, e := NewClient(Config{Address: addr, Timeout: time.Second})
	if e != nil {
		t.Fatal(e)
	}
	defer c.Close()
	p := fmt.Sprintf("test:video:budget:%d:", time.Now().UnixNano())
	defer func() {
		ks, _ := c.Redis().Keys(context.Background(), p+"*").Result()
		if len(ks) > 0 {
			c.Redis().Del(context.Background(), ks...)
		}
	}()
	b := NewBudgetStore(c, p)
	b.RecordSpend("org", "org", "", "total", "m", 10)
	for i := 0; i < 3; i++ {
		if _, ok := b.WithOperation("settlement:job", time.Now()).RecordSpend("org", "org", "", "total", "m", -8); !ok {
			t.Fatal("record")
		}
	}
	total, _, ok := b.GetSpend("org", "org", "", "total")
	if !ok || total != 2 {
		t.Fatal(total, ok)
	}
	// Unresolved jobs can outlive metadata TTL. Daily/weekly budget expiry must
	// not erase their debit or dedup receipt before an operator attaches a job.
	if _, allowed, ok := b.WithOperation("video-reserve:late", time.Now()).CheckAndRecordSpend("org", "org", "", "daily", "m", 1, 10, 0); !ok || !allowed {
		t.Fatal("daily reservation")
	}
	b.RecordSpend("org", "org", "", "daily", "m", 1) // ordinary chat must preserve it too
	if ttl := c.Redis().TTL(context.Background(), b.budgetKey("org", "org", "", "daily")).Val(); ttl >= 0 {
		t.Fatalf("unsettled receipt expires after %s", ttl)
	}
}
