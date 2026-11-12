package redisstate

import (
	"context"
	"fmt"
	"os"
	"testing"
	"time"

	"github.com/oklog/ulid/v2"
	"github.com/redis/go-redis/v9"
)

func TestW11ApprovalCursorSurvivesDeletedAnchorAndEqualScores(t *testing.T) {
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
	prefix := "mapr:{w11-" + ulid.Make().String() + "}:"
	index := prefix + "index"
	keys := []string{index}
	defer func() { c.Redis().Del(ctx, keys...) }()
	store := NewMCPApprovalStore(c)
	score := float64(time.Now().Add(time.Minute).UnixMilli())
	for i := 0; i < 1002; i++ {
		key := prefix + fmt.Sprintf("%04d", i)
		keys = append(keys, key)
		if err := store.Create(ctx, key, index, []byte(key), time.Minute); err != nil {
			t.Fatal(err)
		}
		// All equal scores exercise Redis's reverse lexical tie-break.
		if err := c.Redis().ZAdd(ctx, index, redis.Z{Score: score, Member: key}).Err(); err != nil {
			t.Fatal(err)
		}
	}
	first, cursor, err := store.ListPage(ctx, index, "", 1000)
	if err != nil || len(first) != 1000 || cursor == "" {
		t.Fatalf("first=%d next=%q err=%v", len(first), cursor, err)
	}
	// Remove the anchor AND earlier rows. Offset/rank-only continuation skips
	// the last two records; a score-only continuation skips equal-score rows.
	for _, raw := range first {
		c.Redis().Del(ctx, string(raw))
		c.Redis().ZRem(ctx, index, string(raw))
	}
	tail, next, err := store.ListPage(ctx, index, cursor, 1000)
	if err != nil || len(tail) != 2 || next != "" {
		t.Fatalf("tail=%q next=%q err=%v", tail, next, err)
	}
	if string(tail[0]) != prefix+"0001" || string(tail[1]) != prefix+"0000" {
		t.Fatalf("tail=%q", tail)
	}
	if _, _, err := store.ListPage(ctx, "mapr:{foreign}:index", cursor, 1000); err != ErrApprovalCursor {
		t.Fatal("foreign cursor accepted", err)
	}
	for _, limit := range []int{0, 1001} {
		if _, _, err := store.ListPage(ctx, index, "", limit); err != ErrApprovalCursor {
			t.Fatal("unbounded limit accepted", err)
		}
	}
}
