package capability

import (
	"sync"
	"time"
)

// Accrual settings: an idle run's remainder is dropped after accrualIdle; it
// is always below one micro.
const (
	accrualIdle       = time.Hour
	maxAccrualEntries = 100_000
)

// accrual carries the sub-micro part of managed charges per run, so a call
// priced below a micro (per character, per thousand characters) still pays
// its markup: each charge adds its exact picodollars to its run's remainder,
// and the whole micros are settled once there is at least one. A call with
// no run accrues on its org.
type accrual struct {
	mu   sync.Mutex
	now  func() time.Time
	runs map[string]*remainder
}

type remainder struct {
	pico int64
	seen time.Time
}

func newAccrual(now func() time.Time) *accrual {
	return &accrual{now: now, runs: make(map[string]*remainder)}
}

// take adds pico to key's remainder and returns the whole micros to settle
// now, keeping the rest.
func (a *accrual) take(key string, pico int64) int64 {
	a.mu.Lock()
	defer a.mu.Unlock()
	now := a.now()
	r := a.runs[key]
	if r == nil {
		a.evictIdle(now)
		r = &remainder{}
		a.runs[key] = r
	}
	r.pico += pico
	r.seen = now
	micros := r.pico / picoPerMicro
	r.pico -= micros * picoPerMicro
	if r.pico == 0 {
		delete(a.runs, key)
	}
	return micros
}

// evictIdle drops remainders idle for accrualIdle once the map is full.
func (a *accrual) evictIdle(now time.Time) {
	if len(a.runs) < maxAccrualEntries {
		return
	}
	for k, r := range a.runs {
		if now.Sub(r.seen) > accrualIdle {
			delete(a.runs, k)
		}
	}
}

// accrualKey is the run a charge accrues on.
func accrualKey(org, run string) string {
	return org + "\x00" + run
}
