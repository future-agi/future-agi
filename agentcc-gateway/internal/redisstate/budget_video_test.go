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
	// A video operation keeps the counter's period TTL: its receipt lives in a
	// separate key that outlives the counter, so a replay after the ordinary
	// chat write below is still deduplicated.
	late := b.WithOperation("video-reserve:late", time.Now())
	if _, allowed, ok := late.CheckAndRecordSpend("org", "org", "", "daily", "m", 1, 10, 0); !ok || !allowed {
		t.Fatal("daily reservation")
	}
	b.RecordSpend("org", "org", "", "daily", "m", 1)
	if ttl := c.Redis().TTL(context.Background(), b.budgetKey("org", "org", "", "daily")).Val(); ttl <= 0 || ttl > 48*time.Hour {
		t.Fatalf("daily counter ttl=%s, want its 48h period TTL", ttl)
	}
	if _, allowed, ok := late.CheckAndRecordSpend("org", "org", "", "daily", "m", 1, 10, 0); !ok || !allowed {
		t.Fatal("daily reservation replay")
	}
	if total, _, ok := b.GetSpend("org", "org", "", "daily"); !ok || total != 2 {
		t.Fatalf("replayed reservation applied twice: total=%v ok=%v", total, ok)
	}
}
