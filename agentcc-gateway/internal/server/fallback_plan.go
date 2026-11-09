package server

import (
	"slices"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/routing"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// A request's model fallback plan is the ordered list of models it moves to
// when its routed model fails. This file is the only one in the package that
// touches a plan source; TestCAT2FallbackPlanSourceGuard enforces that, so
// admission and dispatch cannot disagree about which chain a request has.
//
// Every plan is resolved for the request's fallback route
// (models.FallbackRoute): the name the request is routed under, the model its
// first attempt is routed with, and its models[] less those two. Routing fixes
// the route once (fixFallbackRoute), when it has chosen the first attempt and
// just before admission judges it: in resolveRoute for chat, and in
// AnthropicMessages before each of its admissions. That is the request's
// entry into the plan: alias resolution, auto's choice and complexity routing
// have renamed it by then, and provider resolution has chosen the model its
// first attempt is sent. Each dispatch entry also fixes it first, before any
// attempt, which changes nothing for a routed request. Admission
// (mixedCataloguePlan) and every dispatch path pass that fixed route
// (rc.FallbackRoute()); the resolver reads no live request identity. A
// provider failover, or a fallback hop, rewrites rc.Request.Model before a
// dispatch path resolves its plan, and a plan read from it was rebuilt around
// the failover target's model and put the first hop back into it (CAT-2 r13
// close, Sol P2).
//
// Two resolutions run without a fixed route. Auto pool admission judges its
// candidates before auto has chosen one: the zero route, on which only the
// request's own models[], as it sent them, is judged. Resolution-time
// fallback (resolveProviderFor) walks a plan while provider resolution is
// still choosing the first attempt: a provisional route
// (provisionalFallbackRoute) for the model it could not resolve.
//
// The plan has exactly one source, the first that applies:
//  1. the request's own models[] (G26), less the route's two identities. It
//     replaces every configured chain, even when nothing is left of it;
//  2. none, while the requested name is an auto alias (a provider lock skips
//     auto's choice): an alias never identifies a dispatched hop. Auto
//     installs the chosen model's plan (installAutoFallbackPlan) before that
//     model is judged;
//  3. the org's chains, with any plan auto installed, when there are any;
//  4. the node's config.yaml routing.model_fallbacks.
//
// Within sources 3 and 4 the requested name wins by presence: an explicit
// empty entry means no chain. Only a missing requested entry inherits the
// chain of the route's inherited name (fallbackChainModel).
func (h *Handlers) effectiveFallbackPlan(rc *models.RequestContext, route models.FallbackRoute) []string {
	if !route.Routed() {
		return declaredPlanAsSent(rc)
	}
	if route.Declares() {
		return route.Declared()
	}
	requested := route.Requested()
	if routing.IsAutoModel(requested) {
		return nil
	}
	if len(rc.OrgModelFallbacks) > 0 {
		if plan, present := rc.OrgModelFallbacks[requested]; present {
			return plan
		}
		return rc.OrgModelFallbacks[route.Inherit()]
	}
	node := h.modelFallbacks.Load()
	if plan, present := node.LookupChain(requested); present {
		return plan
	}
	plan, _ := node.LookupChain(route.Inherit())
	return plan
}

// suppressDisabledRequestFallbacks applies the effective org switch before
// routing fixes the request identity. A disabled caller chain cannot replace
// configured plans or widen their failure statuses (G26 / 3bef8fa6d8).
func suppressDisabledRequestFallbacks(rc *models.RequestContext, orgCfg *tenant.OrgConfig) {
	if !requestFallbacksEnabled(orgCfg) {
		rc.Routing.Models = nil
	}
}

// declaredPlanAsSent is the request's models[] as it sent them, nil when it
// sent none: the plan judged before routing has chosen a first attempt.
func declaredPlanAsSent(rc *models.RequestContext) []string {
	return slices.Clone(rc.Routing.Models)
}

// fixFallbackRoute fixes rc's fallback route from the route routing chose:
// rc.Model, the name the request is routed under, and rc.Request.Model (rc.Model
// without a request), the model its first attempt is routed with. Routing
// calls it once that attempt is chosen and just before admission judges it;
// each dispatch entry (runNonStream, openRoutedStream) calls it first, before
// any attempt, which changes nothing for a routed request. A route is fixed
// once and never replaced (RequestContext.FixFallbackRoute).
func fixFallbackRoute(rc *models.RequestContext) {
	if rc.FallbackRoute().Routed() {
		return
	}
	hop := rc.Model
	if rc.Request != nil {
		hop = rc.Request.Model
	}
	rc.FixFallbackRoute(fallbackRouteFor(rc, rc.Model, hop))
}

// provisionalFallbackRoute is the route resolveProviderFor walks a plan on
// when no provider resolves model, the model the request's first attempt was
// to be routed with, while routing is still choosing it. rc.Model is final by
// then (resolveRoute renames it before provider resolution).
func provisionalFallbackRoute(rc *models.RequestContext, model string) models.FallbackRoute {
	return fallbackRouteFor(rc, rc.Model, model)
}

// fallbackRouteFor is the route of a request routed under requested whose
// first attempt is routed with hop: its models[] less those two, and the name
// its configured chains are inherited under.
func fallbackRouteFor(rc *models.RequestContext, requested, hop string) models.FallbackRoute {
	declares := len(rc.Routing.Models) > 0
	var declared []string
	if declares {
		declared = make([]string, 0, len(rc.Routing.Models))
		for _, m := range rc.Routing.Models {
			if m != requested && m != hop {
				declared = append(declared, m)
			}
		}
	}
	return models.NewFallbackRoute(requested, hop, fallbackChainModel(rc, hop), declares, declared)
}

// declaresFallbackPlan reports whether the request sent its own models[].
func declaresFallbackPlan(rc *models.RequestContext) bool {
	return len(rc.Routing.Models) > 0
}

// fallbackChainModel is the model a chat request's model fallback chain is
// looked up under, given routed, the model its first attempt was sent. An org
// provider match sends the org's registered id rather than the model the
// request was routed with (resolveProviderOrgFirst), and the chain is keyed
// by the latter.
func fallbackChainModel(rc *models.RequestContext, routed string) string {
	if m := rc.Metadata["org_provider_model_match"]; m != "" {
		return m
	}
	return routed
}

// installOrgFallbackPlans makes the org's chains the request's org source
// (call-order step 4). An org that turned its chains off (fallback_enabled
// false) keeps them stored but never tried (FallbackOn).
func installOrgFallbackPlans(rc *models.RequestContext, r *tenant.RoutingConfig) {
	if len(r.ModelFallbacks) > 0 && r.FallbackOn() {
		rc.Metadata["org_has_fallbacks"] = "true"
		rc.OrgModelFallbacks = r.ModelFallbacks
	}
}

// installAutoFallbackPlan makes plan the chain of model, the model auto
// chose, over any org chain under that name. The org's chains map is shared
// with other requests, so it is copied. An empty plan stays installed: an
// inherited org or node chain must not restore a runner-up auto removed.
func installAutoFallbackPlan(rc *models.RequestContext, model string, plan []string) {
	chains := make(map[string][]string, len(rc.OrgModelFallbacks)+1)
	for k, v := range rc.OrgModelFallbacks {
		chains[k] = v
	}
	chains[model] = plan
	rc.OrgModelFallbacks = chains
}

// nodeFallbackPlans is the node's config.yaml chains, nil when it has none.
func nodeFallbackPlans(r *config.RoutingConfig) *routing.ModelFallbacks {
	if len(r.ModelFallbacks) == 0 {
		return nil
	}
	return routing.NewModelFallbacks(r.ModelFallbacks)
}

// nodeFallbackCount is how many models the node's config.yaml gives a chain.
func nodeFallbackCount(r *config.RoutingConfig) int {
	return len(r.ModelFallbacks)
}

// setNodeFallbackPlans swaps in the node's chains (nil for none).
func (h *Handlers) setNodeFallbackPlans(mf *routing.ModelFallbacks) {
	h.modelFallbacks.Store(mf)
}
