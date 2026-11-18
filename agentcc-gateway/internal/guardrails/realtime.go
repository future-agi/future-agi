package guardrails

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"log/slog"
	"maps"
	"math"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/guardrails/policy"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// ErrRealtimeUnavailable marks a supported checker whose required dependency
// configuration is unavailable. Unsupported protocols or invalid local rules do
// not use this classification.
var ErrRealtimeUnavailable = errors.New("realtime guardrail unavailable")

// RealtimePolicy is an immutable session snapshot. Blocking checks run synchronously,
// fail closed, and never share a request with another check or with the relay.
// Reconnect after changing org/key policy to start a session with the new policy.
type RealtimePolicy struct {
	rules    [2][]resolvedRule
	metadata map[string]string
	model    string
}

// BindRealtime uses the same authenticated org/key/request policy precedence as chat
// and resolves configured checks before opening the speech session.
func (p *GuardrailPlugin) BindRealtime(rc *models.RequestContext) (*RealtimePolicy, *models.APIError) {
	merged, requested, err := p.resolvePolicy(rc)
	if err != nil {
		return nil, err
	}
	bound := &RealtimePolicy{metadata: maps.Clone(rc.Metadata), model: rc.Model}
	if requested == policy.RequestPolicyDisabled {
		return bound, nil
	}
	add := func(stage Stage, rule resolvedRule) {
		if merged != nil {
			if ov, ok := merged.Overrides[rule.guardrail.Name()]; ok {
				if ov.Disabled {
					return
				}
				if ov.Action != "" {
					rule.action = parseAction(ov.Action)
				}
				if ov.Threshold != nil {
					rule.threshold = *ov.Threshold
				}
			}
		}
		switch requested {
		case policy.RequestPolicyLogOnly:
			rule.action = ActionLog
		case policy.RequestPolicyStrict:
			rule.action, rule.threshold = ActionBlock, 0
		}
		rule.mode = "sync"
		bound.rules[stage] = append(bound.rules[stage], rule)
	}
	if p.engine != nil {
		for _, rule := range p.engine.unresolved {
			action := parseAction(rule.Action)
			if merged != nil {
				ov := merged.Overrides[rule.Name]
				if ov.Disabled {
					continue
				}
				if ov.Action != "" {
					action = parseAction(ov.Action)
				}
			}
			if requested == policy.RequestPolicyLogOnly {
				action = ActionLog
			}
			if requested == policy.RequestPolicyStrict {
				action = ActionBlock
			}
			if action == ActionBlock {
				return nil, realtimeUnsupported("configured guardrail implementation or stage is unavailable")
			}
		}
		for _, rule := range p.engine.preGuardrails {
			add(StagePre, rule)
		}
		for _, rule := range p.engine.postGuardrails {
			add(StagePost, rule)
		}
	}
	if p.tenantStore != nil {
		org := p.tenantStore.Get(rc.Metadata["org_id"])
		if org != nil && org.Guardrails != nil {
			for name, check := range org.Guardrails.Checks {
				if check == nil || !check.Enabled || (p.engine != nil && p.engine.HasRule(name)) {
					continue
				}
				if merged != nil && merged.Overrides[name].Disabled {
					continue
				}
				action := parseAction(check.Action)
				if merged != nil && merged.Overrides[name].Action != "" {
					action = parseAction(merged.Overrides[name].Action)
				}
				if requested == policy.RequestPolicyLogOnly {
					action = ActionLog
				}
				if requested == policy.RequestPolicyStrict {
					action = ActionBlock
				}
				g := p.getGuardrail(rc.Metadata["org_id"], name, check)
				if g == nil || (g.Stage() != StagePre && g.Stage() != StagePost) {
					if action == ActionBlock {
						return nil, realtimeUnsupported("configured guardrail implementation or stage is unavailable")
					}
					continue
				}
				timeout := p.defaultTimeout
				if org.Guardrails.TimeoutMs > 0 {
					timeout = time.Duration(org.Guardrails.TimeoutMs) * time.Millisecond
				}
				add(g.Stage(), resolvedRule{guardrail: g, action: parseAction(check.Action), threshold: check.ConfidenceThreshold, timeout: timeout})
			}
		}
	}
	for _, rules := range bound.rules {
		for _, rule := range rules {
			if rule.action != ActionBlock {
				continue
			}
			if math.IsNaN(rule.threshold) || math.IsInf(rule.threshold, 0) || rule.threshold < 0 || rule.threshold > 1 {
				return nil, realtimeUnsupported("configured guardrail threshold is invalid")
			}
			if ready, ok := rule.guardrail.(interface{ RealtimeReady() error }); ok {
				if err := ready.RealtimeReady(); err != nil {
					if errors.Is(err, ErrRealtimeUnavailable) {
						return nil, &models.APIError{Status: 503, Type: models.ErrTypeGuardrail,
							Code: "guardrail_unavailable", Message: "configured guardrail is unavailable for realtime checks"}
					}
					return nil, realtimeUnsupported("configured guardrail is unavailable for realtime checks")
				}
			}
		}
	}
	return bound, nil
}

