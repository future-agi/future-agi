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

// ownedR6Money is the org's settled state, read through a new client so a
// breaker opened by a deliberately stale caller is never read as a zero balance.
type ownedR6Money struct {
	spent, held int64
	rows        []redis.XMessage
	journal     billing.OwnedRequest
}

func ownedR6Read(t *testing.T, prefix string) ownedR6Money {
	t.Helper()
	c := r7AgentRedis(t, prefix)
	acct, err := redisstate.NewManagedCreditStore(c, prefix).Account(context.Background(), "org-m")
	if err != nil {
		t.Fatal(err)
	}
	rows, err := c.Redis().XRange(context.Background(), prefix+"mc:{org-m}:outbox", "-", "+").Result()
	if err != nil {
		t.Fatal(err)
	}
	m := ownedR6Money{spent: acct.SpentMicros, held: acct.HeldMicros, rows: rows}
	keys, err := c.Redis().Keys(context.Background(), prefix+"mc:{org-m}:owned:*").Result()
	if err != nil || len(keys) != 1 {
		t.Fatalf("owned journals=%v err=%v", keys, err)
	}
	raw, err := c.Redis().Get(context.Background(), keys[0]).Bytes()
	if err != nil {
		t.Fatal(err)
	}
	if err = json.Unmarshal(raw, &m.journal); err != nil {
		t.Fatal(err)
	}
	return m
}

// dispatchedHold is the admitted hold of every dispatched managed hop.
func (m ownedR6Money) dispatchedHold() int64 {
	var total int64
	for _, a := range m.journal.Attempts {
		if a.Dispatched {
			total += a.Micros
		}
	}
	return total
}

func ownedR6Body(model string, stream bool) string {
	return fmt.Sprintf(`{"model":%q,"max_tokens":64,"stream":%t,"messages":[{"role":"user","content":"hi"}]}`, model, stream)
}

// ownedR6Rejected is a definitive upstream HTTP refusal: no usage, no cost,
// no generation identity and no output, on either surface or stream mode.
func ownedR6Rejected(w http.ResponseWriter) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusInternalServerError)
	fmt.Fprint(w, `{"error":{"code":500,"message":"upstream overloaded"}}`)
}

// A clean upstream rejection of an owned token hop is never charged its hold.
// G7 bills nothing for it, and the exit path, recovery and a later billed hop
// must all agree: the hold is released whether or not a managed hop is billed
// afterwards. Each retried attempt takes, and must release, its own hold.
func TestOwnedR6CleanTokenRejectionReleasesHold(t *testing.T) {
	const primary, managedFinal, bridge = "openai/o1-pro", "google/gemini-3.5-flash", "owned/bridge"
	for _, shape := range []string{"single", "then-managed", "then-byok", "retry-rejected", "retry-served"} {
		for _, surface := range managedSurfaces {
			for _, stream := range []bool{false, true} {
				t.Run(fmt.Sprintf("%s%s/stream=%t", shape, surface, stream), func(t *testing.T) {
					g, _, _, prefix := ownedR4Gateway(t)
					cfg := managedOptIn()
					cfg.Routing = &tenant.RoutingConfig{Retry: &tenant.RetryConfig{Enabled: false}}
					want, wantStatus, wantManaged, wantBYOK := int64(0), 0, 1, 0
					retry := &tenant.RetryConfig{Enabled: true, MaxRetries: 1, InitialDelay: "1ms", MaxDelay: "2ms", OnStatusCodes: []int{500, 502, 503}}
					switch shape {
					case "then-managed":
						cfg.Routing = &tenant.RoutingConfig{FallbackEnabled: true, Retry: &tenant.RetryConfig{Enabled: false}, ModelFallbacks: map[string][]string{primary: {managedFinal}}}
						want, wantStatus, wantManaged = 11, http.StatusOK, 2
					case "then-byok":
						cfg.Providers = map[string]*tenant.ProviderConfig{"operator": {APIKey: "synthetic-owned-bridge", APIFormat: "openai", Models: []string{bridge}, Enabled: true}}
						cfg.Routing = &tenant.RoutingConfig{FallbackEnabled: true, Retry: &tenant.RetryConfig{Enabled: false}, ModelFallbacks: map[string][]string{primary: {bridge}}}
						wantStatus, wantBYOK = http.StatusOK, 1
					case "retry-rejected":
						cfg.Routing = &tenant.RoutingConfig{Retry: retry}
						wantManaged = 2
					case "retry-served":
						cfg.Routing = &tenant.RoutingConfig{Retry: retry}
						want, wantStatus, wantManaged = 53, http.StatusOK, 2
					}
					g.tenants.Set("org-m", cfg)
					var calls atomic.Int32
					managed := upstreamStream(func(w http.ResponseWriter, r *http.Request, model string) {
						n := calls.Add(1)
						switch {
						case model == primary && shape == "retry-served" && n == 2:
							r601Response("0.00005", stream)(w, r, model)
						case model == primary:
							ownedR6Rejected(w)
						default:
							r601Response("0.0000095", stream)(w, r, model)
						}
					})
					own := upstreamStream(func(w http.ResponseWriter, r *http.Request, model string) { r601Response("0.2", stream)(w, r, model) })
					g.managed.responseWith.Store(&managed)
					g.managed.streamWith.Store(&managed)
					g.operator.responseWith.Store(&own)
					g.operator.streamWith.Store(&own)
					status := sendBillabilityBody(t, g, surface, ownedR6Body(primary, stream))
					m := ownedR6Read(t, prefix)
					if wantStatus != 0 && status != wantStatus || wantStatus == 0 && status < 400 {
						t.Errorf("status=%d want=%d", status, wantStatus)
					}
					if g.managed.calls() != wantManaged || g.operator.calls() != wantBYOK {
						t.Errorf("calls managed=%d byok=%d want=%d/%d", g.managed.calls(), g.operator.calls(), wantManaged, wantBYOK)
					}
					if m.spent != want || m.held != 0 || len(m.rows) != 1 || m.journal.Terminal == "" {
						t.Fatalf("clean rejection charged: spent=%d want=%d held=%d rows=%d terminal=%t hold=%d", m.spent, want, m.held, len(m.rows), m.journal.Terminal != "", m.dispatchedHold())
					}
					if got := fmt.Sprint(m.rows[0].Values["billed_micros"]); got != fmt.Sprint(want) {
						t.Fatalf("outbox billed=%s want=%d", got, want)
					}
				})
			}
		}
	}
}

