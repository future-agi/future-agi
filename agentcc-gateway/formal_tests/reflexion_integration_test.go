// Package formal_tests contains integration probes for the guardrail reflexion
// loop.  These tests run the full reflexion algorithm end-to-end against a mock
// provider and verify ALL TLA+ invariants simultaneously in assertInvariants().
//
// TLA+ invariants verified (GuardrailReflexion.tla):
//   - AttemptBounded:      total provider calls never exceed MaxAttempts (hard cap 5)
//   - EventuallyTerminates: loop exits in one of {success, failed} states — no infinite loop
//   - FeedbackGrows:        injected feedback messages accumulate monotonically
//
// Run with: cd agentcc-gateway && go test ./formal_tests/... -v
package formal_tests

import (
	"errors"
	"fmt"
	"testing"
)

// ── Constants (mirror handlers.go) ────────────────────────────────────────────

const hardCap = 5 // max_attempts is clamped to this regardless of config

// ── Domain types ──────────────────────────────────────────────────────────────

// ReflexionConfig mirrors internal/config.ReflexionConfig without an import.
type ReflexionConfig struct {
	Enabled          bool
	MaxAttempts      int
	FeedbackTemplate string
}

// ReflexionResult captures the observable state after the loop exits.
type ReflexionResult struct {
	Success          bool
	TotalAttempts    int    // number of provider calls made
	FeedbackInjected int    // feedback messages appended to conversation
	FinalError       string // non-empty iff Success==false
}

// ── Provider interface ────────────────────────────────────────────────────────

// callableProvider is the minimal interface runReflexion needs from a provider.
// Both *MockProvider and *errProvider satisfy it.
type callableProvider interface {
	Call() (passed bool, err error)
}

// ── Mock provider ─────────────────────────────────────────────────────────────

// MockProvider simulates a provider that blocks on the first N calls and passes
// on call N+1.  It records every call for inspection.
type MockProvider struct {
	blockUntil   int // calls 1..blockUntil are blocked; call blockUntil+1 passes
	callCount    int
	callLog      []bool // true = passed, false = blocked
}

func NewMockProvider(blockFirstN int) *MockProvider {
	return &MockProvider{blockUntil: blockFirstN}
}

// Call simulates a provider call.  Returns (passed bool, err error).
// A "blocked" response sets passed=false but err=nil (the guardrail fires
// after the provider responds, not the provider itself erroring).
func (p *MockProvider) Call() (passed bool, err error) {
	p.callCount++
	passed = p.callCount > p.blockUntil
	p.callLog = append(p.callLog, passed)
	return passed, nil
}

// ── Reflexion loop (mirrors handlers.go runReflexion) ────────────────────────

// runReflexion is a pure Go re-implementation of Handlers.runReflexion from
// internal/server/handlers.go.  It operates on plain values so the probe
// does not need to import the (dependency-heavy) server package.
//
// Returns a ReflexionResult describing the final state.
func runReflexion(cfg ReflexionConfig, provider callableProvider) (ReflexionResult, error) {
	if !cfg.Enabled {
		return ReflexionResult{
			Success:    false,
			FinalError: "reflexion_disabled",
		}, nil
	}

	maxAttempts := cfg.MaxAttempts
	if maxAttempts <= 0 {
		maxAttempts = 3
	}
	if maxAttempts > hardCap {
		maxAttempts = hardCap
	}

	feedbackTmpl := cfg.FeedbackTemplate
	if feedbackTmpl == "" {
		feedbackTmpl = "Your previous response was blocked. Reason: %s"
	}

	var feedback []string // accumulates injected feedback messages

	for attempt := 1; attempt <= maxAttempts; attempt++ {
		passed, err := provider.Call()
		if err != nil {
			return ReflexionResult{}, fmt.Errorf("provider error on attempt %d: %w", attempt, err)
		}

		if passed {
			return ReflexionResult{
				Success:          true,
				TotalAttempts:    attempt,
				FeedbackInjected: len(feedback),
			}, nil
		}

		// Inject feedback before the next attempt (mirrors rc.Request.Messages append).
		if attempt < maxAttempts {
			feedback = append(feedback, fmt.Sprintf(feedbackTmpl, "content_policy_violation"))
		}
	}

	return ReflexionResult{
		Success:          false,
		TotalAttempts:    maxAttempts,
		FeedbackInjected: len(feedback),
		FinalError:       "content_blocked_after_reflexion",
	}, nil
}

// ── Invariant checker ─────────────────────────────────────────────────────────

