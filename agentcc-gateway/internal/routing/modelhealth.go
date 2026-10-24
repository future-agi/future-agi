package routing

import (
	"math"
	"sync"
	"sync/atomic"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// ModelHealthRecorder takes the result of an upstream attempt. errClass is ""
// for a success. The request path feeds it at each attempt's finish, leaving
// out the attempts that say nothing about the model on this node (see
// Handlers.beginAttempt).
type ModelHealthRecorder interface {
	Record(providerID, model string, latency time.Duration, errClass models.ErrorClass)
}

const (
	healthWindow = 5 * time.Minute
	healthSlots  = 10
	healthSlot   = healthWindow / healthSlots

	// Latency bins grow by healthBinGrowth from 1 ms: bin 0 holds calls under
	// 1 ms, bin k holds [growth^(k-1), growth^k) ms, and the last bin holds
	// everything from about 10 minutes up. A percentile reads as its bin's
	// geometric midpoint, within 5% of the true value.
	healthBinGrowth = 1.1
	healthBins      = 142

	// maxHealthModels bounds how many (provider, model) windows a node keeps,
	// so callers naming arbitrary models cannot grow it without limit.
	maxHealthModels = 2048
)

var logHealthBinGrowth = math.Log(healthBinGrowth)

// ModelHealth is this node's rolling five-minute health of each (provider,
// model) it has called: call count, success and error rates, latency
// percentiles and the latest error's class. It implements ModelHealthReader
// and ModelHealthRecorder, and is safe for concurrent use.
type ModelHealth struct {
	now       func() time.Time
	maxModels int

	mu      sync.RWMutex
	windows map[healthKey]*healthWindowState
}

type healthKey struct{ provider, model string }

// healthWindowState is one model's ring of time slots.
type healthWindowState struct {
	lastNanos atomic.Int64 // when the latest call was recorded

	mu        sync.Mutex
	slots     [healthSlots]healthSlotState
	lastClass models.ErrorClass
	lastErrAt time.Time
}

// healthSlotState counts the calls whose finish fell in one slot.
type healthSlotState struct {
	index     int64 // the slot's number since the epoch; stale when it is not the current one
	n, errors uint32
	latency   [healthBins]uint32
}

// NewModelHealth returns an empty health store.
func NewModelHealth() *ModelHealth {
	return &ModelHealth{now: time.Now, maxModels: maxHealthModels, windows: make(map[healthKey]*healthWindowState)}
}

// Record adds one attempt's result to model's window on providerID: a success
// when errClass is "", else a failure. A failure counts only when its class
// speaks about the upstream (healthFailure); any other attempt is ignored and
// makes no window, so model names an upstream rejects cannot fill the store.
func (m *ModelHealth) Record(providerID, model string, latency time.Duration, errClass models.ErrorClass) {
	if errClass != "" && !healthFailure(errClass) {
		return
	}
	now := m.now()
	w := m.window(healthKey{providerID, model}, now)
	w.lastNanos.Store(now.UnixNano())
	idx := now.UnixNano() / int64(healthSlot)

	w.mu.Lock()
	defer w.mu.Unlock()
	s := &w.slots[idx%healthSlots]
	if s.index != idx {
		*s = healthSlotState{index: idx}
	}
	s.n++
	s.latency[latencyBin(latency)]++
	if errClass != "" {
		s.errors++
		w.lastClass, w.lastErrAt = errClass, now
	}
}

// healthFailure reports whether a failed attempt of class counts against its
// model's health: the upstream rate-limited it, timed out, failed or could not
// be reached. Health is shared by every org on the node, so the classes that
// come from the request (invalid_request, context_length, content_filter,
// upstream_4xx), from the caller's credential or balance (auth,
// insufficient_credits) or from the gateway (policy, budget, circuit_open,
// cancelled, unknown) never move it: a model's shared health reflects only
// how the upstream answered.
func healthFailure(class models.ErrorClass) bool {
	switch class {
	case models.ErrClassRateLimited, models.ErrClassTimeout, models.ErrClassUpstream5xx, models.ErrClassNetwork:
		return true
	}
	return false
}

// Snapshot is model's health on providerID over the last five minutes. It is
// the zero snapshot when nothing was recorded in that time.
func (m *ModelHealth) Snapshot(providerID, model string) ModelHealthSnapshot {
	m.mu.RLock()
	w := m.windows[healthKey{providerID, model}]
	m.mu.RUnlock()
	if w == nil {
		return ModelHealthSnapshot{}
	}
	now := m.now()
	oldest := now.UnixNano()/int64(healthSlot) - healthSlots + 1

	var merged [healthBins]uint32
	var n, errs uint32
	w.mu.Lock()
	for i := range w.slots {
		s := &w.slots[i]
		if s.index < oldest || s.n == 0 {
			continue
		}
		n += s.n
		errs += s.errors
		for b, c := range s.latency {
			merged[b] += c
		}
	}
	lastClass := ""
	if !w.lastErrAt.IsZero() && w.lastErrAt.UnixNano()/int64(healthSlot) >= oldest {
		lastClass = string(w.lastClass)
	}
	w.mu.Unlock()

	if n == 0 {
		return ModelHealthSnapshot{}
	}
	return ModelHealthSnapshot{
		N:              int(n),
		SuccessRate:    float64(n-errs) / float64(n),
		ErrorRate:      float64(errs) / float64(n),
		P50Ms:          percentileMs(&merged, n, 0.50),
		P95Ms:          percentileMs(&merged, n, 0.95),
		LastErrorClass: lastClass,
	}
}

// window returns key's window, creating it, and making room for it, when it
// is new.
func (m *ModelHealth) window(key healthKey, now time.Time) *healthWindowState {
	m.mu.RLock()
	w := m.windows[key]
	m.mu.RUnlock()
	if w != nil {
		return w
	}
	m.mu.Lock()
	defer m.mu.Unlock()
	if w := m.windows[key]; w != nil {
		return w
	}
	if len(m.windows) >= m.maxModels {
		m.evictLocked(now)
	}
	w = &healthWindowState{}
	m.windows[key] = w
	return w
}

// evictLocked drops every window idle for the whole health window, or when
// none is, the least recently recorded one. m.mu must be held for writing.
func (m *ModelHealth) evictLocked(now time.Time) {
	idleBefore := now.Add(-healthWindow).UnixNano()
	var oldestKey healthKey
	oldest := int64(math.MaxInt64)
	evicted := false
	for k, w := range m.windows {
		last := w.lastNanos.Load()
		if last <= idleBefore {
			delete(m.windows, k)
			evicted = true
			continue
		}
		if last < oldest {
			oldest, oldestKey = last, k
		}
	}
	if !evicted {
		delete(m.windows, oldestKey)
	}
}

// size is how many windows are kept.
func (m *ModelHealth) size() int {
	m.mu.RLock()
	defer m.mu.RUnlock()
	return len(m.windows)
}

// latencyBin is the histogram bin of latency.
func latencyBin(latency time.Duration) int {
	ms := float64(latency) / float64(time.Millisecond)
	if ms < 1 {
		return 0
	}
	b := 1 + int(math.Log(ms)/logHealthBinGrowth)
	if b >= healthBins {
		return healthBins - 1
	}
	return b
}

// percentileMs reads the q-th percentile of n calls from their latency bins,
// as the geometric midpoint of the bin it falls in.
func percentileMs(bins *[healthBins]uint32, n uint32, q float64) float64 {
	rank := uint32(math.Ceil(q * float64(n)))
	if rank == 0 {
		rank = 1
	}
	var seen uint32
	for b, c := range bins {
		seen += c
		if seen >= rank {
			if b == 0 {
				return 0.5
			}
			return math.Pow(healthBinGrowth, float64(b)-0.5)
		}
	}
	return math.Pow(healthBinGrowth, float64(healthBins)-1.5)
}
