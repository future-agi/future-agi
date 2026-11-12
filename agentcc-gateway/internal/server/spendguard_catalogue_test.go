package server

import (
	"encoding/json"
	"fmt"
	"math/big"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/billing"
	"github.com/futureagi/agentcc-gateway/internal/billing/inclusive"
	"github.com/futureagi/agentcc-gateway/internal/models"
	creditsplugin "github.com/futureagi/agentcc-gateway/internal/plugins/credits"
	"github.com/futureagi/agentcc-gateway/internal/plugins/spendguard"
	"github.com/futureagi/agentcc-gateway/internal/routing/eligibility"
	"github.com/futureagi/agentcc-gateway/internal/routing/reqopts"
)

func catalogueSpendEstimator(g *managedTestGateway) *spendguard.Estimator {
	h := g.srv.handlers
	return &spendguard.Estimator{
		Settings: creditsplugin.DefaultSettings(), Prices: h.Deps().Prices,
		OrgMaxTokens: h.OrgDefaultMaxTokensEstimate,
		MaxOutput: func(providerID, model string) int {
			if db := h.ModelDB(); db != nil {
				if info, ok := db.Get(providerID + "/" + model); ok {
					return info.MaxOutputTokens
				}
				if info, ok := db.Get(model); ok {
					return info.MaxOutputTokens
				}
			}
			return 0
		},
	}
}

func catalogueSpendRequest(model string, images, out, n int) *models.ChatCompletionRequest {
	content := json.RawMessage(`"synthetic"`)
	if images > 0 {
		parts := make([]map[string]any, images)
		for i := range parts {
			parts[i] = map[string]any{"type": "image_url", "image_url": map[string]string{"url": "http://127.0.0.1/never-fetched"}}
		}
		content, _ = json.Marshal(parts)
	}
	return &models.ChatCompletionRequest{Model: model, N: &n, MaxTokens: &out,
		Messages: []models.Message{{Role: "user", Content: content}}}
}

// Use the actual credits reserve, not a second call to the guard's helper as
// the oracle. Each side receives the same admitted hop, with the catalogue's
// pinned card and endpoint set. The estimator must not mutate that hop.
func assertCatalogueSpendParity(t *testing.T, g *managedTestGateway, req *models.ChatCompletionRequest, verdicts ...eligibility.Verdict) int64 {
	t.Helper()
	card, ok := g.srv.handlers.catalog.Load().PriceCard("openrouter", req.Model, "")
	if !ok || !card.Billable() {
		t.Fatalf("no billable card for %s", req.Model)
	}
	rc := models.AcquireRequestContext()
	defer rc.Release()
	rc.RequestID = t.Name()
	rc.Provider, rc.Model, rc.Request = "openrouter", req.Model, req
	rc.Metadata["org_id"], rc.Metadata["billing_mode"] = "org-m", models.BillingModeManaged
	parsed, apiErr := reqopts.Parse(req)
	if apiErr != nil {
		t.Fatal(apiErr)
	}
	rc.Routing.Provider = parsed.Options.Provider
	v := g.srv.handlers.judge(rc, managedOptIn(), rc.Provider, req.Model, req.Model, false)
	if len(verdicts) > 0 {
		v = verdicts[0]
		v.Price = &card
	}
	if !v.OK {
		t.Fatalf("admitted hop ineligible: %+v", v)
	}
	g.srv.handlers.applyVerdict(rc, &v)
	before, _ := json.Marshal(rc.Billing)
	reqBefore, _ := json.Marshal(req)
	got := catalogueSpendEstimator(g).Estimate(rc)
	after, _ := json.Marshal(rc.Billing)
	reqAfter, _ := json.Marshal(req)
	if string(before) != string(after) || string(reqBefore) != string(reqAfter) || rc.Metadata["billing_alert"] != "" {
		t.Fatal("guard estimate mutated the admitted hop")
	}
	if result := g.credits.ProcessRequest(t.Context(), rc); result.Error != nil {
		t.Fatalf("credits reserve: %v", result.Error)
	}
	want := rc.Billing.HoldMicros
	if got != want {
		t.Errorf("%s: guard estimate=%d credits hold=%d", req.Model, got, want)
	}
	if card.Inclusive != nil {
		bound, err := inclusive.Bound(req, card.Inclusive)
		if err != nil || bound != want {
			t.Fatalf("credits hold=%d independent inclusive bound=%d err=%v", want, bound, err)
		}
	} else {
		// Keep the existing scalar calculation byte-for-byte, including rounding.
		model, _ := g.srv.handlers.catalog.Load().Get(rc.Provider, req.Model)
		defaultOut := billing.DefaultMaxOut(model.MaxOutputTokens, 4096)
		old := billing.EstimateWorstCaseMicros(req, card, 500, defaultOut)
		if len(rc.Billing.EndpointSet) > 0 {
			_, old, _ = billing.HighestCard(rc.Billing.EndpointSet, req, 500, defaultOut)
		}
		if got != old {
			t.Errorf("token estimate changed: got=%d old=%d", got, old)
		}
	}
	return got
}

