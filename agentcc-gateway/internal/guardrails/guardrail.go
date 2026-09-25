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
)

// TrajectoryContext is the request-scoped correlation snapshot available to
// stateful guardrails. It intentionally contains identity/correlation data only;
// policy, risk, and accumulated trajectory state belong in separate layers.
type TrajectoryContext struct {
	RequestID    string
	TraceID      string
	SessionID    string
	UserID       string
	A2ATaskID    string
	A2AContextID string
	AgentID      string
}

// TrajectoryContextFromContext returns the normalized correlation context for a
// guardrail invocation. AgentID is correlation-only metadata and must never be
// treated as an authorization identity.
func TrajectoryContextFromContext(ctx context.Context) *TrajectoryContext {
	rc := models.GetRequestContext(ctx)
	if rc == nil {
		return nil
	}

	return &TrajectoryContext{
		RequestID:    rc.RequestID,
		TraceID:      rc.TraceID,
		SessionID:    rc.SessionID,
		UserID:       rc.UserID,
		A2ATaskID:    rc.Metadata["a2a_task_id"],
		A2AContextID: rc.Metadata["a2a_context_id"],
		AgentID:      firstNonEmpty(rc.Metadata["gen_ai.agent.id"], rc.Metadata["agent_id"]),
	}
}

func firstNonEmpty(values ...string) string {
	for _, value := range values {
		if value != "" {
			return value
		}
	}
	return ""
}

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
	if result.Score > threshold {
		return true
	}
	return !result.Pass && result.Score <= 0
}
