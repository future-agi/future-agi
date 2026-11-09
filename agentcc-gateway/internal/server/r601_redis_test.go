package server

import (
	"context"
	"encoding/json"
	"fmt"
	"strconv"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/billing"
	creditsplugin "github.com/futureagi/agentcc-gateway/internal/plugins/credits"
	"github.com/futureagi/agentcc-gateway/internal/redisstate"
)

func TestR601FamilyRedisHTTP(t *testing.T) {
	all, _ := installBundledCatalogue(t)
	for _, id := range r601IDs(all) {
		for _, surface := range managedSurfaces {
			for _, stream := range []bool{false, true} {
				for _, tc := range []struct {
					name, cost string
					want       int64
				}{{"actual", "0.0000095", 11}, {"missing", "missing", -1}, {"zero", "0", 0}, {"overhold", "100", 105000000}} {
					t.Run(fmt.Sprintf("%s/%s/%t/%s", id, surface, stream, tc.name), func(t *testing.T) {
						prefix := fmt.Sprintf("test-r601-%d:", time.Now().UnixNano())
						client := r7AgentRedis(t, prefix)
						g, _, _ := r601Gateway(t)
						store := redisstate.NewManagedCreditStore(client, prefix)
						g.ledger = creditsplugin.NewOrgLedger(store)
						g.credits.SetOrgLedger(g.ledger, true)
						g.srv.EnableManaged(ManagedSettings{Catalog: g.srv.handlers.catalog, Ledger: g.ledger, MarkupPercent: 5, RequireCredits: true})
						if _, e := store.Grant(context.Background(), "org-m", billing.GrantPush{GrantedTotalMicros: 1000_000_000, Version: 1}); e != nil {
							t.Fatal(e)
						}
						answer := r601Response(tc.cost, stream)
						g.managed.responseWith.Store(&answer)
						g.managed.streamWith.Store(&answer)
						body := fmt.Sprintf(`{"model":%q,"max_tokens":64,"stream":%t,"messages":[{"role":"user","content":"generate"}]}`, id, stream)
						status := sendBillabilityBody(t, g, surface, body)
						rows, e := client.Redis().XRange(context.Background(), prefix+"mc:{org-m}:outbox", "-", "+").Result()
						if e != nil {
							t.Fatal(e)
						}
						if id == "google/gemini-2.5-flash-image" {
							if status != 410 || len(rows) != 0 || g.managed.calls() != 0 {
								t.Fatal("retired listing reached upstream")
							}
							return
						}
						if status != 200 || len(rows) != 1 || g.managed.calls() != 1 {
							t.Fatalf("status=%d rows=%d calls=%d", status, len(rows), g.managed.calls())
						}
						rec := rows[0].Values
						hold, _ := strconv.ParseInt(fmt.Sprint(rec["hold_micros"]), 10, 64)
						want := tc.want
						if want < 0 {
							want = hold
						}
						acct, e := store.Account(context.Background(), "org-m")
						if e != nil || acct.HeldMicros != 0 || acct.SpentMicros != want || rec["billed_micros"] != fmt.Sprint(want) || rec["v"] != "1" || rec["dimensions"] != nil {
							t.Fatalf("wrong wallet projection acct=%+v row=%v err=%v", acct, rec, e)
						}
						raw, e := client.Redis().Get(context.Background(), prefix+"mc:{org-m}:owned:"+fmt.Sprint(rec["request_id"])).Bytes()
						if e != nil {
							t.Fatal(e)
						}
						var owned billing.OwnedRequest
						if e = json.Unmarshal(raw, &owned); e != nil {
							t.Fatal(e)
						}
						attempts := owned.Record.Dimensional.InclusiveAttempts
						if len(attempts) != 1 || attempts[0].GenerationID != "gen-r601" || attempts[0].ServedModel != id || owned.Terminal == "" {
							t.Fatalf("missing durable evidence %+v", owned)
						}
						if tc.name == "overhold" {
							if q, e := store.InclusiveQuarantined(context.Background(), "org-m", id); e != nil || !q {
								t.Fatal("overhold not atomically quarantined")
							}
						}
					})
				}
			}
		}
	}
}
