package artifacts

import (
	"context"
	"errors"
	"fmt"
	"os"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/redisstate"
)

func TestSweeper_RetentionAndRetry(t *testing.T) {
	addr := os.Getenv("TEST_REDIS_ADDR")
	if addr == "" {
		t.Skip("TEST_REDIS_ADDR not set")
	}
	c, e := redisstate.NewClient(redisstate.Config{Address: addr})
	if e != nil {
		t.Fatal(e)
	}
	defer c.Close()
	prefix := fmt.Sprintf("th8088:artifacts:%d:", time.Now().UnixNano())
	defer c.Redis().Del(context.Background(), prefix+"expire", prefix+"lease")
	d := disk(t, 100)
	s := NewSweeper(c, d, prefix)
	ctx := context.Background()
	for _, key := range []string{"video/old", "video/new"} {
		if _, e = d.Put(ctx, key, strings.NewReader("data"), Meta{}); e != nil {
			t.Fatal(e)
		}
	}
	if e = s.Track(ctx, "video/old", time.Now().Add(-time.Minute)); e != nil {
		t.Fatal(e)
	}
	if e = s.Track(ctx, "video/new", time.Now().Add(time.Hour)); e != nil {
		t.Fatal(e)
	}
	retry := errors.New("metadata unavailable")
	n, e := s.Sweep(ctx, 10, func(context.Context, string) error { return retry })
	if n != 0 || !errors.Is(e, retry) {
		t.Fatal(n, e)
	}
	var marked atomic.Int32
	n, e = s.Sweep(ctx, 10, func(ctx context.Context, key string) error {
		if key != "video/old" {
			t.Error(key)
		}
		marked.Add(1)
		return nil
	})
	if n != 1 || e != nil || marked.Load() != 1 {
		t.Fatal(n, e)
	}
	if _, e = d.Stat(ctx, "video/old"); !errors.Is(e, ErrNotFound) {
		t.Fatal(e)
	}
	if _, e = d.Stat(ctx, "video/new"); e != nil {
		t.Fatal(e)
	}
	n, e = s.Sweep(ctx, 10, func(context.Context, string) error { t.Fatal("duplicate mark"); return nil })
	if n != 0 || e != nil {
		t.Fatal(n, e)
	}
	if e = s.Track(ctx, "video/lock", time.Now().Add(-time.Minute)); e != nil {
		t.Fatal(e)
	}
	entered := make(chan struct{})
	release := make(chan struct{})
	done := make(chan error, 1)
	go func() {
		_, err := s.Sweep(ctx, 10, func(context.Context, string) error { close(entered); <-release; return nil })
		done <- err
	}()
	<-entered
	n, e = NewSweeper(c, d, prefix).Sweep(ctx, 10, func(context.Context, string) error { t.Error("concurrent lease"); return nil })
	close(release)
	if n != 0 || e != nil {
		t.Fatal(n, e)
	}
	if e = <-done; e != nil {
		t.Fatal(e)
	}
}
