package main

// An org whose config arrives after the start-up seed has read the spend
// summary still gets its key, user, team and tag levels seeded once the config
// is there, with the node's own budgets on or off, and the seed does not hold
// the node's budgets back while it waits.

import (
	"context"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

func TestALateOrgConfigStillGetsItsLevelsSeeded(t *testing.T) {
	for _, tc := range []struct {
		name        string
		nodeBudgets bool
	}{
		{"node budgets on", true},
		{"org budgets only", false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			cfg := config.DefaultConfig()
			cfg.ControlPlane.URL = teamTagControlPlane(t)
			cfg.Budgets = config.BudgetsConfig{Enabled: tc.nodeBudgets, DefaultPeriod: "monthly"}
			tenants := tenant.NewStore()
			// Org B's config is there at start; org A's arrives after the seed.
			tenants.Set(seedTeamOrgB, teamTagOrgConfig())

			plugin, tracker := buildBudgetPlugin(cfg, tenants, nil)
			if plugin == nil || (tracker != nil) != tc.nodeBudgets {
				t.Fatalf("budget plugin %v, node tracker %v", plugin, tracker)
			}
			seeded := make(chan struct{})
			go func() {
				defer close(seeded)
				seedBudgetSpend(cfg, tenants, plugin, tracker, nil, nil, nil)
			}()
			select {
			case <-seeded:
			case <-time.After(10 * time.Second):
				t.Fatal("the start-up seed waited on a config that had not arrived")
			}

			tenants.Set(seedTeamOrgA, teamTagOrgConfig())

			// Org A spent $6 in team eng and $7 on tag project:atlas, both over
			// $5: each is refused once the late config is seeded. A budget
			// check reads the counters and reserves nothing, so it is polled.
			for _, c := range []struct {
				name      string
				team, tag bool
			}{
				{"org A's team, $6 of $5", true, false},
				{"org A's tag, $7 of $5", false, true},
			} {
				refused := false
				for deadline := time.Now().Add(10 * time.Second); !refused && time.Now().Before(deadline); {
					if plugin.ProcessRequest(context.Background(), teamTagRequest(seedTeamOrgA, c.team, c.tag)).Error != nil {
						refused = true
						break
					}
					time.Sleep(25 * time.Millisecond)
				}
				if !refused {
					t.Errorf("%s after a restart whose org config arrived late: not refused within 10s", c.name)
				}
			}
		})
	}
}
