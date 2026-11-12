package server

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"sync/atomic"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/billing"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
	"github.com/redis/go-redis/v9"
)

type ownedR5TerminalSealFault struct {
	key  string
	fail atomic.Bool
	hits atomic.Int32
}

func (h *ownedR5TerminalSealFault) DialHook(next redis.DialHook) redis.DialHook { return next }
func (h *ownedR5TerminalSealFault) ProcessPipelineHook(next redis.ProcessPipelineHook) redis.ProcessPipelineHook {
	return next
}
func (h *ownedR5TerminalSealFault) ProcessHook(next redis.ProcessHook) redis.ProcessHook {
	return func(ctx context.Context, cmd redis.Cmder) error {
		if cmd.Name() == "hset" && len(cmd.Args()) > 1 && fmt.Sprint(cmd.Args()[1]) == h.key && h.fail.Load() {
			h.hits.Add(1)
			return errors.New("synthetic final-hop catalogue seal unavailable")
		}
		return next(ctx, cmd)
	}
}

func TestOwnedR5TerminalSealFailureRetainsKnownCharge(t *testing.T) {
	for _, surface := range managedSurfaces {
		for _, stream := range []bool{false, true} {
			for _, tokenLast := range []bool{false, true} {
				t.Run(fmt.Sprintf("%s/stream=%t/tokenLast=%t", surface, stream, tokenLast), func(t *testing.T) {
					g, store, client, prefix := ownedR4Gateway(t)
					primary, final := "openai/o1-pro", "google/gemini-3.5-flash"
					if tokenLast {
						primary, final = final, primary
					}
					cfg := managedOptIn()
					cfg.Routing = &tenant.RoutingConfig{FallbackEnabled: true, Retry: &tenant.RetryConfig{Enabled: false}, ModelFallbacks: map[string][]string{primary: {final}}}
					g.tenants.Set("org-m", cfg)
					fault := &ownedR5TerminalSealFault{key: prefix + "mc:{catalogue}:quarantine"}
					hooks, ok := client.Redis().(interface{ AddHook(redis.Hook) })
					if !ok {
						t.Fatal("Redis hook unsupported")
					}
					hooks.AddHook(fault)
					answer := upstreamStream(func(w http.ResponseWriter, r *http.Request, model string) {
						if model == primary {
							ownedR4Failure(w, model, "0.00005", stream)
							return
						}
						fault.fail.Store(true)
						r601Response("100", stream)(w, r, model)
					})
					g.managed.responseWith.Store(&answer)
					g.managed.streamWith.Store(&answer)
					status := sendBillabilityBody(t, g, surface, fmt.Sprintf(`{"model":%q,"max_tokens":64,"stream":%t,"messages":[{"role":"user","content":"hi"}]}`, primary, stream))
					// Current settlement contract (since 2356ffce78): the terminal journal seal
					// runs first and fails closed, so exactly one catalogue seal reaches the
					// fault. Live settlement never prepares, debits or enqueues; the known
					// liability stays recovery-owned until the pre-recovery checks below.
					if status != 200 || g.managed.calls() != 2 || fault.hits.Load() != 1 {
						t.Fatalf("fixture failed: status=%d calls=%d seal_fault_hits=%d (want 200, 2 managed calls, exactly 1 failed terminal seal)", status, g.managed.calls(), fault.hits.Load())
					}
					keys, err := client.Redis().Keys(context.Background(), prefix+"mc:{org-m}:owned:*").Result()
					if err != nil || len(keys) != 1 {
						t.Fatalf("keys=%v err=%v", keys, err)
					}
					raw, err := client.Redis().Get(context.Background(), keys[0]).Bytes()
					if err != nil {
						t.Fatal(err)
					}
					var o billing.OwnedRequest
					if err = json.Unmarshal(raw, &o); err != nil {
						t.Fatal(err)
					}
					for _, a := range o.Attempts {
						if a.Model == final && (a.Summary == nil || a.Summary.BilledMicros != 105000000) {
							t.Errorf("terminal known $100 provider charge lost before failed seal: %+v", a)
						}
					}
					// The loop above passes vacuously without a final-hop journal entry.
					finalAttempts := 0
					for _, a := range o.Attempts {
						if a.Model == final {
							finalAttempts++
						}
					}
					// Before recovery the failed seal left the liability journaled but not
					// debited: nothing spent, the admission hold still held, no v1 outbox
					// row, and the global fence never written for the final model.
					acct0, err := store.Account(context.Background(), "org-m")
					if err != nil {
						t.Fatal(err)
					}
					rows0, err := client.Redis().XRange(context.Background(), prefix+"mc:{org-m}:outbox", "-", "+").Result()
					if err != nil {
						t.Fatal(err)
					}
					fenced0, err := store.InclusiveQuarantined(context.Background(), "different-org", final)
					if err != nil {
						t.Fatal(err)
					}
					if finalAttempts != 1 || acct0.SpentMicros != 0 || acct0.HeldMicros <= 0 || len(rows0) != 0 || fenced0 {
						t.Fatalf("live settlement did not fail closed before recovery: final_attempts=%d acct=%+v outbox=%v fenced=%t", finalAttempts, acct0, rows0, fenced0)
					}
					// Restore the synthetic failed catalogue shard and restart recovery from
					// durable state alone, without the lost caller's response snapshot.
					fault.fail.Store(false)
					if err = client.Redis().ZAdd(context.Background(), prefix+"mc:{org-m}:recovery", redis.Z{Score: 0, Member: o.Record.RequestID}).Err(); err != nil {
						t.Fatal(err)
					}
					if _, err = store.RecoverInclusive(context.Background(), "org-m", o.Record.RequestID); err != nil {
						t.Fatal(err)
					}
					acct, _ := store.Account(context.Background(), "org-m")
					rows, _ := client.Redis().XRange(context.Background(), prefix+"mc:{org-m}:outbox", "-", "+").Result()
					blocked, e := store.InclusiveQuarantined(context.Background(), "different-org", final)
					if acct.SpentMicros != 105000053 || acct.HeldMicros != 0 || len(rows) != 1 || !blocked || e != nil {
						t.Fatalf("terminal fault waived actual cost or fence: acct=%+v outbox=%v blocked=%t err=%v", acct, rows, blocked, e)
					}
				})
			}
		}
	}
}
