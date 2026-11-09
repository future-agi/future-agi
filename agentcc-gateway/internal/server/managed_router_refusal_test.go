package server

import (
	"fmt"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/catalog"
	"github.com/futureagi/agentcc-gateway/internal/routerbilling"
)

// A listed router remains unavailable until the gateway can enforce its target
// envelope, not just parse a positive quote or pin an upstream provider. No
// mock success is treated as evidence that the real vendor honors a model cap.
func TestManagedRouterCreditsRefusalEvidence(t *testing.T) {
	routers := bundledRouters(t)
	if len(routers) != 7 {
		t.Fatalf("router inventory changed: %d", len(routers))
	}
	for _, fixedQuote := range []bool{false, true} {
		list := make([]*catalog.Model, len(routers))
		for i, m := range routers {
			copy := *m
			if fixedQuote {
				copy.DynamicPrice = false
				copy.Prices = catalog.Prices{Prompt: 1e-6, Completion: 2e-6}
			}
			list[i] = &copy
		}
		install := withRouters(list)
		for _, m := range list {
			for _, surface := range managedSurfaces {
				for _, stream := range []bool{false, true} {
					for _, named := range []string{m.ID, "openrouter/" + m.ID} {
						t.Run(fmt.Sprintf("quote=%t/%s/%s/stream=%t", fixedQuote, named, surface, stream), func(t *testing.T) {
							r := runManagedOn(t, install, surface, named, stream, managedGatewayOptions{}, nil, false, nil)
							refusedRouterAsNotAvailable(t, "router credit refusal", named, r)
							assertRouterRefusalReason(t, m.ID, r.message)
						})
					}
				}
			}
			for _, stream := range []bool{false, true} {
				t.Run(fmt.Sprintf("quote=%t/%s/locked/stream=%t", fixedQuote, m.ID, stream), func(t *testing.T) {
					r := runManagedOn(t, install, "/v1/chat/completions", m.ID, stream, managedGatewayOptions{}, nil, false, managedLock)
					refusedRouterAsNotAvailable(t, "locked router credit refusal", "openrouter", r)
					assertRouterRefusalReason(t, m.ID, r.message)
				})
			}
		}
	}
}

func assertRouterRefusalReason(t *testing.T, id, message string) {
	t.Helper()
	if routerbilling.FamilyMember(id) {
		if message != catalog.RouterCreditsUnavailable {
			t.Errorf("router refusal is not value-free guidance: %q", message)
		}
		return
	}
	if !strings.Contains(message, "finite target-model set") {
		t.Errorf("%s refusal does not explain the absent enforced bound: %q", id, message)
	}
	if id == "openrouter/fusion" && !strings.Contains(message, "child invocations and search/fetch charges") {
		t.Errorf("Fusion refusal omits its compound billing: %q", message)
	}
	if id == "openrouter/bodybuilder" && !strings.Contains(message, "generated request objects are not evidence of executed model calls") {
		t.Errorf("Bodybuilder refusal confuses its output with executed calls: %q", message)
	}
}

func TestManagedRouterCreditsBYOKUnchanged(t *testing.T) {
	routers := bundledRouters(t)
	install := withRouters(routers)
	for _, m := range routers {
		t.Run(m.ID, func(t *testing.T) {
			for _, named := range []string{m.ID, "openrouter/" + m.ID} {
				servedOnTheOrgsOwnKeyOn(t, install, named, m.ID)
			}
		})
	}
}

func TestManagedRouterCreditsFixedQuoteNotAdvertised(t *testing.T) {
	routers := bundledRouters(t)
	for _, m := range routers {
		m.DynamicPrice = false
		m.Prices = catalog.Prices{Prompt: 1e-6, Completion: 2e-6}
	}
	g := newManagedTestGateway(t, managedOptIn())
	withRouters(routers)(t, g)
	for _, offered := range g.srv.handlers.managedModelObjects() {
		for _, router := range routers {
			if offered.ID == router.ID {
				t.Errorf("fixed quote advertised unbounded router %s", router.ID)
			}
		}
	}
	cat := g.srv.handlers.catalog.Load()
	for _, row := range buildCatalogAccess(cat, managedOptIn(), 5, g.srv.handlers.catalog.Now()).Data {
		for _, router := range routers {
			if row.ID == router.ID && (row.Managed || row.ManagedAvailableOnOptIn) {
				t.Errorf("access view advertised unbounded router %s", router.ID)
			}
		}
	}
}

// Only router tests accept the new value-free guidance. Unrelated availability
// tests must keep their existing model/provider identity assertion.
func refusedRouterAsNotAvailable(t *testing.T, what, named string, r settledRun) {
	t.Helper()
	if r.message != catalog.RouterCreditsUnavailable {
		refusedAsNotAvailable(t, what, named, r)
		return
	}
	if r.status != 403 || r.calls != 0 || r.balance != "1.000000" || r.spent != 0 || r.held != 0 || len(r.recs) != 0 {
		t.Errorf("%s: status=%d calls=%d balance=%s spent=%d held=%d records=%v; want pre-dispatch credit refusal", what, r.status, r.calls, r.balance, r.spent, r.held, r.recs)
	}
}
