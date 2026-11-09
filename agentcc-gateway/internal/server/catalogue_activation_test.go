package server

import (
	"context"
	"fmt"
	"net/http"
	"os"
	"path/filepath"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/catalog"
	"github.com/futureagi/agentcc-gateway/internal/config"
	creditsplugin "github.com/futureagi/agentcc-gateway/internal/plugins/credits"
	"github.com/futureagi/agentcc-gateway/internal/routerbilling"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

func cat2OperatorGateway(t *testing.T, enabled bool) (*managedTestGateway, *r601Owner, []string) {
	t.Helper()
	families := "[]"
	if enabled {
		families = "[cat2]"
	}
	path := filepath.Join(t.TempDir(), "gateway.yaml")
	if err := os.WriteFile(path, []byte("managed:\n  catalogue_families: "+families+"\n"), 0600); err != nil {
		t.Fatal(err)
	}
	cfg, err := config.Load(path)
	if err != nil {
		t.Fatal(err)
	}
	g := newManagedTestGateway(t, managedOptIn())
	g.srv.cfg.Managed.CatalogueFamilies = cfg.Managed.CatalogueFamilies
	if err := g.srv.handlers.catalog.Configure(catalog.OptionsFromConfig(cfg, []catalog.Source{{ProviderID: "openrouter", Type: catalog.SourceOpenRouter}})); err != nil {
		t.Fatal(err)
	}
	all, install := installBundledCatalogue(t)
	install(t, g)
	owner := newR601Owner()
	g.ledger = creditsplugin.NewOrgLedger(owner)
	g.credits.SetOrgLedger(g.ledger, true)
	// EnableManaged wires one ledger into the server and its handlers too. The
	// fallback credit check (managedCreditAvailable) reads the handlers' copy,
	// so leaving the old unfunded ledger there would skip every managed hop.
	g.srv.managed.Ledger = g.ledger
	g.srv.handlers.orgLedger = g.ledger
	g.ledger.SetMicros("org-m", 2000000000)
	ids := append(r601IDs(all), r602Routers...)
	return g, owner, ids
}

func TestCAT2OperatorHTTP(t *testing.T) {
	for _, enabled := range []bool{false, true} {
		for _, surface := range managedSurfaces {
			for _, stream := range []bool{false, true} {
				g, owner, ids := cat2OperatorGateway(t, enabled)
				count := 0
				for _, id := range ids {
					if id == "google/gemini-2.5-flash-image" {
						continue
					}
					count++
					t.Run(fmt.Sprintf("%t/%s/%t/%s", enabled, surface, stream, id), func(t *testing.T) {
						before := g.managed.calls()
						beforeAcct, _ := owner.Account(context.Background(), "org-m")
						beforeRows := len(owner.Outbox("org-m"))
						answer := upstreamStream(func(w http.ResponseWriter, r *http.Request, model string) {
							for _, router := range r602Routers {
								if model == router {
									model = "openai/o1-pro"
								}
							}
							r601Response("0.0000095", stream)(w, r, model)
						})
						g.managed.responseWith.Store(&answer)
						g.managed.streamWith.Store(&answer)
						body := fmt.Sprintf(`{"model":%q,"max_tokens":64,"stream":%t,"messages":[{"role":"user","content":"synthetic"}]}`, id, stream)
						status := sendBillabilityBody(t, g, surface, body)
						if !enabled || routerbilling.FamilyMember(id) {
							acct, _ := owner.Account(context.Background(), "org-m")
							if status != 403 || g.managed.calls() != before || acct.SpentMicros != beforeAcct.SpentMicros || acct.HeldMicros != 0 || len(owner.Outbox("org-m")) != beforeRows {
								t.Fatalf("off status=%d calls=%d acct=%+v", status, g.managed.calls(), acct)
							}
						} else if status != 200 || g.managed.calls() != before+1 {
							t.Fatalf("on status=%d calls=%d before=%d", status, g.managed.calls(), before)
						}
					})
				}
				if count != 34 {
					t.Fatalf("family IDs=%d", count)
				}
			}
		}
	}
}

func TestCAT2OperatorMixedChainRefused(t *testing.T) {
	for _, surface := range managedSurfaces {
		for _, stream := range []bool{false, true} {
			for _, reverse := range []bool{false, true} {
				t.Run(fmt.Sprintf("%s/%t/%t", surface, stream, reverse), func(t *testing.T) {
					g, ledger, _ := cat2OperatorGateway(t, true)
					primary, fallback := "openai/o1-pro", "google/lyria-3-pro-preview"
					if reverse {
						primary, fallback = fallback, primary
					}
					cfg := managedOptIn()
					cfg.Routing = &tenant.RoutingConfig{FallbackEnabled: true, Retry: &tenant.RetryConfig{Enabled: false}, ModelFallbacks: map[string][]string{primary: {fallback}}}
					g.tenants.Set("org-m", cfg)
					answer := upstreamStream(func(w http.ResponseWriter, _ *http.Request, _ string) {
						w.WriteHeader(500)
						fmt.Fprint(w, `{"error":{"message":"synthetic failure"}}`)
					})
					g.managed.responseWith.Store(&answer)
					g.managed.streamWith.Store(&answer)
					body := fmt.Sprintf(`{"model":%q,"max_tokens":64,"stream":%t,"messages":[{"role":"user","content":"synthetic"}]}`, primary, stream)
					status := sendBillabilityBody(t, g, surface, body)
					acct, _ := ledger.Account(context.Background(), "org-m")
					if status != 403 || g.managed.calls() != 0 || acct.HeldMicros != 0 || acct.SpentMicros != 0 || len(ledger.Outbox("org-m")) != 0 {
						t.Fatalf("mixed chain not refused before work: status=%d calls=%d account=%+v", status, g.managed.calls(), acct)
					}
				})
			}
		}
	}
}