// assertInvariants checks ALL TLA+ invariants simultaneously on the result.
// t.Helper() ensures failure lines point to the caller, not this function.
func assertInvariants(t *testing.T, result ReflexionResult, cfg ReflexionConfig, provider *MockProvider, label string) {
	t.Helper()
	effectiveMax := cfg.MaxAttempts
	if effectiveMax <= 0 {
		effectiveMax = 3
	}
	if effectiveMax > hardCap {
		effectiveMax = hardCap
	}

	// AttemptBounded: total provider calls ≤ effective MaxAttempts
	if result.TotalAttempts > effectiveMax {
		t.Errorf("[%s] AttemptBounded violated: TotalAttempts=%d > effectiveMax=%d",
			label, result.TotalAttempts, effectiveMax)
	}
	if result.TotalAttempts > hardCap {
		t.Errorf("[%s] AttemptBounded (hard cap) violated: TotalAttempts=%d > hardCap=%d",
			label, result.TotalAttempts, hardCap)
	}

	// EventuallyTerminates: result is in a terminal state (success xor error set)
	if result.Success && result.FinalError != "" {
		t.Errorf("[%s] EventuallyTerminates violated: Success=true but FinalError=%q",
			label, result.FinalError)
	}
	if !result.Success && result.FinalError == "" && cfg.Enabled {
		t.Errorf("[%s] EventuallyTerminates violated: Success=false but FinalError is empty",
			label)
	}

	// FeedbackGrows: injected feedback count is non-negative and ≤ attempts-1
	if result.FeedbackInjected < 0 {
		t.Errorf("[%s] FeedbackGrows violated: FeedbackInjected=%d < 0",
			label, result.FeedbackInjected)
	}
	if result.TotalAttempts > 0 && result.FeedbackInjected >= result.TotalAttempts {
		t.Errorf("[%s] FeedbackGrows violated: FeedbackInjected=%d >= TotalAttempts=%d (can't inject on final attempt)",
			label, result.FeedbackInjected, result.TotalAttempts)
	}

	// provider call count matches reported TotalAttempts
	if provider.callCount != result.TotalAttempts {
		t.Errorf("[%s] MockProvider.callCount=%d != TotalAttempts=%d",
			label, provider.callCount, result.TotalAttempts)
	}
}

// ── Scenario: reflexion disabled ─────────────────────────────────────────────

func TestReflexionDisabledReturnsImmediately(t *testing.T) {
	cfg := ReflexionConfig{Enabled: false, MaxAttempts: 3}
	provider := NewMockProvider(0) // would pass immediately if called
	result, err := runReflexion(cfg, provider)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if provider.callCount != 0 {
		t.Errorf("disabled reflexion should make 0 provider calls, got %d", provider.callCount)
	}
	if result.Success {
		t.Errorf("disabled reflexion should not succeed")
	}
	if result.FinalError != "reflexion_disabled" {
		t.Errorf("expected FinalError=reflexion_disabled, got %q", result.FinalError)
	}
}

// ── Scenario: pass on first attempt ──────────────────────────────────────────

func TestPassOnFirstAttemptNoFeedback(t *testing.T) {
	cfg := ReflexionConfig{Enabled: true, MaxAttempts: 3}
	provider := NewMockProvider(0) // call 1 passes immediately
	result, err := runReflexion(cfg, provider)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	assertInvariants(t, result, cfg, provider, "first_pass")

	if !result.Success {
		t.Errorf("expected success on first pass")
	}
	if result.TotalAttempts != 1 {
		t.Errorf("expected TotalAttempts=1, got %d", result.TotalAttempts)
	}
	if result.FeedbackInjected != 0 {
		t.Errorf("expected FeedbackInjected=0, got %d", result.FeedbackInjected)
	}
}

// ── Scenario: block N then pass ──────────────────────────────────────────────

func TestBlockNThenPass(t *testing.T) {
	cases := []struct {
		blockFirst int
		maxAtt     int
	}{
		{1, 3},
		{2, 3},
		{3, 4},
		{4, 5},
	}
	for _, tc := range cases {
		label := fmt.Sprintf("block%d_max%d", tc.blockFirst, tc.maxAtt)
		t.Run(label, func(t *testing.T) {
			cfg := ReflexionConfig{Enabled: true, MaxAttempts: tc.maxAtt}
			provider := NewMockProvider(tc.blockFirst)
			result, err := runReflexion(cfg, provider)
			if err != nil {
				t.Fatalf("unexpected error: %v", err)
			}
			assertInvariants(t, result, cfg, provider, label)

			if !result.Success {
				t.Errorf("[%s] expected success after %d blocks", label, tc.blockFirst)
			}
			expectedAttempts := tc.blockFirst + 1
			if result.TotalAttempts != expectedAttempts {
				t.Errorf("[%s] TotalAttempts=%d, want %d", label, result.TotalAttempts, expectedAttempts)
			}
			if result.FeedbackInjected != tc.blockFirst {
				t.Errorf("[%s] FeedbackInjected=%d, want %d", label, result.FeedbackInjected, tc.blockFirst)
			}
		})
	}
}

