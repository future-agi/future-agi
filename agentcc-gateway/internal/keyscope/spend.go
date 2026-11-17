package keyscope

import (
	"math"
	"sync"
	"time"
)

// spendLevel is the level name of a key budget's counters in the shared
// budget store, apart from the org config's key budgets ("key", keyed by key
// name).
const spendLevel = "agent_key"

// SharedSpend is the shared budget counter store (redisstate.BudgetStore):
// one counter per (org, level, key, period), in USD, that every replica
// reads and adds to.
type SharedSpend interface {
	RecordSpend(org, level, key, period, model string, cost float64) (float64, bool)
	GetSpend(org, level, key, period string) (float64, map[string]float64, bool)
	Available() bool
}

// Spend is what each budgeted key spent in its current period. With a shared
// store every replica counts against the same counter; without one (a single
// gateway with no Redis), or while it cannot be reached, this replica's own
// count stands in for it.
type Spend struct {
	shared SharedSpend

	mu    sync.Mutex
	local map[spendKey]periodSpend
}

type spendKey struct{ org, key string }

type periodSpend struct {
	start  int64 // the period's start, Unix seconds
	micros int64
}

// NewSpend returns a spend store over shared, which may be nil.
func NewSpend(shared SharedSpend) *Spend {
	return &Spend{shared: shared, local: make(map[spendKey]periodSpend)}
}

// Spent is what keyID spent in b's period that now falls in, in microdollars.
func (s *Spend) Spent(orgID, keyID string, b Budget, now time.Time) int64 {
	if s.shared != nil && s.shared.Available() {
		if usd, _, ok := s.shared.GetSpend(orgID, spendLevel, keyID, b.Period); ok {
			return int64(math.Round(usd * 1_000_000))
		}
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	if p := s.local[spendKey{orgID, keyID}]; p.start == b.PeriodStart(now).Unix() {
		return p.micros
	}
	return 0
}

// Record adds micros to keyID's spend in b's period.
func (s *Spend) Record(orgID, keyID string, b Budget, micros int64, now time.Time) {
	if micros <= 0 {
		return
	}
	if s.shared != nil {
		s.shared.RecordSpend(orgID, spendLevel, keyID, b.Period, "", float64(micros)/1_000_000)
	}
	start := b.PeriodStart(now).Unix()
	k := spendKey{orgID, keyID}
	s.mu.Lock()
	defer s.mu.Unlock()
	p := s.local[k]
	if p.start != start { // a new period starts at zero
		p = periodSpend{start: start}
	}
	p.micros += micros
	s.local[k] = p
}
