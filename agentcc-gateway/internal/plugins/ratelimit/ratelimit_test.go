package ratelimit

import (
	"context"
	"net/http"
	"strconv"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
)

// ---------------------------------------------------------------------------
// Limiter tests
// ---------------------------------------------------------------------------

func TestLimiter_AllowUnderLimit(t *testing.T) {
	rl := NewRateLimiter()
	limit := 10

	for i := 0; i < 5; i++ {
		allowed, remaining, resetSec := rl.Allow("key1", limit)
		if !allowed {
			t.Fatalf("request %d: expected allowed, got rejected", i+1)
		}
		wantRemaining := limit - (i + 1)
		if remaining != wantRemaining {
			t.Errorf("request %d: remaining = %d, want %d", i+1, remaining, wantRemaining)
		}
		if resetSec < 1 || resetSec > 60 {
			t.Errorf("request %d: resetSeconds = %d, want [1,60]", i+1, resetSec)
		}
	}
}

func TestLimiter_RejectOverLimit(t *testing.T) {
	rl := NewRateLimiter()
	limit := 3

	for i := 0; i < limit; i++ {
		allowed, _, _ := rl.Allow("key1", limit)
		if !allowed {
			t.Fatalf("request %d: expected allowed within limit", i+1)
		}
	}

	// The 4th request should be rejected.
	allowed, remaining, resetSec := rl.Allow("key1", limit)
	if allowed {
		t.Fatal("request 4: expected rejected, got allowed")
	}
	if remaining != 0 {
		t.Errorf("remaining on rejection = %d, want 0", remaining)
	}
	if resetSec < 1 {
		t.Errorf("resetSeconds = %d, want >= 1", resetSec)
	}
}

func TestLimiter_WindowReset(t *testing.T) {
	// We cannot easily advance time because windowState fields are unexported.
	// Instead, we verify that the window is correctly initialised and that
	// resetSeconds stays within the expected range [1, 60].
	rl := NewRateLimiter()
	limit := 2

	// Exhaust the limit.
	for i := 0; i < limit; i++ {
		allowed, _, _ := rl.Allow("reset-key", limit)
		if !allowed {
			t.Fatalf("request %d: expected allowed", i+1)
		}
	}

	// Confirm the next request is rejected.
	allowed, _, _ := rl.Allow("reset-key", limit)
	if allowed {
		t.Fatal("expected rejected after limit exhausted")
	}

	// Manually reset the window by moving windowStart into the past.
	// This is safe because we are in the same package (white-box test).
	rl.mu.Lock()
	w := rl.windows["reset-key"]
	w.windowStart = w.windowStart.Add(-windowDuration - 1)
	w.count = 0 // Simulating what the limiter does on a fresh window is not necessary;
	// Allow will detect the elapsed >= windowDuration and reset count automatically.
	// But we manually set count just to be explicit about starting fresh.
	rl.mu.Unlock()

	// Now the window should have expired; next request should be allowed.
	allowed, remaining, resetSec := rl.Allow("reset-key", limit)
	if !allowed {
		t.Fatal("expected allowed after window reset")
	}
	if remaining != limit-1 {
		t.Errorf("remaining = %d, want %d", remaining, limit-1)
	}
	if resetSec < 1 || resetSec > 60 {
		t.Errorf("resetSeconds = %d, want [1,60]", resetSec)
	}
}

func TestLimiter_IndependentKeys(t *testing.T) {
	rl := NewRateLimiter()
	limit := 2

	// Exhaust limit for key-a.
	for i := 0; i < limit; i++ {
		rl.Allow("key-a", limit)
	}
	allowed, _, _ := rl.Allow("key-a", limit)
	if allowed {
		t.Fatal("key-a should be rejected after limit exhausted")
	}

	// key-b should still be allowed.
	allowed, remaining, _ := rl.Allow("key-b", limit)
	if !allowed {
		t.Fatal("key-b should be allowed (independent counter)")
	}
	if remaining != limit-1 {
		t.Errorf("key-b remaining = %d, want %d", remaining, limit-1)
	}
}

func TestLimiter_ResetSeconds(t *testing.T) {
	rl := NewRateLimiter()

	_, _, resetSec := rl.Allow("timing-key", 100)
	if resetSec < 1 || resetSec > 60 {
		t.Errorf("resetSeconds = %d, want in [1,60]", resetSec)
	}
}

// ---------------------------------------------------------------------------
// Plugin basics
// ---------------------------------------------------------------------------

func TestPlugin_Name(t *testing.T) {
	p := New(config.RateLimitConfig{}, nil, nil)
	if got := p.Name(); got != "ratelimit" {
		t.Errorf("Name() = %q, want %q", got, "ratelimit")
	}
}

func TestPlugin_Priority(t *testing.T) {
	p := New(config.RateLimitConfig{}, nil, nil)
	if got := p.Priority(); got != 80 {
		t.Errorf("Priority() = %d, want 80", got)
	}
}

