package server

import (
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
)

// A reload whose conditional routes the router refuses changes nothing: the
// routing pieces that can fail are built before any is swapped, so the
// gateway keeps serving whole on its old state and the reload answers with
// the router's refusal.
func TestReloadRefusedByTheConditionalRouterKeepsTheOldStateWhole(t *testing.T) {
	g := newG5Gateway(t, nil)
	h := g.srv.handlers
	beforeRouter := h.registry.Router()
	beforeDB := h.modelDB.Load()
	beforeConditional := h.conditionalRouter.Load()

	next := *g.srv.cfg
	next.Routing.Targets = map[string][]config.RoutingTargetConfig{"m": {{Provider: "cheap"}}}
	next.Routing.ConditionalRoutes = []config.ConditionalRouteConfig{{
		Name:      "no-provider",
		Condition: config.ConditionConfig{Field: "model", Op: "$eq", Value: "m"},
	}}
	err := h.ReloadRouting(&next, h.registry)
	if err == nil {
		t.Fatal("ReloadRouting accepted a route list the router refuses")
	}
	if !strings.Contains(err.Error(), `conditional_routes[0] "no-provider": action.provider`) {
		t.Errorf("error %q should carry the router's refusal", err)
	}
	if h.registry.Router() != beforeRouter {
		t.Error("a refused reload swapped the load-balancing router")
	}
	if h.modelDB.Load() != beforeDB {
		t.Error("a refused reload swapped the model database")
	}
	if h.conditionalRouter.Load() != beforeConditional {
		t.Error("a refused reload swapped the conditional router")
	}
	for i := 0; i < 6; i++ {
		g.chat(t, "m")
	}
	if g.ups["pricey"].calls.Load() == 0 || g.ups["mid"].calls.Load() == 0 {
		t.Error("after a refused reload the gateway no longer spreads m over its old targets")
	}
}

// A reload whose load-balancing targets the registry refuses (a provider it
// does not hold) changes nothing either, even with valid conditional routes.
func TestReloadRefusedByTheRegistryRouterKeepsTheOldStateWhole(t *testing.T) {
	g := newG5Gateway(t, nil)
	h := g.srv.handlers
	beforeRouter := h.registry.Router()
	beforeDB := h.modelDB.Load()
	beforeConditional := h.conditionalRouter.Load()

	next := *g.srv.cfg
	next.Routing.Targets = map[string][]config.RoutingTargetConfig{"m": {{Provider: "nope"}}}
	next.Routing.ConditionalRoutes = []config.ConditionalRouteConfig{{
		Name:      "valid",
		Condition: config.ConditionConfig{Field: "model", Op: "$eq", Value: "m"},
		Action:    config.RouteActionConfig{Provider: "cheap"},
	}}
	if err := h.ReloadRouting(&next, h.registry); err == nil {
		t.Fatal("ReloadRouting accepted targets naming a provider the registry does not hold")
	}
	if h.registry.Router() != beforeRouter {
		t.Error("a refused reload swapped the load-balancing router")
	}
	if h.modelDB.Load() != beforeDB {
		t.Error("a refused reload swapped the model database")
	}
	if h.conditionalRouter.Load() != beforeConditional {
		t.Error("a refused reload swapped the conditional router")
	}
}

// An organization's conditional_routes are stored but not applied: routes
// that match every request, on the model and on request metadata, move none
// of them, while the same org config's routing strategy does apply, which
// shows the requests are read against that org config.
func TestAnOrganizationsConditionalRoutesAreNotApplied(t *testing.T) {
	// The org's router selects through the failover (orgRouterSelects), so
	// the gateway's failover stays on; every upstream answers 200, so it
	// never moves a request.
	g := newG5Gateway(t, nil)
	g.srv.handlers.SetInjectedDeps(InjectedDeps{Prices: newG5Prices().lookup})
	cfg := g.setOrg(t, `{"strategy":"cost_optimized","conditional_routes":[`+
		`{"field":"model","operator":"$eq","value":"m","target":"mid"},`+
		`{"field":"metadata.tier","operator":"$eq","value":"gold","target":"mid"},`+
		`{"field":"tier","operator":"$eq","value":"gold","target":"mid"}]}`)
	if cfg.Routing == nil || len(cfg.Routing.ConditionalRoutes) != 3 {
		t.Fatalf("the org config does not hold the routes: %+v", cfg.Routing)
	}
	for i := 0; i < 5; i++ {
		got := g.chat(t, "m", "x-agentcc-metadata", `{"tier":"gold"}`).Header().Get("x-agentcc-provider")
		if got != "cheap" {
			t.Fatalf("request %d went to %q; want cheap, the org's cost_optimized choice, and never mid, the org routes' target", i, got)
		}
	}
	if n := g.ups["mid"].calls.Load(); n != 0 {
		t.Errorf("the org routes' target served %d calls, want 0", n)
	}
	// A route read as a model override after the provider is chosen would
	// change the model sent; none is applied, so cheap gets "m".
	if got := g.ups["cheap"].lastModel(); got != "m" {
		t.Errorf("cheap was sent model %q, want m: an org route changed the model", got)
	}
}
