package routing

import (
	"sync"
	"sync/atomic"
	"time"
)

// ProviderHealth holds aggregated health data for a single provider.
type ProviderHealth struct {
	ProviderID      string     `json:"id"`
	Healthy         bool       `json:"healthy"`
	CircuitState    string     `json:"circuit_state"`
	LatencyEWMAMs   int64      `json:"latency_ewma_ms"`
	RequestCount    int64      `json:"request_count"`
	ErrorCount      int64      `json:"error_count"`
	SuccessRate     float64    `json:"success_rate"`
	LastError       string     `json:"last_error,omitempty"`
	LastErrorTime   *time.Time `json:"last_error_time,omitempty"`
	LastSuccessTime *time.Time `json:"last_success_time,omitempty"`
	// RecentSuccessRate is an EWMA of request outcomes, so old results fade.
	// Adaptive routing scores on it; the health endpoints keep SuccessRate.
	RecentSuccessRate float64 `json:"-"`
	// SuccessLatencyEWMAMs averages successful calls only. Adaptive routing
	// scores on it, so failing fast never counts as speed. LatencyEWMAMs (the
	// least-latency tracker) also folds in failed calls.
	SuccessLatencyEWMAMs float64 `json:"-"`
}

// successRateAlpha weights each new outcome in RecentSuccessRate, so roughly
// the last 20 calls dominate and a provider recovers once it succeeds again.
const successRateAlpha = 0.1

// successLatencyAlpha matches the least-latency tracker's default smoothing.
const successLatencyAlpha = 0.3

// providerStats tracks per-provider request statistics.
type providerStats struct {
	requests      atomic.Int64
	errors        atomic.Int64
	mu            sync.Mutex
	lastError     string
	lastErrorTime time.Time
	lastSuccessAt time.Time
	recentSuccess float64 // EWMA of outcomes (1 success, 0 error); guarded by mu
	hasOutcome    bool    // guarded by mu
	// EWMA of successful calls' latency in ms; guarded by mu.
	successLatencyMs  float64
	hasSuccessLatency bool
}

// recordOutcomeLocked folds one outcome into the EWMA. s.mu must be held.
func (s *providerStats) recordOutcomeLocked(success bool) {
	sample := 0.0
	if success {
		sample = 1
	}
	if !s.hasOutcome {
		s.recentSuccess = sample
		s.hasOutcome = true
		return
	}
	s.recentSuccess = successRateAlpha*sample + (1-successRateAlpha)*s.recentSuccess
}

// HealthMonitor tracks per-provider health metrics.
type HealthMonitor struct {
	mu      sync.RWMutex
	stats   map[string]*providerStats
	tracker *LatencyTracker
	cbReg   *CircuitBreakerRegistry
}

// NewHealthMonitor creates a health monitor.
// tracker and cbReg may be nil.
func NewHealthMonitor(tracker *LatencyTracker, cbReg *CircuitBreakerRegistry) *HealthMonitor {
	return &HealthMonitor{
		stats:   make(map[string]*providerStats),
		tracker: tracker,
		cbReg:   cbReg,
	}
}

// SetCircuitBreakerRegistry refreshes the active circuit-breaker view on
// startup and config reload without losing collected provider statistics.
func (hm *HealthMonitor) SetCircuitBreakerRegistry(cbReg *CircuitBreakerRegistry) {
	if hm == nil {
		return
	}
	hm.mu.Lock()
	hm.cbReg = cbReg
	hm.mu.Unlock()
}

// CircuitBreakerRegistry returns the active registry, including its persistent
// org breakers, even when only an org's config enables failover.
func (hm *HealthMonitor) CircuitBreakerRegistry() *CircuitBreakerRegistry {
	if hm == nil {
		return nil
	}
	hm.mu.RLock()
	defer hm.mu.RUnlock()
	return hm.cbReg
}

// getOrCreate returns the stats for a provider, creating if necessary.
func (hm *HealthMonitor) getOrCreate(providerID string) *providerStats {
	hm.mu.RLock()
	s, ok := hm.stats[providerID]
	hm.mu.RUnlock()
	if ok {
		return s
	}

	hm.mu.Lock()
	defer hm.mu.Unlock()
	// Double-check after acquiring write lock.
	if s, ok = hm.stats[providerID]; ok {
		return s
	}
	s = &providerStats{}
	hm.stats[providerID] = s
	return s
}

// RecordLatency records a successful call's latency: in the least-latency
// tracker, and in the success-only average adaptive routing scores on.
// Observations are kept even while routing is disabled.
func (hm *HealthMonitor) RecordLatency(providerID string, latency time.Duration) {
	if hm == nil {
		return
	}
	if hm.tracker != nil {
		hm.tracker.Record(providerID, latency)
	}
	ms := float64(latency.Milliseconds())
	s := hm.getOrCreate(providerID)
	s.mu.Lock()
	if s.hasSuccessLatency {
		s.successLatencyMs = successLatencyAlpha*ms + (1-successLatencyAlpha)*s.successLatencyMs
	} else {
		s.successLatencyMs = ms
		s.hasSuccessLatency = true
	}
	s.mu.Unlock()
}

