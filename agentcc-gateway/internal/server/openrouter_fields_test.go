package server

import (
	"net/http"
	"slices"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/catalog"
	"github.com/futureagi/agentcc-gateway/internal/routing/eligibility"
	"github.com/futureagi/agentcc-gateway/internal/routing/reqopts"
)

// OpenRouter's request fields on the gateway (G26): models[] (an ordered
// fallback list), provider.order, provider.sort and provider.allow_fallbacks,
// on BYOK and Future AGI traffic alike. Every field the gateway does not
// implement keeps its 400 routing_field_not_yet_supported, and managed
// traffic keeps plugins, route and transforms refused.

// TestModelsFallbackBYOK: when the request's model fails, models[] is tried
// in order on the org's own keys, and the list never reaches an upstream.
func TestModelsFallbackBYOK(t *testing.T) {
	g := newFailingRoutingGateway(t, byokOrg(), 0, 1)

	w := g.chat(t, body("gpt-4o", `"models":["`+managedModel+`"]`))

	if w.Code != http.StatusOK {
		t.Fatalf("status %d body %s", w.Code, w.Body)
	}
	if n := len(g.operator.received()); n != 1 {
		t.Fatalf("the primary was tried %d times, want once", n)
	}
	got := lastBody(t, g.openrouter)
	if got["model"] != managedModel {
		t.Fatalf("fallback upstream model = %v, want %s", got["model"], managedModel)
	}
	if got := w.Header().Get("x-agentcc-billing-mode"); got != "byok" {
		t.Errorf("billing mode %q, want byok: the fallback ran on the org's own OpenRouter key", got)
	}
	for _, b := range append(g.operator.received(), g.openrouter.received()...) {
		if _, ok := b["models"]; ok {
			t.Fatalf("models[] reached an upstream: %v", b)
		}
	}
}

// TestModelsFallbackManaged: on Future AGI traffic a models[] hop is a
// managed attempt of its own: it reaches the managed upstream with the
// managed rewrite, and the request is billed as managed.
func TestModelsFallbackManaged(t *testing.T) {
	g := newFailingRoutingGateway(t, managedOrg(), 1, 0)

	w := g.chat(t, body(managedModel, `"models":["openai/gpt-6-sol"]`))

	if w.Code != http.StatusOK {
		t.Fatalf("status %d body %s", w.Code, w.Body)
	}
	bodies := g.openrouter.received()
	if len(bodies) != 2 || bodies[0]["model"] != managedModel || bodies[1]["model"] != "openai/gpt-6-sol" {
		t.Fatalf("managed upstream got %v, want the primary then openai/gpt-6-sol", bodies)
	}
	if got := w.Header().Get("x-agentcc-billing-mode"); got != "managed" {
		t.Errorf("billing mode %q, want managed", got)
	}
	last := bodies[1]
	if last["user"] != reqopts.UpstreamUser(routingOrg) || last["models"] != nil {
		t.Errorf("managed fallback body = %v: the hashed org id as user, no models[]", last)
	}
	if u, _ := last["usage"].(map[string]any); u["include"] != true {
		t.Errorf("managed fallback usage = %v, want include true", last["usage"])
	}
}

// TestModelsUnusedWhenThePrimaryServes: a request whose model answers makes
// one upstream call.
func TestModelsUnusedWhenThePrimaryServes(t *testing.T) {
	g := newRoutingGateway(t, byokOrg())
	if w := g.chat(t, body("gpt-4o", `"models":["`+managedModel+`"]`)); w.Code != http.StatusOK {
		t.Fatalf("status %d body %s", w.Code, w.Body)
	}
	if n := len(g.operator.received()) + len(g.openrouter.received()); n != 1 {
		t.Fatalf("%d upstream calls, want 1", n)
	}
}

