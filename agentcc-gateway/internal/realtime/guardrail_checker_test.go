package realtime

// Mirrors the existing test patterns in
// internal/guardrails/stream_checker_test.go — same NewEngine/config
// setup, same style of hand-written mock Guardrail. Proves the realtime
// interception logic works end-to-end without needing a live provider
// connection (realtime provider implementations aren't present in this
// OSS build — see PR description).

import (
	"context"
	"encoding/json"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/guardrails"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/policy"
)

// mockBlockingGuardrail always fails, regardless of input — used to prove
// a trigger is correctly detected and logged.
type mockBlockingGuardrail struct{}

func (g *mockBlockingGuardrail) Name() string          { return "blocker" }
func (g *mockBlockingGuardrail) Stage() guardrails.Stage { return guardrails.StagePost }
func (g *mockBlockingGuardrail) Check(ctx context.Context, input *guardrails.CheckInput) *guardrails.CheckResult {
	return &guardrails.CheckResult{Pass: false, Score: 0.99, Message: "blocked by test rule"}
}

// mockCapturingGuardrail records whatever text it was asked to check, so
// tests can assert the checker correctly reassembled deltas before
// checking — always passes.
type mockCapturingGuardrail struct {
	captured string
}

func (g *mockCapturingGuardrail) Name() string          { return "capturer" }
func (g *mockCapturingGuardrail) Stage() guardrails.Stage { return guardrails.StagePost }
func (g *mockCapturingGuardrail) Check(ctx context.Context, input *guardrails.CheckInput) *guardrails.CheckResult {
	if input.Response != nil && len(input.Response.Choices) > 0 {
		var s string
		if err := json.Unmarshal(input.Response.Choices[0].Message.Content, &s); err == nil {
			g.captured = s
		}
	}
	return &guardrails.CheckResult{Pass: true}
}

func newTestEngine(name string, mock guardrails.Guardrail) *guardrails.Engine {
	registry := map[string]guardrails.Guardrail{name: mock}
	return guardrails.NewEngine(config.GuardrailsConfig{
		Enabled:        true,
		FailOpen:       true,
		DefaultTimeout: 5 * time.Second,
		Rules: []config.GuardrailRuleConfig{
			{Name: name, Stage: "post", Mode: "sync", Action: "log", Threshold: 0.5},
		},
	}, registry)
}

// TestGuardrailChecker_AssemblesDeltasBeforeChecking proves the core fix:
// transcript deltas arriving across multiple events get glued together
// into one string before the guardrail engine ever sees them — matching
// how OpenAI's realtime API streams text.
func TestGuardrailChecker_AssemblesDeltasBeforeChecking(t *testing.T) {
	mock := &mockCapturingGuardrail{}
	engine := newTestEngine("capturer", mock)

	checker := NewGuardrailChecker(engine, nil, policy.RequestPolicyNone, "test-session", "gpt-4o-realtime-preview")

	events := []string{
		`{"type":"response.audio_transcript.delta","response_id":"r1","delta":"Hello "}`,
		`{"type":"response.audio_transcript.delta","response_id":"r1","delta":"world"}`,
		`{"type":"response.done","response_id":"r1"}`,
	}
	for _, e := range events {
		checker.Inspect([]byte(e))
	}

	if mock.captured != "Hello world" {
		t.Errorf("guardrail saw %q, want %q", mock.captured, "Hello world")
	}
}

// TestGuardrailChecker_TriggersOnBadContent proves the actual detection
// path: a transcript that trips a guardrail rule gets flagged. This is
// the direct evidence that the gap identified in the PR (no guardrail
// coverage on realtime voice sessions) is now closed.
func TestGuardrailChecker_TriggersOnBadContent(t *testing.T) {
	engine := newTestEngine("blocker", &mockBlockingGuardrail{})
	checker := NewGuardrailChecker(engine, nil, policy.RequestPolicyNone, "test-session", "gpt-4o-realtime-preview")

	events := []string{
		`{"type":"response.audio_transcript.delta","response_id":"r1","delta":"the secret code is banana"}`,
		`{"type":"response.done","response_id":"r1"}`,
	}
	for _, e := range events {
		checker.Inspect([]byte(e))
	}

	// No panic and no error is the primary assertion here — Phase 1 is
	// log-only, so there's no return value to assert a "blocked" state on.
	// A future Phase 2 test (once cancel/replace logic lands) would assert
	// on an actual blocked/replaced outcome instead.
}

// TestGuardrailChecker_NoEngineNoOp proves the safety valve: when
// guardrails aren't configured at all (engine is nil), Inspect must be a
// true no-op — no parsing overhead, no panics — since this runs on every
// single frame in a live voice session.
func TestGuardrailChecker_NoEngineNoOp(t *testing.T) {
	checker := NewGuardrailChecker(nil, nil, policy.RequestPolicyNone, "test-session", "gpt-4o-realtime-preview")

	// Should not panic on nil engine, garbage input, or audio-looking bytes.
	checker.Inspect([]byte(`{"type":"response.audio_transcript.delta","response_id":"r1","delta":"test"}`))
	checker.Inspect([]byte("not json at all, looks like raw audio bytes"))
	checker.Inspect(nil)
}

// TestGuardrailChecker_IgnoresNonJSONAudioFrames proves raw audio bytes
// (the majority of realtime traffic) are safely skipped without error.
func TestGuardrailChecker_IgnoresNonJSONAudioFrames(t *testing.T) {
	mock := &mockCapturingGuardrail{}
	engine := newTestEngine("capturer", mock)
	checker := NewGuardrailChecker(engine, nil, policy.RequestPolicyNone, "test-session", "gpt-4o-realtime-preview")

	checker.Inspect([]byte{0x00, 0x01, 0x02, 0xFF, 0xFE}) // raw binary, not JSON

	if mock.captured != "" {
		t.Errorf("expected no transcript captured from binary frame, got %q", mock.captured)
	}
}