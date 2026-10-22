package main

import (
	"log/slog"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/plugins/agentbudget"
	"github.com/futureagi/agentcc-gateway/internal/redisstate"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// initAgentBudget wires per-run agent budgets: the run-budget plugin (pre
// 515, settle after the credits settle), its scopes in Redis when the
// gateway has it (keys <redis.prefix>abud:{<org>}:<scope>:..., under the same
// key prefix as every other Redis state key of this gateway), and, once the
// server is built, the plugin as the chat path's runBudgetCap hook. The
// plugin reads prices, model limits and org configs per call through the
// handlers and the server, because main installs them only after the server
// wiring has run.
func initAgentBudget(d *wiringDeps) error {
	var store *redisstate.AgentBudgetStore
	if d.redis != nil {
		store = redisstate.NewAgentBudgetStore(d.redis, redisKeyPrefix(d.cfg))
	} else {
		slog.Warn("run budgets have no Redis: a call that sends a run budget is refused (503 budget_state_unavailable) unless its org sets budgets.fail_mode=open")
	}
	m := d.cfg.Managed
	plugin := agentbudget.New(agentbudget.Deps{
		Store: store,
		Settings: agentbudget.Settings{
			MarkupPercent:              m.MarkupPercent,
			HoldTTL:                    m.HoldTTL,
			ReserveDefaultOutputTokens: m.ReserveDefaultOutputTokens,
		},
		Prices: func(providerID, model, endpointID string) (models.PriceCard, bool) {
			if d.handlers == nil {
				return models.PriceCard{}, false
			}
			return d.handlers.Deps().Prices(providerID, model, endpointID)
		},
		MaxOutput: func(providerID, model string) int {
			if d.handlers == nil {
				return 0
			}
			db := d.handlers.ModelDB()
			if db == nil {
				return 0
			}
			if _, out, ok := db.GetLimits(providerID + "/" + model); ok {
				return out
			}
			_, out, _ := db.GetLimits(model)
			return out
		},
		OrgConfig: func(orgID string) *tenant.OrgConfig {
			if d.server == nil || d.server.TenantStore == nil {
				return nil
			}
			return d.server.TenantStore.Get(orgID)
		},
	})
	d.plugins = append(d.plugins, plugin)
	d.afterServer(func() error {
		d.handlers.SetRunBudget(plugin)
		return nil
	})
	return nil
}
