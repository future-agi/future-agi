package server

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/billing"
	creditsplugin "github.com/futureagi/agentcc-gateway/internal/plugins/credits"
	"github.com/futureagi/agentcc-gateway/internal/redisstate"
	"github.com/futureagi/agentcc-gateway/internal/routerbilling"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

func TestMixedFamilyFallbackAndRetryHTTP(t *testing.T) {
	all, _ := installBundledCatalogue(t)
	ids := append(r601IDs(all), r602Routers...)
	for _, id := range ids {
		if id == "google/gemini-2.5-flash-image" {
			continue
		}
		for _, surface := range managedSurfaces {
			for _, stream := range []bool{false, true} {
				for _, direction := range []string{"into", "out", "retry"} {
					t.Run(fmt.Sprintf("%s/%s/%t/%s", id, surface, stream, direction), func(t *testing.T) {
						prefix := fmt.Sprintf("test-mixed-family-%d:", time.Now().UnixNano())
						client := r7AgentRedis(t, prefix)
						g, _, _ := r601Gateway(t)
						store := redisstate.NewManagedCreditStore(client, prefix)
						g.ledger = creditsplugin.NewOrgLedger(store)
						g.credits.SetOrgLedger(g.ledger, true)
						g.srv.EnableManaged(ManagedSettings{Catalog: g.srv.handlers.catalog, Ledger: g.ledger, MarkupPercent: 5, RequireCredits: true})
						if _, err := store.Grant(context.Background(), "org-m", billing.GrantPush{GrantedTotalMicros: 2000000000, Version: 1}); err != nil {
							t.Fatal(err)
						}
						primary, fallback := "openai/o1-pro", id
						if direction == "out" {
							primary, fallback = id, primary
						}
						if direction == "retry" {
							primary, fallback = id, id
						}
						cfg := managedOptIn()
						cfg.Routing = &tenant.RoutingConfig{FallbackEnabled: true, Retry: &tenant.RetryConfig{Enabled: false}, ModelFallbacks: map[string][]string{primary: {fallback}}}
						if direction == "retry" {
							cfg.Routing = &tenant.RoutingConfig{Retry: &tenant.RetryConfig{Enabled: true, MaxRetries: 1, InitialDelay: "1ms", MaxDelay: "2ms", OnStatusCodes: []int{500, 502, 503}}}
						}
						g.tenants.Set("org-m", cfg)
						var calls atomic.Int32
						answer := upstreamStream(func(w http.ResponseWriter, r *http.Request, model string) {
							n := calls.Add(1)
							served := model
							if routerbilling.FamilyMember(model) {
								served = "openai/o1-pro"
							}
							if n == 1 {
								if stream {
									w.Header().Set("Content-Type", "text/event-stream")
									fmt.Fprintf(w, "data: {\"id\":\"gen-mixed-prior\",\"model\":%q,\"error\":{\"code\":500,\"message\":\"billable failure\"},\"usage\":{\"cost\":0.00005}}\n\ndata: [DONE]\n\n", served)
								} else {
									w.Header().Set("Content-Type", "application/json")
									w.WriteHeader(500)
									fmt.Fprintf(w, `{"id":"gen-mixed-prior","model":%q,"error":{"code":500,"message":"billable failure"},"usage":{"cost":0.00005}}`, served)
								}
								return
							}
							if model != fallback {
								t.Errorf("model=%s want=%s", model, fallback)
							}
							r601Response("0.0000095", stream)(w, r, served)
						})
						g.managed.responseWith.Store(&answer)
						g.managed.streamWith.Store(&answer)
						body := fmt.Sprintf(`{"model":%q,"max_tokens":64,"stream":%t,"messages":[{"role":"user","content":"hi"}]}`, primary, stream)
						status := sendBillabilityBody(t, g, surface, body)
						rows, err := client.Redis().XRange(context.Background(), prefix+"mc:{org-m}:outbox", "-", "+").Result()
						acct, _ := store.Account(context.Background(), "org-m")
						if routerbilling.FamilyMember(id) {
							if direction != "into" {
								if err != nil || status != 403 || calls.Load() != 0 || acct.HeldMicros != 0 || acct.SpentMicros != 0 || len(rows) != 0 {
									t.Fatalf("router primary/retry was not refused before work: status=%d calls=%d acct=%+v rows=%v err=%v", status, calls.Load(), acct, rows, err)
								}
								return
							}
							// A billable fixed-model failure still costs 53. The router
							// fallback must add neither a dispatch nor a credit charge.
							if err != nil || status != 502 || calls.Load() != 1 || acct.HeldMicros != 0 || acct.SpentMicros != 53 || len(rows) != 1 {
								t.Fatalf("router fallback changed the primary-only settlement: status=%d calls=%d acct=%+v rows=%v err=%v", status, calls.Load(), acct, rows, err)
							}
							var attempts []billing.AttemptSummary
							if err := json.Unmarshal([]byte(fmt.Sprint(rows[0].Values["attempts"])), &attempts); err != nil || len(attempts) != 1 || attempts[0].Model != primary || attempts[0].BilledMicros != 53 {
								t.Fatalf("unexpected router attempt: %+v err=%v", attempts, err)
							}
							return
						}
						if err != nil || status != 200 || calls.Load() != 2 || acct.HeldMicros != 0 || len(rows) != 1 {
							t.Fatalf("status=%d calls=%d acct=%+v rows=%v err=%v", status, calls.Load(), acct, rows, err)
						}
						var attempts []billing.AttemptSummary
						if err := json.Unmarshal([]byte(fmt.Sprint(rows[0].Values["attempts"])), &attempts); err != nil {
							t.Fatal(err)
						}
						if len(attempts) != 2 || acct.SpentMicros != 64 || attempts[0].BilledMicros != 53 || attempts[1].BilledMicros != 11 {
							t.Fatalf("lost charge acct=%+v attempts=%+v", acct, attempts)
						}
					})
				}
			}
		}
	}
}
