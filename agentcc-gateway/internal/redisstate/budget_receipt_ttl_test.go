package redisstate

import (
	"context"
	"testing"
	"time"
)

// Receipt lifetime is the configured minimum (job retention plus settlement
// window) but never shorter than the counter it protects.
func TestReceiptTTLCoversRetentionAndCounter(t *testing.T) {
	c, b, p := receiptReviewStore(t)
	ctx := context.Background()
	now := time.Now()
	for _, tc := range []struct {
		name, period string
		store        *BudgetStore
		want         time.Duration
	}{
		{"default total", "total", b, DefaultReceiptTTL},
		{"configured total", "total", b.WithReceiptTTL(2 * time.Hour), 2 * time.Hour},
		{"configured daily", "daily", b.WithReceiptTTL(2 * time.Hour), 48 * time.Hour},
		{"configured monthly", "monthly", b.WithReceiptTTL(800 * time.Hour), 840 * time.Hour},
	} {
		op := "video:" + tc.name + ":settle"
		if _, ok := tc.store.WithOperation(op, now).RecordSpend("org", "org", "", tc.period, "m", 1); !ok {
			t.Fatalf("%s: record", tc.name)
		}
		ttl, err := c.Redis().TTL(ctx, p+"receipt:"+op).Result()
		if err != nil {
			t.Fatal(err)
		}
		if ttl <= tc.want-time.Minute || ttl > tc.want {
			t.Errorf("%s: receipt ttl=%s, want %s", tc.name, ttl, tc.want)
		}
	}
	// Ordinary spend writes no receipt.
	if _, ok := b.RecordSpend("org", "org", "", "total", "m", 1); !ok {
		t.Fatal("ordinary record")
	}
	if n := len(receiptReviewKeys(t, c, p+"receipt:")); n != 4 {
		t.Errorf("receipt keys=%d, want 4", n)
	}
}

// A reservation fence without an operation receipt cannot dedup a lost reply.
func TestFenceRequiresOperation(t *testing.T) {
	_, b, p := receiptReviewStore(t)
	fenced := *b
	fenced.guardKey, fenced.guardToken = p+"guard", "token"
	if _, allowed, ok := fenced.CheckAndRecordSpend("org", "org", "", "total", "m", 1, 10, 0); ok || allowed {
		t.Fatalf("fenced spend without operation accepted: allowed=%v ok=%v", allowed, ok)
	}
}
