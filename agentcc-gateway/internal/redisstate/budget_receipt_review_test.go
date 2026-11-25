package redisstate

import (
	"context"
	"fmt"
	"math"
	"os"
	"strings"
	"testing"
	"time"
)

func receiptReviewStore(t *testing.T) (*Client, *BudgetStore, string) {
	t.Helper()
	addr := os.Getenv("TEST_REDIS_ADDR")
	if addr == "" {
		t.Skip("TEST_REDIS_ADDR unset")
	}
	c, err := NewClient(Config{Address: addr, Timeout: time.Second})
	if err != nil {
		t.Fatal(err)
	}
	p := fmt.Sprintf("test:receipts:%s:%d:", t.Name(), time.Now().UnixNano())
	t.Cleanup(func() {
		ctx := context.Background()
		for _, key := range receiptReviewKeys(t, c, p) {
			c.Redis().Del(ctx, key)
		}
		c.Close()
	})
	return c, NewBudgetStore(c, p), p
}

func receiptReviewKeys(t *testing.T, c *Client, prefix string) []string {
	t.Helper()
	var out []string
	var cursor uint64
	for {
		keys, next, err := c.Redis().Scan(context.Background(), cursor, prefix+"*", 1000).Result()
		if err != nil {
			t.Fatal(err)
		}
		out = append(out, keys...)
		if cursor = next; cursor == 0 {
			return out
		}
	}
}

