package budget

import (
	"context"
	"encoding/json"
	"os"
	"strconv"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// An organization's budgets keep their spend across a restart without Redis:
// for two organizations, every org, key, user, team and tag counter after the
// start-up seed equals what it counted before the restart, read from a spend
// summary shaped as the control plane writes it (testdata shared with the
// Django view's test).

const (
	restartOrgA = "org-restart-a"
	restartOrgB = "org-restart-b"
)

type restartKey struct {
	Org          string `json:"org"`
	GatewayKeyID string `json:"gateway_key_id"`
	Name         string `json:"name"`
	Owner        string `json:"owner"`
}

type restartCall struct {
	Org   string            `json:"org"`
	Key   string            `json:"key"`
	User  string            `json:"user"`
	Team  string            `json:"team"`
	Model string            `json:"model"`
	Tags  map[string]string `json:"tags"`
	Cost  float64           `json:"cost"`
}

type restartFixture struct {
	Keys     []restartKey        `json:"keys"`
	Requests []restartCall       `json:"requests"`
	Summary  tenant.SpendSummary `json:"summary"`
}

// loadRestartFixture reads the shared fixture with the placeholders replaced
// by this test's organization ids.
func loadRestartFixture(t *testing.T, path, orgA, orgB string) restartFixture {
	t.Helper()
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	text := strings.NewReplacer("<ORG_A>", orgA, "<ORG_B>", orgB).Replace(string(raw))
	var f restartFixture
	if err := json.Unmarshal([]byte(text), &f); err != nil {
		t.Fatal(err)
	}
	return f
}

// restartRequest is the request as the gateway sees it after its key
// authenticated: the key's id, name, owner and team, the caller's tags.
func restartRequest(f restartFixture, i int) *models.RequestContext {
	r := f.Requests[i]
	meta := map[string]string{tenant.MetadataKeyOrgID: r.Org, "auth_key_id": r.Key}
	for _, k := range f.Keys {
		if k.GatewayKeyID == r.Key {
			meta["auth_key_name"], meta["auth_key_owner"] = k.Name, k.Owner
		}
	}
	if r.Team != "" {
		meta["key_team"] = r.Team
	}
	for name, v := range r.Tags {
		meta["tag:"+name] = v
	}
	rc := makeRC(r.Model, "openai", meta)
	rc.UserID = r.User
	return rc
}

// restartBudgets names every key, user, team and tag of the fixture, the way
// an operator writes them: keys by name, tags as <name>:<value>.
func restartBudgets() *tenant.BudgetsConfig {
	level := func(names ...string) map[string]*tenant.BudgetLevelConfig {
		m := map[string]*tenant.BudgetLevelConfig{}
		for _, n := range names {
			m[n] = &tenant.BudgetLevelConfig{Limit: 100}
		}
		return m
	}
	return &tenant.BudgetsConfig{Enabled: true, OrgLimit: 100, HardLimit: true,
		Keys:  level("svc-a", "svc-b"),
		Users: level("alice", "bob", "carol", "dave"),
		Teams: level("eng"),
		Tags:  level("project:atlas", "env:prod"),
	}
}

func restartNode() *Plugin {
	store := tenant.NewStore()
	store.Set(restartOrgA, &tenant.OrgConfig{Budgets: restartBudgets()})
	store.Set(restartOrgB, &tenant.OrgConfig{Budgets: restartBudgets()})
	return New(nil, testPricing(), false, store)
}

func counted(p *Plugin, org, level, key string) float64 {
	bc := p.getOrgHierarchy(org).getOrCreateCounter(level, key)
	bc.mu.Lock()
	defer bc.mu.Unlock()
	return bc.totalSpend
}

func TestOrgBudgetsKeepEveryLevelsSpendAcrossARestartWithoutRedis(t *testing.T) {
	f := loadRestartFixture(t, "../../tenant/testdata/spend_summary_restart.json", restartOrgA, restartOrgB)

	before := restartNode()
	for i, r := range f.Requests {
		rc := restartRequest(f, i)
		rc.Metadata["cost"] = strconv.FormatFloat(r.Cost, 'f', -1, 64)
		rc.Response = &models.ChatCompletionResponse{}
		before.ProcessResponse(context.Background(), rc)
	}

	after := restartNode()
	for org, s := range f.Summary.Orgs {
		after.SeedOrgSpend(org, OrgSpendSummary{TotalSpend: s.TotalSpend, PerKey: s.PerKey,
			PerUser: s.PerUser, PerModel: s.PerModel, PerTeam: s.PerTeam, PerTag: s.PerTag})
	}

	levels := []struct{ level, key string }{{"org", ""}}
	for _, k := range []string{"svc-a", "svc-b"} {
		levels = append(levels, struct{ level, key string }{"key", k})
	}
	for _, u := range []string{"alice", "bob", "carol", "dave"} {
		levels = append(levels, struct{ level, key string }{"user", u})
	}
	levels = append(levels, struct{ level, key string }{"team", "eng"},
		struct{ level, key string }{"tag", "project:atlas"}, struct{ level, key string }{"tag", "env:prod"})
	for _, org := range []string{restartOrgA, restartOrgB} {
		for _, lv := range levels {
			want := counted(before, org, lv.level, lv.key)
			if got := counted(after, org, lv.level, lv.key); got != want {
				t.Errorf("%s %s %q after the restart = %v, want %v as counted before it", org, lv.level, lv.key, got, want)
			}
		}
	}
	// The fixture's spend reaches every level kind, in both organizations.
	if counted(before, restartOrgA, "user", "carol") == 0 || counted(before, restartOrgB, "tag", "project:atlas") == 0 {
		t.Fatal("the fixture no longer exercises a key owner's spend or org B's tag")
	}
}

// A level whose period is not the summary's starts from the restart: the
// summary is for the gateway's default period only.
func TestATeamBudgetOfAnotherPeriodIsNotSeeded(t *testing.T) {
	daily := restartBudgets()
	daily.Teams["eng"].Period = "daily"
	store := tenant.NewStore()
	store.Set(restartOrgA, &tenant.OrgConfig{Budgets: daily})
	p := New(nil, testPricing(), false, store)
	p.SeedOrgSpend(restartOrgA, OrgSpendSummary{PerTeam: map[string]float64{"eng": 4}})
	if got := counted(p, restartOrgA, "team", "eng"); got != 0 {
		t.Fatalf("a daily team budget was seeded with the monthly summary's %v", got)
	}
}