func TestPlugin_ProcessResponse_Noop(t *testing.T) {
	p := New(config.RateLimitConfig{Enabled: true, GlobalRPM: 10}, nil, nil)
	rc := newRC()
	result := p.ProcessResponse(context.Background(), rc)
	if result.Action != pipeline.Continue {
		t.Errorf("ProcessResponse action = %v, want Continue", result.Action)
	}
}

// ---------------------------------------------------------------------------
// Plugin ProcessRequest tests
// ---------------------------------------------------------------------------

func TestProcessRequest_Disabled(t *testing.T) {
	p := New(config.RateLimitConfig{Enabled: false, GlobalRPM: 1}, nil, nil)
	rc := newRC()

	result := p.ProcessRequest(context.Background(), rc)
	if result.Action != pipeline.Continue {
		t.Errorf("expected Continue when disabled, got %v", result.Action)
	}
}

func TestProcessRequest_NoLimit(t *testing.T) {
	// Enabled but GlobalRPM = 0 and no per-key RPM -> unlimited.
	p := New(config.RateLimitConfig{Enabled: true, GlobalRPM: 0}, nil, nil)
	rc := newRC()

	for i := 0; i < 100; i++ {
		result := p.ProcessRequest(context.Background(), rc)
		if result.Action != pipeline.Continue {
			t.Fatalf("request %d: expected Continue (unlimited), got ShortCircuit", i+1)
		}
	}
}

func TestProcessRequest_GlobalLimit(t *testing.T) {
	p := New(config.RateLimitConfig{Enabled: true, GlobalRPM: 5}, nil, nil)

	for i := 0; i < 5; i++ {
		rc := newRC()
		result := p.ProcessRequest(context.Background(), rc)
		if result.Action != pipeline.Continue {
			t.Fatalf("request %d: expected Continue, got ShortCircuit", i+1)
		}
	}
}

func TestProcessRequest_GlobalLimit_Exceeded(t *testing.T) {
	globalRPM := 3
	p := New(config.RateLimitConfig{Enabled: true, GlobalRPM: globalRPM}, nil, nil)

	// Exhaust the limit.
	for i := 0; i < globalRPM; i++ {
		rc := newRC()
		result := p.ProcessRequest(context.Background(), rc)
		if result.Action != pipeline.Continue {
			t.Fatalf("request %d: expected Continue", i+1)
		}
	}

	// Exceed the limit.
	rc := newRC()
	result := p.ProcessRequest(context.Background(), rc)
	if result.Action != pipeline.ShortCircuit {
		t.Fatal("expected ShortCircuit for exceeded global limit")
	}
	if result.Error == nil {
		t.Fatal("expected APIError on rejection")
	}
	if result.Error.Status != http.StatusTooManyRequests {
		t.Errorf("status = %d, want 429", result.Error.Status)
	}
	if result.Error.Code != "rate_limit_exceeded" {
		t.Errorf("code = %q, want %q", result.Error.Code, "rate_limit_exceeded")
	}
	if result.Error.Type != "rate_limit_error" {
		t.Errorf("type = %q, want %q", result.Error.Type, "rate_limit_error")
	}
}

func TestProcessRequest_PerKeyLimit(t *testing.T) {
	ks, keyID := newKeyStoreWithKey("test-key", "sk-agentcc-testkey123", 5)
	p := New(config.RateLimitConfig{Enabled: true, GlobalRPM: 100}, ks, nil)

	for i := 0; i < 5; i++ {
		rc := newRCWithKey(keyID)
		result := p.ProcessRequest(context.Background(), rc)
		if result.Action != pipeline.Continue {
			t.Fatalf("request %d: expected Continue with per-key limit", i+1)
		}
	}
}

func TestProcessRequest_PerKeyLimit_Exceeded(t *testing.T) {
	perKeyRPM := 3
	ks, keyID := newKeyStoreWithKey("limited", "sk-agentcc-limited999", perKeyRPM)
	p := New(config.RateLimitConfig{Enabled: true, GlobalRPM: 1000}, ks, nil)

	// Exhaust per-key limit.
	for i := 0; i < perKeyRPM; i++ {
		rc := newRCWithKey(keyID)
		result := p.ProcessRequest(context.Background(), rc)
		if result.Action != pipeline.Continue {
			t.Fatalf("request %d: expected Continue", i+1)
		}
	}

	// 4th request should be rejected.
	rc := newRCWithKey(keyID)
	result := p.ProcessRequest(context.Background(), rc)
	if result.Action != pipeline.ShortCircuit {
		t.Fatal("expected ShortCircuit for exceeded per-key limit")
	}
	if result.Error == nil {
		t.Fatal("expected APIError on per-key rejection")
	}
	if result.Error.Status != http.StatusTooManyRequests {
		t.Errorf("status = %d, want 429", result.Error.Status)
	}
	if result.Error.Code != "rate_limit_exceeded" {
		t.Errorf("code = %q, want %q", result.Error.Code, "rate_limit_exceeded")
	}
	if result.Error.Type != "rate_limit_error" {
		t.Errorf("type = %q, want %q", result.Error.Type, "rate_limit_error")
	}
	if result.Error.Message == "" {
		t.Error("expected non-empty error message")
	}
}

