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
	"github.com/futureagi/agentcc-gateway/internal/tenant"
	"github.com/redis/go-redis/v9"
)

func ownedR4Gateway(t *testing.T) (*managedTestGateway, *redisstate.ManagedCreditStore, *redisstate.Client, string) {
	t.Helper()
	prefix := fmt.Sprintf("test-sol-r3-adv-%d:", time.Now().UnixNano())
	client := r7AgentRedis(t, prefix)
	g, _, _ := r601Gateway(t)
	store := redisstate.NewManagedCreditStore(client, prefix)
	g.ledger = creditsplugin.NewOrgLedger(store)
	g.credits.SetOrgLedger(g.ledger, true)
	g.srv.EnableManaged(ManagedSettings{Catalog: g.srv.handlers.catalog, Ledger: g.ledger, MarkupPercent: 5, RequireCredits: true})
	if _, err := store.Grant(context.Background(), "org-m", billing.GrantPush{GrantedTotalMicros: 2000000000, Version: 1}); err != nil {
		t.Fatal(err)
	}
	return g, store, client, prefix
}
func ownedR4Failure(w http.ResponseWriter, model, cost string, stream bool) {
	if stream {
		w.Header().Set("Content-Type", "text/event-stream")
		fmt.Fprintf(w, "data: {\"id\":\"gen-adv-prior\",\"model\":%q,\"error\":{\"code\":500,\"message\":\"billable failure\"},\"usage\":{\"cost\":%s}}\n\ndata: [DONE]\n\n", model, cost)
	} else {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(500)
		fmt.Fprintf(w, `{"id":"gen-adv-prior","model":%q,"error":{"code":500,"message":"billable failure"},"usage":{"cost":%s}}`, model, cost)
	}
}

func TestOwnedR4OverholdFenceBeforeNextHop(t *testing.T) {
	for _, tokenFirst := range []bool{false, true} {
		for _, surface := range managedSurfaces {
			for _, stream := range []bool{false, true} {
				for _, retry := range []bool{false, true} {
					t.Run(fmt.Sprintf("%s/stream=%t/retry=%t/token=%t", surface, stream, retry, tokenFirst), func(t *testing.T) {
						g, store, _, _ := ownedR4Gateway(t)
						primary, fallback := "google/gemini-3.5-flash", "openai/o1-pro"
						if tokenFirst {
							primary, fallback = fallback, primary
						}
						cfg := managedOptIn()
						cfg.Routing = &tenant.RoutingConfig{FallbackEnabled: true, Retry: &tenant.RetryConfig{Enabled: false}, ModelFallbacks: map[string][]string{primary: {fallback}}}
						if retry {
							fallback = primary
							cfg.Routing = &tenant.RoutingConfig{Retry: &tenant.RetryConfig{Enabled: true, MaxRetries: 1, InitialDelay: "1ms", MaxDelay: "2ms", OnStatusCodes: []int{500, 502, 503}}}
						}
						g.tenants.Set("org-m", cfg)
						var calls atomic.Int32
						var sawUnfenced atomic.Bool
						answer := upstreamStream(func(w http.ResponseWriter, r *http.Request, model string) {
							if calls.Add(1) == 1 {
								ownedR4Failure(w, model, "100", stream)
								return
							}
							blocked, err := store.InclusiveQuarantined(context.Background(), "independent-org", primary)
							if err != nil || !blocked {
								sawUnfenced.Store(true)
							}
							r601Response("0.0000095", stream)(w, r, model)
						})
						g.managed.responseWith.Store(&answer)
						g.managed.streamWith.Store(&answer)
						body := fmt.Sprintf(`{"model":%q,"max_tokens":64,"stream":%t,"messages":[{"role":"user","content":"hi"}]}`, primary, stream)
						status := sendBillabilityBody(t, g, surface, body)
						acct, _ := store.Account(context.Background(), "org-m")
						t.Logf("status=%d calls=%d spent=%d held=%d", status, calls.Load(), acct.SpentMicros, acct.HeldMicros)
						if sawUnfenced.Load() {
							t.Errorf("known over-hold attempt still globally unfenced when a subsequent hop dispatched")
						}
						if retry && calls.Load() > 1 {
							t.Errorf("quarantined model retried after reporting $100 against its smaller admitted hold")
						}
						if acct.SpentMicros < 105000000 {
							t.Errorf("known charge lost: %+v", acct)
						}
					})
				}
			}
		}
	}
}