func TestSpendGuardEstimateMatchesCreditsHoldCatalogue(t *testing.T) {
	for _, enabled := range []bool{false, true} {
		t.Run(fmt.Sprintf("cat2=%t", enabled), func(t *testing.T) {
			g, _, _ := cat2OperatorGateway(t, enabled)
			rows, inclusiveRows := 0, 0
			for _, row := range g.srv.handlers.managedModelObjects() {
				card, ok := g.srv.handlers.catalog.Load().PriceCard("openrouter", row.ID, "")
				if !ok {
					t.Fatalf("listed row has no card: %s", row.ID)
				}
				rows++
				if card.Inclusive != nil {
					inclusiveRows++
				}
				t.Run(row.ID, func(t *testing.T) {
					assertCatalogueSpendParity(t, g, catalogueSpendRequest(row.ID, 0, 64, 1))
					if card.Inclusive != nil {
						t.Run("multiple-generations", func(t *testing.T) {
							images := 2
							if card.Inclusive.Generation != "" {
								images = 0
							}
							assertCatalogueSpendParity(t, g, catalogueSpendRequest(row.ID, images, 64, 2))
						})
					}
				})
			}
			// Pinned to the bundled catalogue at 9bfa4d2b: managed listings
			// follow managedOffer (models_list.go:312). RouterCreditRefusal
			// (catalog/router_credit_refusal.go:20-35) excludes unqualified
			// routers even with CAT-2 on; qualifyRouters clears their cards
			// (catalog/router_inclusive.go:14-19). Do not restore those rows.
			// cat2OperatorGateway fixes the catalog clock, so expiring listings
			// count as the bundle has them, whatever day the test runs.
			wantRows, wantInclusive := 415, 0
			if enabled {
				wantRows, wantInclusive = 444, 29
			}
			if rows != wantRows || inclusiveRows != wantInclusive {
				t.Fatalf("catalogue coverage: rows=%d inclusive=%d want=%d/%d", rows, inclusiveRows, wantRows, wantInclusive)
			}
			if !t.Failed() {
				t.Logf("PARITY cat2=%t listings=%d inclusive=%d exact_micros=true", enabled, rows, inclusiveRows)
			}
		})
	}
}

func TestSpendGuardCatalogueMaximizers(t *testing.T) {
	t.Run("stock-astra", func(t *testing.T) {
		g, _, _ := cat2OperatorGateway(t, false)
		seedSpendCatalogue(t, g, false)
		req := catalogueSpendRequest("openai/gpt-6-astra", 0, 128000, 1)
		req.Extra = map[string]json.RawMessage{"provider": json.RawMessage(`{"only":["openai"]}`)}
		req.Messages[0].Content, _ = json.Marshal(strings.Repeat("x", (1_050_000-7)*4))
		if got := assertCatalogueSpendParity(t, g, req); got != 106_470_000 {
			t.Fatalf("stock maximizer=%d want=106470000", got)
		}
		t.Log("MAXIMIZER stock-astra=106470000 micros")
	})
	t.Run("cat2-router", func(t *testing.T) {
		g, _, _ := cat2OperatorGateway(t, true)
		req := catalogueSpendRequest("openrouter/auto", 64, 65536, 1)
		before := g.account(t, "org-m")
		body, err := json.Marshal(req)
		if err != nil {
			t.Fatal(err)
		}
		w := g.chat(t, string(body), nil)
		if w.Code != 403 || errorCode(t, w) != "managed_router_unbounded" || g.managed.calls() != 0 {
			t.Fatalf("router must not reach the estimator: %d %s", w.Code, w.Body)
		}
		if after := g.account(t, "org-m"); after != before || len(g.outbox("org-m")) != 0 {
			t.Fatalf("router changed ledger: before=%+v after=%+v", before, after)
		}
		card, _ := g.srv.handlers.catalog.Load().PriceCard("openrouter", req.Model, "")
		if card.Inclusive != nil {
			t.Fatal("router retained an unqualified finite-universe claim")
		}
	})
	t.Run("cat2-nonrouter-synthetic-envelope", func(t *testing.T) {
		g, _, _ := cat2OperatorGateway(t, true)
		unionSpendFixture(t, g)
		if got := assertCatalogueSpendParity(t, g, catalogueSpendRequest(unionSpendModel, 64, 65536, 1)); got != unionSpendBound {
			t.Fatalf("non-router exact bound and credits hold=%d want=%d", got, unionSpendBound)
		}
	})
}

// Independent rational upper bound over every routed candidate, including
// dimensions the token heuristic does not count. No production Bound reuse.
func assertRouterUniverseCeiling(t *testing.T, q *models.InclusivePrice) {
	t.Helper()
	var universe []map[string]string
	if q == nil || json.Unmarshal([]byte(q.RouterUniverse), &universe) != nil || len(universe) == 0 {
		t.Fatal("missing router universe")
	}
	best := new(big.Rat)
	for _, row := range universe {
		rate := func(keys ...string) *big.Rat {
			v := new(big.Rat)
			for _, k := range keys {
				if row[k] == "" {
					continue
				}
				r, ok := new(big.Rat).SetString(row[k])
				if !ok || r.Sign() < 0 {
					t.Fatalf("invalid universe rate %s", k)
				}
				if r.Cmp(v) > 0 {
					v = r
				}
			}
			return v
		}
		v := new(big.Rat).Mul(rate("Prompt", "CacheRead", "CacheWrite"), big.NewRat(q.ContextLimit, 1))
		v.Add(v, new(big.Rat).Mul(rate("Completion", "Reasoning"), big.NewRat(65536, 1)))
		v.Add(v, new(big.Rat).Mul(rate("Image"), big.NewRat(64, 1)))
		v.Add(v, rate("Request", "Search"))
		if v.Cmp(best) > 0 {
			best.Set(v)
		}
	}
	best.Mul(best, big.NewRat(1_050_000, 1))
	if best.Cmp(big.NewRat(357_000_000, 1)) >= 0 {
		t.Fatalf("universe bound=%s exceeds $357", best.RatString())
	}
	if best.Cmp(big.NewRat(356_298_180, 1)) != 0 {
		t.Fatalf("universe maximizer changed: %s", best.RatString())
	}
	t.Logf("UNIVERSE candidates=%d upper_bound_micros=%s", len(universe), best.RatString())
}
