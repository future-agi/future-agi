package routing

import (
	"slices"
	"sync"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
)

const (
	// defaultScopedBreakerMaxEntries caps how many (org, provider) breakers are
	// kept. Past it, idle entries go first, then the least recently used.
	defaultScopedBreakerMaxEntries = 10000
	// minScopedBreakerIdleTTL is the shortest time an unused breaker is kept.
	minScopedBreakerIdleTTL = 10 * time.Minute
)

// orgKeyFailureStatusCodes always count as failures for an org breaker, on
// top of the configured codes: a rejected org key (401) or a key without
// access (403) keeps failing until the org fixes it, so its breaker should
// stop calling it. The shared operator breaker keeps the configured codes.
var orgKeyFailureStatusCodes = []int{401, 403}

type scopedBreakerKey struct {
	scope      string
	providerID string
}

type scopedBreakerEntry struct {
	cb       *CircuitBreaker
	lastUsed time.Time
}

// ScopedCircuitBreakerRegistry keeps one circuit breaker per (scope, provider),
// with the shared breaker's thresholds and half-open behaviour. It is for
// traffic on an org's own provider key: those breakers never change shared
// routing health, and their state is not reported on health endpoints.
type ScopedCircuitBreakerRegistry struct {
	mu         sync.Mutex
	cfg        config.CircuitBreakerConfig
	breakers   map[scopedBreakerKey]*scopedBreakerEntry
	idleTTL    time.Duration
	maxEntries int
	lastSweep  time.Time
	now        func() time.Time
}

// NewScopedCircuitBreakerRegistry creates a registry using cfg's thresholds.
// Its breakers also count 401 and 403 as failures, in addition to cfg's
// status codes (DefaultCBStatusCodes when none are set).
// Entries unused for max(10 minutes, 2 × cooldown) are dropped.
func NewScopedCircuitBreakerRegistry(cfg config.CircuitBreakerConfig) *ScopedCircuitBreakerRegistry {
	cfg.OnStatusCodes = withOrgKeyFailureCodes(cfg.OnStatusCodes)
	idle := 2 * cfg.Cooldown
	if idle < minScopedBreakerIdleTTL {
		idle = minScopedBreakerIdleTTL
	}
	return &ScopedCircuitBreakerRegistry{
		cfg:        cfg,
		breakers:   make(map[scopedBreakerKey]*scopedBreakerEntry),
		idleTTL:    idle,
		maxEntries: defaultScopedBreakerMaxEntries,
		now:        time.Now,
	}
}

// withOrgKeyFailureCodes returns a new slice of codes (or the shared defaults)
// plus 401 and 403, leaving the caller's slice untouched.
func withOrgKeyFailureCodes(codes []int) []int {
	if len(codes) == 0 {
		codes = DefaultCBStatusCodes
	}
	out := make([]int, 0, len(codes)+len(orgKeyFailureStatusCodes))
	out = append(out, codes...)
	for _, extra := range orgKeyFailureStatusCodes {
		if !slices.Contains(out, extra) {
			out = append(out, extra)
		}
	}
	return out
}

// Get returns the breaker for (scope, providerID), creating it if needed.
func (r *ScopedCircuitBreakerRegistry) Get(scope, providerID string) *CircuitBreaker {
	if r == nil {
		return nil
	}
	r.mu.Lock()
	defer r.mu.Unlock()

	now := r.now()
	if now.Sub(r.lastSweep) >= r.idleTTL {
		r.sweepIdleLocked(now)
	}
	key := scopedBreakerKey{scope: scope, providerID: providerID}
	if e, ok := r.breakers[key]; ok {
		e.lastUsed = now
		return e.cb
	}
	if len(r.breakers) >= r.maxEntries {
		r.sweepIdleLocked(now)
		if len(r.breakers) >= r.maxEntries {
			r.evictLeastRecentLocked()
		}
	}
	// No state-change callback: an org breaker must not mark the provider
	// unhealthy in the shared router. The label tells its logs apart.
	e := &scopedBreakerEntry{cb: NewCircuitBreaker(providerID+" (org "+scope+")", r.cfg, nil), lastUsed: now}
	r.breakers[key] = e
	return e.cb
}

// Len returns the number of breakers held.
func (r *ScopedCircuitBreakerRegistry) Len() int {
	r.mu.Lock()
	defer r.mu.Unlock()
	return len(r.breakers)
}

func (r *ScopedCircuitBreakerRegistry) sweepIdleLocked(now time.Time) {
	r.lastSweep = now
	for key, e := range r.breakers {
		if now.Sub(e.lastUsed) >= r.idleTTL {
			delete(r.breakers, key)
		}
	}
}

func (r *ScopedCircuitBreakerRegistry) evictLeastRecentLocked() {
	var oldestKey scopedBreakerKey
	var oldest time.Time
	found := false
	for key, e := range r.breakers {
		if !found || e.lastUsed.Before(oldest) {
			oldestKey, oldest, found = key, e.lastUsed, true
		}
	}
	if found {
		delete(r.breakers, oldestKey)
	}
}
