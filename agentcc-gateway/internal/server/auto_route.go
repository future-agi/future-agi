package server

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"strconv"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/routing"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// autoPreferenceHeader overrides the org's auto-routing preference for one
// request: cost, balanced or quality.
const autoPreferenceHeader = "x-agentcc-auto-preference"

// routeHintLatencyError refuses route-hint latency on a model "auto"
// request. Auto ranks by price tier against the task; the only latency it
// has is this node's recent per-model P50 (ModelHealth), which a model lacks
// until it has been called here and which mixes in output length, so a
// "fastest" ranking on it would be arbitrary on a fresh node. A clear
// refusal beats a hint that silently does something else.
func routeHintLatencyError() *models.APIError {
	return &models.APIError{
		Status: http.StatusBadRequest,
		Type:   models.ErrTypeInvalidRequest,
		Code:   "route_hint_unsupported",
		Message: `route-hint "latency" is not supported with model "auto": auto ranks models by price tier ` +
			`for the task and has no latency ranking; send route-hint cost, balanced or quality, or no route-hint`,
	}
}

// routeAuto chooses the model for a `model: "auto"` request and points rc at
// it. The runners-up become the model fallback chain.
func (h *Handlers) routeAuto(ctx context.Context, r *http.Request, rc *models.RequestContext, orgCfg *tenant.OrgConfig) (*routing.AutoDecision, error) {
	policy := routing.AutoPolicy{}
	var candidates []string
	if orgCfg != nil && orgCfg.Routing != nil && orgCfg.Routing.Auto != nil {
		a := orgCfg.Routing.Auto
		policy = routing.AutoPolicy{Preference: a.Preference, MaxInputPerMTok: a.MaxInputPerMTok, MaxOutputPerMTok: a.MaxOutputPerMTok}
		candidates = a.Candidates
	}
	// The request's own preference: x-agentcc-auto-preference, else the agent
	// context's route-hint (cost, balanced or quality; latency is refused).
	if v := strings.ToLower(strings.TrimSpace(r.Header.Get(autoPreferenceHeader))); v != "" {
		policy.Preference = v
	} else if hint := rc.Agent.RouteHint; hint != "" {
		if hint == "latency" {
			return nil, routeHintLatencyError()
		}
		policy.Preference = hint
	}
	// The agent context's require sets aside every candidate that lacks one of
	// the capabilities.
	policy.Require = rc.Agent.Require

	pool, rejected := h.buildAutoPool(ctx, rc, orgCfg, candidates)
	d, err := routing.ChooseAuto(rc.Request, pool, policy)
	if err != nil {
		var none *routing.NoAutoCandidateError
		if errors.As(err, &none) {
			none.Rejected = append(none.Rejected, rejected...)
		}
		return nil, err
	}
	d.Rejected = append(d.Rejected, rejected...)

	rc.Metadata["auto_requested"] = rc.Model
	rc.Model = d.Model
	rc.Request.Model = d.Model
	if len(d.Fallbacks) > 0 {
		chains := make(map[string][]string, len(rc.OrgModelFallbacks)+1)
		for k, v := range rc.OrgModelFallbacks {
			chains[k] = v
		}
		chains[d.Model] = d.Fallbacks
		rc.OrgModelFallbacks = chains
	}
	recordAutoDecision(rc, d)
	return d, nil
}

// recordAutoDecision puts the decision in metadata, which the request log,
// the response headers and the console read.
func recordAutoDecision(rc *models.RequestContext, d *routing.AutoDecision) {
	rc.Metadata["auto_model"] = d.Model
	rc.Metadata["auto_task_tier"] = d.TaskTier
	rc.Metadata["auto_complexity"] = strconv.Itoa(d.Complexity)
	rc.Metadata["auto_preference"] = d.Preference
	rc.Metadata["auto_eligible"] = strconv.Itoa(len(d.Ranked))
	rc.Metadata["auto_considered"] = strconv.Itoa(d.Considered)
	rc.Metadata["auto_reason"] = d.Reason()
	if len(d.Fallbacks) > 0 {
		rc.Metadata["auto_fallbacks"] = strings.Join(d.Fallbacks, ",")
	}
	// The top of the ranking and a sample of rejections, for the console's
	// decision view. Bounded so a large catalog does not bloat every log row.
	summary := *d
	if len(summary.Ranked) > 5 {
		summary.Ranked = summary.Ranked[:5]
	}
	if len(summary.Rejected) > 10 {
		summary.Rejected = summary.Rejected[:10]
	}
	if b, err := json.Marshal(summary); err == nil {
		rc.Metadata["auto_decision"] = string(b)
	}
}

// autoErrorResponse turns a failed choice into the client's error.
func autoErrorResponse(err error) *models.APIError {
	if apiErr, ok := err.(*models.APIError); ok {
		return apiErr
	}
	msg := err.Error()
	if none, ok := err.(*routing.NoAutoCandidateError); ok && len(none.Rejected) > 0 {
		reasons := make([]string, 0, 3)
		for _, r := range none.Rejected {
			if len(reasons) == 3 {
				break
			}
			reasons = append(reasons, r.Model+": "+r.Reason)
		}
		msg += " (" + strings.Join(reasons, "; ") + ")"
	}
	return &models.APIError{
		Status:  http.StatusBadRequest,
		Type:    models.ErrTypeInvalidRequest,
		Code:    "no_auto_candidate",
		Message: msg,
	}
}