// orderedCatalog is the managed model with three OpenRouter endpoints:
// cheap is the best-ranked (cheapest), fast has the lowest latency and the
// most throughput.
func orderedCatalog() map[string]*catalog.Model {
	lat := func(ms float64) func(*catalog.Endpoint) { return func(e *catalog.Endpoint) { e.LatencyP50Ms = &ms } }
	tps := func(v float64) func(*catalog.Endpoint) { return func(e *catalog.Endpoint) { e.ThroughputP50 = &v } }
	m := eligModel(managedModel, 1e-6,
		orEndpoint(managedModel, "cheap", 0.5e-6, lat(900), tps(20)),
		orEndpoint(managedModel, "mid", 1e-6, lat(400), tps(60)),
		orEndpoint(managedModel, "fast", 2e-6, lat(100), tps(200)),
	)
	return map[string]*catalog.Model{managedModel: m}
}

// withOrderedCatalog gives the routing gateway's managed provider the
// ordered catalogue.
func withOrderedCatalog(t *testing.T, g *routingGateway) {
	t.Helper()
	g.srv.handlers.catalog.Put(&catalog.Snapshot{ProviderID: "openrouter", Source: "bundled",
		CatalogSource: catalog.SourceOpenRouter, FetchedAt: time.Now(), Models: orderedCatalog()})
}

func pinnedOrder(t *testing.T, g *routingGateway) []any {
	t.Helper()
	prov, _ := lastBody(t, g.openrouter)["provider"].(map[string]any)
	return jsonList(prov["order"])
}

// TestManagedProviderOrderAndSort: on Future AGI traffic the gateway applies
// provider.order and provider.sort to the endpoints it pins, so OpenRouter
// is asked for them in that order.
func TestManagedProviderOrderAndSort(t *testing.T) {
	g := newRoutingGateway(t, managedOrg())
	withOrderedCatalog(t, g)

	for fragment, want := range map[string][]any{
		``:                                 {"cheap", "mid", "fast"},
		`"provider":{"order":["fast"]}`:    {"fast", "cheap", "mid"},
		`"provider":{"sort":"latency"}`:    {"fast", "mid", "cheap"},
		`"provider":{"sort":"throughput"}`: {"fast", "mid", "cheap"},
		`"provider":{"sort":"price"}`:      {"cheap", "mid", "fast"},
		`"provider":{"order":["mid"],"allow_fallbacks":false}`: {"mid"},
		`"provider":{"allow_fallbacks":false}`:                 {"cheap"},
	} {
		w := g.chat(t, body(managedModel, fragment))
		if w.Code != http.StatusOK {
			t.Fatalf("%s: status %d body %s", fragment, w.Code, w.Body)
		}
		if got := pinnedOrder(t, g); !slices.Equal(got, want) {
			t.Errorf("%s: pinned order %v, want %v", fragment, got, want)
		}
		prov, _ := lastBody(t, g.openrouter)["provider"].(map[string]any)
		if prov["allow_fallbacks"] != false || !slices.Equal(jsonList(prov["only"]), want) {
			t.Errorf("%s: provider object %v, want only = order and allow_fallbacks false", fragment, prov)
		}
	}
}

// TestManagedProviderPreferenceRefused: an order with allow_fallbacks false
// that names none of the model's providers is 404 no_provider_matches; a
// sort the gateway has no figures for, or an order or sort on a model with
// no list of its providers, is 400 provider_preference_unavailable. None
// reaches the upstream.
func TestManagedProviderPreferenceRefused(t *testing.T) {
	g := newRoutingGateway(t, managedOrg())
	withOrderedCatalog(t, g)

	w := g.chat(t, body(managedModel, `"provider":{"order":["elsewhere"],"allow_fallbacks":false}`))
	expectEligibilityRefusal(t, w, eligibilityRefusal{http.StatusNotFound, eligibility.CodeNoProviderMatches, eligibility.CodeNoProviderMatches})

	bare := newRoutingGateway(t, managedOrg()) // testCatalogModels: no endpoint list
	for _, fragment := range []string{`"provider":{"order":["anthropic"]}`, `"provider":{"sort":"latency"}`} {
		w := bare.chat(t, body(managedModel, fragment))
		if w.Code != http.StatusBadRequest {
			t.Fatalf("%s: status %d body %s, want 400", fragment, w.Code, w.Body)
		}
		if code, _ := apiError(t, w); code != eligibility.CodeProviderPreferenceUnavailable {
			t.Fatalf("%s: code %s, want %s", fragment, code, eligibility.CodeProviderPreferenceUnavailable)
		}
	}
	if n := len(g.openrouter.received()) + len(bare.openrouter.received()); n != 0 {
		t.Fatalf("%d refused requests reached the managed upstream", n)
	}
}

