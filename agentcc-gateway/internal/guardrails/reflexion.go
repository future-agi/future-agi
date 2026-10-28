package guardrails

import (
	"context"
	"encoding/json"
	"fmt"
	"maps"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/guardrails/policy"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/reflexion"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

func configuredStage(cfg map[string]interface{}, fallback Stage) Stage {
	if cfg["stage"] == "pre" {
		return StagePre
	}
	if cfg["stage"] == "post" {
		return StagePost
	}
	return fallback
}

func (p *GuardrailPlugin) validateReflectionConfig(rc *models.RequestContext) *models.APIError {
	if p.engine != nil {
		if p.engine.configErr != nil {
			return models.ErrBadRequest("invalid_guardrail_config", p.engine.configErr.Error())
		}
		for _, rules := range [][]resolvedRule{p.engine.preGuardrails, p.engine.postGuardrails} {
			for _, rule := range rules {
				if rule.configErr != nil {
					return models.ErrBadRequest("invalid_guardrail_config", rule.configErr.Error())
				}
			}
		}
	}
	if p.tenantStore != nil {
		orgID := rc.Metadata["org_id"]
		return p.validateOrgReflectionConfig(orgID, p.tenantStore.Get(orgID))
	}
	return nil
}

// ValidateOrgReflectionConfig uses request-time resolution without consulting or
// populating the live tenant cache: this config has not been accepted yet.
func (p *GuardrailPlugin) ValidateOrgReflectionConfig(cfg *tenant.OrgConfig) error {
	resolver := NewPlugin(p.engine, p.registry, p.dynamicFactory, nil, nil)
	if err := resolver.validateOrgReflectionConfig("", cfg); err != nil {
		return err
	}
	return nil
}

func (p *GuardrailPlugin) validateOrgReflectionConfig(orgID string, cfg *tenant.OrgConfig) *models.APIError {
	if cfg == nil || cfg.Guardrails == nil {
		return nil
	}
	for name, check := range cfg.Guardrails.Checks {
		if check == nil || !check.Enabled {
			continue
		}
		if _, err := reflexion.Parse(check.Config); err != nil {
			return models.ErrBadRequest("invalid_guardrail_config", err.Error())
		}
		if p.engine != nil && p.engine.HasRule(name) {
			continue
		}
		g := p.getGuardrail(orgID, name, check)
		if g == nil {
			if check.Config["on_block"] == "reflect" {
				return models.ErrBadRequest("invalid_guardrail_config", "reflect check has no registered implementation")
			}
			continue
		}
		stage := "pre"
		if configuredStage(check.Config, g.Stage()) == StagePost {
			stage = "post"
		}
		if _, err := reflexion.Validate(check.Config, name, stage, check.Action, "sync"); err != nil {
			return models.ErrBadRequest("invalid_guardrail_config", err.Error())
		}
	}
	return nil
}

func (p *GuardrailPlugin) BufferResponse(rc *models.RequestContext) bool {
	return p.reflectionRequested(rc, false)
}

// ReflectionDebug includes diagnostics even when the first candidate passes.
func (p *GuardrailPlugin) ReflectionDebug(rc *models.RequestContext) bool {
	return p.reflectionRequested(rc, true)
}

func (p *GuardrailPlugin) reflectionRequested(rc *models.RequestContext, debugOnly bool) bool {
	keyPolicy, reqPolicy, err := p.resolvePolicy(rc)
	if err != nil || reqPolicy == policy.RequestPolicyDisabled {
		return false
	}
	if p.engine != nil {
		for _, rule := range p.engine.postGuardrails {
			if rule.mode != "" && rule.mode != "sync" {
				continue
			}
			action, _, enabled := effectiveCheckPolicy(rule.guardrail.Name(), rule.action, rule.threshold, keyPolicy, reqPolicy)
			if enabled && action == ActionBlock && rule.reflection.Enabled && (!debugOnly || rule.reflection.Debug) {
				return true
			}
		}
	}
	if p.tenantStore != nil {
		if cfg := p.tenantStore.Get(rc.Metadata["org_id"]); cfg != nil && cfg.Guardrails != nil {
			for name, check := range cfg.Guardrails.Checks {
				if check == nil || !check.Enabled {
					continue
				}
				action, _, enabled := effectiveCheckPolicy(name, parseAction(check.Action), check.ConfidenceThreshold, keyPolicy, reqPolicy)
				if !enabled || action != ActionBlock {
					continue
				}
				if p.engine != nil && p.engine.HasRule(name) {
					continue
				}
				r, err := reflexion.Parse(check.Config)
				if err != nil || !r.Enabled || (debugOnly && !r.Debug) {
					continue
				}
				g := p.getGuardrail(rc.Metadata["org_id"], name, check)
				if g != nil && configuredStage(check.Config, g.Stage()) == StagePost {
					return true
				}
			}
		}
	}
	return false
}

func effectiveCheckPolicy(name string, action Action, threshold float64, p *policy.Policy, rp policy.RequestPolicy) (Action, float64, bool) {
	if rp == policy.RequestPolicyDisabled {
		return action, threshold, false
	}
	if p != nil {
		if ov, ok := p.Overrides[name]; ok {
			if ov.Disabled {
				return action, threshold, false
			}
			if ov.Action != "" {
				action = parseAction(ov.Action)
			}
			if ov.Threshold != nil {
				threshold = *ov.Threshold
			}
		}
	}
	switch rp {
	case policy.RequestPolicyLogOnly:
		action = ActionLog
	case policy.RequestPolicyStrict:
		action, threshold = ActionBlock, 0
	}
	return action, threshold, true
}

// postBlock leaves the body available solely for the controller's private
// feedback. The pipeline already settled it and clears it before final sinks.
func (p *GuardrailPlugin) postBlock(rc *models.RequestContext, checks *PipelineResult) pipeline.PluginResult {
	rc.Flags.GuardrailTriggered = true
	rc.Metadata["guardrail_action"] = "blocked"
	decision := &pipeline.ReflectionDecision{MaxAttempts: reflexion.MaxAttempts}
	allow := true
	operationalError := false
	var reasons []string
	for _, tg := range checks.Triggered {
		if tg.Action != ActionBlock {
			continue
		}
		rc.Metadata["guardrail_name"] = tg.Name
		operationalError = operationalError || tg.OperationalError
		if !tg.Reflection.Enabled {
			allow = false
		}
		decision.MaxAttempts = min(decision.MaxAttempts, tg.Reflection.MaxAttempts)
		decision.Debug = decision.Debug || tg.Reflection.Debug
		reasons = append(reasons, fmt.Sprintf("%s: %s", tg.Name, tg.Message))
		// Check messages can quote the rejected answer. Never persist them.
		rc.GuardrailResults = append(rc.GuardrailResults, models.GuardrailResult{
			Name: tg.Name, Score: tg.Score, Threshold: tg.Threshold, Action: "block", Message: "response check failed",
		})
	}
	result := pipeline.ResultError(models.ErrGuardrailBlocked("content_blocked", "Response blocked by guardrail"))
	if operationalError {
		result = pipeline.ResultError(models.ErrGuardrailBlocked("guardrail_evaluation_failed", "Response guardrail evaluation failed"))
	}
	if allow {
		decision.Reason = strings.Join(reasons, "\n")
		result.Reflection = decision
	}
	return result
}

// ObserveResponse is called once, after the candidate has passed every sync
// check. Rejected candidates never escape into asynchronous snapshots.
func (p *GuardrailPlugin) ObserveResponse(_ context.Context, rc *models.RequestContext) {
	if p.engine == nil || rc.Response == nil {
		return
	}
	keyPolicy, reqPolicy, err := p.resolvePolicy(rc)
	if err != nil || reqPolicy == policy.RequestPolicyDisabled {
		return
	}
	input := &CheckInput{Request: rc.Request, Response: rc.Response, Metadata: rc.Metadata}
	for _, rule := range p.engine.postGuardrails {
		if rule.mode != "async" {
			continue
		}
		if keyPolicy != nil && keyPolicy.Overrides[rule.guardrail.Name()].Disabled {
			continue
		}
		p.engine.runAsync(rule, input)
	}
}

// responseOnlyInput adapts the reserved native content scanners whose extractors
// otherwise scan both request and response messages. Do not infer this from Stage:
// expressions, webhooks, tool permissions and external evaluators need both sides.
// Keep request settings, but isolate prompt-bearing fields and request mutations.
func responseOnlyInput(g Guardrail, input *CheckInput) *CheckInput {
	if input.Request == nil || input.Response == nil {
		return input
	}
	switch g.Name() {
	case "input-validation", "keyword-blocklist", "pii-detection", "content-moderation", "secret-detection":
	default:
		return input
	}
	view := *input
	request := *input.Request
	request.Messages = nil
	request.Stop = nil
	request.LogitBias = nil
	request.User = ""
	request.Tools = nil
	request.ToolChoice = nil
	request.ResponseFormat = nil
	request.Extra = nil
	view.Request = &request
	return &view
}

// Successful synchronous checks may redact messages in place. Copy those
// changes back only after the check has returned, never from a timed-out worker.
func applyCheckMutations(original, owned *CheckInput) {
	if original.Request != nil && owned.Request != nil {
		*original.Request = *owned.Request
	}
	if original.Response != nil && owned.Response != nil {
		*original.Response = *owned.Response
	}
}

// Checks may outlive their timeout. Give them owned data, never pooled maps or
// a candidate whose usage the controller will subsequently aggregate.
func snapshotInput(input *CheckInput) (*CheckInput, error) {
	b, err := json.Marshal(input)
	if err != nil {
		return nil, err
	}
	var copy CheckInput
	if err := json.Unmarshal(b, &copy); err != nil {
		return nil, err
	}
	copy.Metadata = maps.Clone(input.Metadata)
	return &copy, nil
}
