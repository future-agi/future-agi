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

func TestInclusiveToTokenFallback(t *testing.T) {
	for _, stream := range []bool{false, true} {
		t.Run(fmt.Sprint(stream), func(t *testing.T) {
			prefix := fmt.Sprintf("test-sol-fallback-%d:", time.Now().UnixNano())
			client := r7AgentRedis(t, prefix)
			g, _, _ := r601Gateway(t)
			store := redisstate.NewManagedCreditStore(client, prefix)
			g.ledger = creditsplugin.NewOrgLedger(store)
			g.credits.SetOrgLedger(g.ledger, true)
			g.srv.EnableManaged(ManagedSettings{Catalog: g.srv.handlers.catalog, Ledger: g.ledger, MarkupPercent: 5, RequireCredits: true})
			if _, err := store.Grant(context.Background(), "org-m", billing.GrantPush{GrantedTotalMicros: 2000000000, Version: 1}); err != nil {
				t.Fatal(err)
			}
			primary := "google/gemini-3.5-flash"
			fallback := "openai/o1-pro"
			cfg := managedOptIn()
			cfg.Routing = &tenant.RoutingConfig{FallbackEnabled: true, ModelFallbacks: map[string][]string{primary: {fallback}}}
			g.tenants.Set("org-m", cfg)
			g.managed.failFor.Store(primary, 500)
			answer := r601Response("0.0000095", stream)
			g.managed.responseWith.Store(&answer)
			g.managed.streamWith.Store(&answer)
			body := fmt.Sprintf(`{"model":%q,"max_tokens":64,"stream":%t,"messages":[{"role":"user","content":"hi"}]}`, primary, stream)
			code := sendBillabilityBody(t, g, "/v1/chat/completions", body)
			acct, err := store.Account(context.Background(), "org-m")
			rows, _ := client.Redis().XRange(context.Background(), prefix+"mc:{org-m}:outbox", "-", "+").Result()
			if code != http.StatusOK || g.managed.lastModel() != fallback || acct.HeldMicros != 0 || len(rows) != 1 || err != nil {
				t.Fatalf("inclusive fallback failed: status=%d calls=%d final=%q acct=%+v outbox=%v err=%v", code, g.managed.calls(), g.managed.lastModel(), acct, rows, err)
			}
		})
	}
}

func TestMissingGenerationNeverInvented(t *testing.T) {
	g, l, _ := r601Gateway(t)
	answer := upstreamStream(func(w http.ResponseWriter, r *http.Request, _ string) {
		w.Header().Set("Content-Type", "application/json")
		fmt.Fprint(w, `{"model":"google/gemini-3.5-flash","choices":[{"index":0,"message":{"role":"assistant","content":"ok"},"finish_reason":"stop"}],"usage":{"cost":0.0000095}}`)
	})
	g.managed.responseWith.Store(&answer)
	code := sendBillabilityBody(t, g, "/v1/chat/completions", `{"model":"google/gemini-3.5-flash","max_tokens":64,"messages":[{"role":"user","content":"hi"}]}`)
	rows := l.Outbox("org-m")
	if code != 200 || len(rows) != 1 {
		t.Fatalf("status=%d rows=%v", code, rows)
	}
	rec := l.OwnedSettlement("org-m", rows[0]["request_id"])
	b, _ := json.Marshal(rec)
	if rec == nil || rec.Dimensional == nil || len(rec.Dimensional.InclusiveAttempts) != 1 {
		t.Fatalf("missing journal %s", b)
	}
	a := rec.Dimensional.InclusiveAttempts[0]
	if a.GenerationID != "" || a.Anomaly == "" {
		t.Fatalf("missing identity invented or unflagged: %+v", a)
	}
	if blocked, e := l.InclusiveQuarantined(context.Background(), "another-org", "google/gemini-3.5-flash"); e != nil || !blocked {
		t.Fatalf("missing identity not fenced: %t %v", blocked, e)
	}
}
