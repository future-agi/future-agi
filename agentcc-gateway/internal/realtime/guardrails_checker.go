package realtime

// NEW FILE: agentcc-gateway/internal/realtime/guardrail_checker.go
//
// Phase 1: parses provider->client realtime events, accumulates transcript
// text per response_id, and runs it through the existing guardrail engine
// in LOG-ONLY mode. It never blocks or mutates the stream yet — the goal
// of this phase is to prove the interception point works and to measure
// real added latency before attempting cancel/replace (Phase 2).
//
// This mirrors the pattern already used for text streaming in
// internal/guardrails/stream_checker.go, adapted for the realtime
// WebSocket event protocol instead of SSE chat chunks.

import (
	"context"
	"encoding/json"
	"log/slog"
	"sync"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/guardrails"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/policy"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// realtimeEvent is the minimal shape we need to read out of provider
// frames. Realtime APIs (OpenAI-style) send JSON text frames with a
// "type" discriminator; binary/audio frames are left untouched.
type realtimeEvent struct {
	Type       string `json:"type"`
	ResponseID string `json:"response_id"`
	Delta      string `json:"delta"`
}

// GuardrailChecker inspects transcript deltas in a realtime session and
// runs them through the guardrail engine, logging triggers without
// blocking the stream. Safe for concurrent use by the relay goroutines.
type GuardrailChecker struct {
	engine        *guardrails.Engine
	keyPolicy     *policy.Policy
	requestPolicy policy.RequestPolicy
	sessionID     string
	model         string

	mu          sync.Mutex
	accumulated map[string]*responseBuffer // response_id -> buffer
}

type responseBuffer struct {
	text      string
	lastCheck time.Time
}

// NewGuardrailChecker creates a checker bound to a single realtime session.
// engine may be nil (e.g. guardrails disabled) — callers should check
// engine.PostCount() > 0 before wiring this in, to avoid parsing overhead
// on sessions with no guardrails configured.
func NewGuardrailChecker(engine *guardrails.Engine, keyPolicy *policy.Policy, rp policy.RequestPolicy, sessionID, model string) *GuardrailChecker {
	return &GuardrailChecker{
		engine:        engine,
		keyPolicy:     keyPolicy,
		requestPolicy: rp,
		sessionID:     sessionID,
		model:         model,
		accumulated:   make(map[string]*responseBuffer),
	}
}

// Inspect is called by the relay for every provider->client frame, before
// it is forwarded to the client. It never blocks the forward path in
// Phase 1 — checks run synchronously but only log; forwarding is never
// delayed on the check completing. Returns immediately for non-JSON
// (audio) frames.
func (c *GuardrailChecker) Inspect(data []byte) {
	if c.engine == nil || c.engine.PostCount() == 0 {
		return
	}

	var evt realtimeEvent
	if err := json.Unmarshal(data, &evt); err != nil {
		// Not a JSON control frame (likely raw/base64 audio) — skip.
		return
	}

	switch evt.Type {
	case "response.audio_transcript.delta", "response.text.delta":
		c.appendDelta(evt.ResponseID, evt.Delta)
	case "response.done", "response.audio_transcript.done":
		c.checkAndClear(evt.ResponseID)
	}
}

func (c *GuardrailChecker) appendDelta(responseID, delta string) {
	if responseID == "" || delta == "" {
		return
	}
	c.mu.Lock()
	buf, ok := c.accumulated[responseID]
	if !ok {
		buf = &responseBuffer{}
		c.accumulated[responseID] = buf
	}
	buf.text += delta
	c.mu.Unlock()
}

// checkAndClear runs the guardrail engine against the full accumulated
// transcript for a response and logs the result. Phase 2 will replace the
// "log" branch with cancel + replacement-response logic.
func (c *GuardrailChecker) checkAndClear(responseID string) {
	c.mu.Lock()
	buf, ok := c.accumulated[responseID]
	if ok {
		delete(c.accumulated, responseID)
	}
	c.mu.Unlock()
	if !ok || buf.text == "" {
		return
	}

	start := time.Now()

	// Wrap the transcript in a synthetic ChatCompletionResponse so we can
	// reuse the existing guardrail Guardrail implementations unchanged —
	// they already know how to read response.Choices[0].Message.Content.
	contentJSON, err := json.Marshal(buf.text)
	if err != nil {
		slog.Error("failed to marshal transcript for guardrail check", "error", err)
		return
	}

	syntheticResp := &models.ChatCompletionResponse{
		Model: c.model,
		Choices: []models.Choice{
			{Message: models.Message{Role: "assistant", Content:json.RawMessage(contentJSON)}},
		},
	}

	result := c.engine.RunPost(context.Background(), &guardrails.CheckInput{
		Response: syntheticResp,
		Metadata: map[string]string{
			"session_id":  c.sessionID,
			"response_id": responseID,
			"channel":     "realtime_voice",
		},
	}, c.keyPolicy, c.requestPolicy)

	elapsed := time.Since(start)

	if len(result.Triggered) > 0 {
		slog.Warn("realtime guardrail triggered (log-only, Phase 1)",
			"session_id", c.sessionID,
			"response_id", responseID,
			"triggered", result.Triggered,
			"check_latency_ms", elapsed.Milliseconds(),
		)
	} else {
		slog.Debug("realtime guardrail check passed",
			"session_id", c.sessionID,
			"response_id", responseID,
			"check_latency_ms", elapsed.Milliseconds(),
		)
	}
}
