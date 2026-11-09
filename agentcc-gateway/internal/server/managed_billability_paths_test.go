package server

import (
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/catalog"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

func sendBillabilityBody(t *testing.T, g *managedTestGateway, surface, body string) int {
	t.Helper()
	ts := httptest.NewServer(g.srv.httpServer.Handler)
	defer ts.Close()
	req, _ := http.NewRequest(http.MethodPost, ts.URL+surface, strings.NewReader(body))
	req.Header.Set("Authorization", "Bearer "+managedOrgKey)
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("anthropic-version", "2023-06-01")
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	_, _ = io.Copy(io.Discard, resp.Body)
	return resp.StatusCode
}

func TestManagedBillabilityAutoAliasesAndFallbacks(t *testing.T) {
	all := billabilityModels(t)
	for _, id := range refusedBillabilityIDs {
		t.Run(id, func(t *testing.T) {
			for _, path := range []string{"alias", "auto", "fallback"} {
				for _, surface := range managedSurfaces {
					g := newManagedTestGateway(t, managedOptIn())
					installBillabilityModels(all)(t, g)
					g.ledger.SetMicros("org-m", 1_000_000)
					model := id
					extra := ""
					switch path {
					case "alias":
						_, model, _ = strings.Cut(id, "/")
					case "auto":
						cfg := managedOptIn()
						cfg.Routing = &tenant.RoutingConfig{Auto: &tenant.AutoRoutingConfig{Candidates: []string{id}}}
						g.tenants.Set("org-m", cfg)
						model = "auto"
					case "fallback":
						model = "acme/token"
						extra = fmt.Sprintf(`,"models":[%q]`, id)
						g.managed.failFor.Store(model, 500)
					}
					body := fmt.Sprintf(`{"model":%q,"stream":true,"max_tokens":64,"messages":[{"role":"user","content":"hi"}]%s}`, model, extra)
					status := sendBillabilityBody(t, g, surface, body)
					spent, held := settledAccount(t, g)
					if status == http.StatusOK || spent != 0 || held != 0 {
						t.Errorf("%s %s: status=%d spent=%d held=%d", surface, path, status, spent, held)
					}
					wantCalls := 0
					if path == "fallback" && surface == "/v1/chat/completions" {
						wantCalls = 1
					}
					if g.managed.calls() != wantCalls {
						t.Errorf("%s %s: calls=%d, want=%d", surface, path, g.managed.calls(), wantCalls)
					}
					for _, sent := range g.managed.models {
						if sent != "acme/token" {
							t.Errorf("%s %s sent refused model %s", surface, path, sent)
						}
					}
					recs := g.outbox("org-m")
					if len(recs) != wantCalls {
						t.Errorf("%s %s: records=%v, want %d", surface, path, recs, wantCalls)
					}
					for _, rec := range recs {
						if rec["kind"] != "void" {
							t.Errorf("failed call spends: %v", rec)
						}
					}
				}
			}
		})
	}
}

func TestManagedBillabilityEndpointPolicyIsNotRelaxed(t *testing.T) {
	for _, only := range []string{"mock", "priced"} {
		g := newManagedTestGateway(t, managedOptIn())
		installBillabilityModels(billabilityModels(t))(t, g)
		g.ledger.SetMicros("org-m", 1_000_000)
		body := fmt.Sprintf(`{"model":"acme/mixed-endpoints","messages":[{"role":"user","content":"hi"}],"provider":{"only":[%q],"allow_fallbacks":false}}`, only)
		status := sendBillabilityBody(t, g, managedSurfaces[0], body)
		spent, held := settledAccount(t, g)
		if only == "mock" {
			if status < 400 || g.managed.calls() != 0 || spent != 0 || held != 0 || len(g.outbox("org-m")) != 0 {
				t.Fatalf("unpriced policy-only: status=%d calls=%d spent=%d held=%d", status, g.managed.calls(), spent, held)
			}
		} else if status != 200 || g.managed.calls() != 1 || spent != 7350 || held != 0 {
			t.Fatalf("priced policy-only: status=%d calls=%d spent=%d held=%d", status, g.managed.calls(), spent, held)
		}
	}
}

// Every advertised bundled listing is dispatched to a local mock without a
// usage report. This sweep checks actual settlement, not just matching filters.
func TestManagedBillabilityBundledSweep(t *testing.T) {
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
	g := newManagedTestGateway(t, managedOptIn())
	g.srv.handlers.catalog.Put(&catalog.Snapshot{ProviderID: "openrouter", CatalogSource: catalog.SourceOpenRouter, Source: catalog.OriginBundled, FetchedAt: fetched, Models: all})
	g.srv.RefreshModelDB()
	g.ledger.SetMicros("org-m", 1_000_000_000)
	up := upstreamStream(textThenEnd)
	g.managed.streamWith.Store(&up)
	advertised := g.srv.handlers.managedModelObjects()
	if len(advertised) < 100 {
		t.Fatalf("sweep unexpectedly small: %d", len(advertised))
	}
	for _, m := range advertised {
		t.Run(m.ID, func(t *testing.T) {
			beforeCalls := g.managed.calls()
			before := g.account(t, "org-m")
			beforeRecords := len(g.outbox("org-m"))
			status := sendBillabilityBody(t, g, managedSurfaces[0], fmt.Sprintf(`{"model":%q,"stream":true,"max_tokens":64,"messages":[{"role":"user","content":"hi"}]}`, m.ID))
			_, held := settledAccount(t, g)
			after := g.account(t, "org-m")
			recs := g.outbox("org-m")
			if status != 200 || g.managed.calls() != beforeCalls+1 || held != 0 || len(recs) != beforeRecords+1 {
				t.Fatalf("status=%d calls delta=%d held=%d records delta=%d", status, g.managed.calls()-beforeCalls, held, len(recs)-beforeRecords)
			}
			rec := recs[len(recs)-1]
			entry, _ := g.srv.handlers.catalog.Load().Get("openrouter", m.ID)
			if rec["usage_estimated"] != "1" || rec["cost_source"] == "unpriced" {
				t.Errorf("unpriced advertised call: %v", rec)
			}
			if entry.ExplicitlyFree(catalog.SourceOpenRouter) && (after.SpentMicros != before.SpentMicros || rec["kind"] != "void") {
				t.Errorf("free offering charged: %v", rec)
			}
		})
	}
	t.Logf("bundled listings=%d one-key-advertised=%d", len(all), len(advertised))
}