// RecordFailureLatency records how long a failed call took, for least-latency
// only (see LatencyTracker.RecordFailure). Adaptive scoring ignores it.
func (hm *HealthMonitor) RecordFailureLatency(providerID string, elapsed time.Duration) {
	if hm != nil && hm.tracker != nil {
		hm.tracker.RecordFailure(providerID, elapsed)
	}
}

// RecordSuccess records a successful request for a provider.
func (hm *HealthMonitor) RecordSuccess(providerID string) {
	if hm == nil {
		return
	}
	s := hm.getOrCreate(providerID)
	s.requests.Add(1)
	now := time.Now()
	s.mu.Lock()
	s.lastSuccessAt = now
	s.recordOutcomeLocked(true)
	s.mu.Unlock()
}

// RecordError records a failed request for a provider.
func (hm *HealthMonitor) RecordError(providerID string, err error) {
	if hm == nil {
		return
	}
	s := hm.getOrCreate(providerID)
	s.requests.Add(1)
	s.errors.Add(1)
	now := time.Now()
	errMsg := ""
	if err != nil {
		errMsg = err.Error()
	}
	s.mu.Lock()
	s.lastError = errMsg
	s.lastErrorTime = now
	s.recordOutcomeLocked(false)
	s.mu.Unlock()
}

// GetHealth returns aggregated health for a single provider.
func (hm *HealthMonitor) GetHealth(providerID string) ProviderHealth {
	if hm == nil {
		return ProviderHealth{ProviderID: providerID, Healthy: true, CircuitState: "n/a"}
	}

	h := ProviderHealth{
		ProviderID:   providerID,
		Healthy:      true,
		CircuitState: "n/a",
	}

	// Stats from our tracking.
	hm.mu.RLock()
	s, ok := hm.stats[providerID]
	hm.mu.RUnlock()

	if ok {
		h.RequestCount = s.requests.Load()
		h.ErrorCount = s.errors.Load()
		if h.RequestCount > 0 {
			h.SuccessRate = float64(h.RequestCount-h.ErrorCount) / float64(h.RequestCount)
		}

		s.mu.Lock()
		h.RecentSuccessRate = s.recentSuccess
		h.SuccessLatencyEWMAMs = s.successLatencyMs
		if s.lastError != "" {
			h.LastError = s.lastError
			t := s.lastErrorTime
			h.LastErrorTime = &t
		}
		if !s.lastSuccessAt.IsZero() {
			t := s.lastSuccessAt
			h.LastSuccessTime = &t
		}
		s.mu.Unlock()
	}

	// Latency from tracker.
	if hm.tracker != nil {
		h.LatencyEWMAMs = int64(hm.tracker.Get(providerID))
	}

	// Circuit breaker state. The registry may change on config reload.
	hm.mu.RLock()
	cbReg := hm.cbReg
	hm.mu.RUnlock()
	if cbReg != nil && cbReg.IsEnabled() {
		cb := cbReg.Get(providerID)
		state := cb.State()
		switch state {
		case StateClosed:
			h.CircuitState = "closed"
			h.Healthy = true
		case StateOpen:
			h.CircuitState = "open"
			h.Healthy = false
		case StateHalfOpen:
			h.CircuitState = "half-open"
			h.Healthy = true // half-open allows test requests
		}
	}

	return h
}

// GetAllHealth returns health for all tracked providers.
func (hm *HealthMonitor) GetAllHealth() []ProviderHealth {
	return hm.GetAllHealthWithProviders(nil)
}

// GetAllHealthWithProviders returns health for all tracked providers plus any
// additional provider IDs that may not have received traffic yet. This ensures
// configured providers always appear in health output even with zero requests.
func (hm *HealthMonitor) GetAllHealthWithProviders(extraIDs []string) []ProviderHealth {
	if hm == nil {
		return nil
	}

	seen := make(map[string]struct{})

	hm.mu.RLock()
	ids := make([]string, 0, len(hm.stats)+len(extraIDs))
	for id := range hm.stats {
		ids = append(ids, id)
		seen[id] = struct{}{}
	}
	hm.mu.RUnlock()

	// Append configured providers that haven't had traffic yet.
	for _, id := range extraIDs {
		if _, ok := seen[id]; !ok {
			ids = append(ids, id)
		}
	}

	results := make([]ProviderHealth, 0, len(ids))
	for _, id := range ids {
		results = append(results, hm.GetHealth(id))
	}
	return results
}
