package budget

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"strconv"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/redisstate"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

func TestCapAgentRefusalLogContract(t *testing.T) {
	addr := os.Getenv("TEST_REDIS_ADDR")
	if addr == "" {
		t.Fatal("synthetic Redis required")
	}
	db, err := strconv.Atoi(os.Getenv("TEST_REDIS_DB"))
	if err != nil {
		t.Fatal(err)
	}
	client, err := redisstate.NewClient(redisstate.Config{Address: addr, DB: db, Timeout: time.Second})
	if err != nil {
		t.Fatal(err)
	}
	prefix := fmt.Sprintf("test:g8-contract:%d:", time.Now().UnixNano())
	t.Cleanup(func() {
		iter := client.Redis().Scan(context.Background(), 0, prefix+"*", 100).Iterator()
		for iter.Next(context.Background()) {
			client.Redis().Del(context.Background(), iter.Val())
		}
		client.Close()
	})
	amount := 0.005
	cfg := &tenant.OrgConfig{Meta: &tenant.ConfigMeta{Version: 1},
		Budgets: &tenant.BudgetsConfig{Enabled: true, AdmissionCapsEnabled: true, PolicyRevision: 1, NoMaxTokens: "clamp", Agents: map[string]*tenant.AdmissionCapConfig{"agent-a": {Limit: &amount, ActivatedAt: "2026-10-27T00:00:00Z"}}},
		Routing: &tenant.RoutingConfig{Agents: &tenant.AgentsConfig{KeyBindings: map[string]string{"key_rf_logerr_b": "agent-a"}}}}
	admission, apiErr := ResolveAdmission(auth.Identity{OrgID: r7Org, KeyID: "key_rf_logerr_b"}, cfg, time.Now().Add(time.Minute))
	if apiErr != nil {
		t.Fatal(apiErr)
	}
	admission.period = time.Date(2026, 10, 27, 0, 0, 0, 0, time.UTC)
	store := redisstate.NewCapHoldStore(client, prefix)
	p := New(nil, nil, false, nil)
	p.SetCapStore(store)
	rp := newRFLogerrPipeline(t, p)
	rc := models.AcquireRequestContext()
	defer rc.Release()
	rc.RequestID = "req_g8_agent_cap_refusal"
	rc.StartTime = admission.period.Add(12 * time.Hour)
	rc.Model, rc.Provider, rc.EndpointType = "synthetic-chat", "synthetic", "chat"
	rc.Privacy.ContentMode = models.ContentModeFull
	rc.Metadata["auth_key_id"] = "key_rf_logerr_b"
	rc.Metadata["org_id"] = r7Org
	rc.Metadata["billing_mode"] = models.BillingModeManaged
	rc.Metadata["budget_exceeded_level"] = "key:spoofed"
	rc.Metadata["budget_period"] = "total"
	rc.Metadata["budget_period_start"] = "spoofed"
	rc.Metadata["budget_action"] = "warn"
	out := 256
	rc.Request = &models.ChatCompletionRequest{Model: rc.Model, MaxTokens: &out, Messages: []models.Message{{Role: "user", Content: json.RawMessage(`"synthetic"`)}}}
	rc.Billing.EndpointSet = []models.EndpointRef{{ID: "synthetic", Price: models.PriceCard{Request: 0.01, Source: models.PriceSourceCatalog}}}
	BindAdmission(rc, admission)
	if err := QualifyCapChat(rc, nil, 500, 256); err != nil {
		t.Fatal(err)
	}
	err = rp.engine.Process(context.Background(), rc, func(context.Context, *models.RequestContext) error { t.Fatal("cap refusal dispatched"); return nil })
	if err == nil {
		t.Fatal("cap did not refuse")
	}
	entries := rp.sent(t)
	if len(entries) != 1 {
		t.Fatalf("entries=%d", len(entries))
	}
	checkRFLogerrRefusal(t, entries[0], rfLogerrRefusal{"org_budget_exceeded", "agent:agent-a", "daily", "2026-10-27T00:00:00Z", "block"}, "log-entry-v2-agent-cap-refusal.json")
}
