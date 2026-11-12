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

func TestW12ApprovalPagesSurviveMissingTiedCursor(t *testing.T) {
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
	tag := "mapr:{w12-pages-" + ulid.Make().String() + "}:"
	index := tag + "index"
	keys := []string{index}
	defer func() { c.Redis().Del(ctx, keys...) }()
	score := float64(time.Now().Add(time.Hour).UnixMilli())
	pipe := c.Redis().Pipeline()
	for i := 0; i < 1003; i++ {
		key := fmt.Sprintf("%s%04d", tag, i)
		keys = append(keys, key)
		pipe.Set(ctx, key, key, time.Hour)
		pipe.ZAdd(ctx, index, redis.Z{Score: score, Member: key})
	}
	if _, err := pipe.Exec(ctx); err != nil {
		t.Fatal(err)
	}
	store := NewMCPApprovalStore(c)
	pager, ok := any(store).(interface {
		ListPage(context.Context, string, string, int) ([][]byte, string, error)
	})
	if !ok {
		t.Fatal("approval store cannot traverse beyond its first page")
	}
	rows, cursor, err := pager.ListPage(ctx, index, "", 1000)
	if err != nil || len(rows) != 1000 || cursor == "" {
		t.Fatalf("first page count=%d cursor=%t err=%v", len(rows), cursor != "", err)
	}
	// Lose both a row ahead of the cursor and the cursor itself. A rank/offset
	// implementation skips records, and an exact-member cursor gets stuck.
	for _, raw := range [][]byte{rows[0], rows[len(rows)-1]} {
		key := string(raw)
		if err := c.Redis().Del(ctx, key).Err(); err != nil {
			t.Fatal(err)
		}
		if err := c.Redis().ZRem(ctx, index, key).Err(); err != nil {
			t.Fatal(err)
		}
	}
	rows, next, err := pager.ListPage(ctx, index, cursor, 1000)
	if err != nil || len(rows) != 3 || next != "" {
		t.Fatalf("remaining page count=%d cursor=%t err=%v", len(rows), next != "", err)
	}
	for i, row := range rows {
		if string(row) != fmt.Sprintf("%s%04d", tag, 2-i) {
			t.Fatalf("remaining row %d is not the next tied key", i)
		}
	}
	if _, _, err := pager.ListPage(ctx, tag+"other-index", cursor, 1000); err == nil {
		t.Fatal("accepted a cursor from another index")
	}
}
