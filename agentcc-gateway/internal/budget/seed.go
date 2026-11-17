package budget

import (
	"log/slog"
	"time"
)

// SeedSpend sets the initial spend for a budget level so that counters
// survive gateway restarts. Called at startup with the control plane's
// spend summary, which is read for the node's default period: a level that
// counts over another period is not seeded (it counts from the start).
// A seed never lowers a counter: a level already in the current period keeps
// the larger of what it counted and the seed, in total and per model (both
// count real spend, the seed from the request logs).
func (t *Tracker) SeedSpend(level, key string, totalSpend float64, modelSpend map[string]float64) {
	lb := t.lookupLevel(level, key)
	if lb == nil {
		return
	}
	if RefusalPeriod(lb.period) != RefusalPeriod(t.defaultPeriod) {
		return
	}

	lb.counter.mu.Lock()
	now := time.Now().UTC()
	start := PeriodStart(lb.period, now)

	if !lb.counter.periodStart.Equal(start) || lb.counter.modelSpend == nil {
		lb.counter.periodStart = start
		lb.counter.totalSpend = 0
		lb.counter.modelSpend = make(map[string]float64)
	}

	// The local counter is the fallback view used while the shared ledger is
	// unreadable, so it is seeded exactly as it is without a ledger.
	if totalSpend > lb.counter.totalSpend {
		lb.counter.totalSpend = totalSpend
	}
	for m, s := range modelSpend {
		if s > lb.counter.modelSpend[m] {
			lb.counter.modelSpend[m] = s
		}
	}
	seeded := lb.counter.totalSpend
	lb.counter.mu.Unlock()

	if backend, ok := t.durable.(interface {
		SeedSpendFloor(string, string, string, string, float64, map[string]float64) bool
	}); ok {
		if level == "org" {
			key = ""
		}
		// Logs include video costs already in the shared ledger. Seed a floor, not
		// a second additive balance, and never erase a live reservation/receipt.
		backend.SeedSpendFloor("", "global_"+level, key, lb.period, totalSpend, modelSpend)
	}

	slog.Info("budget seeded", "level", level, "key", key, "spend", seeded)
}
