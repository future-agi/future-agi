package main

// After a restart without Redis, the start-up seed restores each
// organization's team and tag budget spend from the control plane's spend
// summary (per_team, per_tag), so a team or tag over its hard limit stays
// refused, in that organization only.

import (
	"context"
	"fmt"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/logspool"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

const (
	seedTeamOrgA = "org-seed-team-a"
	seedTeamOrgB = "org-seed-team-b"
)

// teamTagControlPlane answers the spend summary as the control plane builds
// it: org A spent $6 in team eng and $7 on tag project:atlas; org B $1 and $2.
func teamTagControlPlane(t *testing.T) string {
	t.Helper()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		org := func(total, team, tag float64) string {
			return fmt.Sprintf(`{"total_spend":%v,"per_key":{},"per_user":{},"per_model":{},"per_team":{"eng":%v},"per_tag":{"project:atlas":%v}}`, total, team, tag)
		}
		fmt.Fprintf(w, `{"status":true,"result":{"period":"monthly","period_start":%q,"orgs":{%q:%s,%q:%s}}}`,
			logspool.PeriodStart("monthly", time.Now()).Format(time.RFC3339),
			seedTeamOrgA, org(13, 6, 7), seedTeamOrgB, org(3, 1, 2))
	}))
	t.Cleanup(srv.Close)
	return srv.URL
}

func teamTagOrgConfig() *tenant.OrgConfig {
	return &tenant.OrgConfig{Budgets: &tenant.BudgetsConfig{
		Enabled: true,
		Teams:   map[string]*tenant.BudgetLevelConfig{"eng": {Limit: 5}},
		Tags:    map[string]*tenant.BudgetLevelConfig{"project:atlas": {Limit: 5}},
	}}
}

func teamTagRequest(org string, team, tag bool) *models.RequestContext {
	rc := &models.RequestContext{Model: "gpt-4o", Provider: "openai",
		Metadata: map[string]string{tenant.MetadataKeyOrgID: org}}
	if team {
		rc.Metadata["key_team"] = "eng"
	}
	if tag {
		rc.Metadata["tag:project"] = "atlas"
	}
	return rc
}

func TestRestartWithoutRedisKeepsEachOrgsTeamAndTagSpend(t *testing.T) {
	cfg := config.DefaultConfig()
	cfg.ControlPlane.URL = teamTagControlPlane(t)
	cfg.Budgets.DefaultPeriod = "monthly"
	tenants := tenant.NewStore()
	tenants.Set(seedTeamOrgA, teamTagOrgConfig())
	tenants.Set(seedTeamOrgB, teamTagOrgConfig())

	plugin, tracker := buildBudgetPlugin(cfg, tenants, nil)
	if plugin == nil {
		t.Fatal("no budget plugin for org budgets")
	}
	seedBudgetSpend(cfg, tenants, plugin, tracker, nil, nil, nil)

	for _, tc := range []struct {
		name      string
		org       string
		team, tag bool
		refused   bool
	}{
		{"org A's team, $6 of $5", seedTeamOrgA, true, false, true},
		{"org A's tag, $7 of $5", seedTeamOrgA, false, true, true},
		{"org B's team, $1 of $5", seedTeamOrgB, true, false, false},
		{"org B's tag, $2 of $5", seedTeamOrgB, false, true, false},
	} {
		res := plugin.ProcessRequest(context.Background(), teamTagRequest(tc.org, tc.team, tc.tag))
		if refused := res.Error != nil; refused != tc.refused {
			t.Errorf("%s after the restart: refused %v (%v), want %v", tc.name, refused, res.Error, tc.refused)
		}
	}
}
