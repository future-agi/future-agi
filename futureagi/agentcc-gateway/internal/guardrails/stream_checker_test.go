package guardrails

import (
	"context"
	"encoding/json"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/policy"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

func makeChunk(content string) models.StreamChunk {
	return models.StreamChunk{
		Choices: []models.StreamChoice{
			{Delta: models.Delta{Content: &content}},
		},
	}
}

func TestStreamChecker_AccumulatesText(t *testing.T) {
	// No post guardrails — just accumulates.
	engine := NewEngine(config.GuardrailsConfig{
		Enabled:        true,
		FailOpen:       true,
		DefaultTimeout: 5 * time.Second,
	}, nil)

	sc := NewStreamChecker(engine, config.StreamingGuardrailConfig{
		Enabled:       true,
		CheckInterval: 100,
		FailureAction: "stop",
	}, nil, policy.RequestPolicyNone, &models.ChatCompletionRequest{Model: "gpt-4o"}, nil)

	sc.ProcessChunk(context.Background(), makeChunk("Hello "), nil)
	sc.ProcessChunk(context.Background(), makeChunk("world"), nil)

	if sc.AccumulatedText() != "Hello world" {
		t.Errorf("accumulated = %q, want 'Hello world'", sc.AccumulatedText())
	}
}

func TestStreamChecker_NoBlockWithoutGuardrails(t *testing.T) {
	engine := NewEngine(config.GuardrailsConfig{
		Enabled:        true,
		FailOpen:       true,
		DefaultTimeout: 5 * time.Second,
	}, nil)

	sc := NewStreamChecker(engine, config.StreamingGuardrailConfig{
		Enabled:       true,
		CheckInterval: 5,
		FailureAction: "stop",
	}, nil, policy.RequestPolicyNone, &models.ChatCompletionRequest{Model: "gpt-4o"}, nil)

	res := sc.ProcessChunk(context.Background(), makeChunk("Hello world this is a test"), nil)
	if res.Blocked {
		t.Fatal("should not block with no guardrails")
	}
}

func TestStreamChecker_BlocksOnTrigger(t *testing.T) {
	// Create a guardrail that always blocks.
	registry := map[string]Guardrail{
		"blocker": &mockGuardrail{
			name:  "blocker",
			stage: StagePost,
			result: &CheckResult{
				Pass:    false,
				Score:   0.95,
				Message: "toxic content detected",
			},
		},
	}
	engine := NewEngine(config.GuardrailsConfig{
		Enabled:        true,
		FailOpen:       true,
		DefaultTimeout: 5 * time.Second,
		Rules: []config.GuardrailRuleConfig{
			{Name: "blocker", Stage: "post", Mode: "sync", Action: "block", Threshold: 0.5},
		},
	}, registry)

	sc := NewStreamChecker(engine, config.StreamingGuardrailConfig{
		Enabled:       true,
		CheckInterval: 10,
		FailureAction: "stop",
	}, nil, policy.RequestPolicyNone, &models.ChatCompletionRequest{Model: "gpt-4o"}, nil)

	// Send enough content to trigger a check.
	res := sc.ProcessChunk(context.Background(), makeChunk("This is more than ten characters of content"), nil)
	if !res.Blocked {
		t.Fatal("expected block when guardrail triggers")
	}
	if res.Message != "toxic content detected" {
		t.Errorf("message = %q", res.Message)
	}
}

func TestStreamChecker_DisclaimerMode(t *testing.T) {
	registry := map[string]Guardrail{
		"blocker": &mockGuardrail{
			name:  "blocker",
			stage: StagePost,
			result: &CheckResult{
				Pass:    false,
				Score:   0.95,
				Message: "policy violation",
			},
		},
	}
	engine := NewEngine(config.GuardrailsConfig{
		Enabled:        true,
		FailOpen:       true,
		DefaultTimeout: 5 * time.Second,
		Rules: []config.GuardrailRuleConfig{
			{Name: "blocker", Stage: "post", Mode: "sync", Action: "block", Threshold: 0.5},
		},
	}, registry)

	sc := NewStreamChecker(engine, config.StreamingGuardrailConfig{
		Enabled:       true,
		CheckInterval: 10,
		FailureAction: "disclaimer",
	}, nil, policy.RequestPolicyNone, &models.ChatCompletionRequest{Model: "gpt-4o"}, nil)

	res := sc.ProcessChunk(context.Background(), makeChunk("Some long content that exceeds the interval"), nil)
	if res.Blocked {
		t.Fatal("disclaimer mode should not block")
	}
	if res.Disclaimer == "" {
		t.Fatal("expected disclaimer text")
	}
}

func TestStreamChecker_IntervalRespected(t *testing.T) {
	var checkCount int
	registry := map[string]Guardrail{
		"counter": &countingGuardrail{
			name:      "counter",
			stage:     StagePost,
			result:    &CheckResult{Pass: true, Score: 0.0},
			callCount: &checkCount,
		},
	}
	engine := NewEngine(config.GuardrailsConfig{
		Enabled:        true,
		FailOpen:       true,
		DefaultTimeout: 5 * time.Second,
		Rules: []config.GuardrailRuleConfig{
			{Name: "counter", Stage: "post", Mode: "sync", Action: "log", Threshold: 0.5},
		},
	}, registry)

	sc := NewStreamChecker(engine, config.StreamingGuardrailConfig{
		Enabled:       true,
		CheckInterval: 20,
		FailureAction: "stop",
	}, nil, policy.RequestPolicyNone, &models.ChatCompletionRequest{Model: "gpt-4o"}, nil)

	// Send 5 chars — not enough for a check.
	sc.ProcessChunk(context.Background(), makeChunk("Hello"), nil)
	if checkCount != 0 {
		t.Errorf("expected 0 checks after 5 chars, got %d", checkCount)
	}

	// Send 20 more — should trigger a check.
	sc.ProcessChunk(context.Background(), makeChunk("This is twenty chars!"), nil)
	if checkCount != 1 {
		t.Errorf("expected 1 check after 25 chars, got %d", checkCount)
	}
}

func TestStreamChecker_FinishRunsCheck(t *testing.T) {
	var checkCount int
	registry := map[string]Guardrail{
		"counter": &countingGuardrail{
			name:      "counter",
			stage:     StagePost,
			result:    &CheckResult{Pass: true, Score: 0.0},
			callCount: &checkCount,
		},
	}
	engine := NewEngine(config.GuardrailsConfig{
		Enabled:        true,
		FailOpen:       true,
		DefaultTimeout: 5 * time.Second,
		Rules: []config.GuardrailRuleConfig{
			{Name: "counter", Stage: "post", Mode: "sync", Action: "log", Threshold: 0.5},
		},
	}, registry)

	sc := NewStreamChecker(engine, config.StreamingGuardrailConfig{
		Enabled:       true,
		CheckInterval: 1000, // high interval, won't trigger during chunks
		FailureAction: "stop",
	}, nil, policy.RequestPolicyNone, &models.ChatCompletionRequest{Model: "gpt-4o"}, nil)

	sc.ProcessChunk(context.Background(), makeChunk("Short"), nil)
	if checkCount != 0 {
		t.Fatalf("no check expected yet, got %d", checkCount)
	}

	// Finish should run the final check.
	sc.Finish(context.Background(), nil)
	if checkCount != 1 {
		t.Errorf("expected 1 check on finish, got %d", checkCount)
	}
}

func TestStreamChecker_FinishNoDoubleCheck(t *testing.T) {
	var checkCount int
	registry := map[string]Guardrail{
		"counter": &countingGuardrail{
			name:      "counter",
			stage:     StagePost,
			result:    &CheckResult{Pass: true, Score: 0.0},
			callCount: &checkCount,
		},
	}
	engine := NewEngine(config.GuardrailsConfig{
		Enabled:        true,
		FailOpen:       true,
		DefaultTimeout: 5 * time.Second,
		Rules: []config.GuardrailRuleConfig{
			{Name: "counter", Stage: "post", Mode: "sync", Action: "log", Threshold: 0.5},
		},
	}, registry)

	sc := NewStreamChecker(engine, config.StreamingGuardrailConfig{
		Enabled:       true,
		CheckInterval: 5,
		FailureAction: "stop",
	}, nil, policy.RequestPolicyNone, &models.ChatCompletionRequest{Model: "gpt-4o"}, nil)

	// Send enough to trigger a check.
	sc.ProcessChunk(context.Background(), makeChunk("Hello World!"), nil)
	if checkCount != 1 {
		t.Fatalf("expected 1 check, got %d", checkCount)
	}

	// Finish should NOT re-check since we just checked at this length.
	// But we'll add a tiny bit more content to test.
	res := sc.Finish(context.Background(), nil)
	// lastCheckLen should equal accumulated len, so Finish skips.
	if res.Blocked {
		t.Fatal("should not block")
	}
}

func TestStreamChecker_EmptyStreamFinish(t *testing.T) {
	engine := NewEngine(config.GuardrailsConfig{
		Enabled:        true,
		FailOpen:       true,
		DefaultTimeout: 5 * time.Second,
	}, nil)

	sc := NewStreamChecker(engine, config.StreamingGuardrailConfig{
		Enabled:       true,
		CheckInterval: 10,
		FailureAction: "stop",
	}, nil, policy.RequestPolicyNone, &models.ChatCompletionRequest{Model: "gpt-4o"}, nil)

	res := sc.Finish(context.Background(), nil)
	if res.Blocked {
		t.Fatal("empty stream should not block")
	}
}

func TestStreamChecker_WithPolicy(t *testing.T) {
	registry := map[string]Guardrail{
		"blocker": &mockGuardrail{
			name:  "blocker",
			stage: StagePost,
			result: &CheckResult{
				Pass:    false,
				Score:   0.95,
				Message: "blocked",
			},
		},
	}
	engine := NewEngine(config.GuardrailsConfig{
		Enabled:        true,
		FailOpen:       true,
		DefaultTimeout: 5 * time.Second,
		Rules: []config.GuardrailRuleConfig{
			{Name: "blocker", Stage: "post", Mode: "sync", Action: "block", Threshold: 0.5},
		},
	}, registry)

	// Policy disables the blocker.
	p := &policy.Policy{
		Overrides: map[string]policy.Override{
			"blocker": {Disabled: true},
		},
	}

	sc := NewStreamChecker(engine, config.StreamingGuardrailConfig{
		Enabled:       true,
		CheckInterval: 5,
		FailureAction: "stop",
	}, p, policy.RequestPolicyNone, &models.ChatCompletionRequest{Model: "gpt-4o"}, nil)

	res := sc.ProcessChunk(context.Background(), makeChunk("Lots of content exceeding interval"), nil)
	if res.Blocked {
		t.Fatal("policy should disable the blocker")
	}
}

func TestStreamChecker_SynthesizesCorrectResponse(t *testing.T) {
	// Use a guardrail that captures the input to verify the synthetic response.
	var capturedContent string
	registry := map[string]Guardrail{
		"inspector": &inspectorGuardrail{
			name:  "inspector",
			stage: StagePost,
			onCheck: func(input *CheckInput) *CheckResult {
				if input.Response != nil && len(input.Response.Choices) > 0 {
					var s string
					if err := json.Unmarshal(input.Response.Choices[0].Message.Content, &s); err == nil {
						capturedContent = s
					}
				}
				return &CheckResult{Pass: true}
			},
		},
	}
	engine := NewEngine(config.GuardrailsConfig{
		Enabled:        true,
		FailOpen:       true,
		DefaultTimeout: 5 * time.Second,
		Rules: []config.GuardrailRuleConfig{
			{Name: "inspector", Stage: "post", Mode: "sync", Action: "log", Threshold: 0.5},
		},
	}, registry)

	sc := NewStreamChecker(engine, config.StreamingGuardrailConfig{
		Enabled:       true,
		CheckInterval: 5,
		FailureAction: "stop",
	}, nil, policy.RequestPolicyNone, &models.ChatCompletionRequest{Model: "gpt-4o"}, nil)

	sc.ProcessChunk(context.Background(), makeChunk("Hello "), nil)
	sc.ProcessChunk(context.Background(), makeChunk("World"), nil)

	if capturedContent != "Hello World" {
		t.Errorf("captured = %q, want 'Hello World'", capturedContent)
	}
}

// --- Test Helpers ---

type countingGuardrail struct {
	name      string
	stage     Stage
	result    *CheckResult
	callCount *int
}

func (g *countingGuardrail) Name() string { return g.name }
func (g *countingGuardrail) Stage() Stage { return g.stage }
func (g *countingGuardrail) Check(ctx context.Context, input *CheckInput) *CheckResult {
	*g.callCount++
	return g.result
}

type inspectorGuardrail struct {
	name    string
	stage   Stage
	onCheck func(*CheckInput) *CheckResult
}

func (g *inspectorGuardrail) Name() string { return g.name }
func (g *inspectorGuardrail) Stage() Stage { return g.stage }
func (g *inspectorGuardrail) Check(ctx context.Context, input *CheckInput) *CheckResult {
	return g.onCheck(input)
}
