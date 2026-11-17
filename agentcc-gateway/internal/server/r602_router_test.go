package server

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/billing"
	creditsplugin "github.com/futureagi/agentcc-gateway/internal/plugins/credits"
	"github.com/futureagi/agentcc-gateway/internal/redisstate"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

var r602Routers = []string{"openrouter/auto", "openrouter/auto-beta", "openrouter/pareto-code", "openrouter/bodybuilder", "typesafe/jev-router", "nvidia/switchyard"}

func TestR602RouterRedisHTTP(t *testing.T) {
	for _, id := range r602Routers {
		for _, surface := range managedSurfaces {
			for _, stream := range []bool{false, true} {
				for _, tc := range []struct {
					name, cost, served string
					want               int64
					anomaly            bool
				}{
					{"actual", "0.0000095", "openai/o1-pro", 11, false},
					{"zero", "0", "openai/o1-pro", 0, false},
					{"missing", "missing", "openai/o1-pro", -1, false},
					{"invalid", "-1", "openai/o1-pro", -1, false},
					{"outside", "0.0000095", "synthetic/outside-snapshot", -1, true},
					{"overhold", "1000", "openai/o1-pro", 1050000000, true},
				} {
					t.Run(fmt.Sprintf("%s/%s/%t/%s", id, surface, stream, tc.name), func(t *testing.T) {
						prefix := fmt.Sprintf("test-r602-%d:", time.Now().UnixNano())
						client := r7AgentRedis(t, prefix)
						g, _, _ := r601Gateway(t)
						store := redisstate.NewManagedCreditStore(client, prefix)
						g.ledger = creditsplugin.NewOrgLedger(store)
						g.credits.SetOrgLedger(g.ledger, true)
						g.srv.EnableManaged(ManagedSettings{Catalog: g.srv.handlers.catalog, Ledger: g.ledger, MarkupPercent: 5, RequireCredits: true})
						if _, err := store.Grant(context.Background(), "org-m", billing.GrantPush{GrantedTotalMicros: 2000000000, Version: 1}); err != nil {
							t.Fatal(err)
						}
						answer := upstreamStream(func(w http.ResponseWriter, r *http.Request, _ string) { r601Response(tc.cost, stream)(w, r, tc.served) })
						g.managed.responseWith.Store(&answer)
						g.managed.streamWith.Store(&answer)
						body := fmt.Sprintf(`{"model":%q,"max_tokens":64,"stream":%t,"messages":[{"role":"user","content":"hello"}]}`, id, stream)
						code := sendBillabilityBody(t, g, surface, body)
						rows, err := client.Redis().XRange(context.Background(), prefix+"mc:{org-m}:outbox", "-", "+").Result()
						if err != nil || code != 403 || g.managed.calls() != 0 || len(rows) != 0 {
							t.Fatalf("router must refuse before work: code=%d calls=%d rows=%d err=%v", code, g.managed.calls(), len(rows), err)
						}
						acct, err := store.Account(context.Background(), "org-m")
						if err != nil || acct.HeldMicros != 0 || acct.SpentMicros != 0 {
							t.Fatalf("refused router changed credits: account=%+v err=%v", acct, err)
						}
						keys, err := client.Redis().Keys(context.Background(), prefix+"mc:{org-m}:owned:*").Result()
						if err != nil || len(keys) != 0 {
							t.Fatalf("refused router created dispatch ownership: keys=%v err=%v", keys, err)
						}
					})
				}
			}
		}
	}
}

func TestR602RouterBYOKAndCaps(t *testing.T) {
	for _, id := range r602Routers {
		t.Run(id, func(t *testing.T) {
			g, l, _ := r601Gateway(t)
			g.tenants.Set("org-m", &tenant.OrgConfig{Providers: map[string]*tenant.ProviderConfig{"openrouter": {APIKey: "synthetic-user-key", APIFormat: "openai", Models: []string{id}, Enabled: true}}})
			w := g.chat(t, fmt.Sprintf(`{"model":%q,"max_tokens":64,"messages":[{"role":"user","content":"hi"}]}`, id), nil)
			if w.Code != 200 || w.Header().Get("x-agentcc-billing-mode") != "byok" || len(l.Outbox("org-m")) != 0 {
				t.Fatalf("BYOK charged/refused %d %s", w.Code, w.Body)
			}
			for _, cap := range []string{"0", "65537"} {
				g, l, _ := r601Gateway(t)
				w := g.chat(t, fmt.Sprintf(`{"model":%q,"max_tokens":%s,"messages":[{"role":"user","content":"hi"}]}`, id, cap), nil)
				if w.Code == 200 || g.managed.calls() != 0 || len(l.Outbox("org-m")) != 0 {
					t.Fatal("invalid cap dispatched")
				}
			}
		})
	}
}

func TestR602LyriaGenerationListing(t *testing.T) {
	g, _, _ := r601Gateway(t)
	r := httptest.NewRequest(http.MethodGet, "/v1/models", nil)
	r.Header.Set("Authorization", "Bearer "+managedOrgKey)
	w := httptest.NewRecorder()
	g.srv.httpServer.Handler.ServeHTTP(w, r)
	var doc struct {
		Data []struct {
			ID         string         `json:"id"`
			Pricing    map[string]any `json:"pricing"`
			Generation map[string]any `json:"generation_pricing"`
		}
	}
	if w.Code != 200 || json.Unmarshal(w.Body.Bytes(), &doc) != nil {
		t.Fatal("invalid list")
	}
	found := 0
	for _, m := range doc.Data {
		want := ""
		switch m.ID {
		case "google/lyria-3-clip-preview":
			want = "0.042000"
		case "google/lyria-3-pro-preview":
			want = "0.084000"
		}
		if want == "" {
			continue
		}
		found++
		if m.Pricing != nil || m.Generation["unit"] != "generation" || m.Generation["currency"] != "USD" || m.Generation["amount"] != want {
			t.Fatalf("paid generation advertised incorrectly: %+v", m)
		}
	}
	if found != 2 {
		t.Fatalf("found %d Lyria listings", found)
	}
}
