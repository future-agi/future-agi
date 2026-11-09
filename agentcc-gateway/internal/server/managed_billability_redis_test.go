package server

import (
	"context"
	"fmt"
	"net/http"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/billing"
	creditsplugin "github.com/futureagi/agentcc-gateway/internal/plugins/credits"
	"github.com/futureagi/agentcc-gateway/internal/redisstate"
)

// Each case uses real HTTP and an isolated prefix on the explicitly selected
// test Redis. The production wallet and settlement drain are not involved.
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
					if spent != tc.spent || held != 0 {
						t.Fatalf("spent=%d held=%d; want %d,0", spent, held, tc.spent)
					}
					records, err := client.Redis().XRange(context.Background(), prefix+"mc:{org-m}:outbox", "-", "+").Result()
					if err != nil {
						t.Fatal(err)
					}
					if tc.refused {
						if status != http.StatusForbidden || g.managed.calls() != 0 || len(records) != 0 {
							t.Fatalf("refused status=%d calls=%d records=%v", status, g.managed.calls(), records)
						}
						return
					}
					wantMode, wantAuth := "managed", "Bearer fagi-openrouter-key"
					if tc.byok {
						wantMode, wantAuth = "byok", "Bearer org-or-key"
					}
					if status != http.StatusOK || headers.Get("x-agentcc-billing-mode") != wantMode || g.managed.calls() != 1 || g.managed.auths[0] != wantAuth {
						t.Fatalf("status=%d mode=%s calls=%d credential=%v", status, headers.Get("x-agentcc-billing-mode"), g.managed.calls(), g.managed.auths)
					}
					if tc.byok {
						if len(records) != 0 {
							t.Fatalf("BYOK records=%v", records)
						}
						return
					}
					kind := "spend"
					if tc.spent == 0 {
						kind = "void"
					}
					if len(records) != 1 {
						t.Fatalf("records=%v", records)
					}
					r := records[0].Values
					if r["kind"] != kind || r["usage_estimated"] != "1" || r["billed_micros"] != fmt.Sprint(tc.spent) || r["cost_source"] != "catalog" {
						t.Fatalf("settlement=%v", r)
					}
				})
			}
		}
	}
}