func TestProcessRequest_SetsMetadata(t *testing.T) {
	globalRPM := 10
	p := New(config.RateLimitConfig{Enabled: true, GlobalRPM: globalRPM}, nil, nil)

	rc := newRC()
	result := p.ProcessRequest(context.Background(), rc)
	if result.Action != pipeline.Continue {
		t.Fatal("expected Continue")
	}

	// Verify metadata was set.
	assertMetadata(t, rc, "ratelimit_limit", strconv.Itoa(globalRPM))
	assertMetadata(t, rc, "ratelimit_remaining", strconv.Itoa(globalRPM-1))

	resetStr, ok := rc.Metadata["ratelimit_reset"]
	if !ok {
		t.Fatal("ratelimit_reset not set in metadata")
	}
	resetVal, err := strconv.Atoi(resetStr)
	if err != nil {
		t.Fatalf("ratelimit_reset is not a valid int: %q", resetStr)
	}
	if resetVal < 1 || resetVal > 60 {
		t.Errorf("ratelimit_reset = %d, want [1,60]", resetVal)
	}
}

func TestProcessRequest_PerKeyOverridesGlobal(t *testing.T) {
	perKeyRPM := 2
	globalRPM := 1000
	ks, keyID := newKeyStoreWithKey("override", "sk-agentcc-override777", perKeyRPM)
	p := New(config.RateLimitConfig{Enabled: true, GlobalRPM: globalRPM}, ks, nil)

	// Exhaust the per-key limit (which is lower than global).
	for i := 0; i < perKeyRPM; i++ {
		rc := newRCWithKey(keyID)
		result := p.ProcessRequest(context.Background(), rc)
		if result.Action != pipeline.Continue {
			t.Fatalf("request %d: expected Continue", i+1)
		}
	}

	// Next request should be rejected by per-key limit, not global.
	rc := newRCWithKey(keyID)
	result := p.ProcessRequest(context.Background(), rc)
	if result.Action != pipeline.ShortCircuit {
		t.Fatal("expected ShortCircuit: per-key limit should override global")
	}
	if result.Error == nil {
		t.Fatal("expected APIError")
	}
	if result.Error.Status != http.StatusTooManyRequests {
		t.Errorf("status = %d, want 429", result.Error.Status)
	}

	// Verify the metadata shows the per-key limit, not the global limit.
	assertMetadata(t, rc, "ratelimit_limit", strconv.Itoa(perKeyRPM))
}

func TestProcessRequest_NoKeyStore(t *testing.T) {
	// keyStore is nil but auth_key_id is present in metadata.
	// Should fall back to global limit.
	globalRPM := 3
	p := New(config.RateLimitConfig{Enabled: true, GlobalRPM: globalRPM}, nil, nil)

	// Even with a key ID in metadata, nil keyStore means global is used.
	for i := 0; i < globalRPM; i++ {
		rc := newRCWithKey("nonexistent-key-id")
		result := p.ProcessRequest(context.Background(), rc)
		if result.Action != pipeline.Continue {
			t.Fatalf("request %d: expected Continue (global fallback)", i+1)
		}
	}

	// Should be rejected at the global limit.
	rc := newRCWithKey("nonexistent-key-id")
	result := p.ProcessRequest(context.Background(), rc)
	if result.Action != pipeline.ShortCircuit {
		t.Fatal("expected ShortCircuit at global limit")
	}
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

// newRC creates a fresh RequestContext with an initialised Metadata map.
func newRC() *models.RequestContext {
	return &models.RequestContext{
		Metadata: make(map[string]string),
	}
}

// newRCWithKey creates a RequestContext with the given auth_key_id set.
func newRCWithKey(keyID string) *models.RequestContext {
	rc := newRC()
	rc.Metadata["auth_key_id"] = keyID
	return rc
}

// newKeyStoreWithKey creates a KeyStore with a single key and returns both
// the store and the resolved key ID.
func newKeyStoreWithKey(name, rawKey string, rpm int) (*auth.KeyStore, string) {
	cfg := config.AuthConfig{
		Enabled: true,
		Keys: []config.AuthKeyConfig{
			{Name: name, Key: rawKey, RateLimitRPM: rpm},
		},
	}
	ks := auth.NewKeyStore(cfg)
	key := ks.Authenticate(rawKey)
	if key == nil {
		panic("failed to authenticate test key")
	}
	return ks, key.ID
}

// assertMetadata checks that rc.Metadata[key] equals the expected value.
func assertMetadata(t *testing.T, rc *models.RequestContext, key, want string) {
	t.Helper()
	got, ok := rc.Metadata[key]
	if !ok {
		t.Fatalf("metadata %q not set", key)
	}
	if got != want {
		t.Errorf("metadata %q = %q, want %q", key, got, want)
	}
}
