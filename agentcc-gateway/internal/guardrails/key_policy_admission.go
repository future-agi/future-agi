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
	if p.tenantStore == nil || rc.Metadata["org_id"] == "" || rc.Metadata["auth_key_id"] == "" {
		return ctx
	}
	orgID, keyID := rc.Metadata["org_id"], rc.Metadata["auth_key_id"]
	org := p.tenantStore.Get(orgID)
	if org == nil || org.Guardrails == nil {
		return ctx
	}
	resolved := p.resolveKeyPolicies(orgID, keyID, org.Guardrails)
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
// becomes a PRE-stage execution failure rather than disappearing from enforcement.
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
			g := p.getKeyPolicyGuardrail(orgID, name, keyPolicyCacheKey(name, pol.ID, check), check)
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
type unavailableKeyPolicyCheck struct{ name string }

func (g unavailableKeyPolicyCheck) Name() string { return g.name }
func (unavailableKeyPolicyCheck) Stage() Stage   { return StagePre }
func (unavailableKeyPolicyCheck) Check(context.Context, *CheckInput) *CheckResult {
	return &CheckResult{Err: errors.New("configured guardrail implementation is unavailable")}
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
	orgID, keyID := rc.Metadata["org_id"], rc.Metadata["auth_key_id"]
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
			if c.g.Stage() != StagePost || parseAction(c.check.Action) != ActionBlock {
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
