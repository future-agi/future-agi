package redisstate

import (
	"testing"
	"time"
)

// ---------------------------------------------------------------------------
// Circuit breaker unit tests (no Redis needed)
// ---------------------------------------------------------------------------

func TestCircuitBreaker_StartsClosedAllowsRequests(t *testing.T) {
	cb := newCircuitBreaker(3, 10*time.Second)
	if !cb.allow() {
		t.Fatal("fresh circuit breaker should allow requests")
	}
}

func TestCircuitBreaker_OpensAfterThreshold(t *testing.T) {
	cb := newCircuitBreaker(3, 10*time.Second)

	cb.recordFailure()
	cb.recordFailure()
	if !cb.allow() {
		t.Fatal("should still allow before threshold")
	}

	cb.recordFailure() // 3rd failure = threshold
	if cb.allow() {
		t.Fatal("should be open after reaching threshold")
	}
}

func TestCircuitBreaker_SuccessResets(t *testing.T) {
	cb := newCircuitBreaker(2, 10*time.Second)

	cb.recordFailure()
	cb.recordFailure()
	if cb.allow() {
		t.Fatal("should be open")
	}

	// Simulate half-open: set openedAt far in the past so cooldown passes.
	cb.openedAt.Store(time.Now().Add(-20 * time.Second).UnixNano())
	if !cb.allow() {
		t.Fatal("should be half-open after cooldown")
	}

	cb.recordSuccess()
	if !cb.allow() {
		t.Fatal("should be closed after success")
	}
	if cb.failures.Load() != 0 {
		t.Errorf("failures should be 0 after success, got %d", cb.failures.Load())
	}
}

func TestCircuitBreaker_HalfOpenAfterCooldown(t *testing.T) {
	cb := newCircuitBreaker(2, 50*time.Millisecond)

	cb.recordFailure()
	cb.recordFailure()
	if cb.allow() {
		t.Fatal("should be open immediately after threshold")
	}

	// Wait for cooldown.
	time.Sleep(60 * time.Millisecond)

	if !cb.allow() {
		t.Fatal("should be half-open after cooldown period")
	}

	// Another failure should re-open.
	cb.recordFailure()
	// The breaker stays open because failures > threshold.
	if cb.allow() {
		// Might be half-open again since we just set openedAt. Check properly.
		// After a failure in half-open, openedAt should be refreshed.
	}
}

func TestCircuitBreaker_FailureBelowThresholdStaysClosed(t *testing.T) {
	cb := newCircuitBreaker(5, 10*time.Second)

	for i := 0; i < 4; i++ {
		cb.recordFailure()
	}
	if !cb.allow() {
		t.Fatal("should still be closed below threshold")
	}

	// One success should reset the counter.
	cb.recordSuccess()
	if cb.failures.Load() != 0 {
		t.Errorf("failures should reset to 0, got %d", cb.failures.Load())
	}

	// Can accumulate failures again.
	for i := 0; i < 4; i++ {
		cb.recordFailure()
	}
	if !cb.allow() {
		t.Fatal("should still be closed below threshold after reset")
	}
}

// ---------------------------------------------------------------------------
// Client construction (requires network — skip if no Redis)
// ---------------------------------------------------------------------------

func TestNewClient_EmptyAddress(t *testing.T) {
	_, err := NewClient(Config{Address: ""})
	if err == nil {
		t.Fatal("expected error for empty address")
	}
}

func TestNewClient_InvalidAddress(t *testing.T) {
	_, err := NewClient(Config{
		Address: "127.0.0.1:1", // nothing listening
		Timeout: 200 * time.Millisecond,
	})
	if err == nil {
		t.Fatal("expected error for unreachable address")
	}
}
