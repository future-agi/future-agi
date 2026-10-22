package server

import (
	"bytes"
	"context"
	"fmt"
	"net/http"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/routing"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// applyAgentContext is call-order step 7: the org-dependent part of the
// agent context (step inference, the omega-default alias, the metadata
// rules), after applyRoutingOptions has parsed the routing fields (step 6).
// Keep observeHook as its first call.
//
// A request whose parse step found an agent identity is agent-tagged: it
// gets rc.Metadata["agent_tagged"] and, when it sent no step, an inferred
// one. Attribution, the body-strip of metadata.agent and metadata.omega and
// the omega aliases apply to every request.
func (h *Handlers) applyAgentContext(rc *models.RequestContext, orgCfg *tenant.OrgConfig, req *models.ChatCompletionRequest) *models.APIError {
	h.observeHook(hookApplyAgentContext, rc)
	a := &rc.Agent
	if a.IdentitySource != "" {
		if a.Step == "" {
			a.Step, a.StepSource = inferStep(a, req), stepSourceInferred
		}
		rc.Metadata[metaAgentTagged] = "true"
	}
	if err := applyAttribution(rc, req); err != nil {
		return err
	}
	stripAgentMetadata(req)
	return h.resolveOmegaAlias(rc, orgCfg, req)
}

const (
	metaAgentTagged           = "agent_tagged"
	metaModelAliasRequested   = "model_alias_requested"
	stepSourceInferred        = "inferred"
	stepSourceHeader          = "header"
	stepSourceBody            = "body"
	omegaDefaultRemedyHint    = "enable managed models or add a provider"
	gatewayTagsParam          = "providerOptions.gateway.tags"
	errAgentHeaderConflict    = "agent_header_conflict"
	errAgentHeaderInvalid     = "agent_header_invalid"
	errInvalidTags            = "invalid_tags"
	errOmegaDefaultUnroutable = "omega_default_unroutable"
)

// inferStep is the step of an agent call that named none (the first rule
// that matches): tool_choice "none" after the first turn is the final
// answer, a response_format asks for extraction, offered tools mean acting,
// turn 0 is planning, and anything else is other.
func inferStep(a *models.AgentContext, req *models.ChatCompletionRequest) models.Step {
	turn := -1
	if a.Turn != nil {
		turn = *a.Turn
	}
	switch {
	case turn > 0 && bytes.Equal(bytes.TrimSpace(req.ToolChoice), []byte(`"none"`)):
		return models.StepFinal
	case req.ResponseFormat != nil && req.ResponseFormat.Type != "text":
		return models.StepExtract
	case len(req.Tools) > 0:
		return models.StepAct
	case turn == 0:
		return models.StepPlan
	}
	return models.StepOther
}

// applyAttribution makes x-agentcc-user the request's user (end_user, the
// body's user and rc.UserID); without it the body's user is the end user. A
// framework's user id (x-resource-id) is the request's user only when the
// body names none. The request's tags merge with
// providerOptions.gateway.tags, at most 10 in all.
func applyAttribution(rc *models.RequestContext, req *models.ChatCompletionRequest) *models.APIError {
	a := &rc.Agent
	if a.EndUser != "" {
		req.User, rc.UserID = a.EndUser, a.EndUser
	} else if req.User != "" {
		a.EndUser = models.TruncateRunes(req.User, maxEndUserLen)
	}
	if rc.UserID == "" {
		rc.UserID = a.UserID
	}
	if g := rc.Routing.Gateway; g != nil && len(g.Tags) > 0 {
		param := gatewayTagsParam
		if len(a.Tags) > 0 {
			param = otherHeaderNames[otherTags]
		}
		merged := append([]string(nil), a.Tags...)
		for _, t := range g.Tags {
			if !contains(merged, t) {
				merged = append(merged, t)
			}
		}
		if err := models.ValidAgentTags(merged); err != nil {
			return invalidTags(param, err.Error())
		}
		a.Tags = merged
	}
	return nil
}

// resolveOmegaAlias resolves omega-default and omega/auto: to
// routing.agents.default_model when it names a model; else to the model the
// org's model map gives omega-default, when that is a model and not one of
// the provider ids model_map entries normally name; else to auto, whose pool
// must not be empty (400 omega_default_unroutable).
func (h *Handlers) resolveOmegaAlias(rc *models.RequestContext, orgCfg *tenant.OrgConfig, req *models.ChatCompletionRequest) *models.APIError {
	alias := req.Model
	if !routing.IsOmegaAlias(alias) {
		return nil
	}
	rc.Metadata[metaModelAliasRequested] = alias
	if target := h.omegaAliasTarget(orgCfg); target != "" {
		rc.Model, req.Model = target, target
		return nil
	}
	if h.autoPoolEmpty(rc, orgCfg) {
		param := "model"
		return &models.APIError{
			Status:   http.StatusBadRequest,
			Type:     models.ErrTypeInvalidRequest,
			Code:     errOmegaDefaultUnroutable,
			Message:  fmt.Sprintf("model %q has nothing to route to: this organization has no managed models enabled and no provider of its own that auto routing can use", alias),
			Param:    &param,
			Metadata: map[string]string{"remedy_hint": omegaDefaultRemedyHint, metaModelAliasRequested: alias},
		}
	}
	return nil
}

// omegaAliasTarget is the model an omega alias is pinned to, or "" for auto.
// An auto name in either setting means auto (the org-config golden sets
// default_model to omega-default itself).
func (h *Handlers) omegaAliasTarget(orgCfg *tenant.OrgConfig) string {
	if m := orgCfg.AgentsConfig().DefaultModel; !routing.IsAutoModel(m) {
		return m
	}
	if orgCfg == nil {
		return ""
	}
	m := orgCfg.ModelMap[routing.OmegaDefaultModel]
	if m == "" || routing.IsAutoModel(m) || h.isProviderID(orgCfg, m) {
		return ""
	}
	return m
}

// isProviderID reports whether id names one of the gateway's or the org's
// providers, as a model_map value normally does.
func (h *Handlers) isProviderID(orgCfg *tenant.OrgConfig, id string) bool {
	if orgCfg != nil && orgCfg.Providers[id] != nil {
		return true
	}
	if h.registry == nil {
		return false
	}
	_, ok := h.registry.GetProvider(id)
	return ok
}

// autoPoolEmpty reports whether auto routing has no candidate for the
// request: the candidates routeAuto will build at step 11, before
// checkEligibility (step 11's, run only there) refuses any. A pool that
// exists but is refused in full is routeAuto's no_auto_candidate.
//
// The request's context carries only the license bit, which this step does
// not see. It can only widen the pool (orgProvidersOnly), and an org's key
// never has it, so the pool built without it is step 11's for an org's
// request, and for any request that is not limited to org providers anyway
// (no key store, an internal key). An org-less request that would be limited
// may be license-authorized and is left to routeAuto.
func (h *Handlers) autoPoolEmpty(rc *models.RequestContext, orgCfg *tenant.OrgConfig) bool {
	if h.registry == nil {
		return false
	}
	ctx := context.Background()
	if rc.Metadata["org_id"] == "" && h.orgProvidersOnly(ctx, rc) {
		return false
	}
	var listed []string
	if orgCfg != nil && orgCfg.Routing != nil && orgCfg.Routing.Auto != nil {
		listed = orgCfg.Routing.Auto.Candidates
	}
	if len(listed) > 0 {
		return len(h.listedAutoCandidates(ctx, rc, orgCfg, listed)) == 0
	}
	return len(h.defaultAutoCandidates(ctx, rc, orgCfg)) == 0
}

func contains(list []string, v string) bool {
	for _, s := range list {
		if s == v {
			return true
		}
	}
	return false
}

// agentHeaderInvalid is 400 agent_header_invalid for one header or body key.
func agentHeaderInvalid(param, reason string) *models.APIError {
	return &models.APIError{Status: http.StatusBadRequest, Type: models.ErrTypeInvalidRequest,
		Code: errAgentHeaderInvalid, Message: param + " " + reason, Param: &param}
}

// agentHeaderConflict is 400 agent_header_conflict: the same agent field sent
// twice with different values, or against a runtime-token claim (meta).
func agentHeaderConflict(param, msg string, meta map[string]string) *models.APIError {
	err := &models.APIError{Status: http.StatusBadRequest, Type: models.ErrTypeInvalidRequest,
		Code: errAgentHeaderConflict, Message: msg, Param: &param}
	if meta != nil {
		err.Metadata = meta
	}
	return err
}

// invalidTags is 400 invalid_tags.
func invalidTags(param, msg string) *models.APIError {
	return &models.APIError{Status: http.StatusBadRequest, Type: models.ErrTypeInvalidRequest,
		Code: errInvalidTags, Message: msg, Param: &param}
}
