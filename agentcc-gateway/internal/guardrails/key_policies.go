package guardrails

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"sort"
	"strings"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// Precedence: org checks run first, followed by key policies in priority/name/ID
// order. Block > redact > warn > log; key policies cannot disable or weaken an
// org check. Monitor policies inspect snapshots only: they never block, redact,
// or change any response, including when a check fails closed. Post-stage checks
// on streamed output can report a block but cannot retract already relayed text.
func (p *GuardrailPlugin) runKeyPolicies(ctx context.Context, rc *models.RequestContext, orgID string, cfg *tenant.GuardrailConfig, stage Stage) pipeline.PluginResult {
	keyID := rc.Metadata["auth_key_id"]
	if keyID == "" {
		return pipeline.ResultContinue()
	}
	policies := make([]*tenant.KeyGuardrailPolicy, 0, len(cfg.KeyPolicies))
	for _, pol := range cfg.KeyPolicies {
		if pol == nil {
			continue
		}
		for _, id := range pol.KeyIDs {
			if id == keyID {
				policies = append(policies, pol)
				break
			}
		}
	}
	sort.Slice(policies, func(i, j int) bool {
		a, b := policies[i], policies[j]
		if a.Priority != b.Priority {
			return a.Priority < b.Priority
		}
		if a.Name != b.Name {
			return a.Name < b.Name
		}
		return a.ID < b.ID
	})
	timeout := p.defaultTimeout
	if cfg.TimeoutMs > 0 {
		timeout = time.Duration(cfg.TimeoutMs) * time.Millisecond
	}
	for _, pol := range policies {
		names := make([]string, 0, len(pol.Checks))
		for name := range pol.Checks {
			names = append(names, name)
		}
		sort.Strings(names)
		checks := make([]orgCheck, 0, len(names))
		for _, name := range names {
			check := pol.Checks[name]
			if check == nil || !check.Enabled {
				continue
			}
			// Unlike org checks, these still run when a static rule uses the same name.
			g := p.getKeyPolicyGuardrail(orgID, name, name+"@"+pol.ID, check)
			if g != nil && g.Stage() == stage {
				checks = append(checks, orgCheck{name: name, check: check, g: g})
			}
		}
		if len(checks) == 0 {
			continue
		}
		input := checkInput(rc, stage == StagePost)
		monitor := strings.EqualFold(pol.Mode, "monitor")
		var outcomes []*CheckResult
		blockIdx := -1
		if monitor {
			outcomes = make([]*CheckResult, len(checks))
			for i, c := range checks {
				owned, err := cloneCheckInput(input)
				if err != nil {
					outcomes[i] = &CheckResult{Err: err, FailureAdmitted: true, Message: "guardrail_unavailable: could not snapshot guardrail input"}
					continue
				}
				// Never publish the snapshot, even if the guardrail mutates or returns late.
				before, _ := json.Marshal(struct {
					Request  *models.ChatCompletionRequest
					Response *models.ChatCompletionResponse
				}{owned.Request, owned.Response})
				outcomes[i] = p.runGuardrailSafe(ctx, c.g, owned, timeout, true)
				if outcomes[i] != nil && outcomes[i].Err == nil && mutatesInput(c.g) {
					after, _ := json.Marshal(struct {
						Request  *models.ChatCompletionRequest
						Response *models.ChatCompletionResponse
					}{owned.Request, owned.Response})
					outcomes[i].InputRewritten = !bytes.Equal(before, after)
				}
			}
		} else if strings.EqualFold(strings.TrimSpace(cfg.PipelineMode), "sequential") {
			outcomes, blockIdx = p.runOrgChecksSequential(ctx, checks, input, timeout, cfg.FailOpen)
		} else {
			outcomes, blockIdx = p.runOrgChecksParallel(ctx, checks, input, timeout, cfg.FailOpen)
		}
		result := &PipelineResult{}
		if blockIdx >= 0 {
			p.recordKeyOutcome(result, rc, pol, checks[blockIdx], outcomes[blockIdx], monitor)
		}
		for i, c := range checks {
			if i != blockIdx {
				p.recordKeyOutcome(result, rc, pol, c, outcomes[i], monitor)
			}
		}
		if monitor {
			if len(result.Triggered) > 0 {
				rc.Flags.GuardrailMonitored = true
				storeGuardrailResults(rc, result)
			}
			continue
		}
		if blockIdx >= 0 {
			result.Blocked = true
			storeGuardrailResults(rc, result)
			if len(result.Warnings) > 0 {
				rc.Metadata["guardrail_warnings"] = strings.Join(result.Warnings, "; ")
			}
			rc.Flags.GuardrailTriggered = true
			rc.Metadata["guardrail_name"] = checks[blockIdx].name
			rc.Metadata["guardrail_action"] = "blocked"
			if stage == StagePost && rc.Response != nil {
				rc.Response = &models.ChatCompletionResponse{Model: rc.Response.Model, Usage: rc.Response.Usage}
			}
			if outcomes[blockIdx].Err != nil {
				return pipeline.ResultError(models.ErrGuardrailUnavailable(p.buildBlockMessage(result)))
			}
			return pipeline.ResultError(models.ErrGuardrailBlocked("content_blocked", p.buildBlockMessage(result)))
		}
		if len(result.Triggered) > 0 {
			p.applyMetadata(rc, result)
		}
	}
	return pipeline.ResultContinue()
}

