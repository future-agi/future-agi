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
	defer lb.counter.mu.Unlock()

	now := time.Now().UTC()
	start := PeriodStart(lb.period, now)

	// Only seed if counter is in the current period (not already populated).
	if lb.counter.periodStart.Before(start) {
		lb.counter.periodStart = start
		lb.counter.totalSpend = 0
		lb.counter.modelSpend = make(map[string]float64)
	}

	lb.counter.totalSpend = totalSpend
	for m, s := range modelSpend {
		lb.counter.modelSpend[m] = s
	}

	slog.Info("budget seeded", "level", level, "key", key, "spend", totalSpend)
}