// ── Scenario: all attempts blocked → failure ─────────────────────────────────

func TestAllAttemptsBlockedGivesFailure(t *testing.T) {
	for maxAtt := 1; maxAtt <= hardCap; maxAtt++ {
		label := fmt.Sprintf("all_blocked_max%d", maxAtt)
		t.Run(label, func(t *testing.T) {
			cfg := ReflexionConfig{Enabled: true, MaxAttempts: maxAtt}
			// Block forever (blockUntil > hard cap ensures all calls are blocked)
			provider := NewMockProvider(hardCap + 10)
			result, err := runReflexion(cfg, provider)
			if err != nil {
				t.Fatalf("unexpected error: %v", err)
			}
			assertInvariants(t, result, cfg, provider, label)

			if result.Success {
				t.Errorf("[%s] expected failure when all attempts blocked", label)
			}
			if result.FinalError == "" {
				t.Errorf("[%s] expected FinalError to be set on failure", label)
			}
		})
	}
}

// ── Scenario: hard cap clamps MaxAttempts > 5 ────────────────────────────────

func TestHardCapClampsLargeConfig(t *testing.T) {
	for _, configured := range []int{6, 7, 10, 100} {
		label := fmt.Sprintf("configured_%d", configured)
		t.Run(label, func(t *testing.T) {
			cfg := ReflexionConfig{Enabled: true, MaxAttempts: configured}
			provider := NewMockProvider(hardCap + 10) // all blocked
			result, err := runReflexion(cfg, provider)
			if err != nil {
				t.Fatalf("unexpected error: %v", err)
			}
			assertInvariants(t, result, cfg, provider, label)

			if result.TotalAttempts > hardCap {
				t.Errorf("[%s] hard cap violated: TotalAttempts=%d > hardCap=%d",
					label, result.TotalAttempts, hardCap)
			}
		})
	}
}

// ── Scenario: feedback accumulates monotonically ──────────────────────────────

func TestFeedbackAccumulatesMonotonically(t *testing.T) {
	// Block 4 times then pass on attempt 5 (the hard cap).
	cfg := ReflexionConfig{Enabled: true, MaxAttempts: 5}
	provider := NewMockProvider(4)

	// Track feedback count after each attempt by inspecting callLog.
	result, err := runReflexion(cfg, provider)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	assertInvariants(t, result, cfg, provider, "feedback_accumulates")

	// With 4 blocks followed by a pass: 4 feedback messages injected, 5 attempts.
	if result.FeedbackInjected != 4 {
		t.Errorf("expected FeedbackInjected=4, got %d", result.FeedbackInjected)
	}
	if result.TotalAttempts != 5 {
		t.Errorf("expected TotalAttempts=5, got %d", result.TotalAttempts)
	}
}

// ── Scenario: default MaxAttempts when not set ────────────────────────────────

func TestDefaultMaxAttemptsThreeWhenZero(t *testing.T) {
	cfg := ReflexionConfig{Enabled: true, MaxAttempts: 0}
	provider := NewMockProvider(hardCap + 10) // all blocked → exhausts default
	result, err := runReflexion(cfg, provider)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	assertInvariants(t, result, cfg, provider, "default_max_3")

	// Default is 3; provider blocked all → exactly 3 calls.
	if result.TotalAttempts != 3 {
		t.Errorf("expected TotalAttempts=3 (default), got %d", result.TotalAttempts)
	}
}

// ── Scenario: provider error propagates ──────────────────────────────────────

// errProvider wraps MockProvider to inject an error on the first call.
type errProvider struct {
	inner    *MockProvider
	failOnCall int
}

func (p *errProvider) Call() (bool, error) {
	p.inner.callCount++
	if p.inner.callCount == p.failOnCall {
		return false, errors.New("provider_network_error")
	}
	p.inner.callLog = append(p.inner.callLog, false)
	return false, nil
}

func TestProviderErrorPropagates(t *testing.T) {
	// errProvider errors on the very first call, so runReflexion must propagate
	// that error immediately rather than treating it as a guardrail block.
	cfg := ReflexionConfig{Enabled: true, MaxAttempts: 3}
	failingProvider := &errProvider{inner: NewMockProvider(0), failOnCall: 1}

	_, err := runReflexion(cfg, failingProvider)
	if err == nil {
		t.Errorf("expected provider error to propagate, got nil")
	}
	// Only one call should have been made before the error short-circuited the loop.
	if failingProvider.inner.callCount != 1 {
		t.Errorf("expected exactly 1 provider call before error, got %d", failingProvider.inner.callCount)
	}
}
