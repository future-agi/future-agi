package routing

import (
	"math"
	"sync"
	"time"
)

// LatencyTracker maintains exponentially weighted moving averages of provider latencies.
type LatencyTracker struct {
	mu    sync.RWMutex
	ewma  map[string]float64 // providerID → EWMA in milliseconds
	alpha float64            // smoothing factor (0 < alpha <= 1)
}

// NewLatencyTracker creates a tracker with the given EWMA alpha.
func NewLatencyTracker(alpha float64) *LatencyTracker {
	if alpha <= 0 || alpha > 1 {
		alpha = 0.3
	}
	return &LatencyTracker{
		ewma:  make(map[string]float64),
		alpha: alpha,
	}
}

// Record updates the EWMA for a provider with a new latency observation.
func (t *LatencyTracker) Record(providerID string, d time.Duration) {
	ms := float64(d.Milliseconds())
	t.mu.Lock()
	defer t.mu.Unlock()

	if prev, ok := t.ewma[providerID]; ok {
		t.ewma[providerID] = t.alpha*ms + (1-t.alpha)*prev
	} else {
		t.ewma[providerID] = ms
	}
}

// RecordFailure folds a failed call's duration into the EWMA as
// max(elapsed, current average): a slow failure pushes the average up, so
// least-latency moves off a provider that starts failing, while a fast
// failure never makes a provider look faster than it is.
func (t *LatencyTracker) RecordFailure(providerID string, elapsed time.Duration) {
	ms := float64(elapsed.Milliseconds())
	t.mu.Lock()
	defer t.mu.Unlock()

	prev, ok := t.ewma[providerID]
	if !ok {
		t.ewma[providerID] = ms
		return
	}
	t.ewma[providerID] = t.alpha*math.Max(ms, prev) + (1-t.alpha)*prev
}

// Get returns the current EWMA latency for a provider in milliseconds.
// Returns 0 if no data has been recorded (new providers are treated favorably).
func (t *LatencyTracker) Get(providerID string) float64 {
	t.mu.RLock()
	defer t.mu.RUnlock()
	return t.ewma[providerID]
}

// LeastLatencyStrategy selects the target with the lowest EWMA latency.
type LeastLatencyStrategy struct{}

func (s *LeastLatencyStrategy) Name() string { return "least-latency" }

func (s *LeastLatencyStrategy) Select(targets []RoutingTarget, tracker *LatencyTracker) (int, error) {
	if tracker == nil {
		return 0, nil
	}

	bestIdx := 0
	bestLatency := math.MaxFloat64
	for i, t := range targets {
		lat := tracker.Get(t.ProviderID)
		if lat < bestLatency {
			bestLatency = lat
			bestIdx = i
		}
	}
	return bestIdx, nil
}
