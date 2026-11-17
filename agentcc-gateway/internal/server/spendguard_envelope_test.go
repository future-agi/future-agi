package server

import (
	"encoding/json"
	"fmt"
	"sort"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/billing"
	"github.com/futureagi/agentcc-gateway/internal/billing/inclusive"
	"github.com/futureagi/agentcc-gateway/internal/catalog"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/routing/eligibility"
	"github.com/futureagi/agentcc-gateway/internal/routing/reqopts"
)

func seedSpendCatalogue(t *testing.T, g *managedTestGateway, enabled bool) time.Time {
	t.Helper()
	at := time.Date(2026, 11, 3, 0, 0, 0, 0, time.UTC)
	cfg := config.DefaultConfig()
	if enabled {
		cfg.Managed.CatalogueFamilies = []string{"cat2"}
	}
	sources := []catalog.Source{{ProviderID: "openrouter", Type: catalog.SourceOpenRouter}}
	store := g.srv.handlers.catalog
	opts := catalog.OptionsFromConfig(cfg, sources)
	opts.SnapshotDir, opts.Now = "", func() time.Time { return at }
	if err := store.Configure(opts); err != nil {
		t.Fatal(err)
	}
	catalog.Seed(store, sources)
	g.srv.RefreshModelDB()
	return at
}

// Reconstruct the admitted launch envelope's 830 stock / 859 CAT-2 rows from the
// bundled catalogue, not a copied price fixture: full input context, default
// and maximal output for each token listing, and the maximal admissible shape
// for each inclusive listing. Each token row selects the dearest real eligible
// endpoint set (including caller pins), using the unchanged scalar estimator.
func TestSpendGuardLaunchEnvelopeParity(t *testing.T) {
	for _, enabled := range []bool{false, true} {
		t.Run(fmt.Sprintf("cat2=%t", enabled), func(t *testing.T) {
			g, _, _ := cat2OperatorGateway(t, enabled)
			at := seedSpendCatalogue(t, g, enabled)
			cat := g.srv.handlers.catalog.Load()
			g.credits.SetMaxOutput(catalogueSpendEstimator(g).MaxOutput)
			g.ledger.SetMicros("org-m", 1<<50)
			var mu sync.Mutex
			rows, inclusiveRows := 0, 0
			maxHold := int64(0)
			// A token row's message fills the model's whole context, 4 bytes a
			// token: about 1 GB over a mode's rows for each scenario, from only
			// a few dozen distinct context lengths. Each message is built once
			// per mode and shared. Rows only read it (the parity check fails
			// if the estimate changes the request), and its capacity is
			// trimmed so an append copies.
			var promptMu sync.Mutex
			prompts := map[int]json.RawMessage{}
			fullContext := func(contextLength int) json.RawMessage {
				promptMu.Lock()
				defer promptMu.Unlock()
				p, ok := prompts[contextLength]
				if !ok {
					p, _ = json.Marshal(strings.Repeat("x", max(0, contextLength-7)*4))
					p = p[:len(p):len(p)]
					prompts[contextLength] = p
				}
				return p
			}
			// The real holds are independent. Bound concurrency by go test's
			// -parallel limit rather than serially reparsing every full context.
			t.Run("rows", func(t *testing.T) {
				for _, snapshot := range cat.Snapshots() {
					ids := make([]string, 0, len(snapshot.Models))
					for id := range snapshot.Models {
						ids = append(ids, id)
					}
					sort.Strings(ids)
					for _, id := range ids {
						m := snapshot.Models[id]
						if !cat.ServesManaged(snapshot.ProviderID, id) || !m.Servable(at) {
							continue
						}
						t.Run(id, func(t *testing.T) {
							t.Parallel()
							card, ok := cat.PriceCard(snapshot.ProviderID, id, "")
							if !ok {
								t.Fatalf("served model lacks a card: %s", id)
							}
							for _, scenario := range []string{"default-output", "max-output"} {
								if card.Inclusive != nil && scenario == "default-output" {
									continue
								}
								out := billing.DefaultMaxOut(m.MaxOutputTokens, 4096)
								if scenario == "max-output" && m.MaxOutputTokens > 0 {
									out = m.MaxOutputTokens
								}
								req := catalogueSpendRequest(id, 0, out, 1)
								req.Messages[0].Content = fullContext(m.ContextLength)
								if scenario == "default-output" {
									req.MaxTokens = nil
								}
								if q := card.Inclusive; q != nil {
									out = inclusive.MaxOutputTokens
									if q.OutputLimit > 0 {
										out = min(out, int(q.OutputLimit))
									}
									images := 64
									if q.Generation != "" {
										images = 0
									}
									req = catalogueSpendRequest(id, images, out, 1)
									low, high, best := 1, out, 0
									for low <= high {
										probe := low + (high-low)/2
										req.MaxTokens = &probe
										if _, err := inclusive.Bound(req, q); err == nil {
											best, low = probe, probe+1
										} else {
											high = probe - 1
										}
									}
									if best == 0 {
										t.Fatalf("inclusive listing lacks an admissible envelope: %s", id)
									}
									req.MaxTokens = &best
								}
								in := eligibility.Input{Model: id, ProviderID: snapshot.ProviderID, Managed: true, Aggregator: true,
									Entry: m, Org: managedOptIn(), Request: req, Now: at, Shutdowns: g.srv.handlers.deprecations, Host: "openrouter"}
								v := eligibility.Check(&in)
								if !v.OK {
									continue
								}
								if err := reqopts.CheckManaged(req, &models.RoutingOptions{}, &card); err != nil {
									t.Fatal(err)
								}
								if card.Inclusive == nil {
									v = dearestSpendVerdict(t, g, in, v, card)
								}
								t.Run(scenario, func(t *testing.T) {
									got := assertCatalogueSpendParity(t, g, req, v)
									mu.Lock()
									rows++
									if card.Inclusive != nil {
										inclusiveRows++
									}
									maxHold = max(maxHold, got)
									mu.Unlock()
									if id == "openai/gpt-6-astra" && scenario == "max-output" && got != 106_470_000 {
										t.Fatalf("stock maximizer changed: %d", got)
									}
								})
							}
						})
					}
				}
			})
			// Pinned to the bundled catalogue at 9bfa4d2b and seed time above:
			// 415 token listings have two envelopes; CAT-2 adds 29 inclusive
			// envelopes. ManagedBillable (catalog/billability.go:14-22) and
			// qualifyRouters (catalog/router_inclusive.go:14-19) exclude the
			// former router maximizer. Both profiles now peak at the stock
			// gpt-6-astra hold, also checked per row above and in Maximizers.
			wantRows, wantInclusive, wantMax := 830, 0, int64(106_470_000)
			if enabled {
				wantRows, wantInclusive = 859, 29
			}
			if rows != wantRows || inclusiveRows != wantInclusive || maxHold != wantMax {
				t.Fatalf("envelope rows=%d inclusive=%d max=%d want=%d/%d/%d", rows, inclusiveRows, maxHold, wantRows, wantInclusive, wantMax)
			}
			if !t.Failed() {
				t.Logf("ENVELOPE cat2=%t rows=%d inclusive=%d exact_micros=true maximizer=%d", enabled, rows, inclusiveRows, maxHold)
			}
		})
	}
}

