package server

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"reflect"
	"sort"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/catalog"
	creditsplugin "github.com/futureagi/agentcc-gateway/internal/plugins/credits"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// These HTTP reds also compile on e933462ee3: reflection only selects the new
// opt-in profile when it exists. At the base every paid family ID refuses before
// dispatch, a behavioural red rather than a missing-symbol compile failure.
func r601Gateway(t *testing.T) (*managedTestGateway, *r601Owner, map[string]*catalog.Model) {
	t.Helper()
	all, install := installBundledCatalogue(t)
	for _, m := range all {
		flag := reflect.ValueOf(m).Elem().FieldByName("InclusiveEnabled")
		if flag.IsValid() {
			flag.SetBool(true)
		}
	}
	g := newManagedTestGateway(t, managedOptIn())
	install(t, g)
	l := newR601Owner()
	g.ledger = creditsplugin.NewOrgLedger(l)
	g.credits.SetOrgLedger(g.ledger, true)
	g.ledger.SetMicros("org-m", 1000_000_000)
	return g, l, all
}

func r601IDs(all map[string]*catalog.Model) []string {
	var ids []string
	at := time.Date(2026, 10, 27, 12, 0, 0, 0, time.UTC)
	for id, m := range all {
		if m.Servable(at) && ((m.Prices.Image > 0 && strings.Contains(id, "google/gemini")) || strings.HasPrefix(id, "google/lyria-3-")) {
			ids = append(ids, id)
		}
	}
	sort.Strings(ids)
	return ids
}

func r601Response(cost string, stream bool) upstreamStream {
	return func(w http.ResponseWriter, _ *http.Request, model string) {
		usage := `{"prompt_tokens":0,"completion_tokens":0}`
		if cost != "missing" {
			usage = `{"prompt_tokens":0,"completion_tokens":0,"cost":` + cost + `}`
		}
		if stream {
			w.Header().Set("Content-Type", "text/event-stream")
			fmt.Fprintf(w, "data: {\"id\":\"gen-r601\",\"model\":%q,\"choices\":[{\"index\":0,\"delta\":{\"role\":\"assistant\",\"content\":\"generated\"}}]}\n\n", model)
			fmt.Fprintf(w, "data: {\"id\":\"gen-r601\",\"model\":%q,\"choices\":[],\"usage\":%s}\n\ndata: [DONE]\n\n", model, usage)
		} else {
			w.Header().Set("Content-Type", "application/json")
			fmt.Fprintf(w, `{"id":"gen-r601","model":%q,"choices":[{"index":0,"message":{"role":"assistant","content":"generated"},"finish_reason":"stop"}],"usage":%s}`, model, usage)
		}
	}
}

