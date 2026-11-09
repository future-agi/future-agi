package server

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/billing"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

func TestOwnedR4OwnedTokenOverholdQuarantinesListing(t *testing.T) {
	for _, surface := range managedSurfaces {
		for _, stream := range []bool{false, true} {
			t.Run(fmt.Sprintf("%s/stream=%t", surface, stream), func(t *testing.T) {
				g, store, client, prefix := ownedR4Gateway(t)
				primary, final := "google/gemini-3.5-flash", "openai/o1-pro"
				cfg := managedOptIn()
				cfg.Routing = &tenant.RoutingConfig{FallbackEnabled: true, Retry: &tenant.RetryConfig{Enabled: false}, ModelFallbacks: map[string][]string{primary: {final}}}
				g.tenants.Set("org-m", cfg)
				answer := upstreamStream(func(w http.ResponseWriter, r *http.Request, model string) {
					if model == primary {
						ownedR4Failure(w, model, "0.00005", stream)
						return
					}
					r601Response("100", stream)(w, r, model)
				})
				g.managed.responseWith.Store(&answer)
				g.managed.streamWith.Store(&answer)
				body := fmt.Sprintf(`{"model":%q,"max_tokens":64,"stream":%t,"messages":[{"role":"user","content":"hi"}]}`, primary, stream)
				status := sendBillabilityBody(t, g, surface, body)
				acct, _ := store.Account(context.Background(), "org-m")
				blocked, err := store.InclusiveQuarantined(context.Background(), "independent-org", final)
				if status != 200 || acct.SpentMicros != 105000053 || acct.HeldMicros != 0 {
					t.Fatalf("bad settlement status=%d acct=%+v", status, acct)
				}
				if err != nil || !blocked {
					t.Fatalf("owned token hop reported $100 beyond its small admitted hold but was not globally quarantined: blocked=%t err=%v acct=%+v", blocked, err, acct)
				}
				rows, err := client.Redis().XRange(context.Background(), prefix+"mc:{org-m}:outbox", "-", "+").Result()
				if err != nil || len(rows) != 1 || rows[0].Values["v"] != "1" || rows[0].Values["dimensions"] != nil {
					t.Fatalf("not one v1 spend: %v %v", rows, err)
				}
				request, _ := rows[0].Values["request_id"].(string)
				raw, err := client.Redis().Get(context.Background(), prefix+"mc:{org-m}:owned:"+request).Bytes()
				if err != nil {
					t.Fatal(err)
				}
				var o billing.OwnedRequest
				if err = json.Unmarshal(raw, &o); err != nil {
					t.Fatal(err)
				}
				a := o.Record.ManagedJournal[request+".2"]
				if !a.TokenPriced || a.Anomaly == "" || a.Summary == nil || a.Summary.BilledMicros <= a.Micros {
					t.Fatalf("missing durable token over-hold anomaly: %+v", a)
				}
			})
		}
	}
}
