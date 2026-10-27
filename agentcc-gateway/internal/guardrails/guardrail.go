package guardrails

import (
	"context"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/reflexion"
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
)

// CheckInput is the input to a guardrail check.
type CheckInput struct {
	Request    *models.ChatCompletionRequest
	Response   *models.ChatCompletionResponse // nil for pre-stage
	Metadata   map[string]string
	DeferAsync bool // asynchronous observers see only the final accepted candidate
}

// CheckResult is the output of a guardrail check.
type CheckResult struct {
	Pass             bool                   // true = safe, false = triggered
	Score            float64                // 0.0 = safe, 1.0 = max violation
	Action           Action                 // what action to take if triggered
	Message          string                 // human-readable explanation
	Details          map[string]interface{} // guardrail-specific metadata
	OperationalError bool                   // transport/auth/parse errors and timeouts/panics are not repairable content failures
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
	Name       string           `json:"name"`
	Score      float64          `json:"score"`
	Threshold  float64          `json:"threshold"`
	Action     Action           `json:"action"`
	Message    string           `json:"message"`
	Reflection reflexion.Policy `json:"-"`
}

// PipelineResult is the aggregate result of running all guardrails in a stage.
type PipelineResult struct {
	Blocked   bool
	Warnings  []string
	Triggered []TriggeredGuardrail
}

// shouldTrigger applies the configured threshold to score-based guardrails.
// Guardrails that return Pass=false without a score still trigger as hard failures.
func shouldTrigger(result *CheckResult, threshold float64) bool {
	if result == nil {
		return false
	}
	if result.OperationalError {
		return true
	}
	if result.Score > threshold {
		return true
	}
	return !result.Pass && result.Score <= 0
}
