package server

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/billing"
	creditsplugin "github.com/futureagi/agentcc-gateway/internal/plugins/credits"
	"github.com/futureagi/agentcc-gateway/internal/redisstate"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

func TestBYOKCAT2MessagesFallbackChain(t *testing.T) {
	testBYOKStreamFallbackChain(t, "/v1/messages")
}

// The verifier's original token/inclusive bridge, using real Redis and local
// synthetic HTTP providers. Both directions must reach the final managed hop.
func TestBYOKCAT2InclusiveBridge(t *testing.T) {
	for _, surface := range managedSurfaces {
		for _, reverse := range []bool{false, true} {
			t.Run(fmt.Sprintf("%s/reverse=%t", surface, reverse), func(t *testing.T) {
				prefix := fmt.Sprintf("test-byok-cat2-%d:", time.Now().UnixNano())
				client := r7AgentRedis(t, prefix)
				g, _, _ := r601Gateway(t)
				allowMixedBillingChains(g) // This bridge explicitly exercises both credential modes.
				store := redisstate.NewManagedCreditStore(client, prefix)
				g.ledger = creditsplugin.NewOrgLedger(store)
				g.credits.SetOrgLedger(g.ledger, true)
				g.srv.EnableManaged(ManagedSettings{Catalog: g.srv.handlers.catalog, Ledger: g.ledger, MarkupPercent: 5, RequireCredits: true})
				if _, err := store.Grant(context.Background(), "org-m", billing.GrantPush{GrantedTotalMicros: 2000000000, Version: 1}); err != nil {
					t.Fatal(err)
				}
				primary, final := "google/gemini-3.5-flash", "openai/o1-pro"
				if reverse {
					primary, final = final, primary
				}
				cfg := byokStreamOrg()
				cfg.Routing = &tenant.RoutingConfig{FallbackEnabled: true, Retry: &tenant.RetryConfig{Enabled: false}, ModelFallbacks: map[string][]string{primary: {byokBridgeModel, final}}}
				g.tenants.Set("org-m", cfg)
				managed := upstreamStream(func(w http.ResponseWriter, r *http.Request, model string) {
					if model == primary {
						byokEarlyStreamFailure(w, model, "0.00005", false)
						return
					}
					r601Response("0.0000095", true)(w, r, model)
				})
				own := upstreamStream(func(w http.ResponseWriter, _ *http.Request, model string) {
					byokEarlyStreamFailure(w, model, "0.2", false)
				})
				g.managed.streamWith.Store(&managed)
				g.operator.streamWith.Store(&own)
				body := fmt.Sprintf(`{"model":%q,"max_tokens":64,"stream":true,"messages":[{"role":"user","content":"hi"}]}`, primary)
				status := sendBillabilityBody(t, g, surface, body)
				acct, err := store.Account(context.Background(), "org-m")
				if err != nil {
					t.Fatal(err)
				}
				rows, err := client.Redis().XRange(context.Background(), prefix+"mc:{org-m}:outbox", "-", "+").Result()
				if err != nil || status != 200 || g.managed.calls() != 2 || g.operator.calls() != 1 || acct.SpentMicros != 64 || acct.HeldMicros != 0 || len(rows) != 1 {
					t.Fatalf("BYOK bridge lost or charged: status=%d managed=%d byok=%d acct=%+v rows=%v err=%v", status, g.managed.calls(), g.operator.calls(), acct, rows, err)
				}
				var attempts []billing.AttemptSummary
				if err := json.Unmarshal([]byte(fmt.Sprint(rows[0].Values["attempts"])), &attempts); err != nil || len(attempts) != 3 {
					t.Fatalf("attempts=%v err=%v", attempts, err)
				}
				if a := attempts[1]; a.CredentialType != "byok" || a.Outcome != "error" || a.UpstreamMicros != 200000 || a.BilledMicros != 200000 {
					t.Errorf("BYOK evidence or fee incorrect: %+v", a)
				}
			})
		}
	}
}
