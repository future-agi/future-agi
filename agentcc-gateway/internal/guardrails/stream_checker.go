package guardrails

import (
	"context"
	"encoding/json"
	"log/slog"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/policy"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// StreamGuardrailChecker inspects streaming response chunks and runs
// post-stage guardrails on accumulated text at configurable intervals.
type StreamGuardrailChecker struct {
	engine        *Engine
	keyPolicy     *policy.Policy
	requestPolicy policy.RequestPolicy
	request       *models.ChatCompletionRequest
	metadata      map[string]string
	checkInterval int    // chars between checks (0 = only check at end)
	failureAction string // "stop" or "disclaimer"
	accumulated   strings.Builder
	lastCheckLen  int
}

// NewStreamChecker creates a new streaming guardrail checker.
func NewStreamChecker(
	engine *Engine,
	cfg config.StreamingGuardrailConfig,
	keyPolicy *policy.Policy,
	requestPolicy policy.RequestPolicy,
	request *models.ChatCompletionRequest,
	metadata map[string]string,
) *StreamGuardrailChecker {
	interval := cfg.CheckInterval
	if interval <= 0 {
		interval = 100 // default: check every 100 chars
	}
	action := cfg.FailureAction
	if action == "" {
		action = "stop"
	}
	return &StreamGuardrailChecker{
		engine:        engine,
		keyPolicy:     keyPolicy,
		requestPolicy: requestPolicy,
		request:       request,
		metadata:      metadata,
		checkInterval: interval,
		failureAction: action,
	}
}

// StreamCheckResult indicates the outcome of processing a chunk.
type StreamCheckResult struct {
	// Blocked is true if a guardrail triggered and the stream should stop.
	Blocked bool
	// Message is the guardrail message when blocked.
	Message string
	// Disclaimer is set when failureAction is "disclaimer" and guardrail triggered at end.
	Disclaimer string
}

// ProcessChunk accumulates text from a stream chunk and runs guardrails
// if enough new content has been received. Returns a result indicating
// whether the stream should continue.
func (sc *StreamGuardrailChecker) ProcessChunk(ctx context.Context, chunk models.StreamChunk) *StreamCheckResult {
	// Extract delta content.
	for _, choice := range chunk.Choices {
		if choice.Delta.Content != nil {
			sc.accumulated.WriteString(*choice.Delta.Content)
		}
	}

	// Check if we've accumulated enough new content to run guardrails.
	currentLen := sc.accumulated.Len()
	if currentLen-sc.lastCheckLen < sc.checkInterval {
		return &StreamCheckResult{} // not enough new content yet
	}

	return sc.runCheck(ctx)
}

// Finish runs a final guardrail check on the full accumulated text.
// Should be called when the stream is complete.
func (sc *StreamGuardrailChecker) Finish(ctx context.Context) *StreamCheckResult {
	if sc.accumulated.Len() == 0 {
		return &StreamCheckResult{}
	}
	// Only run if we haven't just checked at this length.
	if sc.accumulated.Len() == sc.lastCheckLen {
		return &StreamCheckResult{}
	}
	return sc.runCheck(ctx)
}

// AccumulatedText returns the full accumulated response text.
func (sc *StreamGuardrailChecker) AccumulatedText() string {
	return sc.accumulated.String()
}

func (sc *StreamGuardrailChecker) runCheck(ctx context.Context) *StreamCheckResult {
	sc.lastCheckLen = sc.accumulated.Len()

	if sc.engine == nil || sc.engine.PostCount() == 0 {
		return &StreamCheckResult{}
	}

	// Build a synthetic response for the guardrails.
	text := sc.accumulated.String()
	contentJSON, _ := json.Marshal(text)

	input := &CheckInput{
		Request: sc.request,
		Response: &models.ChatCompletionResponse{
			Choices: []models.Choice{
				{
					Message: models.Message{
						Role:    "assistant",
						Content: contentJSON,
					},
				},
			},
		},
		Metadata: sc.metadata,
	}

	result := sc.engine.RunPost(ctx, input, sc.keyPolicy, sc.requestPolicy)
	if !result.Blocked {
		return &StreamCheckResult{}
	}

	msg := "Content blocked by streaming guardrail"
	if len(result.Triggered) > 0 {
		msg = result.Triggered[0].Message
	}

	slog.Info("streaming guardrail triggered",
		"action", sc.failureAction,
		"message", msg,
		"accumulated_chars", sc.accumulated.Len(),
	)

	if sc.failureAction == "disclaimer" {
		return &StreamCheckResult{
			Disclaimer: "\n\n[Content warning: " + msg + "]",
		}
	}

	return &StreamCheckResult{
		Blocked: true,
		Message: msg,
	}
}
