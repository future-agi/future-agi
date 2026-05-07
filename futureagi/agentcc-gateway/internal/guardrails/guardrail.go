package guardrails

import (
	"context"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// Stage indicates when a guardrail runs.
type Stage int

const (
	// StagePre runs before the provider call.
	StagePre Stage = iota
	// StagePost runs after the provider call.
	StagePost
)

// Action defines what happens when a guardrail triggers.
type Action int

const (
	// ActionBlock rejects the request with 403.
	ActionBlock Action = iota
	// ActionWarn adds a warning header but continues.
	ActionWarn
	// ActionLog records the result without affecting the request.
	ActionLog
	// ActionReflect triggers a regeneration attempt via the LLM.
	ActionReflect
)

// CheckInput is the input to a guardrail check.
type CheckInput struct {
	Request  *models.ChatCompletionRequest
	Response *models.ChatCompletionResponse // nil for pre-stage
	Metadata map[string]string
}

// CheckResult is the output of a guardrail check.
type CheckResult struct {
	Pass    bool                   // true = safe, false = triggered
	Score   float64                // 0.0 = safe, 1.0 = max violation
	Action  Action                 // what action to take if triggered
	Message string                 // human-readable explanation
	Details map[string]interface{} // guardrail-specific metadata
}

// Guardrail is the interface that all guardrail implementations must satisfy.
type Guardrail interface {
	// Name returns the guardrail identifier.
	Name() string
	// Stage returns when this guardrail runs.
	Stage() Stage
	// Check evaluates input and returns a result.
	Check(ctx context.Context, input *CheckInput) *CheckResult
}

// TriggeredGuardrail records a guardrail that was triggered during execution.
type TriggeredGuardrail struct {
	Name      string  `json:"name"`
	Score     float64 `json:"score"`
	Threshold float64 `json:"threshold"`
	Action    Action  `json:"action"`
	Message   string  `json:"message"`
}

// PipelineResult is the aggregate result of running all guardrails in a stage.
type PipelineResult struct {
	Blocked            bool
	Warnings           []string
	Triggered          []TriggeredGuardrail
	ReflexionAttempts  int
	ReflexionSucceeded bool
}

// shouldTrigger applies the configured threshold to score-based guardrails.
// Guardrails that return Pass=false without a score still trigger as hard failures.
func shouldTrigger(result *CheckResult, threshold float64) bool {
	if result == nil {
		return false
	}
	if result.Score > threshold {
		return true
	}
	return !result.Pass && result.Score <= 0
}

// RuleConfig defines the behavior when a guardrail is triggered, including reflexion settings.
type RuleConfig struct {
	OnBlock            string  `json:"on_block"`
	ReflectMaxAttempts int     `json:"reflect_max_attempts"`
	ReflectTempBump    float64 `json:"reflect_temperature_bump,omitempty"`
}
