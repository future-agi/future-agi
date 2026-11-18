package redisstate

import (
	"context"
	"os"
	"strconv"
	"testing"
	"time"

	"github.com/redis/go-redis/v9"
)

// ---------------------------------------------------------------------------
// Mock local limiter for unit tests
// ---------------------------------------------------------------------------

type mockLocalLimiter struct {
	calls       int
	tokenCalls  int
	tokensAdded int
	allowed     bool
	remain      int
	resetSec    int
}

func (m *mockLocalLimiter) Allow(key string, limit int) (bool, int, int) {
	m.calls++
	return m.allowed, m.remain, m.resetSec
}

func (m *mockLocalLimiter) AllowTokens(key string, limit int) (bool, int, int) {
	m.tokenCalls++
	return m.allowed, m.remain, m.resetSec
}

func (m *mockLocalLimiter) AddTokens(key string, tokens int) {
	m.tokensAdded += tokens
}

func (m *mockLocalLimiter) Stop() {}

// ---------------------------------------------------------------------------
// Unit tests (no Redis)
// ---------------------------------------------------------------------------

func TestRateLimiter_NilClient_FallsBackToLocal(t *testing.T) {
	local := &mockLocalLimiter{allowed: true, remain: 9, resetSec: 55}
	rl := NewRateLimiter(nil, local, "test:rl:")

	allowed, remaining, reset := rl.Allow("key1", 10)
	if !allowed {
		t.Fatal("expected allowed from local fallback")
	}
	if remaining != 9 {
		t.Errorf("remaining = %d, want 9", remaining)
	}
	if reset != 55 {
		t.Errorf("reset = %d, want 55", reset)
	}
	if local.calls != 1 {
		t.Errorf("local.calls = %d, want 1", local.calls)
	}
}

func TestRateLimiter_UnavailableClient_FallsBackToLocal(t *testing.T) {
	// Create a client with a circuit breaker that's already open.
	client := &Client{
		breaker: newCircuitBreaker(1, 10*time.Second),
	}
	client.breaker.recordFailure() // open the circuit

	local := &mockLocalLimiter{allowed: false, remain: 0, resetSec: 30}
	rl := NewRateLimiter(client, local, "test:rl:")

	allowed, remaining, reset := rl.Allow("key1", 10)
	if allowed {
		t.Fatal("expected rejected from local fallback")
	}
	if remaining != 0 {
		t.Errorf("remaining = %d, want 0", remaining)
	}
	if reset != 30 {
		t.Errorf("reset = %d, want 30", reset)
	}
	if local.calls != 1 {
		t.Errorf("local.calls = %d, want 1", local.calls)
	}
}

func TestRateLimiter_Stop_StopsLocal(t *testing.T) {
	local := &mockLocalLimiter{}
	rl := NewRateLimiter(nil, local, "")
	rl.Stop() // Should not panic.
}

// ---------------------------------------------------------------------------
// Integration tests (require real Redis)
// ---------------------------------------------------------------------------

func redisAddr() string {
	addr := os.Getenv("TEST_REDIS_ADDR")
	if addr == "" {
		addr = "localhost:6379"
	}
	return addr
}

func skipIfNoRedis(t *testing.T) *Client {
	t.Helper()
	client, err := NewClient(Config{
		Address: redisAddr(),
		Timeout: 1 * time.Second,
	})
	if err != nil {
		t.Skipf("skipping: Redis not available at %s: %v", redisAddr(), err)
	}
	return client
}

func TestRateLimiter_Redis_AllowUnderLimit(t *testing.T) {
	client := skipIfNoRedis(t)
	defer client.Close()

	ts := time.Now().Format("150405.000")
	local := &mockLocalLimiter{allowed: true, remain: 99, resetSec: 60}
	rl := NewRateLimiter(client, local, "test:rl:allow:"+ts+":")

	limit := 10
	key := "test-allow-key-" + ts
	for i := 0; i < limit; i++ {
		allowed, remaining, reset := rl.Allow(key, limit)
		if !allowed {
			t.Fatalf("request %d: expected allowed", i+1)
		}
		wantRemaining := limit - (i + 1)
		if remaining != wantRemaining {
			t.Errorf("request %d: remaining = %d, want %d", i+1, remaining, wantRemaining)
		}
		if reset < 1 {
			t.Errorf("request %d: reset = %d, want >= 1", i+1, reset)
		}
	}

	// Local should NOT have been called (Redis was available).
	if local.calls != 0 {
		t.Errorf("local.calls = %d, want 0 (Redis handled all)", local.calls)
	}
}

