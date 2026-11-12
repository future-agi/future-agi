package budget

// This fixture is copied onto the immutable base on the cloud VM. It uses
// only base symbols: missing fields are decoded by the existing tenant JSON
// path, and a real admission (not a compiler error) must make the red.
import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"strconv"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/billing"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/plugins/agentbudget"
	"github.com/futureagi/agentcc-gateway/internal/plugins/credits"
	"github.com/futureagi/agentcc-gateway/internal/redisstate"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

func g8RedConfig(t *testing.T, enabled bool, run bool) *tenant.OrgConfig {
	t.Helper()
	runField := ""
	if run {
		runField = `,"max_run_usd":0.005`
	}
	wire := fmt.Sprintf(`{"meta":{"version":1},"budgets":{"enabled":true,"admission_caps_enabled":%t,"agents":{"agent-a":{"limit":0.005,"period":"daily"}},"key_ids":{"key-a":{"limit":0.005,"period":"daily"}}},"routing":{"agents":{"key_bindings":{"key-a":"agent-a"}%s}}}`, enabled, runField)
	var cfg tenant.OrgConfig
	if err := json.Unmarshal([]byte(wire), &cfg); err != nil {
		t.Fatal(err)
	}
	return &cfg
}

func g8RedRequest(t *testing.T) *models.RequestContext {
	t.Helper()
	rc := models.AcquireRequestContext()
	t.Cleanup(rc.Release)
	rc.RequestID = fmt.Sprintf("g8-red-%s-%d", t.Name(), time.Now().UnixNano())
	rc.Metadata[tenant.MetadataKeyOrgID] = "g8-synthetic-org"
	rc.Metadata["auth_key_id"] = "key-a"
	rc.Metadata["auth_key_name"] = "display-name-not-authority"
	rc.Metadata["billing_mode"] = models.BillingModeManaged
	rc.EndpointType, rc.Provider, rc.Model = "chat", "synthetic", "priced-chat"
	rc.Agent.AgentID = "agent-a"
	rc.Agent.BudgetScope = rc.RequestID
	maxTokens := 256
	rc.Request = &models.ChatCompletionRequest{Model: rc.Model, MaxTokens: &maxTokens,
		Messages: []models.Message{{Role: "user", Content: json.RawMessage(`"synthetic"`)}}}
	rc.Billing.EndpointSet = []models.EndpointRef{{ID: "synthetic", Price: models.PriceCard{Request: 0.01, Source: models.PriceSourceCatalog}}}
	return rc
}

func g8RedRedis(t *testing.T) (*redisstate.Client, string) {
	t.Helper()
	addr := os.Getenv("TEST_REDIS_ADDR")
	if addr == "" {
		t.Fatal("red requires loopback Redis; missing Redis does not count as red")
	}
	db, err := strconv.Atoi(os.Getenv("TEST_REDIS_DB"))
	if err != nil {
		t.Fatal(err)
	}
	client, err := redisstate.NewClient(redisstate.Config{Address: addr, DB: db, Timeout: time.Second})
	if err != nil {
		t.Fatal(err)
	}
	prefix := fmt.Sprintf("test:g8-red:%s:%d:", t.Name(), time.Now().UnixNano())
	t.Cleanup(func() {
		it := client.Redis().Scan(context.Background(), 0, prefix+"*", 100).Iterator()
		for it.Next(context.Background()) {
			client.Redis().Del(context.Background(), it.Val())
		}
		client.Close()
	})
	return client, prefix
}

func g8RedWallet(t *testing.T, rc *models.RequestContext) *credits.Plugin {
	t.Helper()
	ledger := credits.NewOrgLedger(billing.NewMemoryLedger())
	ledger.SetMicros(rc.Metadata[tenant.MetadataKeyOrgID], 1_000_000)
	p := credits.New(true, nil)
	p.SetOrgLedger(ledger, true)
	p.SetPrices(func(string, string, string) (models.PriceCard, bool) {
		return rc.Billing.EndpointSet[0].Price, true
	})
	// Check the synthetic wallet separately so an insufficient-credit refusal
	// cannot masquerade as the required budget red.
	if res := p.ProcessRequest(context.Background(), rc); res.Error != nil {
		t.Fatalf("wallet control: %v", res.Error)
	}
	if !rc.Billing.Reserved || rc.Billing.HoldMicros != 10_500 {
		t.Fatalf("wallet control: reserved=%v hold=%d; want 10500", rc.Billing.Reserved, rc.Billing.HoldMicros)
	}
	return p
}

func TestG8CapBehavioralRedAgentAndKey(t *testing.T) {
	cfg := g8RedConfig(t, true, false)
	store := tenant.NewStore()
	store.Set("g8-synthetic-org", cfg)
	client, prefix := g8RedRedis(t)
	p := New(nil, NewPricingLookup(nil), false, store)
	p.SetRedisBudget(redisstate.NewBudgetStore(client, prefix+"budget:"))
	rc := g8RedRequest(t)
	g8RedWallet(t, rc)
	res := p.ProcessRequest(context.Background(), rc)
	if res.Error == nil {
		t.Fatal("G8_BEHAVIORAL_RED: admitted a 10500-micro priced chat against 5000-micro configured agent/key caps")
	}
	if res.Error.Status != 429 || res.Error.Code != "org_budget_exceeded" {
		t.Fatalf("want budget 429 org_budget_exceeded, got %v", res.Error)
	}
}

func TestG8CapBehavioralRedConfiguredRun(t *testing.T) {
	cfg := g8RedConfig(t, true, true)
	client, prefix := g8RedRedis(t)
	p := agentbudget.New(agentbudget.Deps{Store: redisstate.NewAgentBudgetStore(client, prefix),
		Settings:  agentbudget.Settings{MarkupPercent: 5, HoldTTL: time.Minute, ReserveDefaultOutputTokens: 256},
		OrgConfig: func(string) *tenant.OrgConfig { return cfg }})
	rc := g8RedRequest(t)
	g8RedWallet(t, rc)
	refusal := p.CallCap(rc, cfg)
	if refusal == nil {
		refusal = p.ProcessRequest(context.Background(), rc).Error
	}
	if refusal == nil {
		t.Fatal("G8_BEHAVIORAL_RED: admitted a 10500-micro priced chat against configured 5000-micro run ceiling with no caller budget")
	}
	if refusal.Status != 402 || refusal.Code != agentbudget.CodeBudgetExhausted {
		t.Fatalf("want run-budget 402, got %v", refusal)
	}
}

func TestG8CapDefaultOffControl(t *testing.T) {
	cfg := g8RedConfig(t, false, false)
	store := tenant.NewStore()
	store.Set("g8-synthetic-org", cfg)
	client, prefix := g8RedRedis(t)
	p := New(nil, NewPricingLookup(nil), false, store)
	p.SetRedisBudget(redisstate.NewBudgetStore(client, prefix+"budget:"))
	rc := g8RedRequest(t)
	g8RedWallet(t, rc)
	if res := p.ProcessRequest(context.Background(), rc); res.Error != nil {
		t.Fatalf("default-off must preserve admission: %v", res.Error)
	}
}
