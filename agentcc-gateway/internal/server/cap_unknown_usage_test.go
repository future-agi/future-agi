package server

import (
	"context"
	"fmt"
	"net/http"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// A positive-control upstream has accepted the request, but provides no usage.
// Wallet charging is intentionally not asserted: the cap must not confuse the
// absence of a debit with evidence of zero model spend.
func TestCapBYOKUnknownUsageCannotReopenCapacity(t *testing.T) {
	for _, mode := range []string{"missing-usage", "lost-response", "deadline"} {
		t.Run(mode, func(t *testing.T) {
			g, client, prefix := capComposedGateway(t)
			cfg := g.tenants.Get("org-m")
			limit := 0.05
			cfg.Budgets.Agents["worker"].Limit = &limit
			cfg.Providers = map[string]*tenant.ProviderConfig{"operator": {APIKey: "synthetic-org-key", APIFormat: "openai", Models: []string{"gpt-4o"}, Enabled: true}}
			g.tenants.Set("org-m", cfg)
			answer := upstreamStream(func(w http.ResponseWriter, r *http.Request, model string) {
				switch mode {
				case "missing-usage":
					w.Header().Set("Content-Type", "application/json")
					fmt.Fprintf(w, `{"id":"accepted","object":"chat.completion","model":%q,"choices":[{"index":0,"message":{"role":"assistant","content":"completed work without metering"},"finish_reason":"stop"}]}`, model)
				case "lost-response":
					conn, _, err := w.(http.Hijacker).Hijack()
					if err != nil {
						panic(err)
					}
					conn.Close()
				case "deadline":
					<-r.Context().Done()
				}
			})
			g.operator.responseWith.Store(&answer)
			headers := map[string]string{}
			if mode == "deadline" {
				headers["x-agentcc-timeout"] = "100ms"
			}
			first := g.chat(t, chatBody("gpt-4o"), headers)
			if g.operator.calls() != 1 {
				t.Fatalf("control did not reach provider: status=%d body=%s calls=%d", first.Code, first.Body, g.operator.calls())
			}
			state := prefix + "bhold:{org-m}:" + time.Now().UTC().Format("2006-01-02") + ":state"
			t.Logf("first_status=%d state=%v", first.Code, client.Redis().HGetAll(context.Background(), state).Val())
			// Remove the failure on the upstream. Any second dispatch demonstrates that
			// uncertain admitted work was erased, rather than kept held or fenced.
			g.operator.responseWith.Store(nil)
			second := g.chat(t, chatBody("gpt-4o"), nil)
			t.Logf("second_status=%d provider_calls=%d state=%v", second.Code, g.operator.calls(), client.Redis().HGetAll(context.Background(), state).Val())
			if second.Code != http.StatusServiceUnavailable {
				t.Errorf("uncertain cap status=%d", second.Code)
			}
			account := g.account(t, "org-m")
			if account.SpentMicros != 0 || account.HeldMicros != 0 || len(g.outbox("org-m")) != 0 {
				t.Fatalf("uncertain BYOK changed wallet: %+v", account)
			}
			if held := client.Redis().HGet(context.Background(), state, "held:agent:worker").Val(); held == "0" || held == "" {
				t.Fatal("uncertain reservation lost")
			}
			if g.operator.calls() != 1 {
				t.Errorf("unknown actual reopened the cap: first=%d next=%d provider_calls=%d", first.Code, second.Code, g.operator.calls())
			}
		})
	}
}