func realtimeUnsupported(detail string) *models.APIError {
	return &models.APIError{Status: 403, Type: models.ErrTypeGuardrail, Code: "realtime_guardrail_unsupported", Message: detail}
}

// Has reports whether this direction has configured checks, including warn/log checks.
func (p *RealtimePolicy) Has(stage Stage) bool { return p != nil && len(p.rules[stage]) > 0 }
func (p *RealtimePolicy) Blocking(stage Stage) bool {
	if p != nil {
		for _, rule := range p.rules[stage] {
			if rule.action == ActionBlock {
				return true
			}
		}
	}
	return false
}

// Check inspects a complete text unit before the original frames can be forwarded.
// Rewriting a copy cannot redact the wire payload: a blocking rewrite is refused too.
func (p *RealtimePolicy) Check(ctx context.Context, stage Stage, text string) error {
	return p.CheckModel(ctx, stage, "", text)
}

// CheckModel is Check for a unit whose own request names its model, as Cartesia
// synthesis does in each message after the session was bound. An empty model
// means the session's model.
func (p *RealtimePolicy) CheckModel(ctx context.Context, stage Stage, model, text string) error {
	if p == nil {
		return nil
	}
	if model == "" {
		model = p.model
	}
	for _, rule := range p.rules[stage] {
		content, _ := json.Marshal(text)
		// Post-stage text is vendor output: it is the response, and the request
		// keeps only the model, as a chat post check sees the text once. Checks
		// that read only request messages receive it as a request message.
		request := &models.ChatCompletionRequest{Model: model}
		input := &CheckInput{Request: request, Metadata: maps.Clone(p.metadata)}
		if _, requestOnly := rule.guardrail.(RequestTextChecker); stage == StagePost && !requestOnly {
			input.Response = &models.ChatCompletionResponse{Model: model, Choices: []models.Choice{{Message: models.Message{Role: "assistant", Content: content}}}}
		} else {
			request.Messages = []models.Message{{Role: "user", Content: content}}
		}
		result, err := runRealtimeCheck(ctx, rule, input)
		if err != nil || shouldTrigger(result, rule.threshold) {
			if rule.action == ActionBlock {
				if err != nil {
					// The connection is already upgraded: preserve the unavailable
					// classification in its terminal reason, not a content verdict.
					return errors.New("guardrail_unavailable")
				}
				return errors.New("realtime_guardrail_blocked")
			}
			slog.Info("realtime guardrail triggered", "guardrail", rule.guardrail.Name(), "action", actionString(rule.action))
		}
	}
	return nil
}

// A timed-out check owns its private input until it exits. Failure is separate
// from the scored result, so even threshold=1 cannot turn a timeout, panic,
// missing result or unapplied wire redaction into a pass.
func runRealtimeCheck(ctx context.Context, rule resolvedRule, input *CheckInput) (*CheckResult, error) {
	ctx, cancel := context.WithTimeout(ctx, rule.timeout)
	defer cancel()
	type outcome struct {
		result *CheckResult
		err    error
	}
	done := make(chan outcome, 1)
	go func() {
		result := outcome{}
		defer func() {
			if recover() != nil {
				result.err = errors.New("guardrail panicked")
			}
			done <- result
		}()
		before, _ := json.Marshal(input)
		result.result = rule.guardrail.Check(ctx, input)
		after, _ := json.Marshal(input)
		if result.result != nil {
			result.err = result.result.Err
			if result.err == nil {
				result.err = result.result.RealtimeErr
			}
			if math.IsNaN(result.result.Score) || math.IsInf(result.result.Score, 0) || result.result.Score < 0 || result.result.Score > 1 {
				result.err = errors.New("guardrail returned an invalid score")
			}
		}
		if result.result == nil || !bytes.Equal(before, after) {
			result.err = errors.New("guardrail did not approve original wire content")
		}
	}()
	select {
	case result := <-done:
		if ctx.Err() != nil {
			return nil, ctx.Err()
		}
		return result.result, result.err
	case <-ctx.Done():
		return nil, ctx.Err()
	}
}
