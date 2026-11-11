package budget

import (
	"log/slog"
	"time"
)

// SeedSpend sets the initial spend for a budget level so that counters
// survive gateway restarts.  Called once at startup with data fetched
// from the control plane spend-summary endpoint.
func (t *Tracker) SeedSpend(level, key string, totalSpend float64, modelSpend map[string]float64) {
	lb := t.lookupLevel(level, key)
	if lb == nil {
		return
	}

	lb.counter.mu.Lock()
	now := time.Now().UTC()
	start := PeriodStart(lb.period, now)

	// Only seed if counter is in the current period (not already populated).
	if lb.counter.periodStart.Before(start) {
		lb.counter.periodStart = start
		lb.counter.totalSpend = 0
		lb.counter.modelSpend = make(map[string]float64)
	}

	// The local counter is the fallback view used while the shared ledger is
	// unreadable, so it is seeded exactly as it is without a ledger.
	lb.counter.totalSpend = totalSpend
	for m, s := range modelSpend {
		lb.counter.modelSpend[m] = s
	}
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

	slog.Info("budget seeded", "level", level, "key", key, "spend", totalSpend)
}
