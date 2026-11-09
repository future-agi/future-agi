package server

import (
	"context"
	"fmt"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/billing"
	creditsplugin "github.com/futureagi/agentcc-gateway/internal/plugins/credits"
	"github.com/futureagi/agentcc-gateway/internal/redisstate"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

func TestSolTokenToInclusiveFallbackControl(t *testing.T) {
	for _, stream := range []bool{false, true} {
		t.Run(fmt.Sprint(stream), func(t *testing.T) {
			prefix := fmt.Sprintf("test-sol-reverse-%d:", time.Now().UnixNano())
			client := r7AgentRedis(t, prefix)
			g, _, _ := r601Gateway(t)
			store := redisstate.NewManagedCreditStore(client, prefix)
			g.ledger = creditsplugin.NewOrgLedger(store)
			g.credits.SetOrgLedger(g.ledger, true)
			g.srv.EnableManaged(ManagedSettings{Catalog: g.srv.handlers.catalog, Ledger: g.ledger, MarkupPercent: 5, RequireCredits: true})
			if _, e := store.Grant(context.Background(), "org-m", billing.GrantPush{GrantedTotalMicros: 2000000000, Version: 1}); e != nil {
				t.Fatal(e)
			}
			primary := "openai/o1-pro"
			fallback := "google/gemini-3.5-flash"
			cfg := managedOptIn()
			cfg.Routing = &tenant.RoutingConfig{FallbackEnabled: true, ModelFallbacks: map[string][]string{primary: {fallback}}}
			g.tenants.Set("org-m", cfg)
			g.managed.failFor.Store(primary, 500)
			answer := r601Response("0.0000095", stream)
			g.managed.responseWith.Store(&answer)
			g.managed.streamWith.Store(&answer)
			body := fmt.Sprintf(`{"model":%q,"max_tokens":64,"stream":%t,"messages":[{"role":"user","content":"hi"}]}`, primary, stream)
			code := sendBillabilityBody(t, g, "/v1/chat/completions", body)
			rows, _ := client.Redis().XRange(context.Background(), prefix+"mc:{org-m}:outbox", "-", "+").Result()
			acct, _ := store.Account(context.Background(), "org-m")
			if code != 200 || g.managed.lastModel() != fallback || acct.HeldMicros != 0 || acct.SpentMicros != 11 || len(rows) != 1 {
				t.Fatalf("control failed status=%d calls=%d final=%q acct=%+v rows=%v", code, g.managed.calls(), g.managed.lastModel(), acct, rows)
			}
		})
	}
}
