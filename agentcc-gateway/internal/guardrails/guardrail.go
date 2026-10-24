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
	// ActionMask is an organization's check that rewrote the request it read
	// (its type's Masked variant) and lets it through, as ActionLog does.
	// Only the action "mask" on a check whose type can rewrite (Masker)
	// takes it; parseAction never returns it.
	ActionMask
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

// RequestRewriter is a check that can rewrite the request it reads, as
// pii-detection does with a remediation other than block (mask, redact or
// hash). RewritesRequest says whether this one does.
type RequestRewriter interface {
	RewritesRequest() bool
}

// rewritesRequest says whether g can rewrite the request it reads.
func rewritesRequest(g Guardrail) bool {
	r, ok := g.(RequestRewriter)
	return ok && r.RewritesRequest()
}

// Masker is a check type that can rewrite the request it reads, whatever one
// check of it is configured to do (pii-detection). Masked is the check that
// rewrites it: itself when it already does (a remediation of mask, redact
// or hash), else a copy that masks each match as "***". An organization's
// check of such a type may take the action "mask" (actionMask); a check of
// any other type cannot.
type Masker interface {
	Masked() Guardrail
}

// CanMask says whether g's type can rewrite the request it reads (Masker),
// so that an organization's check of it may take the action "mask".
func CanMask(g Guardrail) bool {
	_, ok := g.(Masker)
	return ok
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
	// Unapplied names the rule that rewrites the request and did not
	// finish, so its rewrite was not applied: the block is its refusal
	// (remediation_unavailable), and its Triggered entry says why.
	Unapplied string
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
