package guardrails

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"log/slog"
	"maps"
	"math"
	"strings"
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

// RealtimeText names the directions in which a realtime session carries text
// the relay can read: index StagePre is the client's text before the vendor
// (synthesis input), StagePost the vendor's text after it (transcripts).
type RealtimeText [2]bool

// RealtimeTextBoth reads text in both directions.
var RealtimeTextBoth = RealtimeText{StagePre: true, StagePost: true}

// BindRealtime uses the same authenticated org/key/request policy precedence as chat
// and resolves configured checks before opening the speech session. readable
// limits which key-policy checks are bound; org checks and static rules are
// bound in both directions, and the caller refuses blocking ones it cannot read.
func (p *GuardrailPlugin) BindRealtime(rc *models.RequestContext, readable RealtimeText) (*RealtimePolicy, *models.APIError) {
	merged, requested, err := p.resolvePolicy(rc)
	if err != nil {
		return nil, err
	}
	bound := &RealtimePolicy{metadata: maps.Clone(rc.Metadata), model: rc.Model}
	if requested == policy.RequestPolicyDisabled {
		// A request override never turns off the key's own policies, as on chat.
		if err := p.bindRealtimeKeyPolicies(bound, rc, readable); err != nil {
			return nil, err
		}
		return bound, validateRealtimeRules(bound)
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
	if err := p.bindRealtimeKeyPolicies(bound, rc, readable); err != nil {
		return nil, err
	}
	if err := validateRealtimeRules(bound); err != nil {
		return nil, err
	}
	return bound, nil
}

// bindRealtimeKeyPolicies adds the blocking checks of the key's Enforce
// policies that apply to the session's key, after the org checks, in the
// order chat runs them.
//
// Three kinds of key-policy check are left out instead of changing the session:
//   - Monitor policies, and an Enforce policy's warn and log checks. Any bound
//     rule installs the relay's hold/refuse filter, which holds text until a
//     unit completes and closes the session on frames it cannot parse, while
//     a non-blocking check could only write a log line. So only block checks
//     run on speech relays.
//   - Checks for a direction the session carries no readable text in (a
//     pre-stage check on transcription, a post-stage check on synthesis).
//     Org checks refuse such sessions; a key policy is skipped, so enforcing a
//     chat check on a key does not stop that key's speech sessions.
//
// Neither the key's static overrides nor an X-Guardrail-Policy header change
// the bound checks: on chat, too, key policies cannot be weakened.
func (p *GuardrailPlugin) bindRealtimeKeyPolicies(bound *RealtimePolicy, rc *models.RequestContext, readable RealtimeText) *models.APIError {
	if p.tenantStore == nil {
		return nil
	}
	orgID := rc.Metadata["org_id"]
	org := p.tenantStore.Get(orgID)
	if org == nil || org.Guardrails == nil {
		return nil
	}
	timeout := p.defaultTimeout
	if org.Guardrails.TimeoutMs > 0 {
		timeout = time.Duration(org.Guardrails.TimeoutMs) * time.Millisecond
	}
	for _, pol := range sortedKeyPolicies(org.Guardrails.KeyPoliciesFor(rc.Metadata["auth_key_id"])) {
		if strings.EqualFold(pol.Mode, "monitor") {
			continue
		}
		for _, name := range sortedCheckNames(pol.Checks) {
			check := pol.Checks[name]
			if check == nil || !check.Enabled {
				continue
			}
			action := parseAction(check.Action)
			if action != ActionBlock {
				// On a relay a warn or log check could only emit a log line,
				// yet any bound rule installs the hold/refuse filter.
				continue
			}
			g := p.getKeyPolicyGuardrail(orgID, name, keyPolicyCacheKey(name, pol.ID, check), check)
			if g == nil || (g.Stage() != StagePre && g.Stage() != StagePost) {
				return realtimeUnsupported("configured guardrail implementation or stage is unavailable")
			}
			if !readable[g.Stage()] {
				continue
			}
			bound.rules[g.Stage()] = append(bound.rules[g.Stage()], resolvedRule{
				guardrail: g, mode: "sync", action: action, threshold: check.ConfidenceThreshold, timeout: timeout,
			})
		}
	}
	return nil
}

// validateRealtimeRules refuses a session whose blocking checks could not
// decide: an invalid threshold, or a checker that is not ready.
func validateRealtimeRules(bound *RealtimePolicy) *models.APIError {
	for _, rules := range bound.rules {
		for _, rule := range rules {
			if rule.action != ActionBlock {
				continue
			}
			if math.IsNaN(rule.threshold) || math.IsInf(rule.threshold, 0) || rule.threshold < 0 || rule.threshold > 1 {
				return realtimeUnsupported("configured guardrail threshold is invalid")
			}
			if ready, ok := rule.guardrail.(interface{ RealtimeReady() error }); ok {
				if err := ready.RealtimeReady(); err != nil {
					if errors.Is(err, ErrRealtimeUnavailable) {
						return &models.APIError{Status: 503, Type: models.ErrTypeGuardrail,
							Code: "guardrail_unavailable", Message: "configured guardrail is unavailable for realtime checks"}
					}
					return realtimeUnsupported("configured guardrail is unavailable for realtime checks")
				}
			}
		}
	}
	return nil
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
