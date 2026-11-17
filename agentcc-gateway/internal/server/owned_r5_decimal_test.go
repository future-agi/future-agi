package server

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"sync/atomic"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/billing"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/redisstate"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
	"github.com/redis/go-redis/v9"
)

func TestOwnedR5ExactDecimalMixedHTTP(t *testing.T) {
	for _, surface := range managedSurfaces {
		for _, stream := range []bool{false, true} {
			for _, tokenFirst := range []bool{false, true} {
				t.Run(fmt.Sprintf("%s/stream=%t/tokenFirst=%t", surface, stream, tokenFirst), func(t *testing.T) {
					g, store, client, prefix := ownedR4Gateway(t)
					primary, fallback := "google/gemini-3.5-flash", "openai/o1-pro"
					if tokenFirst {
						primary, fallback = fallback, primary
					}
					cfg := managedOptIn()
					cfg.Routing = &tenant.RoutingConfig{FallbackEnabled: true, Retry: &tenant.RetryConfig{Enabled: false}, ModelFallbacks: map[string][]string{primary: {fallback}}}
					g.tenants.Set("org-m", cfg)
					answer := upstreamStream(func(w http.ResponseWriter, r *http.Request, model string) {
						total := "0.0000095"
						if model == "openai/o1-pro" {
							total = "0.0000094999999"
						}
						if model == primary {
							ownedR4Failure(w, model, total, stream)
						} else {
							r601Response(total, stream)(w, r, model)
						}
					})
					g.managed.responseWith.Store(&answer)
					g.managed.streamWith.Store(&answer)
					status := sendBillabilityBody(t, g, surface, fmt.Sprintf(`{"model":%q,"max_tokens":64,"stream":%t,"messages":[{"role":"user","content":"hi"}]}`, primary, stream))
					acct, _ := store.Account(context.Background(), "org-m")
					rows, _ := client.Redis().XRange(context.Background(), prefix+"mc:{org-m}:outbox", "-", "+").Result()
					// 9.4999999 upstream micros rounds half-up to 9, then 9 billed;
					// 9.5 upstream micros rounds to 10, then 11 billed. Total 20.
					if status != 200 || g.managed.calls() != 2 || acct.SpentMicros != 20 || acct.HeldMicros != 0 || len(rows) != 1 {
						t.Fatalf("exact decimal lost across mixed family: status=%d calls=%d acct=%+v rows=%v", status, g.managed.calls(), acct, rows)
					}
				})
			}
		}
	}
}

func TestOwnedR5CrashBetweenReservationAndBeginEachHop(t *testing.T) {
	for _, surface := range managedSurfaces {
		for _, stream := range []bool{false, true} {
			for _, tokenFirst := range []bool{false, true} {
				for crashAt := 2; crashAt <= 3; crashAt++ {
					t.Run(fmt.Sprintf("%s/stream=%t/tokenFirst=%t/hop=%d", surface, stream, tokenFirst, crashAt), func(t *testing.T) {
						g, store, client, prefix := ownedR4Gateway(t)
						chain := []string{"google/gemini-3.5-flash", "openai/o1-pro", "google/gemini-3-pro-image"}
						if tokenFirst {
							chain[0], chain[1] = chain[1], chain[0]
						}
						cfg := managedOptIn()
						cfg.Routing = &tenant.RoutingConfig{FallbackEnabled: true, Retry: &tenant.RetryConfig{Enabled: false}, ModelFallbacks: map[string][]string{chain[0]: chain[1:]}}
						g.tenants.Set("org-m", cfg)
						var hit atomic.Bool
						var failure error
						g.srv.handlers.hookObserver = func(hook, _ string, rc *models.RequestContext) {
							if hook != hookBeginAttempt || rc.AttemptCount() != crashAt-1 || !hit.CompareAndSwap(false, true) {
								return
							}
							keys, e := client.Redis().Keys(context.Background(), prefix+"mc:{org-m}:owned:*").Result()
							if e != nil || len(keys) != 1 {
								failure = fmt.Errorf("journals=%v err=%v", keys, e)
								return
							}
							raw, e := client.Redis().Get(context.Background(), keys[0]).Bytes()
							if e != nil {
								failure = e
								return
							}
							var o billing.OwnedRequest
							if e = json.Unmarshal(raw, &o); e != nil {
								failure = e
								return
							}
							if len(o.Attempts) != crashAt {
								failure = fmt.Errorf("reservation missing before Begin: %+v", o.Attempts)
								return
							}
							for _, a := range o.Attempts {
								if a.Model == chain[crashAt-1] && a.Dispatched {
									failure = fmt.Errorf("undispatched hop became liability: %+v", a)
									return
								}
							}
							if failure = client.Redis().ZAdd(context.Background(), prefix+"mc:{org-m}:recovery", redis.Z{Score: 0, Member: rc.RequestID}).Err(); failure != nil {
								return
							}
							_, failure = store.RecoverInclusive(context.Background(), "org-m", rc.RequestID)
						}
						answer := upstreamStream(func(w http.ResponseWriter, r *http.Request, model string) {
							ownedR4Failure(w, model, "0.00005", stream)
						})
						g.managed.responseWith.Store(&answer)
						g.managed.streamWith.Store(&answer)
						sendBillabilityBody(t, g, surface, fmt.Sprintf(`{"model":%q,"max_tokens":64,"stream":%t,"messages":[{"role":"user","content":"hi"}]}`, chain[0], stream))
						if !hit.Load() || failure != nil {
							t.Fatalf("recovery point not validated: hit=%t err=%v", hit.Load(), failure)
						}
						// Resume the old caller deliberately to test the terminal fence. Its
						// expected conflicts may open that client's breaker; recovery readers
						// use a new client, not an ignored error masquerading as a zero account.
						reader := redisstate.NewManagedCreditStore(r7AgentRedis(t, prefix), prefix)
						acct, err := reader.Account(context.Background(), "org-m")
						if err != nil {
							t.Fatal(err)
						}
						rows, err := client.Redis().XRange(context.Background(), prefix+"mc:{org-m}:outbox", "-", "+").Result()
						if err != nil {
							t.Fatal(err)
						}
						if acct.SpentMicros != int64(crashAt-1)*53 || acct.HeldMicros != 0 || len(rows) != 1 || g.managed.calls() != crashAt-1 {
							t.Fatalf("pre-Begin recovery double charged or lost liability: calls=%d acct=%+v rows=%v", g.managed.calls(), acct, rows)
						}
					})
				}
			}
		}
	}
}
