package redisstate

import (
	"context"
	"fmt"
	"os"
	"strconv"
	"testing"
	"time"
)

// limitsClient is a client on the test Redis (TEST_REDIS_ADDR, DB TEST_REDIS_DB).
// Without TEST_REDIS_ADDR the test is skipped; with it, an unreachable Redis
// fails the test.
func limitsClient(t *testing.T) (*Client, string) {
	t.Helper()
	addr := os.Getenv("TEST_REDIS_ADDR")
	if addr == "" {
		t.Skip("set TEST_REDIS_ADDR (and TEST_REDIS_DB) to run the Redis rate limit tests")
	}
	db, _ := strconv.Atoi(os.Getenv("TEST_REDIS_DB"))
	c, err := NewClient(Config{Address: addr, DB: db, Timeout: 2 * time.Second})
	if err != nil {
		t.Fatalf("TEST_REDIS_ADDR=%s is set but Redis is not reachable: %v", addr, err)
	}
	prefix := fmt.Sprintf("test:orglimits:%s:%d:", t.Name(), time.Now().UnixNano())
	t.Cleanup(func() {
		ctx := context.Background()
		iter := c.Redis().Scan(ctx, 0, prefix+"*", 200).Iterator()
		for iter.Next(ctx) {
			c.Redis().Del(ctx, iter.Val())
		}
		_ = c.Close()
	})
	return c, prefix
}

// recordingLocal is a MultiLimiter local fallback that records its calls.
type recordingLocal struct {
	mockLocalLimiter
	allowN, settle int
	lastDelta      int64
}

func (m *recordingLocal) AllowN(checks []LimitCheck) LimitDecision {
	m.allowN++
	return LimitDecision{Allowed: true, Refused: -1, Window: 42}
}

func (m *recordingLocal) Settle(keys []string, window, delta int64, local bool) {
	m.settle++
	m.lastDelta = delta
}

func (r *RateLimiter) countAt(t *testing.T, key string, window int64) int64 {
	t.Helper()
	v, err := r.client.Redis().Get(context.Background(), r.prefix+key+":"+strconv.FormatInt(window, 10)).Int64()
	if err != nil {
		return 0
	}
	return v
}

// A request one counter refuses adds to none of them.
func TestOrgWideRPMRedisRefusalCountsNothing(t *testing.T) {
	c, prefix := limitsClient(t)
	rl := NewRateLimiter(c, &recordingLocal{}, prefix)
	checks := []LimitCheck{{Key: "org:a:rpm", Limit: 5, Cost: 1}, {Key: "org:a:user:u:rpm", Limit: 1, Cost: 1}}
	d := rl.AllowN(checks)
	if !d.Allowed || d.Local || checks[0].Remaining != 4 || checks[1].Remaining != 0 {
		t.Fatalf("first: %+v %+v", d, checks)
	}
	if checks[0].ResetSeconds < 1 || checks[0].ResetSeconds > 60 {
		t.Errorf("reset %d, want 1..60", checks[0].ResetSeconds)
	}
	d2 := rl.AllowN([]LimitCheck{{Key: "org:a:rpm", Limit: 5, Cost: 1}, {Key: "org:a:user:u:rpm", Limit: 1, Cost: 1}})
	if d2.Allowed || d2.Refused != 1 {
		t.Fatalf("second: %+v, want refused by check 1", d2)
	}
	if got := rl.countAt(t, "org:a:rpm", d.Window); got != 1 {
		t.Errorf("org counter after the refusal = %d, want 1", got)
	}
}

// AllowN's per-key counter is the one Allow keeps.
func TestOrgWideRPMRedisSharesTheKeyCounterWithAllow(t *testing.T) {
	c, prefix := limitsClient(t)
	rl := NewRateLimiter(c, &recordingLocal{}, prefix)
	if ok, _, _ := rl.Allow("key-1", 2); !ok {
		t.Fatal("Allow #1 refused")
	}
	if d := rl.AllowN([]LimitCheck{{Key: "key-1", Limit: 2, Cost: 1}}); !d.Allowed {
		t.Fatalf("AllowN #2 refused: %+v", d)
	}
	if ok, _, _ := rl.Allow("key-1", 2); ok {
		t.Error("Allow #3 admitted past a limit of 2")
	}
}

// Settle adds the difference, never below 0, and keeps the key's expiry.
func TestOrgTPMRedisSettleClampsAtZero(t *testing.T) {
	c, prefix := limitsClient(t)
	rl := NewRateLimiter(c, &recordingLocal{}, prefix)
	d := rl.AllowN([]LimitCheck{{Key: "org:a:tpm", Limit: 1000, Cost: 100}})
	if !d.Allowed {
		t.Fatalf("refused: %+v", d)
	}
	rl.Settle([]string{"org:a:tpm"}, d.Window, 250, false)
	if got := rl.countAt(t, "org:a:tpm", d.Window); got != 350 {
		t.Errorf("after +250: %d, want 350", got)
	}
	rl.Settle([]string{"org:a:tpm"}, d.Window, -1000, false)
	if got := rl.countAt(t, "org:a:tpm", d.Window); got != 0 {
		t.Errorf("after -1000: %d, want 0", got)
	}
	ttl := c.Redis().TTL(context.Background(), prefix+"org:a:tpm:"+strconv.FormatInt(d.Window, 10)).Val()
	if ttl <= 0 {
		t.Errorf("TTL after settle = %v, want the window's expiry kept", ttl)
	}
}

// A window that has expired is not recreated by a late settle.
func TestOrgTPMRedisSettleLeavesAnExpiredWindow(t *testing.T) {
	c, prefix := limitsClient(t)
	rl := NewRateLimiter(c, &recordingLocal{}, prefix)
	rl.Settle([]string{"org:a:tpm"}, 1000, 500, false)
	if n := c.Redis().Exists(context.Background(), prefix+"org:a:tpm:1000").Val(); n != 0 {
		t.Errorf("settle recreated an expired window")
	}
}

// Without Redis the local limiter decides, and the settle goes there too.
func TestOrgWideRPMLocalFallback(t *testing.T) {
	local := &recordingLocal{}
	rl := NewRateLimiter(nil, local, "")
	d := rl.AllowN([]LimitCheck{{Key: "org:a:rpm", Limit: 1, Cost: 1}})
	if !d.Allowed || !d.Local || local.allowN != 1 {
		t.Fatalf("decision %+v, local calls %d", d, local.allowN)
	}
	rl.Settle([]string{"org:a:tpm"}, d.Window, -7, d.Local)
	if local.settle != 1 || local.lastDelta != -7 {
		t.Errorf("local settle calls %d delta %d, want 1 and -7", local.settle, local.lastDelta)
	}
}
