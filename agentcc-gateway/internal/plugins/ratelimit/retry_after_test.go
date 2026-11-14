package ratelimit

import (
	"context"
	"strconv"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// A managed evaluation gets one retry and spends it when Retry-After says. A
// refusal without the header, or with a value short of the window's reset,
// sends that retry back into the window that refused it.

// lateInWindow is how far into its window the refused request arrives: 3.46 s
// before the reset, where truncating to whole seconds says 3 and the retry
// would land 0.46 s early.
const lateInWindow = 56540 * time.Millisecond

// passTime moves a key's window start back by d, as if d had passed since it
// opened.
func passTime(t *testing.T, rl *RateLimiter, key string, d time.Duration) {
	t.Helper()
	rl.mu.Lock()
	defer rl.mu.Unlock()
	w, ok := rl.windows[key]
	if !ok {
		t.Fatalf("no window for %q", key)
	}
	w.windowStart = w.windowStart.Add(-d)
}

func retryAfterSeconds(t *testing.T, apiErr *models.APIError) int {
	t.Helper()
	if apiErr == nil {
		t.Fatal("expected a rate-limit refusal")
	}
	seconds, err := strconv.Atoi(apiErr.RetryAfter)
	if err != nil {
		t.Fatalf("refusal %q carries no usable Retry-After (%q)", apiErr.Message, apiErr.RetryAfter)
	}
	return seconds
}

func TestLimiter_ResetSecondsRoundsUp(t *testing.T) {
	rl := NewRateLimiter()
	defer rl.Stop()

	if allowed, _, _ := rl.Allow("round-up", 1); !allowed {
		t.Fatal("first request refused")
	}
	passTime(t, rl, "round-up", lateInWindow)

	allowed, _, reset := rl.Allow("round-up", 1)
	if allowed {
		t.Fatal("second request allowed inside the window")
	}
	if reset != 4 {
		t.Fatalf("resetSeconds = %d with 3.46 s left in the window, want 4", reset)
	}
}

func TestRequestLimitRefusal_RetryAfterReachesTheNextWindow(t *testing.T) {
	ks, keyID := newKeyStoreWithKey("retry-after", "sk-agentcc-retryafter-rpm", 1)
	rl := NewRateLimiter()
	defer rl.Stop()
	p := New(config.RateLimitConfig{Enabled: true}, ks, rl)

	if res := p.ProcessRequest(context.Background(), newRCWithKey(keyID)); res.Error != nil {
		t.Fatalf("first request refused: %v", res.Error)
	}
	passTime(t, rl, keyID, lateInWindow)

	refused := p.ProcessRequest(context.Background(), newRCWithKey(keyID))
	wait := retryAfterSeconds(t, refused.Error)
	if refused.Error.Code != "rate_limit_exceeded" {
		t.Errorf("code = %q, want rate_limit_exceeded", refused.Error.Code)
	}

	// The caller waits exactly as long as it was told, then retries once.
	passTime(t, rl, keyID, time.Duration(wait)*time.Second)
	if res := p.ProcessRequest(context.Background(), newRCWithKey(keyID)); res.Error != nil {
		t.Fatalf("retry after Retry-After: %d was refused: %v", wait, res.Error)
	}
}

func TestTokenLimitRefusal_RetryAfterReachesTheNextWindow(t *testing.T) {
	ks, keyID := newKeyStoreWithTokenLimit("sk-agentcc-retryafter-tpm", 1000)
	rl := NewRateLimiter()
	defer rl.Stop()
	p := New(config.RateLimitConfig{Enabled: true}, ks, rl)

	rc := newRequestContext(keyID)
	defer rc.Release()
	if res := p.ProcessRequest(context.Background(), rc); res.Error != nil {
		t.Fatalf("first request refused: %v", res.Error)
	}
	answered(rc, 600, 500) // spends the whole token window
	p.ProcessResponse(context.Background(), rc)
	passTime(t, rl, tokenWindowKey(keyID), lateInWindow)

	next := newRequestContext(keyID)
	defer next.Release()
	refused := p.ProcessRequest(context.Background(), next)
	wait := retryAfterSeconds(t, refused.Error)
	if refused.Error.Code != "token_rate_limit_exceeded" {
		t.Errorf("code = %q, want token_rate_limit_exceeded", refused.Error.Code)
	}

	passTime(t, rl, tokenWindowKey(keyID), time.Duration(wait)*time.Second)
	retry := newRequestContext(keyID)
	defer retry.Release()
	if res := p.ProcessRequest(context.Background(), retry); res.Error != nil {
		t.Fatalf("retry after Retry-After: %d was refused: %v", wait, res.Error)
	}
}