// Chat budget checks HGETALL the global_* counter hashes (global_org is shared
// by every tenant) on every request. Video reserve, refund and settle receipts
// must not accumulate in those hashes, and the counters must keep their period
// TTL, while replays of each operation stay deduplicated.
func TestReviewVideoReceiptsStayOutOfCounterHash(t *testing.T) {
	c, b, p := receiptReviewStore(t)
	ctx := context.Background()
	const videos = 50
	now := time.Now()
	periods := []string{"daily", "monthly", "total"}
	for _, period := range periods {
		if _, ok := b.RecordSpend("", "global_org", "", period, "chat-model", 0.5); !ok {
			t.Fatal("chat spend")
		}
	}
	for i := 0; i < videos; i++ {
		id := fmt.Sprintf("video_%03d", i)
		for _, period := range periods {
			if _, allowed, ok := b.WithOperation("video-reserve:"+id, now).CheckAndRecordSpend("", "global_org", "", period, "video-model", 1, 0, 0); !ok || !allowed {
				t.Fatalf("reserve %s %s", id, period)
			}
			if _, ok := b.WithOperation("video:"+id+":settle", now).RecordSpend("", "global_org", "", period, "video-model", -0.25); !ok {
				t.Fatalf("settle %s %s", id, period)
			}
		}
	}
	// A job whose reservation is refunded nets to zero but still leaves receipts.
	for _, period := range periods {
		if _, allowed, ok := b.WithOperation("video-reserve:video_failed", now).CheckAndRecordSpend("", "global_org", "", period, "video-model", 1, 0, 0); !ok || !allowed {
			t.Fatalf("reserve failed job %s", period)
		}
		if _, ok := b.WithOperation("video-refund:video_failed", now).RecordSpend("", "global_org", "", period, "video-model", -1); !ok {
			t.Fatalf("refund failed job %s", period)
		}
		if _, ok := b.RecordSpend("", "global_org", "", period, "chat-model", 0.5); !ok {
			t.Fatal("chat spend after video")
		}
	}

	want := 0.5 + videos*0.75 + 0.5
	counters := map[string]bool{}
	for _, period := range periods {
		key := b.budgetKey("", "global_org", "", period)
		counters[key] = true
		fields, err := c.Redis().HKeys(ctx, key).Result()
		if err != nil {
			t.Fatal(err)
		}
		var extra []string
		for _, field := range fields {
			if field != "total" && !strings.HasPrefix(field, "model:") {
				extra = append(extra, field)
			}
		}
		if len(extra) > 0 {
			t.Errorf("%s counter carries %d non-spend fields after %d videos (e.g. %q); chat reads all of them on every request", period, len(extra), videos+1, extra[0])
		}
		ttl, err := c.Redis().TTL(ctx, key).Result()
		if err != nil {
			t.Fatal(err)
		}
		if period == "total" {
			if ttl != -1 {
				t.Errorf("total counter ttl=%s, want no expiry", ttl)
			}
		} else if ttl <= 0 || ttl > budgetTTL(period) {
			t.Errorf("%s counter ttl=%s, want its %s period TTL", period, ttl, budgetTTL(period))
		}
		total, _, ok := b.GetSpend("", "global_org", "", period)
		if !ok || math.Abs(total-want) > 1e-9 {
			t.Errorf("%s spend=%v ok=%v, want %v", period, total, ok, want)
		}
	}

	// Replays are deduplicated, and recovery can still tell applied from not.
	for _, period := range periods {
		before, _, _ := b.GetSpend("", "global_org", "", period)
		if _, allowed, ok := b.WithOperation("video-reserve:video_000", now).CheckAndRecordSpend("", "global_org", "", period, "video-model", 1, 0, 0); !ok || !allowed {
			t.Fatalf("reserve replay %s", period)
		}
		b.WithOperation("video:video_000:settle", now).RecordSpend("", "global_org", "", period, "video-model", -0.25)
		b.WithOperation("video-refund:video_failed", now).RecordSpend("", "global_org", "", period, "video-model", -1)
		if after, _, _ := b.GetSpend("", "global_org", "", period); after != before {
			t.Errorf("%s replay changed spend %v -> %v", period, before, after)
		}
		if applied, err := b.WithOperation("video-reserve:video_000", now).OperationApplied(ctx, "", "global_org", "", period); err != nil || !applied {
			t.Errorf("%s applied reservation not found: %v %v", period, applied, err)
		}
		if applied, err := b.WithOperation("video-reserve:never", now).OperationApplied(ctx, "", "global_org", "", period); err != nil || applied {
			t.Errorf("%s unapplied reservation reported: %v %v", period, applied, err)
		}
	}

	// Receipts live in their own keys and expire no earlier than job retention.
	receipts := 0
	for _, key := range receiptReviewKeys(t, c, p) {
		if counters[key] {
			continue
		}
		receipts++
		ttl, err := c.Redis().TTL(ctx, key).Result()
		if err != nil {
			t.Fatal(err)
		}
		if ttl <= 720*time.Hour || ttl > budgetTTL("monthly") {
			t.Errorf("receipt %s ttl=%s, want finite and beyond 720h job retention", strings.TrimPrefix(key, p), ttl)
		}
	}
	if receipts == 0 {
		t.Error("no receipt keys outside the counter hashes")
	}
}

// A counter persisted by an earlier build that kept receipts in the hash gets
// its period TTL back on the next write, ordinary or video.
func TestReviewPersistedCounterRegainsPeriodTTL(t *testing.T) {
	c, b, _ := receiptReviewStore(t)
	ctx := context.Background()
	for _, tc := range []struct {
		period string
		write  func()
	}{
		{"monthly", func() { b.RecordSpend("", "global_org", "", "monthly", "m", 1) }},
		{"daily", func() {
			b.WithOperation("video-reserve:new", time.Now()).CheckAndRecordSpend("", "global_org", "", "daily", "m", 1, 0, 0)
		}},
	} {
		key := b.budgetKey("", "global_org", "", tc.period)
		if err := c.Redis().HSet(ctx, key, "total", 1_000_000, "durable_video_receipts", "1", "op:video-reserve:old", "1000000").Err(); err != nil {
			t.Fatal(err)
		}
		tc.write()
		ttl, err := c.Redis().TTL(ctx, key).Result()
		if err != nil {
			t.Fatal(err)
		}
		if ttl <= 0 || ttl > budgetTTL(tc.period) {
			t.Errorf("%s counter ttl=%s after a write, want its %s period TTL", tc.period, ttl, budgetTTL(tc.period))
		}
	}
}
