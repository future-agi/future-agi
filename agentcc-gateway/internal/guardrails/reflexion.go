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
		for _, rules := range [][]resolvedRule{p.engine.preGuardrails, p.engine.postGuardrails} {
			for _, rule := range rules {
				if rule.configErr != nil {
					return models.ErrBadRequest("invalid_guardrail_config", rule.configErr.Error())
				}
			}
		}
	}
	if p.tenantStore != nil {
		if cfg := p.tenantStore.Get(rc.Metadata["org_id"]); cfg != nil && cfg.Guardrails != nil {
			for _, check := range cfg.Guardrails.Checks {
				if check == nil || !check.Enabled {
					continue
				}
				if _, err := reflexion.Parse(check.Config); err != nil {
					return models.ErrBadRequest("invalid_guardrail_config", err.Error())
				}
			}
		}
	}
	return nil
}

func (p *GuardrailPlugin) BufferResponse(rc *models.RequestContext) bool {
	if p.engine != nil {
		for _, rule := range p.engine.postGuardrails {
			if rule.reflection.Enabled {
				return true
			}
		}
	}
	if p.tenantStore != nil {
		if cfg := p.tenantStore.Get(rc.Metadata["org_id"]); cfg != nil && cfg.Guardrails != nil {
			for name, check := range cfg.Guardrails.Checks {
				if check == nil || !check.Enabled || check.Action != "block" || check.Config["mode"] == "async" {
					continue
				}
				if p.engine != nil && p.engine.HasRule(name) {
					continue
				}
				r, err := reflexion.Parse(check.Config)
				if err != nil || !r.Enabled {
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

// postBlock leaves the body available solely for the controller's private
// feedback. The pipeline already settled it and clears it before final sinks.
func (p *GuardrailPlugin) postBlock(rc *models.RequestContext, checks *PipelineResult) pipeline.PluginResult {
	rc.Flags.GuardrailTriggered = true
	rc.Metadata["guardrail_action"] = "blocked"
	decision := &pipeline.ReflectionDecision{MaxAttempts: reflexion.MaxAttempts}
	allow := true
	var reasons []string
	for _, tg := range checks.Triggered {
		if tg.Action != ActionBlock {
			continue
		}
		rc.Metadata["guardrail_name"] = tg.Name
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
