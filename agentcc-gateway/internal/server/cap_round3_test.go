package server

// Permanent regressions from an earlier verification round. Native-adapter
// presence is deliberately tested and fixed in its separate workstream.
import (
	"context"
	"fmt"
	"net/http"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// A reported actual outside the ledger's representable range is established
// spend above the cap. It must not leave capacity open as if only the quote
// were spent. A within-range overshoot is the control.
func TestR3ReportedChargeBeyondLedgerRange(t *testing.T) {
	for _, runOnly := range []bool{false, true} {
		for _, charge := range []string{"1.5", "2000000000"} {
			t.Run(fmt.Sprintf("byok/run=%v/cost=%s", runOnly, charge), func(t *testing.T) {
				g, client, prefix := capComposedGateway(t)
				cfg := g.tenants.Get("org-m")
				headers := map[string]string{}
				if runOnly {
					limit := 1.0
					cfg.Budgets.Agents, cfg.Budgets.KeyIDs = nil, nil
					cfg.Routing.Agents.MaxRunUSD = &limit
					headers["x-agentcc-run-id"] = "huge-charge"
				}
				cfg.Providers = map[string]*tenant.ProviderConfig{"operator": {APIKey: "synthetic-org-key", APIFormat: "openai", Models: []string{"gpt-4o"}, Enabled: true}}
				g.tenants.Set("org-m", cfg)
				answer := upstreamStream(func(w http.ResponseWriter, r *http.Request, model string) {
					w.Header().Set("Content-Type", "application/json")
					fmt.Fprintf(w, `{"id":"huge","object":"chat.completion","model":%q,"choices":[{"index":0,"message":{"role":"assistant","content":"x"},"finish_reason":"stop"}],"usage":{"prompt_tokens":1000,"completion_tokens":500,"total_tokens":1500,"cost":%s}}`, model, charge)
				})
				g.operator.responseWith.Store(&answer)
				first := g.chat(t, chatBody("gpt-4o"), headers)
				if first.Code != http.StatusOK || g.operator.calls() != 1 {
					t.Fatalf("not dispatched: %d %s", first.Code, first.Body)
				}
				ctx := context.Background()
				if runOnly {
					base := prefix + "abud:{org-m}:huge-charge:"
					t.Logf("AFTER-FIRST held=%s spent=%s", client.Redis().Get(ctx, base+"held").Val(), client.Redis().Get(ctx, base+"spent").Val())
				} else {
					t.Logf("AFTER-FIRST ledger=%v", client.Redis().HGetAll(ctx, prefix+"bhold:{org-m}:"+time.Now().UTC().Format("2006-01-02")+":state").Val())
				}
				g.operator.responseWith.Store(nil)
				second := g.chat(t, chatBody("gpt-4o"), headers)
				t.Logf("first=%d second=%d calls=%d body=%s", first.Code, second.Code, g.operator.calls(), second.Body)
				if second.Code == http.StatusOK || g.operator.calls() != 1 {
					t.Errorf("established actual above the cap left capacity open: second=%d calls=%d", second.Code, g.operator.calls())
				}
			})
		}
	}
}

// A strict run counter in a non-canonical numeric form (for example a
// float-formatted restore, numerically equal) must be refused, not used for
// admission while settlement silently fails to record actual spend.
func TestR3StrictRunNonIntegerSpentEndToEnd(t *testing.T) {
	for _, corrupt := range []bool{false, true} {
		t.Run(fmt.Sprintf("corrupt=%v", corrupt), func(t *testing.T) {
			g, client, prefix := capComposedGateway(t)
			cfg := g.tenants.Get("org-m")
			limit := 1.0
			cfg.Budgets.Agents, cfg.Budgets.KeyIDs = nil, nil
			cfg.Routing.Agents.MaxRunUSD = &limit
			cfg.Providers = map[string]*tenant.ProviderConfig{"operator": {APIKey: "synthetic-org-key", APIFormat: "openai", Models: []string{"gpt-4o"}, Enabled: true}}
			g.tenants.Set("org-m", cfg)
			answer := upstreamStream(func(w http.ResponseWriter, r *http.Request, model string) {
				w.Header().Set("Content-Type", "application/json")
				fmt.Fprintf(w, `{"id":"run","object":"chat.completion","model":%q,"choices":[{"index":0,"message":{"role":"assistant","content":"x"},"finish_reason":"stop"}],"usage":{"prompt_tokens":1000,"completion_tokens":500,"total_tokens":1500,"cost":0.3}}`, model)
			})
			g.operator.responseWith.Store(&answer)
			headers := map[string]string{"x-agentcc-run-id": "float-restore"}
			first := g.chat(t, chatBody("gpt-4o"), headers)
			base := prefix + "abud:{org-m}:float-restore:"
			ctx := context.Background()
			if first.Code != http.StatusOK || client.Redis().Get(ctx, base+"spent").Val() != "300000" {
				t.Fatalf("control settlement: code=%d spent=%s body=%s", first.Code, client.Redis().Get(ctx, base+"spent").Val(), first.Body)
			}
			if corrupt {
				client.Redis().Set(ctx, base+"spent", "300000.0", 0)
			}
			admitted := 1
			for i := 0; i < 8; i++ {
				w := g.chat(t, chatBody("gpt-4o"), headers)
				if w.Code == http.StatusOK {
					admitted++
				}
			}
			t.Logf("admitted=%d calls=%d spent=%s held=%s terminal=%d", admitted, g.operator.calls(), client.Redis().Get(ctx, base+"spent").Val(), client.Redis().Get(ctx, base+"held").Val(), client.Redis().HLen(ctx, base+"terminal").Val())
			// $1 run limit, $0.30 per call: at most four dispatches in total.
			if g.operator.calls() > 4 {
				t.Errorf("run limit exceeded through malformed strict counter: calls=%d spent=%s", g.operator.calls(), client.Redis().Get(ctx, base+"spent").Val())
			}
		})
	}
}
