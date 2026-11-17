package server

import (
	"context"
	"encoding/json"
	"net/http"
	"reflect"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/catalog"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// The real route guard must stop inclusive pricing before daily/run plugins,
// the credits outbox, or the loopback provider can do any work.
func TestUnionStrictInclusiveHTTPZeroWork(t *testing.T) {
	for _, model := range []string{"google/lyria-3-pro-preview", "google/gemini-3-pro-image-preview"} {
		for _, run := range []bool{false, true} {
			t.Run(model+map[bool]string{false: "/daily", true: "/daily-and-run"}[run], func(t *testing.T) {
				g, client, prefix := capComposedGateway(t)
				cfg := config.DefaultConfig()
				cfg.Managed.CatalogueFamilies = []string{"cat2"}
				if err := g.srv.handlers.catalog.Configure(catalog.OptionsFromConfig(cfg, []catalog.Source{{ProviderID: "openrouter", Type: catalog.SourceOpenRouter}})); err != nil {
					t.Fatal(err)
				}
				_, install := installBundledCatalogue(t)
				install(t, g)
				card, ok := g.srv.handlers.catalog.Load().PriceCard("openrouter", model, "")
				if !ok || card.Inclusive == nil {
					t.Fatal("fixture is not an admitted inclusive model")
				}
				policy := g.tenants.Get("org-m")
				zero := 0.0
				policy.Budgets.Agents["worker"].Limit = &zero
				for _, cap := range policy.Budgets.KeyIDs {
					cap.Limit = &zero
				}
				headers := map[string]string{}
				if run {
					policy.Routing.Agents.MaxRunUSD = &zero
					headers["x-agentcc-run-id"] = "union-zero-run"
				}
				g.tenants.Set("org-m", policy)
				before := g.account(t, "org-m")
				beforeEvents := g.outbox("org-m")
				beforeKeys, err := client.Redis().Keys(context.Background(), prefix+"*").Result()
				if err != nil {
					t.Fatal(err)
				}
				body, err := json.Marshal(&models.ChatCompletionRequest{Model: model, MaxTokens: newUnionInt(64), Messages: []models.Message{{Role: "user", Content: json.RawMessage(`"synthetic"`)}}})
				if err != nil {
					t.Fatal(err)
				}
				w := g.chat(t, string(body), headers)
				if w.Code != http.StatusPaymentRequired || errorCode(t, w) != "call_cap_unenforceable" {
					t.Fatalf("strict inclusive refusal: %d %s", w.Code, w.Body)
				}
				afterKeys, err := client.Redis().Keys(context.Background(), prefix+"*").Result()
				if err != nil || !reflect.DeepEqual(beforeKeys, afterKeys) {
					t.Fatalf("daily/run storage changed: before=%v after=%v error=%v", beforeKeys, afterKeys, err)
				}
				if g.managed.calls() != 0 || g.operator.calls() != 0 || !reflect.DeepEqual(before, g.account(t, "org-m")) || !reflect.DeepEqual(beforeEvents, g.outbox("org-m")) {
					t.Fatal("refusal dispatched or changed credits/outbox")
				}
			})
		}
	}
}

func newUnionInt(n int) *int { return &n }