func dearestSpendVerdict(t *testing.T, g *managedTestGateway, in eligibility.Input, first eligibility.Verdict, card models.PriceCard) eligibility.Verdict {
	t.Helper()
	sets := []eligibility.Verdict{first}
	seen := map[string]bool{"": true}
	for _, ep := range in.Entry.Endpoints {
		tag := ep.Tag
		if tag == "" {
			tag = ep.UpstreamProvider
		}
		if seen[tag] {
			continue
		}
		seen[tag] = true
		in.Prefs = &models.ProviderPrefs{Only: []string{tag}}
		if v := eligibility.Check(&in); v.OK {
			sets = append(sets, v)
		}
	}
	rc := models.AcquireRequestContext()
	defer rc.Release()
	rc.Request = in.Request
	best, highest := first, int64(-1)
	for _, v := range sets {
		v.Price = &card
		g.srv.handlers.applyVerdict(rc, &v)
		defaultOut := billing.DefaultMaxOut(in.Entry.MaxOutputTokens, 4096)
		micros := billing.EstimateWorstCaseMicros(in.Request, card, 500, defaultOut)
		if len(rc.Billing.EndpointSet) > 0 {
			var ok bool
			_, micros, ok = billing.HighestCard(rc.Billing.EndpointSet, in.Request, 500, defaultOut)
			if !ok {
				t.Fatalf("eligible endpoint set has no price: %s", in.Model)
			}
		}
		if micros > highest {
			best, highest = v, micros
		}
	}
	return best
}