func TestRateLimiter_Redis_RejectsOverLimit(t *testing.T) {
	client := skipIfNoRedis(t)
	defer client.Close()

	local := &mockLocalLimiter{allowed: true, remain: 99, resetSec: 60}
	rl := NewRateLimiter(client, local, "test:rl:reject:")

	limit := 3
	// Use a unique key to avoid collisions with other test runs.
	key := "test-reject-key-" + time.Now().Format("150405.000")

	// Exhaust the limit.
	for i := 0; i < limit; i++ {
		allowed, _, _ := rl.Allow(key, limit)
		if !allowed {
			t.Fatalf("request %d: expected allowed within limit", i+1)
		}
	}

	// Next request should be rejected.
	allowed, remaining, reset := rl.Allow(key, limit)
	if allowed {
		t.Fatal("expected rejected over limit")
	}
	if remaining != 0 {
		t.Errorf("remaining = %d, want 0", remaining)
	}
	if reset < 1 {
		t.Errorf("reset = %d, want >= 1", reset)
	}

	// Local should NOT have been called.
	if local.calls != 0 {
		t.Errorf("local.calls = %d, want 0", local.calls)
	}
}

func TestRateLimiter_Redis_IndependentKeys(t *testing.T) {
	client := skipIfNoRedis(t)
	defer client.Close()

	local := &mockLocalLimiter{allowed: true, remain: 99, resetSec: 60}
	rl := NewRateLimiter(client, local, "test:rl:indep:")

	ts := time.Now().Format("150405.000")
	keyA := "indep-a-" + ts
	keyB := "indep-b-" + ts
	limit := 2

	// Exhaust key A.
	for i := 0; i < limit; i++ {
		rl.Allow(keyA, limit)
	}
	allowed, _, _ := rl.Allow(keyA, limit)
	if allowed {
		t.Fatal("key-a should be rejected")
	}

	// Key B should still be allowed.
	allowed, remaining, _ := rl.Allow(keyB, limit)
	if !allowed {
		t.Fatal("key-b should be allowed (independent)")
	}
	if remaining != limit-1 {
		t.Errorf("key-b remaining = %d, want %d", remaining, limit-1)
	}
}

// scriptAdmitsAt runs the sliding-window script for key as Allow would at unix
// second now, which may be in the future: the script takes the time as an
// argument, so this asks what a caller arriving then would be told.
func scriptAdmitsAt(t *testing.T, client *Client, prefix, key string, limit int, now int64) bool {
	t.Helper()
	windowStart := now - (now % windowSeconds)
	currKey := prefix + key + ":" + strconv.FormatInt(windowStart, 10)
	prevKey := prefix + key + ":" + strconv.FormatInt(windowStart-windowSeconds, 10)
	var result []int64
	err := client.Do(func(rdb redis.UniversalClient) error {
		res, err := slidingWindowScript.Run(context.Background(), rdb,
			[]string{currKey, prevKey}, limit, windowSeconds, now,
		).Int64Slice()
		result = res
		return err
	})
	if err != nil || len(result) < 1 {
		t.Fatalf("running the script at %d: %v (%v)", now, err, result)
	}
	return result[0] == 1
}

// A refusal's reset is sent to the caller as Retry-After. A caller that waits
// exactly that long must be admitted, and one that waits a second less must
// still be refused: the value is when the window reopens, not the counter
// key's two-window TTL (which said 120 s for a window reopening within 61).
func TestRateLimiter_Redis_RefusalResetIsWhenTheWindowReopens(t *testing.T) {
	client := skipIfNoRedis(t)
	defer client.Close()

	for attempt := 0; attempt < 3; attempt++ {
		ts := strconv.FormatInt(time.Now().UnixNano(), 10)
		prefix := "test:rl:reopen:" + ts + ":"
		key := "reopen-key"
		local := &mockLocalLimiter{allowed: true, remain: 99, resetSec: 60}
		rl := NewRateLimiter(client, local, prefix)

		before := time.Now().Unix()
		first, _, _ := rl.Allow(key, 1)
		allowed, _, reset := rl.Allow(key, 1)
		after := time.Now().Unix()
		if before != after {
			continue // the refusal's second is ambiguous; try again
		}
		if !first || allowed {
			t.Fatalf("limit 1: first allowed=%v, second allowed=%v; want true, false", first, allowed)
		}
		if local.calls != 0 {
			t.Fatalf("local fallback answered %d calls; the Redis path is under test", local.calls)
		}

		// The window holds one request, so the next one is admitted one
		// second into the next window, when its weighted share drops below 1.
		elapsed := after % windowSeconds
		if want := int(windowSeconds - elapsed + 1); reset != want {
			t.Errorf("refused %ds into the window: reset = %d, want %d (seconds until the window reopens)", elapsed, reset, want)
		}
		if scriptAdmitsAt(t, client, prefix, key, 1, after+int64(reset)-1) {
			t.Errorf("a caller arriving %ds after the refusal is admitted: reset = %d is longer than the wait", reset-1, reset)
		}
		if !scriptAdmitsAt(t, client, prefix, key, 1, after+int64(reset)) {
			t.Errorf("a caller arriving %ds after the refusal, as Retry-After says, is refused again", reset)
		}
		return
	}
	t.Fatal("every attempt straddled a second boundary")
}
