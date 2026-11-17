package server

import (
	"context"
	"fmt"
	"net/http"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

func TestStrictRunUnknownUsageCannotReopenCapacity(t *testing.T) {
	for _, mode := range []string{"missing-usage", "lost-response", "deadline"} {
		t.Run(mode, func(t *testing.T) {
			g, client, prefix := capComposedGateway(t)
			cfg := g.tenants.Get("org-m")
			limit := 0.05
			cfg.Budgets.Agents = nil
			cfg.Budgets.KeyIDs = nil
			cfg.Routing.Agents.MaxRunUSD = &limit
			cfg.Providers = map[string]*tenant.ProviderConfig{"operator": {APIKey: "synthetic-org-key", APIFormat: "openai", Models: []string{"gpt-4o"}, Enabled: true}}
			g.tenants.Set("org-m", cfg)
			answer := upstreamStream(func(w http.ResponseWriter, r *http.Request, model string) {
				switch mode {
				case "missing-usage":
					w.Header().Set("Content-Type", "application/json")
					fmt.Fprintf(w, `{"id":"accepted","object":"chat.completion","model":%q,"choices":[{"index":0,"message":{"role":"assistant","content":"completed without metering"},"finish_reason":"stop"}]}`, model)
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
			headers := map[string]string{"x-agentcc-run-id": "run-unknown"}
			if mode == "deadline" {
				headers["x-agentcc-timeout"] = "100ms"
			}
			first := g.chat(t, chatBody("gpt-4o"), headers)
			if g.operator.calls() != 1 {
				t.Fatalf("not dispatched: %d %s", first.Code, first.Body)
			}
			g.operator.responseWith.Store(nil)
			second := g.chat(t, chatBody("gpt-4o"), headers)
			if second.Code != http.StatusServiceUnavailable || g.operator.calls() != 1 {
				t.Fatalf("unknown run spend reopened: %d calls=%d %s", second.Code, g.operator.calls(), second.Body)
			}
			base := prefix + "abud:{org-m}:run-unknown:"
			if held := client.Redis().Get(context.Background(), base+"held").Val(); held == "0" || held == "" {
				t.Fatal("run reservation lost")
			}
			if spent := client.Redis().Get(context.Background(), base+"spent").Val(); spent != "0" {
				t.Fatalf("unknown usage invented actuals: %s", spent)
			}
			if g.account(t, "org-m").SpentMicros != 0 || len(g.outbox("org-m")) != 0 {
				t.Fatal("BYOK changed wallet")
			}
		})
	}
}
