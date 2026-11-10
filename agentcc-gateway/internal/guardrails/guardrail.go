package guardrails

import (
	"context"
	"fmt"
	"math"

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
)

// CheckInput is the input to a guardrail check.
type CheckInput struct {
	Request  *models.ChatCompletionRequest
	Response *models.ChatCompletionResponse // nil for pre-stage
	Metadata map[string]string
}

// CheckResult is the output of a guardrail check.
type CheckResult struct {
	// Err marks an execution failure, not a scored content decision. Engines
	// apply fail_open before threshold evaluation; implementations must set it
	// for transport, parsing, or evaluation errors.
	Err     error                  `json:"-"`
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
	Blocked   bool
	Warnings  []string
	Triggered []TriggeredGuardrail
}

// shouldTrigger applies the configured threshold to score-based guardrails.
// Execution failures bypass scores. Pass=false without a score is a hard rejection.
func shouldTrigger(result *CheckResult, threshold float64) bool {
	if result == nil {
		return false
	}
	if result.Err != nil {
		return true
	}
	if result.Score > threshold {
		return true
	}
	return !result.Pass && result.Score <= 0
}

// checkResultError validates the execution envelope without reinterpreting a
// legitimate rejecting result as an outage (which fail_open could bypass).
func checkResultError(cr *CheckResult) error {
	if cr == nil {
		return fmt.Errorf("guardrail returned no result")
	}
	if cr.Err != nil {
		return cr.Err
	}
	if math.IsNaN(cr.Score) || math.IsInf(cr.Score, 0) || cr.Score < 0 || cr.Score > 1 {
		return fmt.Errorf("guardrail returned invalid score: %v", cr.Score)
	}
	return nil
}
