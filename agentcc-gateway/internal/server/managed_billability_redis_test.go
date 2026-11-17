package server

import (
	"context"
	"fmt"
	"net/http"
	"strconv"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/billing"
	creditsplugin "github.com/futureagi/agentcc-gateway/internal/plugins/credits"
	"github.com/futureagi/agentcc-gateway/internal/redisstate"
)

// Each case uses real HTTP and an isolated prefix on the explicitly selected
// test Redis. The production wallet and settlement drain are not involved.
// Every case, refused, BYOK, cut or served, reaches the same checks: what the
// client saw, what the account holds, and every record on the org's outbox.
func TestManagedBillabilityRedisSettlement(t *testing.T) {
	for _, surface := range managedSurfaces {
		for _, tc := range []struct {
			id            string
			spent         int64
			refused, byok bool
		}{
			{"acme/token", 48, false, false}, {"acme/request", 42000, false, false},
			{"acme/mixed-endpoints", 48, false, false}, {"acme/listed:free", 0, false, false},
			{"acme/unknown-zero", 0, true, false}, {"acme/dynamic-endpoint", 0, true, false},
			{"acme/unknown-zero", 0, false, true},
		} {
			for _, end := range cutStreamEnds {
				t.Run(fmt.Sprintf("%s/%s/%s/byok=%t", surface, tc.id, end.name, tc.byok), func(t *testing.T) {
					prefix := fmt.Sprintf("test-billability-%d:", time.Now().UnixNano())
					client := r7AgentRedis(t, prefix)
					org := managedOptIn()
					if tc.byok {
						org = orgKeyUnderFutureAGIID()
					}
					g := newManagedTestGatewayWith(t, org, end.opts)
					installBillabilityModels(billabilityModels(t))(t, g)
					ledger := creditsplugin.NewOrgLedger(redisstate.NewManagedCreditStore(client, prefix))
					g.ledger = ledger
					g.credits.SetOrgLedger(ledger, true)
					g.srv.EnableManaged(ManagedSettings{Catalog: g.srv.handlers.catalog, Ledger: ledger, MarkupPercent: 5, RequireCredits: true})
					if _, err := ledger.Grant(context.Background(), "org-m", billing.GrantPush{GrantedTotalMicros: 1_000_000, Version: 1}); err != nil {
						t.Fatal(err)
					}
					up := end.upstream
					g.managed.streamWith.Store(&up)
					status, _, headers := sendManaged(t, g, surface, tc.id, true, end.leave && !tc.refused, nil)
					spent, held := settledAccount(t, g)
					records, err := client.Redis().XRange(context.Background(), prefix+"mc:{org-m}:outbox", "-", "+").Result()
					if err != nil {
						t.Fatal(err)
					}

					// What the case should leave. A refusal is never sent and
					// settles nothing. A BYOK call is served on the org's own
					// key and settles nothing on Future AGI credits. A managed
					// call leaves one record: a void when it is free, else a
					// spend of its catalogue estimate. A paid managed stream
					// the upstream drops before its usage is charged at its own
					// hold, catalog_max; one its client leaves keeps the
					// estimate of what was received.
					cut := end.name == "the upstream fails mid-stream" && tc.spent > 0 && !tc.byok
					wantStatus, wantCalls, wantRecords := http.StatusOK, 1, 1
					wantMode, wantAuth := "managed", "Bearer fagi-openrouter-key"
					wantKind, wantSource := "spend", "catalog"
					switch {
					case tc.refused:
						wantStatus, wantCalls, wantRecords, wantMode, wantAuth = http.StatusForbidden, 0, 0, "", ""
					case tc.byok:
						wantRecords, wantMode, wantAuth = 0, "byok", "Bearer org-or-key"
					case cut:
						wantSource = "catalog_max"
					case tc.spent == 0:
						wantKind = "void"
					}

					// What the client saw and the upstream received.
					if status != wantStatus || g.managed.calls() != wantCalls {
						t.Errorf("status=%d calls=%d; want %d,%d", status, g.managed.calls(), wantStatus, wantCalls)
					}
					if wantMode != "" && headers.Get("x-agentcc-billing-mode") != wantMode {
						t.Errorf("mode=%q; want %q", headers.Get("x-agentcc-billing-mode"), wantMode)
					}
					if wantAuth != "" && (len(g.managed.auths) == 0 || g.managed.auths[0] != wantAuth) {
						t.Errorf("credential=%v; want %s", g.managed.auths, wantAuth)
					}

					// The account and every record on the org's outbox.
					if held != 0 {
						t.Errorf("held=%d; want 0 once the request settled", held)
					}
					if len(records) != wantRecords {
						t.Fatalf("records=%v; want %d", records, wantRecords)
					}
					wantSpent, billed := tc.spent, int64(0)
					for _, rec := range records {
						r := rec.Values
						micros := func(field string) int64 {
							n, err := strconv.ParseInt(fmt.Sprint(r[field]), 10, 64)
							if err != nil {
								t.Fatalf("%s=%v in settlement %v: %v", field, r[field], r, err)
							}
							return n
						}
						hold, upstream, markup, recBilled := micros("hold_micros"), micros("upstream_micros"), micros("markup_micros"), micros("billed_micros")
						if cut {
							// Charged at its own hold; upstream is the hold less
							// the 5% markup it was reserved with.
							wantSpent = hold
							if upstream != hold*10000/10500 {
								t.Errorf("upstream_micros=%d; want %d, the hold %d less the markup", upstream, hold*10000/10500, hold)
							}
						}
						if r["kind"] != wantKind || r["usage_estimated"] != "1" || recBilled != wantSpent || r["cost_source"] != wantSource {
							t.Errorf("settlement=%v; want kind=%s billed=%d cost_source=%s, estimated", r, wantKind, wantSpent, wantSource)
						}
						if upstream+markup != recBilled || r["org_id"] != "org-m" || r["catalog_snapshot_id"] == "" {
							t.Errorf("settlement=%v; want upstream+markup=billed, org-m and the admitted catalogue revision", r)
						}
						billed += recBilled
					}
					if spent != wantSpent || spent != billed {
						t.Errorf("spent=%d; want %d, and the %d its records bill", spent, wantSpent, billed)
					}
				})
			}
		}
	}
}
