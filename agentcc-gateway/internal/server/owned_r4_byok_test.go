package server

import (
	"context"
	"fmt"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
	"net/http"
	"testing"
)

// Streaming error-start bridges are covered by the separate ONEKEY-API-1 fix.
func TestOwnedR4BYOKBridgeRemainsFree(t *testing.T) {
	for _, surface := range managedSurfaces {
		for _, stream := range []bool{false} {
			for _, reverse := range []bool{false, true} {
				t.Run(fmt.Sprintf("%s/stream=%t/reverse=%t", surface, stream, reverse), func(t *testing.T) {
					g, store, client, prefix := ownedR4Gateway(t)
					allowMixedBillingChains(g) // This bridge explicitly exercises both credential modes.
					primary, final := "google/gemini-3.5-flash", "openai/o1-pro"
					if reverse {
						primary, final = final, primary
					}
					bridge := "owned/bridge"
					cfg := managedOptIn()
					cfg.Providers = map[string]*tenant.ProviderConfig{"operator": {APIKey: "synthetic-owned-bridge", APIFormat: "openai", Models: []string{bridge}, Enabled: true}}
					cfg.Routing = &tenant.RoutingConfig{FallbackEnabled: true, Retry: &tenant.RetryConfig{Enabled: false}, ModelFallbacks: map[string][]string{primary: {bridge, final}}}
					g.tenants.Set("org-m", cfg)
					first := upstreamStream(func(w http.ResponseWriter, r *http.Request, model string) {
						if model == primary {
							ownedR4Failure(w, model, "0.00005", stream)
							return
						}
						r601Response("0.0000095", stream)(w, r, model)
					})
					own := upstreamStream(func(w http.ResponseWriter, r *http.Request, model string) { ownedR4Failure(w, model, "0.2", stream) })
					g.managed.responseWith.Store(&first)
					g.managed.streamWith.Store(&first)
					g.operator.responseWith.Store(&own)
					g.operator.streamWith.Store(&own)
					body := fmt.Sprintf(`{"model":%q,"max_tokens":64,"stream":%t,"messages":[{"role":"user","content":"hi"}]}`, primary, stream)
					status := sendBillabilityBody(t, g, surface, body)
					acct, _ := store.Account(context.Background(), "org-m")
					if q, err := store.InclusiveQuarantined(context.Background(), "independent-org", bridge); err != nil || q {
						t.Fatalf("BYOK charge quarantined a listing: %t %v", q, err)
					}
					rows, _ := client.Redis().XRange(context.Background(), prefix+"mc:{org-m}:outbox", "-", "+").Result()
					if status != 200 || g.managed.calls() != 2 || g.operator.calls() != 1 || acct.SpentMicros != 64 || acct.HeldMicros != 0 || len(rows) != 1 {
						t.Fatalf("BYOK bridge lost or charged: status=%d managed=%d byok=%d acct=%+v rows=%v", status, g.managed.calls(), g.operator.calls(), acct, rows)
					}
				})
			}
		}
	}
}
