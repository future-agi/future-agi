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

// TrajectoryContext is the request-scoped correlation snapshot available to
// stateful guardrails. It intentionally contains identity/correlation data only;
// policy, risk, and accumulated trajectory state belong in separate layers.
//
// Every field is a plain string copied out of the RequestContext, so the
// snapshot stays valid after the request ends and the RequestContext goes back
// to its pool. A guardrail that needs these values later must keep this
// snapshot, never the RequestContext itself.
//
// Most fields are asserted by the caller and none of them is authenticated:
// they say which activity belongs together, not who may do what. An empty
// field means the request did not carry that identifier.
type TrajectoryContext struct {
	// RequestID is the gateway's ID for this one request.
	RequestID string
	// TraceID is the caller's x-agentcc-trace-id header, or one the gateway
	// generated. Routes that do not read it leave it empty.
	TraceID string
	// SessionID is the caller's x-agentcc-session-id header, or the
	// session_id in the body's metadata.
	SessionID string
	// UserID is the body's user field, or the API key's owner.
	UserID string
	// ConversationID is the caller's conversation or chat thread: metadata
	// key gen_ai.conversation.id, falling back to conversation_id. It is
	// narrower than SessionID, which can span unrelated tasks. The gateway
	// never derives it from SessionID or A2AContextID.
	ConversationID string
	// A2ATaskID and A2AContextID are the A2A task and its context: metadata
	// keys a2a_task_id and a2a_context_id, which the gateway's A2A server
	// sets. On other routes a caller's metadata can carry the same keys.
	A2ATaskID    string
	A2AContextID string
	// AgentID is the caller's agent: metadata key gen_ai.agent.id, falling
	// back to agent_id.
	AgentID string
}

// TrajectoryContextFromContext returns the normalized correlation context for a
// guardrail invocation, or nil when ctx carries no RequestContext. The
// pipeline attaches one for plugins' ProcessRequest and ProcessResponse
// calls; guardrails that the engine runs asynchronously get nil.
//
// Call it at the start of Check, on Check's own goroutine, and keep the
// result: a check that times out goes on running after the request may have
// ended. AgentID is correlation-only metadata and must never be treated as an
// authorization identity.
func TrajectoryContextFromContext(ctx context.Context) *TrajectoryContext {
	rc := models.GetRequestContext(ctx)
	if rc == nil {
		return nil
	}

	return &TrajectoryContext{
		RequestID:      rc.RequestID,
		TraceID:        rc.TraceID,
		SessionID:      rc.SessionID,
		UserID:         rc.UserID,
		ConversationID: firstNonEmpty(rc.Metadata["gen_ai.conversation.id"], rc.Metadata["conversation_id"]),
		A2ATaskID:      rc.Metadata["a2a_task_id"],
		A2AContextID:   rc.Metadata["a2a_context_id"],
		AgentID:        firstNonEmpty(rc.Metadata["gen_ai.agent.id"], rc.Metadata["agent_id"]),
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
	// Err marks an execution failure, not a scored content decision. Engines
	// apply fail_open before threshold evaluation, and realtime blocking policies
	// refuse it regardless of score threshold; implementations must set it for
	// transport, parsing, or evaluation errors.
	Err error `json:"-"`
	// FailureAdmitted records an unavailable check admitted by explicit policy.
	FailureAdmitted bool `json:"-"`
	// RealtimeErr marks a verdict envelope that realtime checks refuse as
	// unavailable. Chat checks keep the decoded verdict and ignore it.
	RealtimeErr error                  `json:"-"`
	Pass        bool                   // true = safe, false = triggered
	Score       float64                // 0.0 = safe, 1.0 = max violation
	Action      Action                 // what action to take if triggered
	Message     string                 // human-readable explanation
	Details     map[string]interface{} // guardrail-specific metadata
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
	// Unavailable marks a blocking execution failure, never a content verdict.
	Unavailable bool
	Blocked     bool
	Warnings    []string
	Triggered   []TriggeredGuardrail
}

// shouldTrigger applies the configured threshold to score-based guardrails.
// Execution failures trigger regardless of score. Pass=false without a score is a hard rejection.
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
// legitimate rejecting result as an outage (which fail_open would then let through).
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

// RequestTextChecker is implemented by checks that inspect only request
// messages. Realtime post-stage text is passed to them as a request message.
type RequestTextChecker interface {
	ChecksRequestTextOnly()
}