// TestBYOKOpenRouterGetsProviderPreferences: a BYOK OpenRouter request is
// forwarded the caller's order, sort and allow_fallbacks, which OpenRouter
// applies on the customer's account.
func TestBYOKOpenRouterGetsProviderPreferences(t *testing.T) {
	g := newRoutingGateway(t, byokOrg())
	w := g.chat(t, body(managedModel, `"provider":{"order":["anthropic","google-vertex"],"sort":"throughput","allow_fallbacks":false}`))
	if w.Code != http.StatusOK {
		t.Fatalf("status %d body %s", w.Code, w.Body)
	}
	prov, _ := lastBody(t, g.openrouter)["provider"].(map[string]any)
	if !slices.Equal(jsonList(prov["order"]), []any{"anthropic", "google-vertex"}) || prov["sort"] != "throughput" || prov["allow_fallbacks"] != false {
		t.Fatalf("BYOK OpenRouter provider object = %v", prov)
	}
}

// TestBYOKDirectProviderOrder: on a BYOK upstream that is not an
// aggregator the provider is the one endpoint: order and sort are met, and
// allow_fallbacks false with an order that leaves it out is refused before
// any upstream call.
func TestBYOKDirectProviderOrder(t *testing.T) {
	g := newRoutingGateway(t, byokOrg())
	if w := g.chat(t, body("gpt-4o", `"provider":{"order":["operator"],"sort":"latency"}`)); w.Code != http.StatusOK {
		t.Fatalf("status %d body %s", w.Code, w.Body)
	}
	before := len(g.operator.received())
	w := g.chat(t, body("gpt-4o", `"provider":{"order":["anthropic"],"allow_fallbacks":false}`))
	expectEligibilityRefusal(t, w, eligibilityRefusal{http.StatusNotFound, eligibility.CodeNoProviderMatches, eligibility.CodeNoProviderMatches})
	if len(g.operator.received()) != before {
		t.Fatal("a refused request reached the upstream")
	}
}

// TestOtherOpenRouterFieldsStillRefused: the fields the gateway does not
// implement keep their 400 routing_field_not_yet_supported, and managed
// traffic keeps plugins refused.
func TestOtherOpenRouterFieldsStillRefused(t *testing.T) {
	for _, f := range []string{reqopts.FieldProviderOrder, reqopts.FieldProviderSort, reqopts.FieldProviderAllowFallbacks, reqopts.FieldModels} {
		if !reqopts.Supported(f) {
			t.Errorf("%s is not registered", f)
		}
	}
	g := newRoutingGateway(t, managedOrg())
	for f, fragment := range map[string]string{
		reqopts.FieldProviderPreferredMaxLatency: `"provider":{"preferred_max_latency":2}`,
		reqopts.FieldFallbacks:                   `"fallbacks":[{"model":"gpt-4o"}]`,
		reqopts.FieldPreset:                      `"preset":"cheap"`,
		reqopts.FieldGatewayOrder:                `"providerOptions":{"gateway":{"order":["anthropic"]}}`,
	} {
		if reqopts.Supported(f) {
			continue // a field the gateway implements is checked by its own tests
		}
		expect400(t, g.chat(t, body(managedModel, fragment)), reqopts.CodeNotYetSupported, f)
	}
	expect400(t, g.chat(t, body(managedModel, `"plugins":[{"id":"web"}],"models":["openai/gpt-6-sol"]`)), reqopts.CodeUnsupportedRoutingField, "plugins")
	if n := len(g.openrouter.received()); n != 0 {
		t.Fatalf("%d refused requests reached the managed upstream", n)
	}
}