// Control: an owned token hop whose outcome is unknown (the upstream accepted
// the connection and dropped it with no answer) keeps its full-hold debt.
// Only a definitive upstream rejection without evidence is released.
func TestOwnedR6UnknownTokenOutcomeKeepsHold(t *testing.T) {
	for _, surface := range managedSurfaces {
		for _, stream := range []bool{false, true} {
			t.Run(fmt.Sprintf("%s/stream=%t", surface, stream), func(t *testing.T) {
				g, _, _, prefix := ownedR4Gateway(t)
				cfg := managedOptIn()
				cfg.Routing = &tenant.RoutingConfig{Retry: &tenant.RetryConfig{Enabled: false}}
				g.tenants.Set("org-m", cfg)
				dropped := upstreamStream(func(http.ResponseWriter, *http.Request, string) { panic(http.ErrAbortHandler) })
				g.managed.responseWith.Store(&dropped)
				g.managed.streamWith.Store(&dropped)
				status := sendBillabilityBody(t, g, surface, ownedR6Body("openai/o1-pro", stream))
				m := ownedR6Read(t, prefix)
				hold := m.dispatchedHold()
				if status < 400 || hold <= 0 || m.spent != hold || m.held != 0 || len(m.rows) != 1 {
					t.Fatalf("unknown outcome released: status=%d spent=%d hold=%d held=%d rows=%d", status, m.spent, hold, m.held, len(m.rows))
				}
			})
		}
	}
}

// A crash after a clean rejection, with the next hop reserved but not yet
// dispatched: recovery releases both the rejected hop's hold and the
// undispatched reservation. The resumed stale caller cannot add a charge.
func TestOwnedR6CrashAfterCleanRejectionReleasesHold(t *testing.T) {
	for _, surface := range managedSurfaces {
		for _, stream := range []bool{false, true} {
			t.Run(fmt.Sprintf("%s/stream=%t", surface, stream), func(t *testing.T) {
				g, store, client, prefix := ownedR4Gateway(t)
				primary, fallback := "openai/o1-pro", "google/gemini-3.5-flash"
				cfg := managedOptIn()
				cfg.Routing = &tenant.RoutingConfig{FallbackEnabled: true, Retry: &tenant.RetryConfig{Enabled: false}, ModelFallbacks: map[string][]string{primary: {fallback}}}
				g.tenants.Set("org-m", cfg)
				var hit atomic.Bool
				var failure error
				g.srv.handlers.hookObserver = func(hook, _ string, rc *models.RequestContext) {
					if hook != hookBeginAttempt || rc.AttemptCount() != 1 || !hit.CompareAndSwap(false, true) {
						return
					}
					if failure = client.Redis().ZAdd(context.Background(), prefix+"mc:{org-m}:recovery", redis.Z{Score: 0, Member: rc.RequestID}).Err(); failure != nil {
						return
					}
					_, failure = store.RecoverInclusive(context.Background(), "org-m", rc.RequestID)
				}
				answer := upstreamStream(func(w http.ResponseWriter, r *http.Request, model string) {
					if model == primary {
						ownedR6Rejected(w)
						return
					}
					r601Response("0.0000095", stream)(w, r, model)
				})
				g.managed.responseWith.Store(&answer)
				g.managed.streamWith.Store(&answer)
				sendBillabilityBody(t, g, surface, ownedR6Body(primary, stream))
				if !hit.Load() || failure != nil {
					t.Fatalf("recovery point not reached: hit=%t err=%v", hit.Load(), failure)
				}
				m := ownedR6Read(t, prefix)
				if len(m.journal.Attempts) != 2 {
					t.Fatalf("fallback reservation missing before recovery: %+v", m.journal.Attempts)
				}
				if m.spent != 0 || m.held != 0 || len(m.rows) != 1 || g.managed.calls() != 1 || m.journal.Terminal == "" {
					t.Fatalf("recovery charged a clean rejection: spent=%d held=%d rows=%d calls=%d hold=%d", m.spent, m.held, len(m.rows), g.managed.calls(), m.dispatchedHold())
				}
			})
		}
	}
}
