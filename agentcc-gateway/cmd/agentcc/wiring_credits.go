package main

import (
	"log/slog"

	"github.com/futureagi/agentcc-gateway/internal/billing"
	"github.com/futureagi/agentcc-gateway/internal/models"
	creditsplugin "github.com/futureagi/agentcc-gateway/internal/plugins/credits"
	"github.com/futureagi/agentcc-gateway/internal/redisstate"
)

// initCredits builds the managed-credit ledger managed requests are held on
// and the credits plugin that holds, settles and voids them (and debits
// managed keys' own balances), and installs the plugin as the request path's
// billing hooks once the server is built.
func initCredits(d *wiringDeps) error {
	// Built here, not in an afterServer callback: initCatalog's callback,
	// registered earlier, hands d.orgLedger to EnableManaged.
	d.orgLedger = newOrgLedger(d)

	if d.keyStore == nil {
		return nil
	}
	plugin := creditsplugin.New(true, d.keyStore)
	plugin.SetOrgLedger(d.orgLedger, d.cfg.Managed.RequireCredits)
	m := d.cfg.Managed
	plugin.SetSettings(creditsplugin.Settings{
		MarkupPercent:              m.MarkupPercent,
		HoldTTL:                    m.HoldTTL,
		ReserveDefaultOutputTokens: m.ReserveDefaultOutputTokens,
		UnpricedReserveUSD:         m.UnpricedReserveUSD,
		AccountMissingRetryAfter:   m.AccountMissingRetryAfter,
	})
	if d.creditStore != nil {
		plugin.SetRedisCredit(d.creditStore)
		seedKeyBalances(d)
	}
	d.plugins = append(d.plugins, plugin)

	d.afterServer(func() error {
		h := d.handlers
		// Read per call: main installs the injected prices only after every
		// afterServer callback has run.
		plugin.SetPrices(func(providerID, model, endpointID string) (models.PriceCard, bool) {
			return h.Deps().Prices(providerID, model, endpointID)
		})
		plugin.SetMaxOutput(func(providerID, model string) int {
			db := h.ModelDB()
			if db == nil {
				return 0
			}
			if info, ok := db.Get(providerID + "/" + model); ok {
				return info.MaxOutputTokens
			}
			if info, ok := db.Get(model); ok {
				return info.MaxOutputTokens
			}
			return 0
		})
		plugin.SetOrgMaxTokens(h.OrgDefaultMaxTokensEstimate)
		h.SetBilling(plugin)
		if len(d.catalogSources) > 0 {
			slog.Info("managed models enabled", "providers", len(d.catalogSources),
				"markup_percent", m.MarkupPercent, "require_credits", m.RequireCredits, "ledger", ledgerName(d.orgLedger))
		}
		return nil
	})
	return nil
}

// newOrgLedger chooses the managed-credit ledger once, at startup: Redis
// when it is configured (keys mc:{<org>}:..., which the drain reads, with no
// prefix); the in-memory ledger only with managed.require_redis=false
// (tests); otherwise a ledger that fails closed, so managed traffic gets 503
// managed_billing_unavailable. Nothing falls back silently.
func newOrgLedger(d *wiringDeps) *creditsplugin.OrgLedger {
	switch {
	case d.redis != nil:
		store := redisstate.NewManagedCreditStore(d.redis, "")
		store.SetMaxOverdraftMicros(billing.USDToMicros(d.cfg.Managed.MaxOverdraftUSD))
		return creditsplugin.NewOrgLedger(store)
	case !d.cfg.Managed.RequireRedis:
		mem := billing.NewMemoryLedger()
		mem.SetMaxOverdraftMicros(billing.USDToMicros(d.cfg.Managed.MaxOverdraftUSD))
		slog.Warn("managed credits are held in this replica's memory (managed.require_redis=false): for tests only")
		return creditsplugin.NewOrgLedger(mem)
	default:
		if len(d.catalogSources) > 0 {
			slog.Error("managed billing has no Redis and managed.require_redis is on: managed requests will get 503 managed_billing_unavailable")
		}
		return creditsplugin.NewUnavailableOrgLedger()
	}
}

func ledgerName(l *creditsplugin.OrgLedger) string {
	if k := l.Kind(); k != "" {
		return k
	}
	return "unavailable"
}

// seedKeyBalances seeds Redis with managed keys' balances from the local
// keystore (Postgres-sourced). SETNX: never over a balance another replica
// or real traffic already set.
func seedKeyBalances(d *wiringDeps) {
	seeded := 0
	for _, k := range d.keyStore.List() {
		if !k.IsManaged() {
			continue
		}
		micros := k.BalanceMicros()
		if micros <= 0 {
			continue
		}
		if d.creditStore.SeedBalance(k.ID, micros) {
			seeded++
		}
	}
	if seeded > 0 {
		slog.Info("seeded credit balances into redis", "count", seeded)
	}
}