func TestOwnedR4ThreeHopCrashAtEachDispatch(t *testing.T) {
	for _, surface := range managedSurfaces {
		for _, stream := range []bool{false, true} {
			for _, reverse := range []bool{false, true} {
				for crashAt := 1; crashAt <= 3; crashAt++ {
					t.Run(fmt.Sprintf("%s/stream=%t/reverse=%t/step=%d", surface, stream, reverse, crashAt), func(t *testing.T) {
						g, store, client, prefix := ownedR4Gateway(t)
						chain := []string{"google/gemini-3.5-flash", "openai/o1-pro", "google/gemini-3-pro-image"}
						if reverse {
							chain = []string{"openai/o1-pro", "google/gemini-3.5-flash", "google/gemini-3-pro-image"}
						}
						cfg := managedOptIn()
						cfg.Routing = &tenant.RoutingConfig{FallbackEnabled: true, Retry: &tenant.RetryConfig{Enabled: false}, ModelFallbacks: map[string][]string{chain[0]: chain[1:]}}
						g.tenants.Set("org-m", cfg)
						var calls atomic.Int32
						results := make(chan error, 1)
						answer := upstreamStream(func(w http.ResponseWriter, r *http.Request, model string) {
							n := int(calls.Add(1))
							if n == crashAt {
								keys, err := client.Redis().Keys(context.Background(), prefix+"mc:{org-m}:owned:*").Result()
								if err == nil && len(keys) != 1 {
									err = fmt.Errorf("dispatched mixed-chain step %d has no durable ownership journal (keys=%v)", n, keys)
								}
								if err == nil {
									raw, e := client.Redis().Get(context.Background(), keys[0]).Bytes()
									err = e
									var o billing.OwnedRequest
									if err == nil {
										err = json.Unmarshal(raw, &o)
									}
									var hold int64
									for _, a := range o.Attempts {
										if a.Dispatched && a.Summary == nil {
											hold += a.Micros
										}
									}
									if err == nil {
										err = client.Redis().ZAdd(context.Background(), prefix+"mc:{org-m}:recovery", redis.Z{Score: 0, Member: o.Record.RequestID}).Err()
									}
									if err == nil {
										_, err = store.RecoverInclusive(context.Background(), "org-m", o.Record.RequestID)
									}
									acct, _ := store.Account(context.Background(), "org-m")
									want := int64(n-1)*53 + hold
									if err == nil && (acct.SpentMicros != want || acct.HeldMicros != 0) {
										err = fmt.Errorf("step %d recovery acct=%+v want spent=%d", n, acct, want)
									}
								}
								results <- err
							}
							if n < 3 {
								ownedR4Failure(w, model, "0.00005", stream)
							} else {
								r601Response("0.0000095", stream)(w, r, model)
							}
						})
						g.managed.responseWith.Store(&answer)
						g.managed.streamWith.Store(&answer)
						body := fmt.Sprintf(`{"model":%q,"max_tokens":64,"stream":%t,"messages":[{"role":"user","content":"hi"}]}`, chain[0], stream)
						status := sendBillabilityBody(t, g, surface, body)
						select {
						case err := <-results:
							if err != nil {
								t.Error(err)
							}
						default:
							t.Fatalf("step %d never reached status=%d calls=%d", crashAt, status, calls.Load())
						}
					})
				}
			}
		}
	}
}
