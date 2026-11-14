package budget

import (
	"context"
	"fmt"
	"math"
	"os"
	"testing"
	"time"

	budgetpkg "github.com/futureagi/agentcc-gateway/internal/budget"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/modeldb"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	costplugin "github.com/futureagi/agentcc-gateway/internal/plugins/cost"
	"github.com/futureagi/agentcc-gateway/internal/redisstate"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// A Jev decision call through OpenRouter is priced from the catalogue at
// $0.042 per 1M input tokens, and the budgets count that price: the global
// tracker, the org's own counter and, when Redis is configured, the shared
// ledger. Before the catalogue priced it, the call counted as free.
func TestBudgetRecordsOpenRouterJevDecisionCost(t *testing.T) {
	const org, want = "org-jev", 0.042
	backends := map[string]func(t *testing.T) *redisstate.BudgetStore{"memory": nil}
	if addr := os.Getenv("TEST_REDIS_ADDR"); addr != "" {
		backends["redis"] = func(t *testing.T) *redisstate.BudgetStore {
			c, err := redisstate.NewClient(redisstate.Config{Address: addr, Timeout: time.Second})
			if err != nil {
				t.Fatal(err)
			}
			prefix := fmt.Sprintf("test:jevbudget:%d:", time.Now().UnixNano())
			t.Cleanup(func() {
				keys, _ := c.Redis().Keys(context.Background(), prefix+"*").Result()
				if len(keys) > 0 {
					c.Redis().Del(context.Background(), keys...)
				}
				c.Close()
			})
			return redisstate.NewBudgetStore(c, prefix)
		}
	} else {
		t.Log("TEST_REDIS_ADDR unset: the Redis ledger case is skipped")
	}
	for name, newLedger := range backends {
		t.Run(name, func(t *testing.T) {
			store := tenant.NewStore()
			store.Set(org, &tenant.OrgConfig{Budgets: &tenant.BudgetsConfig{Enabled: true, DefaultPeriod: "monthly", OrgLimit: 100}})
			tracker := budgetpkg.NewTracker(config.BudgetsConfig{Enabled: true, DefaultPeriod: "total", Org: &config.BudgetLevelConfig{Limit: 100}})
			db := modeldb.New(modeldb.BundledModels, nil)
			p := New(tracker, NewPricingLookup(nil), true, store)
			var ledger *redisstate.BudgetStore
			if newLedger != nil {
				ledger = newLedger(t)
				p.SetRedisBudget(ledger)
			}
			engine := pipeline.NewEngine(p, costplugin.New(true, func() *modeldb.ModelDB { return db }, nil, nil))

			rc := models.AcquireRequestContext()
			defer rc.Release()
			rc.Model, rc.Provider, rc.EndpointType = "openrouter/typesafe/jev-1.13", "openrouter", "decision"
			rc.DecisionRequest = &models.DecisionRequest{Model: rc.Model, UpstreamModel: "typesafe/jev-1.13"}
			rc.Metadata[tenant.MetadataKeyOrgID] = org
			err := engine.Process(context.Background(), rc, func(_ context.Context, rc *models.RequestContext) error {
				rc.ResolvedModel = "typesafe/jev-1.13-20260917"
				rc.Response = &models.ChatCompletionResponse{Usage: &models.Usage{PromptTokens: 1_000_000, TotalTokens: 1_000_000}}
				return nil
			})
			if err != nil {
				t.Fatalf("decision call refused: %v", err)
			}
			if got := rc.Metadata["cost"]; got != "0.042000" {
				t.Fatalf("cost = %q, want 0.042000", got)
			}

			near := func(got float64) bool { return math.Abs(got-want) < 1e-9 }
			if got, _ := tracker.GetSpend("org", ""); !near(got) {
				t.Errorf("global tracker spend = %v, want %v", got, want)
			}
			counter := p.getOrgHierarchy(org).getOrCreateCounter("org", "")
			if got, _ := p.getCounterSpendWithRedis(counter, org, "org", "", "monthly", time.Now()); !near(got) {
				t.Errorf("org counter spend = %v, want %v", got, want)
			}
			if ledger != nil {
				got, _, ok := ledger.GetSpend(org, "org", "", "monthly")
				if !ok || !near(got) {
					t.Errorf("Redis ledger spend = %v (ok %v), want %v", got, ok, want)
				}
			}
		})
	}
}
