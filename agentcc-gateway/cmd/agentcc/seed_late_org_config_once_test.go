package main

// An org config that a later pass of the start-up seed sees again is counted
// once: a seed never adds to a counter. When the seed's window ends, the
// gateway logs one warning for each org in the spend summary that the window
// did not seed, with the org, the levels and the reason.

import (
	"bytes"
	"context"
	"log/slog"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

const (
	seedLateOrgA = "org-seed-late-a"
	seedLateOrgB = "org-seed-late-b"
	seedLateOrgC = "org-seed-late-c"
	// orgNotSeededText is the warning's text, word for word.
	orgNotSeededText = "budget seed: an organization in the spend summary was not seeded at these budget levels"
)

// seedLateSummary is each org's spend: $13 in all, $6 in each of teams eng
// and ops, $7 on tag project:atlas.
func seedLateSummary(orgs ...string) *tenant.SpendSummary {
	s := &tenant.SpendSummary{Period: "monthly", Orgs: map[string]tenant.OrgSpendSummary{}}
	for _, org := range orgs {
		s.Orgs[org] = tenant.OrgSpendSummary{
			TotalSpend: 13,
			PerKey:     map[string]float64{},
			PerUser:    map[string]float64{},
			PerModel:   map[string]float64{},
			PerTeam:    map[string]float64{"eng": 6, "ops": 6},
			PerTag:     map[string]float64{"project:atlas": 7},
		}
	}
	return s
}

// seedLateOrgConfig is an org's budgets: the org $20, team eng $10, team ops
// $5, tag project:atlas $10. Counted once, only team ops is over its limit;
// counted twice, every level is.
func seedLateOrgConfig(enabled bool) *tenant.OrgConfig {
	return &tenant.OrgConfig{Budgets: &tenant.BudgetsConfig{
		Enabled:   enabled,
		OrgLimit:  20,
		HardLimit: true,
		Teams:     map[string]*tenant.BudgetLevelConfig{"eng": {Limit: 10}, "ops": {Limit: 5}},
		Tags:      map[string]*tenant.BudgetLevelConfig{"project:atlas": {Limit: 10}},
	}}
}

func seedLateRequest(org, team string, tag bool) *models.RequestContext {
	rc := &models.RequestContext{Model: "gpt-4o", Provider: "openai",
		Metadata: map[string]string{tenant.MetadataKeyOrgID: org}}
	if team != "" {
		rc.Metadata["key_team"] = team
	}
	if tag {
		rc.Metadata["tag:project"] = "atlas"
	}
	return rc
}

func seedLateConfig(nodeBudgets bool) *config.Config {
	cfg := config.DefaultConfig()
	cfg.ControlPlane.URL = "http://control-plane.invalid" // org configs arrive
	cfg.Budgets = config.BudgetsConfig{Enabled: nodeBudgets, DefaultPeriod: "monthly"}
	return cfg
}

var seedLateModes = []struct {
	name        string
	nodeBudgets bool
}{
	{"node budgets on", true},
	{"org budgets only", false},
}

func TestAnOrgConfigSeenAgainByALaterSeedPassIsCountedOnce(t *testing.T) {
	for _, tc := range seedLateModes {
		t.Run(tc.name, func(t *testing.T) {
			cfg := seedLateConfig(tc.nodeBudgets)
			tenants := tenant.NewStore()
			tenants.Set(seedLateOrgA, seedLateOrgConfig(true))
			plugin, tracker := buildBudgetPlugin(cfg, tenants, nil)
			if plugin == nil {
				t.Fatal("no budget plugin")
			}
			summary := seedLateSummary(seedLateOrgA)

			// The first pass seeds org A from its config; a later pass sees
			// the same config again and seeds it again.
			seedSummary(cfg, tenants, plugin, tracker, nil, "monthly", summary)
			seedLateOrgConfigs(tenants, plugin, nil, "monthly", summary, []string{seedLateOrgA}, tc.nodeBudgets, time.Minute)

			if res := plugin.ProcessRequest(context.Background(), seedLateRequest(seedLateOrgA, "ops", false)); res.Error == nil {
				t.Error("team ops, $6 of $5: not refused, so org A was not seeded")
			}
			for _, c := range []struct {
				name string
				team string
				tag  bool
			}{
				{"team eng, $6 of $10 (and the org, $13 of $20)", "eng", false},
				{"tag project:atlas, $7 of $10 (and the org, $13 of $20)", "", true},
			} {
				if res := plugin.ProcessRequest(context.Background(), seedLateRequest(seedLateOrgA, c.team, c.tag)); res.Error != nil {
					t.Errorf("%s: refused (%v), so a level was counted twice", c.name, res.Error)
				}
			}
		})
	}
}

// seedLateLogBuffer collects the default logger's output; the seed logs from
// its own goroutine.
type seedLateLogBuffer struct {
	mu  sync.Mutex
	buf bytes.Buffer
}

func (b *seedLateLogBuffer) Write(p []byte) (int, error) {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.buf.Write(p)
}

func (b *seedLateLogBuffer) String() string {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.buf.String()
}

func captureSeedLateLogs(t *testing.T) *seedLateLogBuffer {
	t.Helper()
	logs := &seedLateLogBuffer{}
	previous := slog.Default()
	slog.SetDefault(slog.New(slog.NewTextHandler(logs, nil)))
	t.Cleanup(func() { slog.SetDefault(previous) })
	return logs
}

// The window ends with org A's config never arrived; org B's arrived in time,
// budgets on (seeded); org C's arrived in time, budgets off. One warning for
// each org the window did not seed, none for org B.
func TestEachOrgTheSeedWindowDidNotSeedIsLoggedOnce(t *testing.T) {
	for _, tc := range seedLateModes {
		t.Run(tc.name, func(t *testing.T) {
			logs := captureSeedLateLogs(t)
			cfg := seedLateConfig(tc.nodeBudgets)
			tenants := tenant.NewStore()
			plugin, _ := buildBudgetPlugin(cfg, tenants, nil)
			if plugin == nil {
				t.Fatal("no budget plugin")
			}

			const window = 600 * time.Millisecond
			done := make(chan struct{})
			go func() {
				defer close(done)
				seedLateOrgConfigs(tenants, plugin, nil, "monthly", seedLateSummary(seedLateOrgA, seedLateOrgB, seedLateOrgC),
					[]string{seedLateOrgA, seedLateOrgB, seedLateOrgC}, tc.nodeBudgets, window)
			}()
			tenants.Set(seedLateOrgB, seedLateOrgConfig(true))
			tenants.Set(seedLateOrgC, seedLateOrgConfig(false))

			select {
			case <-done:
			case <-time.After(10 * time.Second):
				t.Fatal("the seed's window did not end")
			}

			byOrg := map[string][]string{}
			for _, line := range strings.Split(logs.String(), "\n") {
				if !strings.Contains(line, orgNotSeededText) {
					continue
				}
				for _, org := range []string{seedLateOrgA, seedLateOrgB, seedLateOrgC} {
					if strings.Contains(line, "org_id="+org+" ") {
						byOrg[org] = append(byOrg[org], line)
					}
				}
			}
			levels := "levels=org,key,user,team,tag"
			if tc.nodeBudgets {
				levels = "levels=key,user,team,tag" // the org level is seeded without a config
			}
			want := map[string][]string{
				seedLateOrgA: {"level=WARN", levels, `reason="its settings did not arrive within the seed window"`, "window=600ms"},
			}
			if !tc.nodeBudgets {
				// With the node's budgets on, an org is seeded whatever its config says.
				want[seedLateOrgC] = []string{"level=WARN", levels, `reason="its settings do not enable budgets"`, "window=600ms"}
			}
			for _, org := range []string{seedLateOrgA, seedLateOrgB, seedLateOrgC} {
				parts, logged := want[org]
				if !logged {
					if len(byOrg[org]) != 0 {
						t.Errorf("%s was seeded, and logged as not seeded: %v", org, byOrg[org])
					}
					continue
				}
				if len(byOrg[org]) != 1 {
					t.Errorf("%d warnings for %s, want 1. Log:\n%s", len(byOrg[org]), org, logs.String())
					continue
				}
				for _, p := range parts {
					if !strings.Contains(byOrg[org][0], p) {
						t.Errorf("%s's warning lacks %q: %s", org, p, byOrg[org][0])
					}
				}
			}
		})
	}
}
