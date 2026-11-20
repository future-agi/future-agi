package guardrails

import (
	"context"
	"errors"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

type resolvedKeyPolicy struct {
	policy *tenant.KeyGuardrailPolicy
	checks []orgCheck
}

type resolvedKeyPolicies struct {
	orgID, keyID string
	config       *tenant.GuardrailConfig
	policies     []resolvedKeyPolicy
}

type resolvedKeyPoliciesContextKey struct{}

// withResolvedKeyPolicies shares admission's resolution with pre-stage execution.
// The context belongs to this plugin invocation, not the pooled RequestContext;
// no state survives the request or changes speech/realtime policy binding.
func (p *GuardrailPlugin) withResolvedKeyPolicies(ctx context.Context, rc *models.RequestContext) context.Context {
	orgID, keyID, cfg := p.selectedConfig(rc)
	if cfg == nil || keyPolicyResolutionFromContext(ctx, orgID, keyID) != nil {
		return ctx
	}
	resolved := p.resolveKeyPolicies(orgID, keyID, cfg)
	return context.WithValue(ctx, resolvedKeyPoliciesContextKey{}, resolved)
}

func keyPolicyResolutionFromContext(ctx context.Context, orgID, keyID string) *resolvedKeyPolicies {
	if resolved, ok := ctx.Value(resolvedKeyPoliciesContextKey{}).(*resolvedKeyPolicies); ok && resolved.orgID == orgID && resolved.keyID == keyID {
		return resolved
	}
	return nil
}

func (p *GuardrailPlugin) keyPoliciesForInvocation(ctx context.Context, orgID, keyID string, cfg *tenant.GuardrailConfig) *resolvedKeyPolicies {
	if resolved := keyPolicyResolutionFromContext(ctx, orgID, keyID); resolved != nil {
		return resolved
	}
	return p.resolveKeyPolicies(orgID, keyID, cfg)
}

// resolveKeyPolicies resolves every enabled check, including failures, before
// filtering by stage. An unknown implementation has no trustworthy stage, so it
// defaults to a PRE-stage execution failure. selectedCheckStage moves that
// conservative default to output for exact input-N/A content reads.
func (p *GuardrailPlugin) resolveKeyPolicies(orgID, keyID string, cfg *tenant.GuardrailConfig) *resolvedKeyPolicies {
	resolved := &resolvedKeyPolicies{orgID: orgID, keyID: keyID, config: cfg}
	if cfg == nil || orgID == "" || keyID == "" {
		return resolved
	}
	for _, pol := range sortedKeyPolicies(cfg.KeyPoliciesFor(keyID)) {
		entry := resolvedKeyPolicy{policy: pol}
		for _, name := range sortedCheckNames(pol.Checks) {
			check := pol.Checks[name]
			if check == nil || !check.Enabled {
				continue
			}
			// A transport placeholder must never construct valid defaults or fall
			// back to a static instance. Preserve it as a normal execution failure.
			var g Guardrail
			if check.ConfigurationError == "" {
				g = p.getKeyPolicyGuardrail(orgID, name, keyPolicyCacheKey(name, pol.ID, check), check)
				g = validateSelectedKeyPolicyCheck(name, g, check)
			}
			if g == nil || (g.Stage() != StagePre && g.Stage() != StagePost) {
				g = unavailableKeyPolicyCheck{name: name}
			}
			entry.checks = append(entry.checks, orgCheck{name: name, check: check, g: g})
		}
		resolved.policies = append(resolved.policies, entry)
	}
	return resolved
}

// These failures use the normal execution envelope, so effective org fail_open
// applies to Enforce Block while Monitor remains observation-only. Neither the
// policy config nor any request/provider content is included in the error.
type unavailableKeyPolicyCheck struct {
	name       string
	stage      Stage
	stageKnown bool
}

func (g unavailableKeyPolicyCheck) Name() string { return g.name }
func (g unavailableKeyPolicyCheck) Stage() Stage { return g.stage }
func (unavailableKeyPolicyCheck) Check(context.Context, *CheckInput) *CheckResult {
	return &CheckResult{Err: errors.New("configured guardrail implementation is unavailable")}
}

// validateKeyPolicyCheck is deliberately scoped to known built-ins and selected
// keys. Legacy static/org behavior and arbitrary custom names are unchanged.
// The leaf owns parameter validation; invalid configuration uses the existing
// unavailable-check execution envelope (503 only for fail-closed Enforce Block).
func validateKeyPolicyCheck(name string, g Guardrail) Guardrail {
	switch name {
	case "keyword-blocklist", "pii-detection", "content-moderation", "secret-detection",
		"language-detection", "topic-restriction", "input-validation", "prompt-injection":
		if validator, ok := g.(interface{ ValidateConfig() error }); ok && validator.ValidateConfig() != nil {
			if stage := g.Stage(); stage == StagePre || stage == StagePost {
				return unavailableKeyPolicyCheck{name: name, stage: stage, stageKnown: true}
			}
			return unavailableKeyPolicyCheck{name: name}
		}
	}
	return g
}

// validateSelectedKeyPolicyCheck adds envelope-dependent readiness without
// changing the factory signature or broadening org/static/custom validation.
// Both HTTP resolution and the direct realtime resolver must use this helper.
func validateSelectedKeyPolicyCheck(name string, g Guardrail, check *tenant.GuardrailCheck) Guardrail {
	g = validateKeyPolicyCheck(name, g)
	if g == nil || check == nil || name != "content-moderation" {
		return g
	}
	// A shared static fallback must retain its legacy numeric evaluation order.
	// Only this selected caller receives the builtin's independently owned copy.
	if selector, ok := g.(interface{ SelectedPolicyGuardrail() Guardrail }); ok {
		g = selector.SelectedPolicyGuardrail()
	}
	if validator, ok := g.(interface{ ValidateSelectedThreshold(float64) error }); ok &&
		validator.ValidateSelectedThreshold(check.ConfidenceThreshold) != nil {
		if stage := g.Stage(); stage == StagePre || stage == StagePost {
			return unavailableKeyPolicyCheck{name: name, stage: stage, stageKnown: true}
		}
		return unavailableKeyPolicyCheck{name: name}
	}
	return g
}

// unsupportedRouteCheck records a truthful execution failure for observational
// checks. Blocking enforcement of a missing route capability is refused at
// admission, independently of fail_open, rather than admitting uninspected data.
type unsupportedRouteCheck struct {
	name, code, reason string
	stage              Stage
}

func (g unsupportedRouteCheck) Name() string { return g.name }
func (g unsupportedRouteCheck) Stage() Stage { return g.stage }
func (g unsupportedRouteCheck) Check(context.Context, *CheckInput) *CheckResult {
	return &CheckResult{Err: errors.New(g.code + ": " + g.reason)}
}

func keyPolicyRouteCheck(rc *models.RequestContext, c orgCheck) orgCheck {
	stage := selectedCheckStage(rc, c.g)
	_, code, reason := inspectionCapability(rc, stage, true)
	if reason != "" {
		c.g = unsupportedRouteCheck{name: c.name, code: code, reason: reason, stage: stage}
	}
	return c
}

func (p *GuardrailPlugin) refuseKeyPolicyRoute(rc *models.RequestContext, pol *tenant.KeyGuardrailPolicy, c orgCheck, unsupported unsupportedRouteCheck) pipeline.PluginResult {
	message := unsupported.code + ": " + unsupported.reason
	result := &PipelineResult{Blocked: true}
	p.recordKeyOutcome(result, rc, pol, c, &CheckResult{Message: message}, false)
	storeGuardrailResults(rc, result)
	rc.Flags.GuardrailTriggered = true
	rc.Metadata["guardrail_name"] = c.name
	rc.Metadata["guardrail_action"] = "blocked"
	return pipeline.ResultError(models.ErrGuardrailBlocked(unsupported.code, message))
}

// checkKeyPolicyStreamAdmission explicitly refuses streams whose selected-key
// Enforce Block checks require completed output. The existing streaming checker
// releases chunks before its final verdict and cannot enforce arbitrary post
// checks safely. This is unsupported execution, not a transient check failure:
// fail_open must not turn it into a silently unguarded stream.
func (p *GuardrailPlugin) checkKeyPolicyStreamAdmission(ctx context.Context, rc *models.RequestContext) pipeline.PluginResult {
	if !rc.IsStream || rc.EndpointType == "speech_relay" || rc.EndpointType == "transcription_relay" || p.tenantStore == nil {
		return pipeline.ResultContinue()
	}
	orgID, keyID := selectedPrincipal(rc)
	if orgID == "" || keyID == "" {
		return pipeline.ResultContinue()
	}
	resolved := keyPolicyResolutionFromContext(ctx, orgID, keyID)
	if resolved == nil {
		org := p.tenantStore.Get(orgID)
		if org == nil || org.Guardrails == nil {
			return pipeline.ResultContinue()
		}
		resolved = p.resolveKeyPolicies(orgID, keyID, org.Guardrails)
	}
	for _, entry := range resolved.policies {
		if strings.EqualFold(entry.policy.Mode, "monitor") {
			continue
		}
		for _, c := range entry.checks {
			if selectedCheckStage(rc, c.g) != StagePost || parseAction(c.check.Action) != ActionBlock {
				continue
			}
			const message = "stream_guardrail_unsupported: selected-key blocking output checks require a non-streaming request"
			result := &PipelineResult{Blocked: true}
			p.recordKeyOutcome(result, rc, entry.policy, c, &CheckResult{Message: message}, false)
			storeGuardrailResults(rc, result)
			rc.Flags.GuardrailTriggered = true
			rc.Metadata["guardrail_name"] = c.name
			rc.Metadata["guardrail_action"] = "blocked"
			return pipeline.ResultError(models.ErrGuardrailBlocked("stream_guardrail_unsupported", message))
		}
	}
	return pipeline.ResultContinue()
}