func TestR601ImageFamilyHTTP(t *testing.T) {
	all, _ := installBundledCatalogue(t)
	ids := r601IDs(all)
	if len(ids) != 29 {
		t.Fatalf("family count=%d want 29", len(ids))
	}
	for _, id := range ids {
		for _, tc := range []struct {
			name, cost string
			want       int64
		}{
			{"actual", "0.0000095", 11}, {"zero", "0", 0}, {"missing", "missing", -1}, {"invalid", "-0.001", -1}, {"over_hold", "100", 105000000},
		} {
			for _, stream := range []bool{false, true} {
				for _, surface := range managedSurfaces {
					t.Run(fmt.Sprintf("%s/%s/%t/%s", id, tc.name, stream, surface), func(t *testing.T) {
						g, l, _ := r601Gateway(t)
						answer := r601Response(tc.cost, stream)
						g.managed.responseWith.Store(&answer)
						g.managed.streamWith.Store(&answer)
						body := fmt.Sprintf(`{"model":%q,"max_tokens":64,"stream":%t,"messages":[{"role":"user","content":"make something"}]}`, id, stream)
						status := sendBillabilityBody(t, g, surface, body)
						recs := l.Outbox("org-m")
						if id == "google/gemini-2.5-flash-image" {
							// The listing has no expiry, but the independent shutdown
							// calendar retired it on 2026-10-02. Never waive lifecycle.
							if status != 410 || g.managed.calls() != 0 || len(recs) != 0 {
								t.Fatalf("calendar refusal status=%d records=%v", status, recs)
							}
							return
						}
						if status != 200 || g.managed.calls() != 1 || len(recs) != 1 {
							t.Fatalf("status=%d calls=%d records=%v", status, g.managed.calls(), recs)
						}
						rec := recs[0]
						hold, _ := strconv.ParseInt(rec["hold_micros"], 10, 64)
						want := tc.want
						if want < 0 {
							want = hold
						}
						acct, _ := l.Account(context.Background(), "org-m")
						if hold <= 0 || acct.HeldMicros != 0 || acct.SpentMicros != want || rec["billed_micros"] != strconv.FormatInt(want, 10) || rec["kind"] != "spend" || rec["v"] != "1" {
							t.Fatalf("want=%d account=%+v record=%v", want, acct, rec)
						}
						if _, exists := rec["dimensions"]; exists {
							t.Fatal("v2 evidence leaked into BL-LEDGER v1 drain")
						}
						evidence := l.OwnedSettlement("org-m", rec["request_id"])
						if evidence == nil {
							t.Fatal("durable evidence missing")
						}
						rec = evidence.FieldMap()
						var dim struct {
							State    string
							Attempts []struct {
								Model, ServedModel, GenerationID string
								Estimated                        bool
							} `json:"inclusive_attempts"`
						}
						// Assert identities using the raw wire names, never client-rewritten IDs.
						var raw map[string]json.RawMessage
						_ = json.Unmarshal([]byte(rec["dimensions"]), &raw)
						_ = json.Unmarshal([]byte(rec["dimensions"]), &dim)
						var attempts []map[string]any
						_ = json.Unmarshal(raw["inclusive_attempts"], &attempts)
						if len(attempts) != 1 || attempts[0]["generation_id"] != "gen-r601" || attempts[0]["served_model"] != id {
							t.Fatalf("lost attempt identity: %s", rec["dimensions"])
						}
						if tc.want < 0 && dim.State != "estimated_pending_reconciliation" {
							t.Fatalf("estimate unlabeled: %s", rec["dimensions"])
						}
						if tc.name == "over_hold" {
							if attempts[0]["anomaly"] == nil {
								t.Fatal("over-hold anomaly missing")
							}
							_ = sendBillabilityBody(t, g, surface, body)
							if g.managed.calls() != 1 || len(l.Outbox("org-m")) != 1 {
								t.Fatal("quarantined listing dispatched")
							}
						}
					})
				}
			}
		}
	}
}

func TestR601ExpiredFusionAndBYOK(t *testing.T) {
	g, l, all := r601Gateway(t)
	expired := 0
	for id, m := range all {
		if m.ExpirationDate == "" || m.Servable(time.Date(2026, 10, 27, 12, 0, 0, 0, time.UTC)) {
			continue
		}
		expired++
		body := fmt.Sprintf(`{"model":%q,"max_tokens":64,"messages":[{"role":"user","content":"hi"}]}`, id)
		if code := sendBillabilityBody(t, g, managedSurfaces[0], body); code == 200 {
			t.Errorf("expired ID served: %s", id)
		}
	}
	if expired != 8 {
		t.Fatalf("expired=%d", expired)
	}
	body := `{"model":"openrouter/fusion","max_tokens":64,"messages":[{"role":"user","content":"hi"}]}`
	if code := sendBillabilityBody(t, g, managedSurfaces[0], body); code == 200 {
		t.Fatal("Fusion served without inclusive child/search contract")
	}
	if g.managed.calls() != 0 || len(l.Outbox("org-m")) != 0 {
		t.Fatal("refused ID dispatched or charged")
	}
	for _, id := range r601IDs(all) {
		t.Run("BYOK/"+id, func(t *testing.T) {
			g, l, _ := r601Gateway(t)
			g.tenants.Set("org-m", &tenant.OrgConfig{Providers: map[string]*tenant.ProviderConfig{"openrouter": {APIKey: "synthetic-user-key", APIFormat: "openai", Models: []string{id}, Enabled: true}}})
			body := fmt.Sprintf(`{"model":%q,"max_tokens":64,"messages":[{"role":"user","content":"hi"}]}`, id)
			w := g.chat(t, body, nil)
			if id == "google/gemini-2.5-flash-image" {
				if w.Code != 410 || len(l.Outbox("org-m")) != 0 || g.managed.calls() != 0 {
					t.Fatalf("BYOK calendar refusal: %d", w.Code)
				}
				return
			}
			if w.Code != 200 || w.Header().Get("x-agentcc-billing-mode") != "byok" {
				t.Fatalf("BYOK status=%d body=%s", w.Code, w.Body)
			}
			acct, _ := l.Account(context.Background(), "org-m")
			if acct.SpentMicros != 0 || acct.HeldMicros != 0 || len(l.Outbox("org-m")) != 0 || strings.Contains(w.Body.String(), "0.00735") {
				t.Fatalf("BYOK credits/fee applied: %+v %s", acct, w.Body)
			}
		})
	}
}
