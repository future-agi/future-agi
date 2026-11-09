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
	"github.com/redis/go-redis/v9"
)

func TestSolHTTPMixedCrashRecovery(t *testing.T) {
	for _, surface := range managedSurfaces {
		for _, stream := range []bool{false, true} {
			for _, reverse := range []bool{false, true} {
				t.Run(fmt.Sprintf("%s/%t/reverse=%t", surface, stream, reverse), func(t *testing.T) { mixedHTTPRecovery(t, surface, stream, reverse) })
			}
		}
	}
}
func mixedHTTPRecovery(t *testing.T, surface string, stream, reverse bool) {
	prefix := fmt.Sprintf("test-sol-mixed-crash-%d:", time.Now().UnixNano())
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
	if reverse {
		primary, fallback = fallback, primary
	}
	cfg := managedOptIn()
	cfg.Routing = &tenant.RoutingConfig{FallbackEnabled: true, Retry: &tenant.RetryConfig{Enabled: false}, ModelFallbacks: map[string][]string{primary: {fallback}}}
	g.tenants.Set("org-m", cfg)
	type result struct {
		billed, want int64
		err          error
	}
	recovered := make(chan result, 1)
	answer := upstreamStream(func(w http.ResponseWriter, r *http.Request, model string) {
		w.Header().Set("Content-Type", "text/event-stream")
		if model == primary {
			if stream {
				fmt.Fprintf(w, "data: {\"id\":\"gen-legacy\",\"model\":%q,\"error\":{\"code\":500,\"message\":\"billable failure\"},\"usage\":{\"cost\":0.00005}}\n\ndata: [DONE]\n\n", model)
			} else {
				w.Header().Set("Content-Type", "application/json")
				w.WriteHeader(500)
				fmt.Fprintf(w, `{"id":"gen-legacy","model":%q,"error":{"code":500,"message":"billable failure"},"usage":{"cost":0.00005}}`, model)
			}
			return
		}
		if model != fallback {
			recovered <- result{err: fmt.Errorf("unexpected model %s", model)}
			return
		}
		// Upstream I/O proves BeginInclusive durably saved this attempt. Model the
		// worker after a process crash, making its 24-hour recovery timer due.
		keys, e := client.Redis().Keys(context.Background(), prefix+"mc:{org-m}:owned:*").Result()
		if e != nil || len(keys) != 1 {
			recovered <- result{err: fmt.Errorf("owned journal %v %v", keys, e)}
			return
		}
		raw, e := client.Redis().Get(context.Background(), keys[0]).Bytes()
		var o billing.OwnedRequest
		if e == nil {
			e = json.Unmarshal(raw, &o)
		}
		if e != nil {
			recovered <- result{err: e}
			return
		}
		request := o.Record.RequestID
		var unresolvedHold int64
		for _, a := range o.Attempts {
			if a.Dispatched && a.Summary == nil {
				unresolvedHold += a.Micros
			}
		}
		e = client.Redis().ZAdd(context.Background(), prefix+"mc:{org-m}:recovery", redis.Z{Score: 0, Member: request}).Err()
		if e == nil {
			_, e = store.RecoverInclusive(context.Background(), "org-m", request)
		}
		acct, ae := store.Account(context.Background(), "org-m")
		if e == nil {
			e = ae
		}
		recovered <- result{billed: acct.SpentMicros, want: unresolvedHold + 53, err: e}
		r601Response("0.0000095", stream)(w, r, model)
	})
	g.managed.streamWith.Store(&answer)
	g.managed.responseWith.Store(&answer)
	body := fmt.Sprintf(`{"model":%q,"max_tokens":64,"stream":%t,"messages":[{"role":"user","content":"hi"}]}`, primary, stream)
	status := sendBillabilityBody(t, g, surface, body)
	select {
	case got := <-recovered:
		if got.err != nil {
			t.Fatal(got.err)
		}
		if got.billed != got.want {
			t.Fatalf("real HTTP mixed recovery waived first attempt: billed=%d want=%d (unresolved full hold plus 53-micro prior charge), status=%d calls=%d", got.billed, got.want, status, g.managed.calls())
		}
	default:
		t.Fatalf("inclusive fallback not dispatched: status=%d calls=%d", status, g.managed.calls())
	}
	rows, err := client.Redis().XRange(context.Background(), prefix+"mc:{org-m}:outbox", "-", "+").Result()
	if err != nil || len(rows) != 1 {
		t.Fatalf("crash recovery emitted multiple spends: %v %v", rows, err)
	}
}
