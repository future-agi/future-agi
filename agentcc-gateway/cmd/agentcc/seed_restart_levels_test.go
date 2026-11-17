package main

// A node serving two organizations is restarted without Redis: every level of
// the node's own budgets (org, key, user, team, tag) after the start-up seed
// equals what it counted before, from a spend summary shaped as the control
// plane writes it (internal/tenant/testdata, shared with the Django view's
// test). Keys are budgeted by name, as operators write them.

import (
	"context"
	"encoding/json"
	"os"
	"strconv"
	"strings"
	"testing"

	budgetpkg "github.com/futureagi/agentcc-gateway/internal/budget"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	budgetplugin "github.com/futureagi/agentcc-gateway/internal/plugins/budget"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

type restartLevelsFixture struct {
	Keys []struct {
		Org          string `json:"org"`
		GatewayKeyID string `json:"gateway_key_id"`
		Name         string `json:"name"`
		Owner        string `json:"owner"`
	} `json:"keys"`
	Requests []struct {
		Org   string            `json:"org"`
		Key   string            `json:"key"`
		User  string            `json:"user"`
		Team  string            `json:"team"`
		Model string            `json:"model"`
		Tags  map[string]string `json:"tags"`
		Cost  float64           `json:"cost"`
	} `json:"requests"`
	Summary tenant.SpendSummary `json:"summary"`
}

func TestNodeBudgetsKeepEveryLevelsSpendAcrossARestartWithoutRedis(t *testing.T) {
	raw, err := os.ReadFile("../../internal/tenant/testdata/spend_summary_restart.json")
	if err != nil {
		t.Fatal(err)
	}
	var f restartLevelsFixture
	text := strings.NewReplacer("<ORG_A>", "org-node-restart-a", "<ORG_B>", "org-node-restart-b").Replace(string(raw))
	if err := json.Unmarshal([]byte(text), &f); err != nil {
		t.Fatal(err)
	}

	level := func(names ...string) map[string]config.BudgetLevelConfig {
		m := map[string]config.BudgetLevelConfig{}
		for _, n := range names {
			m[n] = config.BudgetLevelConfig{Limit: 100}
		}
		return m
	}
	cfg := config.DefaultConfig()
	cfg.Budgets = config.BudgetsConfig{
		Enabled: true, DefaultPeriod: "monthly",
		Org:   &config.BudgetLevelConfig{Limit: 100},
		Keys:  level("svc-a", "svc-b"),
		Users: level("alice", "bob", "carol", "dave"),
		Teams: level("eng"),
		Tags:  level("project:atlas", "env:prod"),
	}
	tenants := tenant.NewStore()

	// Before the restart: the node counts each request as the gateway does.
	before := budgetpkg.NewTracker(cfg.Budgets)
	counting := budgetplugin.New(before, budgetplugin.NewPricingLookup(nil), true, tenants)
	for _, r := range f.Requests {
		rc := &models.RequestContext{Model: r.Model, Provider: "openai", UserID: r.User,
			Metadata: map[string]string{tenant.MetadataKeyOrgID: r.Org, "auth_key_id": r.Key,
				"cost": strconv.FormatFloat(r.Cost, 'f', -1, 64)}}
		for _, k := range f.Keys {
			if k.GatewayKeyID == r.Key {
				rc.Metadata["auth_key_name"], rc.Metadata["auth_key_owner"] = k.Name, k.Owner
			}
		}
		if r.Team != "" {
			rc.Metadata["key_team"] = r.Team
		}
		for name, v := range r.Tags {
			rc.Metadata["tag:"+name] = v
		}
		rc.Response = &models.ChatCompletionResponse{}
		counting.ProcessResponse(context.Background(), rc)
	}

	// The restart: a new tracker, seeded at start-up from the summary.
	after := budgetpkg.NewTracker(cfg.Budgets)
	plugin := budgetplugin.New(after, budgetplugin.NewPricingLookup(nil), true, tenants)
	seedSummary(cfg, tenants, plugin, after, nil, "monthly", &f.Summary)

	for _, c := range []struct{ level, key string }{
		{"org", ""}, {"key", "svc-a"}, {"key", "svc-b"},
		{"user", "alice"}, {"user", "bob"}, {"user", "carol"}, {"user", "dave"},
		{"team", "eng"}, {"tag", "project:atlas"}, {"tag", "env:prod"},
	} {
		want, _ := before.GetSpend(c.level, c.key)
		got, _ := after.GetSpend(c.level, c.key)
		if got != want {
			t.Errorf("node %s %q after the restart = %v, want %v as counted before it", c.level, c.key, got, want)
		}
	}
	if want, _ := before.GetSpend("user", "carol"); want == 0 {
		t.Fatal("the fixture no longer exercises a key owner's spend")
	}
}
