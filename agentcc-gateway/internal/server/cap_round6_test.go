package server

import (
	"context"
	"fmt"
	"net/http"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// Single-key-loss proof, not the separately owed native-adapter presence case.
// The ordinary synthetic OpenAI-compatible adapter correctly marks a response
// without usage uncertain. Removing just its uncertainty set must not undo it.
func TestSolR6StrictLostUncertaintySetEndToEnd(t *testing.T) {
	for _, refence := range []bool{false, true} {
		t.Run(fmt.Sprintf("refence=%v", refence), func(t *testing.T) {
			g, client, prefix := capComposedGateway(t)
			cfg := g.tenants.Get("org-m")
			limit := 1.0
			cfg.Budgets.Agents = nil
			cfg.Budgets.KeyIDs = nil
			cfg.Routing.Agents.MaxRunUSD = &limit
			cfg.Providers = map[string]*tenant.ProviderConfig{"operator": {APIKey: "synthetic-org-key", APIFormat: "openai", Models: []string{"gpt-4o"}, Enabled: true}}
			g.tenants.Set("org-m", cfg)
			answer := upstreamStream(func(w http.ResponseWriter, r *http.Request, model string) {
				w.Header().Set("Content-Type", "application/json")
				fmt.Fprintf(w, `{"id":"unmetered","object":"chat.completion","model":%q,"choices":[{"index":0,"message":{"role":"assistant","content":"accepted without metering"},"finish_reason":"stop"}]}`, model)
			})
			g.operator.responseWith.Store(&answer)
			headers := map[string]string{"x-agentcc-run-id": "sol-r6-uncertainty-loss"}
			first := g.chat(t, chatBody("gpt-4o"), headers)
			if first.Code != http.StatusOK || g.operator.calls() != 1 {
				t.Fatalf("control status=%d calls=%d", first.Code, g.operator.calls())
			}
			ctx := context.Background()
			rdb := client.Redis()
			base := prefix + "abud:{org-m}:sol-r6-uncertainty-loss:"
			holds := rdb.HKeys(ctx, base+"holds").Val()
			if len(holds) != 1 || rdb.SCard(ctx, base+"limit:uncertain").Val() != 1 || rdb.HLen(ctx, base+"terminal").Val() != 0 || rdb.ZCard(ctx, base+"holdexp").Val() != 0 || rdb.Get(ctx, base+"limit:strict").Val() != "1" {
				t.Fatal("control did not leave an intact durable fenced hold")
			}
			if err := rdb.Del(ctx, base+"limit:uncertain").Err(); err != nil {
				t.Fatal(err)
			}
			if refence {
				// Repeat only the original fence's two writes, as an independent positive
				// control; no counter, hold or initialization marker is changed.
				if err := rdb.SAdd(ctx, base+"limit:uncertain", holds[0]).Err(); err != nil {
					t.Fatal(err)
				}
			}
			g.operator.responseWith.Store(nil)
			second := g.chat(t, chatBody("gpt-4o"), headers)
			t.Logf("result=first:%d second:%d calls:%d refence:%v originalHoldRetained:%v strictMarker:%q", first.Code, second.Code, g.operator.calls(), refence, rdb.HExists(ctx, base+"holds", holds[0]).Val(), rdb.Get(ctx, base+"limit:strict").Val())
			if second.Code != http.StatusServiceUnavailable || g.operator.calls() != 1 {
				t.Error("deleting only strict uncertainty set allowed another upstream dispatch")
			}
			if !rdb.HExists(ctx, base+"holds", holds[0]).Val() {
				t.Error("original unresolved hold was released")
			}
		})
	}
}
