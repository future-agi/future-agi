package server

import (
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"sort"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/catalog"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// A model the managed catalogue lists with no fixed price, a router whose
// price only the upstream reports after the call (DynamicPrice: the bundled
// OpenRouter snapshot's openrouter/auto and the other routers it lists), is
// refused on one Future AGI key as not available before any upstream call,
// however it is named: by its id, behind the managed provider's
// "openrouter/" prefix, or on a chat request locked to the Future AGI
// provider. A stream of it that ended before the upstream's usage chunk
// would have no card to be billed on. A call on the org's own key under the
// Future AGI provider's id is the org's own and is unaffected.

// routerModel is the bundled router a caller is likeliest to send.
const routerModel = "openrouter/auto"

// bundledRouters are the bundled OpenRouter snapshot's models with no fixed
// price, sorted by id. The test fails when routerModel is not among them, so
// a changed snapshot never leaves the tests below checking nothing.
func bundledRouters(t *testing.T) []*catalog.Model {
	t.Helper()
	bundled, _, err := catalog.BundledOpenRouter()
	if err != nil {
		t.Fatal(err)
	}
	var out []*catalog.Model
	for _, m := range bundled {
		if m.DynamicPrice {
			out = append(out, m)
		}
	}
	sort.Slice(out, func(i, j int) bool { return out[i].ID < out[j].ID })
	if r := bundled[routerModel]; r == nil || !r.DynamicPrice {
		t.Fatalf("setup: the bundled OpenRouter snapshot's %d models with no fixed price do not include %s", len(out), routerModel)
	}
	return out
}

// withRouters lists routers in the test gateway's managed catalogue, next to
// testCatalogModels, and rebuilds the model database from it as a catalogue
// refresh does.
func withRouters(routers []*catalog.Model) func(*testing.T, *managedTestGateway) {
	return func(t *testing.T, g *managedTestGateway) {
		t.Helper()
		all := make(map[string]*catalog.Model, len(testCatalogModels)+len(routers))
		for id, m := range testCatalogModels {
			all[id] = m
		}
		for _, m := range routers {
			all[m.ID] = m
		}
		g.srv.handlers.catalog.Put(&catalog.Snapshot{ProviderID: "openrouter", Source: "bundled",
			CatalogSource: catalog.SourceOpenRouter, FetchedAt: time.Now(), Models: all})
		g.srv.RefreshModelDB()
		for _, m := range routers {
			if !g.srv.handlers.catalogHas("openrouter", m.ID) {
				t.Fatalf("setup: the managed catalogue does not list %s", m.ID)
			}
		}
	}
}

// TestManagedModelWithoutAFixedPriceIsRefused: on chat and on /v1/messages,
// every bundled router by its id and behind the "openrouter/" prefix, and on
// chat locked to the Future AGI provider, streamed and not, is refused with
// 403 not available, with no upstream call and the credit untouched.
// openrouter/auto is refused so whichever way its stream would end (the
// upstream reports usage, sends none, fails mid-stream, the client leaves, a
// guardrail withholds the answer).
func TestManagedModelWithoutAFixedPriceIsRefused(t *testing.T) {
	routers := bundledRouters(t)
	lists := withRouters(routers)
	ends := append([]struct {
		name     string
		opts     managedGatewayOptions
		upstream upstreamStream
		leave    bool
	}{{name: "the upstream reports usage", upstream: textThenUsage}}, cutStreamEnds...)
	for _, end := range ends {
		t.Run(end.name, func(t *testing.T) {
			for _, surface := range managedSurfaces {
				for _, model := range []string{routerModel, "openrouter/" + routerModel} {
					// A refused request streams nothing, so the client
					// never gets text to leave after.
					refusedRouterAsNotAvailable(t, fmt.Sprintf("%s %s", surface, model), model,
						runManagedOn(t, lists, surface, model, true, end.opts, end.upstream, false, nil))
				}
			}
			refusedRouterAsNotAvailable(t, "chat locked to openrouter, "+routerModel, "openrouter",
				runManagedOn(t, lists, "/v1/chat/completions", routerModel, true, end.opts, end.upstream, false, managedLock))
		})
	}
	for _, m := range routers {
		for _, stream := range []bool{true, false} {
			for _, surface := range managedSurfaces {
				for _, model := range []string{m.ID, "openrouter/" + m.ID} {
					refusedRouterAsNotAvailable(t, fmt.Sprintf("%s stream=%t %s", surface, stream, model), model,
						runManagedOn(t, lists, surface, model, stream, managedGatewayOptions{}, nil, false, nil))
				}
			}
			refusedRouterAsNotAvailable(t, fmt.Sprintf("chat locked to openrouter, stream=%t, %s", stream, m.ID), "openrouter",
				runManagedOn(t, lists, "/v1/chat/completions", m.ID, stream, managedGatewayOptions{}, nil, false, managedLock))
		}
	}
}

// TestManagedModelWithoutAFixedPriceOnTheOrgsOwnKeyIsByok: with the org's own
// key under the Future AGI provider's id, openrouter/auto, by its id and
// behind the "openrouter/" prefix, goes out as openrouter/auto on the org's
// key as the org's own call, on chat and on /v1/messages: billed byok, the
// credit untouched, no spend.
func TestManagedModelWithoutAFixedPriceOnTheOrgsOwnKeyIsByok(t *testing.T) {
	lists := withRouters(bundledRouters(t))
	for _, model := range []string{routerModel, "openrouter/" + routerModel} {
		servedOnTheOrgsOwnKeyOn(t, lists, model, routerModel)
	}
}

// TestModelListOffersNoModelWithoutAFixedPrice: /v1/models for a key of an
// org with Future AGI models on lists the catalogue's fixed-price models and
// none of the bundled routers, which one Future AGI key does not serve. GET
// /v1/models/{model} reads the same list.
func TestModelListOffersNoModelWithoutAFixedPrice(t *testing.T) {
	routers := bundledRouters(t)
	g := newManagedTestGateway(t, managedOptIn())
	withRouters(routers)(t, g)
	ts := httptest.NewServer(g.srv.httpServer.Handler)
	defer ts.Close()
	req, err := http.NewRequest(http.MethodGet, ts.URL+"/v1/models", nil)
	if err != nil {
		t.Fatal(err)
	}
	req.Header.Set("Authorization", "Bearer "+managedOrgKey)
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	var list models.EnrichedModelListResponse
	if err := json.NewDecoder(resp.Body).Decode(&list); err != nil || resp.StatusCode != http.StatusOK {
		t.Fatalf("/v1/models: status %d, %v", resp.StatusCode, err)
	}
	listed := map[string]bool{}
	for _, m := range list.Data {
		listed[m.ID] = true
	}
	if !listed["anthropic/claude-sonnet-5.5"] {
		t.Errorf("/v1/models does not list anthropic/claude-sonnet-5.5, a model the catalogue prices: %v", listed)
	}
	for _, m := range routers {
		if listed[m.ID] {
			t.Errorf("/v1/models lists %s, which one Future AGI key does not serve", m.ID)
		}
	}
}
