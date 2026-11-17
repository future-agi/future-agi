package server

import (
	"context"
	"fmt"
	"net/http"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// A delegated Console run calls only the models its grant names (fe1c field
// 20; GATEWAY-CONSOLE-RUN-R3). The cap holds at every upstream attempt of the
// call, not only on the body's model:
//
//  1. verifyDelegatedRequest (delegated.go) refuses, before routing, a body
//     model outside the cap and every request-side way to another model:
//     models[], fallbacks, provider options, x-agentcc-config and the
//     routing headers below.
//  2. peekDelegatedKey stamps the cap on the request (rc.ModelCap) from the
//     verified grant.
//  3. Routing chooses no other model for a capped request: org complexity
//     (applyOrgComplexity), global complexity and auto (resolveRoute) are
//     skipped, it has no fallback plan from any source
//     (effectiveFallbackPlan), and the traffic mirror never copies it.
//     resolveRoute then refuses a route whose model left the cap or whose
//     org Model Map entry sends another model in its name
//     (consoleRunRouteRefusal), and a resolution that substituted another
//     model through a conditional route (consoleRunResolvedRefusal).
//  4. startAttempt, which every chat attempt passes before it is sent
//     (primary, retry, failover, model and auto fallback, stream start),
//     refuses an attempt whose logical model is not in the cap
//     (consoleRunAttemptRefusal), and the request is answered 403
//     model_not_permitted (attemptLimitError, consoleRunCapError).
//
// The logical model is the id the attempt was resolved for: rc.Model, the
// name the request is routed under after every rename routing makes. It is
// never the provider's wire id (an org provider's registered id from
// orgModelFor, a routing target's or a managed model's upstream name), which
// says how the same model is asked for, not which model it is.

// consoleRunRoutingHeaders are the request headers that move a call to a
// model other than the one it names: the complexity tier
// (applyOrgComplexity, resolveRoute step 4) and auto's preference
// (routeAuto). A Console run carrying one is refused as one carrying
// x-agentcc-config is. Header names match without case.
var consoleRunRoutingHeaders = []string{"x-agentcc-complexity-override", autoPreferenceHeader}

// errModelNotPermitted is the 403 a Console run gets for model, a model its
// grant does not name.
func errModelNotPermitted(model string) *models.APIError {
	return &models.APIError{Status: http.StatusForbidden, Type: models.ErrTypePermission, Code: "model_not_permitted",
		Message: fmt.Sprintf("model %q is not one this Console run may call", model)}
}

// stampConsoleRunCap holds rc to the cap of the console-run grant that
// verified ctx's request, when there is one.
func stampConsoleRunCap(ctx context.Context, rc *models.RequestContext) {
	if ids, capped := auth.DelegatedModelCap(ctx); capped {
		rc.ModelCap = models.ModelCap{Capped: true, Models: ids}
	}
}

// consoleRunRouteRefusal is the 403 for a capped request whose route left
// its cap, nil otherwise: rc.Model, the name routing settled on, is not in
// the cap (a rename before routing: an access-group alias, the agent
// context's default model), or the org's Model Map entry for it sends
// another model in its name (orgMappedRoute: an org provider with one model
// that does not list the id). An entry that only chooses the provider for
// the same id is served.
func consoleRunRouteRefusal(rc *models.RequestContext, orgCfg *tenant.OrgConfig) *models.APIError {
	if !rc.ModelCap.Capped {
		return nil
	}
	if !rc.ModelCap.Permits(rc.Model) {
		return errModelNotPermitted(rc.Model)
	}
	// Provider resolution is handed rc.Request.Model (equal to rc.Model at
	// this point on every route): it must be a cap id too.
	routed := rc.Model
	if rc.Request != nil {
		routed = rc.Request.Model
	}
	if !rc.ModelCap.Permits(routed) {
		return errModelNotPermitted(routed)
	}
	if orgCfg != nil {
		if _, upstream, ok := orgMappedRoute(orgCfg, routed); ok && upstream != routed {
			return errModelNotPermitted(upstream)
		}
	}
	return nil
}

// consoleRunResolvedRefusal is the 403 for a capped request whose provider
// resolution substituted another model: a conditional route's model_override
// (resolveProviderFor records the rule as routing_rule and points
// rc.Request at the override). routed is rc.Request.Model before
// resolution. Only a key that may use the gateway's own providers reaches a
// conditional route (a delegated principal on a gateway without a key
// store); every other rename resolution makes (an org provider's registered
// id, a managed or routing target's upstream id) names the same model and
// passes.
func consoleRunResolvedRefusal(rc *models.RequestContext, routed string) *models.APIError {
	if !rc.ModelCap.Capped || rc.Request == nil || rc.Metadata["routing_rule"] == "" || rc.Request.Model == routed {
		return nil
	}
	return errModelNotPermitted(rc.Request.Model)
}

// consoleRunAttemptRefusal is models.ReasonPolicy when the attempt of kind
// about to be made for rc may not be sent under its Console run's cap, ""
// when it may or rc has no cap. A request whose principal is a console-run
// grant is held to that grant even if nothing stamped it (a path that did
// not run peekDelegatedKey). A refusal marks rc (ModelCap.Refused), so the
// request is answered 403 model_not_permitted.
func consoleRunAttemptRefusal(ctx context.Context, rc *models.RequestContext, kind models.AttemptKind) string {
	if !rc.ModelCap.Capped {
		stampConsoleRunCap(ctx, rc)
		if !rc.ModelCap.Capped {
			return ""
		}
	}
	model, off := consoleRunOffCapModel(rc, kind)
	if !off {
		return ""
	}
	if !rc.ModelCap.Refused {
		rc.ModelCap.Refused = true
		rc.ModelCap.RefusedModel = model
	}
	return models.ReasonPolicy
}

// consoleRunOffCapModel names the model an attempt of kind for capped rc
// would serve outside its cap, and whether it would:
//   - a hop down a model fallback plan (model, auto or request fallback)
//     serves another model of the plan, which a Console run has none of
//     (effectiveFallbackPlan): it is refused whatever its model, named by
//     the id the hop was resolved to;
//   - a resolution-time fallback (resolveProviderFor) serves the plan model
//     it records as fallback_model;
//   - every other attempt (primary, retry, provider failover) serves
//     rc.Model.
func consoleRunOffCapModel(rc *models.RequestContext, kind models.AttemptKind) (string, bool) {
	switch kind {
	case models.AttemptModelFallback, models.AttemptAutoFallback, models.AttemptRequestFallback:
		if rc.Request != nil && rc.Request.Model != "" {
			return rc.Request.Model, true
		}
		return rc.Model, true
	}
	if fb := rc.Metadata["fallback_model"]; fb != "" {
		return fb, true
	}
	if !rc.ModelCap.Permits(rc.Model) {
		return rc.Model, true
	}
	return "", false
}

// consoleRunCapError is the 403 model_not_permitted of a request one of
// whose attempts its Console run's cap refused, nil for any other.
func consoleRunCapError(rc *models.RequestContext) *models.APIError {
	if !rc.ModelCap.Refused {
		return nil
	}
	return errModelNotPermitted(rc.ModelCap.RefusedModel)
}
