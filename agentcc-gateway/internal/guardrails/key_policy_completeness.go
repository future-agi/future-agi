package guardrails

import (
	"context"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// selectedPrincipal is independent of mutable accounting metadata (stored
// objects may belong to a different creator). The legacy fallback only locates
// policy for unbound internal calls; it never grants a principal or a receipt.
// Such a call cannot complete an applicable blocking inspection decision.
func selectedPrincipal(rc *models.RequestContext) (orgID, keyID string) {
	if orgID, keyID = rc.Inspection.Principal(); orgID != "" && keyID != "" {
		return orgID, keyID
	}
	return rc.Metadata["org_id"], rc.Metadata["auth_key_id"]
}

func (p *GuardrailPlugin) selectedConfig(rc *models.RequestContext) (string, string, *tenant.GuardrailConfig) {
	orgID, keyID := selectedPrincipal(rc)
	if p.tenantStore == nil || orgID == "" || keyID == "" {
		return orgID, keyID, nil
	}
	org := p.tenantStore.Get(orgID)
	if org == nil {
		return orgID, keyID, nil
	}
	return orgID, keyID, org.Guardrails
}

// selectedCheckStage keeps unavailable checks applicable to the content an
// operation actually serves. An unknown implementation historically defaults to
// PRE; on an exact input-N/A stored read that default must not discard its only
// possible content check. Known stages (including invalid POST leaves) remain
// intact. Exact metadata-only operations still have no content stage to inspect.
func selectedCheckStage(rc *models.RequestContext, g Guardrail) Stage {
	if unavailable, ok := g.(unavailableKeyPolicyCheck); ok && !unavailable.stageKnown {
		contract := rc.Inspection.Contract()
		if contract.Operation != "" && contract.Input == models.InspectionNotApplicable && contract.Reason != "" && contract.Output != models.InspectionNotApplicable {
			return StagePost
		}
	}
	return g.Stage()
}

func inspectionStage(stage Stage) models.InspectionStage {
	if stage == StagePost {
		return models.InspectionOutput
	}
	return models.InspectionInput
}

func inspectionPayload(rc *models.RequestContext, stage Stage) any {
	if stage == StagePost {
		if rc.GuardrailResponse != nil {
			return rc.GuardrailResponse
		}
		return rc.Response
	}
	if rc.GuardrailRequest != nil {
		return rc.GuardrailRequest
	}
	return rc.Request
}

// inspectionCapability distinguishes an operation declaration from a complete
// payload receipt. Reasons contain capability information, never user content.
func inspectionCapability(rc *models.RequestContext, stage Stage, requireComplete bool) (notApplicable bool, code, reason string) {
	code = "input_guardrail_unsupported"
	explicitReason := rc.GuardrailInputUnsupported
	contract := rc.Inspection.Contract()
	support := contract.Input
	if stage == StagePost {
		code = "output_guardrail_unsupported"
		explicitReason, support = rc.GuardrailOutputUnsupported, contract.Output
	}
	if explicitReason != "" {
		return false, code, explicitReason
	}
	if contract.Operation == "" {
		return false, code, "operation inspection contract is unknown"
	}
	if requireComplete {
		orgID, keyID := rc.Inspection.Principal()
		if orgID == "" || keyID == "" {
			return false, code, "authenticated inspection principal is missing"
		}
	}
	if support == models.InspectionNotApplicable && contract.Reason != "" {
		return true, "", ""
	}
	if support != models.InspectionSupported {
		return false, code, "operation does not support complete stage inspection"
	}
	if requireComplete && !rc.Inspection.IsComplete(inspectionStage(stage), inspectionPayload(rc, stage)) {
		return false, code, "complete payload inspection receipt is missing or stale"
	}
	return false, "", ""
}

// AdmitSelectedOperation is declaration-only preflight. No checker, org/static
// rule, cache, observer, or accounting plugin runs here. Supported stages remain
// required and pending until the actual projected payload has been checked.
func (p *GuardrailPlugin) AdmitSelectedOperation(ctx context.Context, rc *models.RequestContext) *models.APIError {
	orgID, keyID, cfg := p.selectedConfig(rc)
	if cfg == nil {
		return nil
	}
	resolved := p.keyPoliciesForInvocation(ctx, orgID, keyID, cfg)
	if rc.Inspection == nil {
		rc.Inspection = models.NewInspectionState(models.InspectionContract{})
	}
	for _, entry := range resolved.policies {
		if strings.EqualFold(entry.policy.Mode, "monitor") {
			continue
		}
		for _, c := range entry.checks {
			if parseAction(c.check.Action) != ActionBlock {
				continue
			}
			stage := selectedCheckStage(rc, c.g)
			rc.Inspection.Require(inspectionStage(stage))
			if rc.IsStream && stage == StagePost {
				unsupported := unsupportedRouteCheck{name: c.name, code: "stream_guardrail_unsupported", reason: "selected-key blocking output checks require a non-streaming request"}
				return p.refuseKeyPolicyRoute(rc, entry.policy, c, unsupported).Error
			}
			_, code, reason := inspectionCapability(rc, stage, false)
			if reason != "" {
				return p.refuseKeyPolicyRoute(rc, entry.policy, c, unsupportedRouteCheck{name: c.name, code: code, reason: reason}).Error
			}
		}
	}
	return nil
}

// AdmitSelectedRequest is shared by ProcessRequest and CheckAccess, but unlike
// ProcessRequest intentionally does not execute legacy org/static rules.
func (p *GuardrailPlugin) AdmitSelectedRequest(ctx context.Context, rc *models.RequestContext) *models.APIError {
	ctx = p.withResolvedKeyPolicies(ctx, rc)
	if err := p.AdmitSelectedOperation(ctx, rc); err != nil {
		return err
	}
	orgID, _, cfg := p.selectedConfig(rc)
	if cfg == nil {
		return nil
	}
	return p.runKeyPolicies(ctx, rc, orgID, cfg, StagePre).Error
}

// AdmitSelectedOutput resolves current reader policy for nonbillable stored
// reads. The same implementation runs in ProcessResponse after org/static work.
func (p *GuardrailPlugin) AdmitSelectedOutput(ctx context.Context, rc *models.RequestContext) *models.APIError {
	orgID, _, cfg := p.selectedConfig(rc)
	if cfg == nil {
		return nil
	}
	if rc.Inspection == nil {
		rc.Inspection = models.NewInspectionState(models.InspectionContract{})
	}
	return p.runKeyPolicies(ctx, rc, orgID, cfg, StagePost).Error
}

// refreshInspection is used only around successful, trusted guardrail work that
// began with a complete projection. It cannot upgrade an absent/lossy receipt.
func refreshInspection(rc *models.RequestContext, stage Stage, wasComplete bool) {
	if wasComplete && !rc.Inspection.IsComplete(inspectionStage(stage), inspectionPayload(rc, stage)) {
		rc.Inspection.Complete(inspectionStage(stage), inspectionPayload(rc, stage))
	}
}
