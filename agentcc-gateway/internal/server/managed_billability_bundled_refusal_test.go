package server

import (
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"sort"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/catalog"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/routerbilling"
)

// installBundledCatalogue loads the bundled OpenRouter listings with their
// bundled endpoints once and installs the same listings on each test gateway.
func installBundledCatalogue(t *testing.T) (map[string]*catalog.Model, func(*testing.T, *managedTestGateway)) {
	t.Helper()
	all, fetched, err := catalog.BundledOpenRouter()
	if err != nil {
		t.Fatal(err)
	}
	eps, err := catalog.BundledEndpoints()
	if err != nil {
		t.Fatal(err)
	}
	for id, m := range all {
		m.Endpoints = eps.ByModel[id]
	}
	return all, func(t *testing.T, g *managedTestGateway) {
		t.Helper()
		g.srv.handlers.catalog.Put(&catalog.Snapshot{ProviderID: "openrouter", CatalogSource: catalog.SourceOpenRouter,
			Source: catalog.OriginBundled, FetchedAt: fetched, Models: all})
		g.srv.RefreshModelDB()
	}
}

// Every bundled listing the one-key catalogue does not offer is refused before
// any upstream call, on both APIs, streamed and not, with the organization's
// credit untouched. Together with TestManagedBillabilityBundledSweep this
// partitions the whole bundled catalogue: each listing is either served and
// settled there or refused here, so none is left unaccounted for.
func TestManagedBillabilityBundledRefusalSweep(t *testing.T) {
	all, install := installBundledCatalogue(t)
	g := newManagedTestGateway(t, managedOptIn())
	install(t, g)
	advertised := map[string]bool{}
	for _, m := range g.srv.handlers.managedModelObjects() {
		if advertised[m.ID] {
			t.Fatalf("advertised twice: %s", m.ID)
		}
		advertised[m.ID] = true
	}
	var refused []string
	for id := range all {
		if !advertised[id] {
			refused = append(refused, id)
		}
	}
	sort.Strings(refused)
	if len(advertised)+len(refused) != len(all) || len(refused) == 0 {
		t.Fatalf("partition: listings=%d advertised=%d refused=%d", len(all), len(advertised), len(refused))
	}
	// A listing Future AGI credits could price is left out only because its
	// expiration date has come: it alone is refused as expired, naming that
	// date. Every other listing left out is refused as not available.
	cat, now := g.srv.handlers.catalog.Load(), g.srv.handlers.catalog.Now()
	expired := map[string]string{}
	for _, id := range refused {
		m, _ := cat.Get("openrouter", id)
		if cat.ServesManaged("openrouter", id) && m.State != catalog.StateBlocked && !m.Servable(now) {
			expired[id] = m.ExpirationDate
		}
	}

	t.Run("not-offered", func(t *testing.T) {
		ts := httptest.NewServer(g.srv.httpServer.Handler)
		defer ts.Close()
		req, _ := http.NewRequest(http.MethodGet, ts.URL+"/v1/models", nil)
		req.Header.Set("Authorization", "Bearer "+managedOrgKey)
		resp, err := http.DefaultClient.Do(req)
		if err != nil {
			t.Fatal(err)
		}
		defer resp.Body.Close()
		if resp.StatusCode != http.StatusOK {
			t.Fatalf("/v1/models status %d", resp.StatusCode)
		}
		var list models.EnrichedModelListResponse
		if err := json.NewDecoder(resp.Body).Decode(&list); err != nil {
			t.Fatal(err)
		}
		listed := map[string]bool{}
		for _, m := range list.Data {
			listed[m.ID] = true
		}
		for id := range advertised {
			if !listed[id] {
				t.Errorf("/v1/models leaves out the served %s", id)
			}
		}
		for _, id := range refused {
			if listed[id] {
				t.Errorf("/v1/models offers %s", id)
			}
		}
		// Every canonical group with a served listing retains an access offer.
		// The row itself must name a callable ID (including base-less :free
		// offers), not merely a synthetic canonical grouping key.
		g.ledger.SetMicros("org-m", 1_000_000_000)
		offered := map[string]bool{}
		for _, r := range buildCatalogAccess(cat, managedOptIn(), 5, time.Now()).Data {
			if r.Managed || r.ManagedAvailableOnOptIn {
				if !advertised[r.ID] {
					t.Errorf("access advertises noncallable ID %s", r.ID)
				}
				for _, surface := range managedSurfaces {
					status, _, _ := sendManaged(t, g, surface, r.ID, false, false, nil)
					if status != http.StatusOK {
						t.Errorf("access ID %s on %s: status=%d", r.ID, surface, status)
					}
				}
				offered[catalog.Canonical("openrouter", r.ID)] = true
			}
		}
		canonical, offeredRows := 0, 0
		for _, cm := range cat.CanonicalModels() {
			var served []string
			for _, l := range cm.Listings {
				if advertised[l.Model.ID] {
					served = append(served, l.Model.ID)
				}
			}
			if offered[cm.ID] != (len(served) > 0) {
				t.Errorf("catalogue access %s offered on credits=%t, served listings %v", cm.ID, offered[cm.ID], served)
			}
			canonical++
			if offered[cm.ID] {
				offeredRows++
			}
		}
		if offeredRows == 0 || offeredRows == canonical {
			t.Errorf("catalogue access offers %d of %d canonical models; want some offered and some not", offeredRows, canonical)
		}
		t.Logf("canonical models=%d offered on credits=%d", canonical, offeredRows)
	})

	for _, id := range refused {
		for _, surface := range managedSurfaces {
			for _, stream := range []bool{false, true} {
				t.Run(fmt.Sprintf("%s/%s/%t", id, surface, stream), func(t *testing.T) {
					r := runManagedOn(t, install, surface, id, stream, managedGatewayOptions{}, nil, false, nil)
					// Either way nothing is dispatched or held.
					if date, ok := expired[id]; ok {
						if r.status != http.StatusGone || !strings.Contains(r.message, "expired on "+date) {
							t.Errorf("status %d, message %q; want 410 expired on %s", r.status, r.message, date)
						}
					} else if routerbilling.FamilyMember(id) {
						if r.status != http.StatusForbidden || r.message != catalog.RouterCreditsUnavailable {
							t.Errorf("router status %d, message %q; want value-free 403", r.status, r.message)
						}
					} else if r.status != http.StatusForbidden || !strings.Contains(r.message, `"`+id+`" is not available`) {
						t.Errorf("status %d, message %q; want 403 not available", r.status, r.message)
					}
					if r.calls != 0 {
						t.Errorf("%d upstream calls (last %q), want none", r.calls, r.upstreamSent)
					}
					if r.balance != "1.000000" || r.spent != 0 || r.held != 0 || len(r.recs) != 0 {
						t.Errorf("balance %s, spent %d, held %d, records %v; want the credit untouched", r.balance, r.spent, r.held, r.recs)
					}
					t.Logf("refused status=%d", r.status)
				})
			}
		}
	}
	t.Logf("bundled listings=%d one-key-advertised=%d refused=%d expired=%d", len(all), len(advertised), len(refused), len(expired))
}
