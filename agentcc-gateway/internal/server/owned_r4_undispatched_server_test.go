package server

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/billing"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
	"github.com/redis/go-redis/v9"
)

func TestOwnedR4UndispatchedInclusiveFallbackReleasesImmediately(t *testing.T) {
	for _, surface := range managedSurfaces {
		for _, stream := range []bool{false, true} {
			t.Run(fmt.Sprintf("%s/stream=%t", surface, stream), func(t *testing.T) {
				g, store, client, prefix := ownedR4Gateway(t)
				primary, fallback := "openai/o1-pro", "google/gemini-3.5-flash"
				cfg := managedOptIn()
				cfg.Routing = &tenant.RoutingConfig{FallbackEnabled: true, Retry: &tenant.RetryConfig{Enabled: false}, ModelFallbacks: map[string][]string{primary: {fallback}}}
				g.tenants.Set("org-m", cfg)
				var injected bool
				g.srv.handlers.hookObserver = func(hook, _ string, rc *models.RequestContext) {
					if hook == hookBeginAttempt && rc.AttemptCount() == 1 && rc.Billing.OwnedRequest {
						// Model the real deadline crossing AFTER fallback reservation but BEFORE
						// dispatch, without relying on sleeps or scheduler timing.
						injected = true
						rc.Agent.DeadlineMs = 1
						rc.StartTime = time.Now().Add(-time.Second)
					}
				}
				answer := upstreamStream(func(w http.ResponseWriter, r *http.Request, model string) {
					ownedR4Failure(w, model, "0.00005", stream)
				})
				g.managed.responseWith.Store(&answer)
				g.managed.streamWith.Store(&answer)
				body := fmt.Sprintf(`{"model":%q,"max_tokens":64,"stream":%t,"messages":[{"role":"user","content":"hi"}]}`, primary, stream)
				status := sendBillabilityBody(t, g, surface, body)
				acct, _ := store.Account(context.Background(), "org-m")
				rows, _ := client.Redis().XRange(context.Background(), prefix+"mc:{org-m}:outbox", "-", "+").Result()
				if !injected || g.managed.calls() != 1 {
					t.Fatalf("did not hit pre-dispatch refusal: injected=%t status=%d calls=%d", injected, status, g.managed.calls())
				}
				if acct.SpentMicros != 53 || acct.HeldMicros != 0 || len(rows) != 1 {
					t.Errorf("undispatched fallback blocked ordinary settlement: status=%d acct=%+v outbox=%v", status, acct, rows)
				}
				keys, _ := client.Redis().Keys(context.Background(), prefix+"mc:{org-m}:owned:*").Result()
				if len(keys) != 1 {
					t.Fatalf("owned journals=%v", keys)
				}
				raw, err := client.Redis().Get(context.Background(), keys[0]).Bytes()
				if err != nil {
					t.Fatal(err)
				}
				var o billing.OwnedRequest
				if err = json.Unmarshal(raw, &o); err != nil {
					t.Fatal(err)
				}
				if o.Terminal == "" || o.Record.Dimensional == nil || len(o.Record.Dimensional.InclusiveAttempts) != 0 {
					t.Fatalf("invalid token-only owned receipt: %+v", o)
				}
				if len(o.Attempts) != 2 {
					t.Fatalf("reservations=%+v", o.Attempts)
				}
				for _, a := range o.Attempts {
					if a.Model == fallback && a.Dispatched {
						t.Fatal("undispatched reservation became liability")
					}
				}
				if _, err = client.Redis().ZScore(context.Background(), prefix+"mc:{org-m}:recovery", o.Record.RequestID).Result(); err != redis.Nil {
					t.Fatalf("terminal request retained recovery: %v", err)
				}
				if path := os.Getenv("ONEKEY_UNDISPATCHED_OUTBOX"); path != "" && surface == managedSurfaces[0] && !stream && !t.Failed() {
					data, err := json.MarshalIndent(map[string]any{"undispatched": rows[0].Values}, "", "  ")
					if err != nil {
						t.Fatal(err)
					}
					if err = os.WriteFile(path, append(data, '\n'), 0600); err != nil {
						t.Fatal(err)
					}
				}
			})
		}
	}
}