const passThroughNote = " (client's body was forwarded unchanged on this pass-through route)"

// getKeyPolicyGuardrail resolves a key-policy check from that policy's own
// config, even an empty one, so it never runs the gateway's static instance,
// which config.yaml may have configured for every request. It falls back to
// the static registry only for checks the factory can't build.
func (p *GuardrailPlugin) getKeyPolicyGuardrail(orgID, name, cacheKey string, check *tenant.GuardrailCheck) Guardrail {
	if p.dynamicFactory != nil {
		if g := p.getCachedDynamicGuardrail(orgID, cacheKey); g != nil {
			return g
		}
		cfg := check.Config
		if cfg == nil {
			cfg = map[string]interface{}{}
		}
		if g := p.createDynamicGuardrailScoped(orgID, name, cacheKey, cfg); g != nil {
			return g
		}
	}
	return p.getGuardrailScoped(orgID, name, cacheKey, check)
}

func guardrailOutcome(rc *models.RequestContext, cr *CheckResult, action Action) (string, string) {
	message := cr.Message
	if action == ActionBlock {
		return message, "blocked"
	}
	if cr.InputRewritten {
		if rc.Request != nil {
			return message, "redacted"
		}
		return message + passThroughNote, "logged"
	}
	if action == ActionWarn {
		return message, "warned"
	}
	return message, "logged"
}

func (p *GuardrailPlugin) recordKeyOutcome(result *PipelineResult, rc *models.RequestContext, pol *tenant.KeyGuardrailPolicy, c orgCheck, cr *CheckResult, monitor bool) {
	threshold := c.check.ConfidenceThreshold
	if !shouldTrigger(cr, threshold) {
		return
	}
	action := parseAction(c.check.Action)
	if cr.FailureAdmitted {
		action = ActionLog
	}
	message, outcome := guardrailOutcome(rc, cr, action)
	wouldAction := ""
	if monitor {
		wouldAction = actionString(parseAction(c.check.Action))
		action = ActionLog
		switch {
		case cr.Err != nil:
			outcome = "logged"
		case wouldAction == "block":
			outcome = "would_block"
		case mutatesInput(c.g) && cr.InputRewritten && rc.Request == nil:
			// Even enforced, the gateway forwards the client's own body here.
			outcome = "logged"
		case mutatesInput(c.g) && cr.InputRewritten:
			outcome = "would_redact"
		case wouldAction == "warn":
			outcome = "would_warn"
		default:
			outcome = "logged"
		}
		// A monitor check runs on a snapshot, so no client body was changed.
		message = cr.Message
		if outcome == "logged" && mutatesInput(c.g) && cr.InputRewritten && rc.Request == nil {
			message += passThroughNote
		}
	}
	result.Triggered = append(result.Triggered, TriggeredGuardrail{
		Name: c.name, Score: cr.Score, Threshold: threshold, Action: action, Message: message,
		PolicyID: pol.ID, PolicyName: pol.Name, PolicyScope: "key", Mode: map[bool]string{true: "monitor", false: "enforce"}[monitor],
		Outcome: outcome, WouldAction: wouldAction,
	})
	if !monitor && action == ActionWarn {
		result.Warnings = append(result.Warnings, fmt.Sprintf("%s: %s", c.name, cr.Message))
	}
}
