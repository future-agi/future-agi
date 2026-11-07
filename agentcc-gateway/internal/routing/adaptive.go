package routing

import (
	"math"
	"math/rand/v2"
	"sync/atomic"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
)

// adaptiveWeightSnapshot is immutable after publication. Select can therefore
// read weights without contending with the background recalculation loop.
type adaptiveWeightSnapshot struct {
	byProvider map[string]float64
}

// AdaptiveStrategy dynamically adjusts provider weights based on real-time metrics.
type AdaptiveStrategy struct {
	weights         atomic.Pointer[adaptiveWeightSnapshot]
	requestCount    atomic.Int64
	counter         atomic.Uint64 // for learning-phase round-robin
	learningReqs    int
	minWeight       float64
	smoothingFactor float64
	signalLatency   float64
	signalError     float64
	tracker         *LatencyTracker
	healthMon       *HealthMonitor
	updateInterval  time.Duration
	stopCh          chan struct{}
	phase           atomic.Int32 // 0=learning, 1=active
}

// NewAdaptiveStrategy creates an adaptive routing strategy.
func NewAdaptiveStrategy(cfg config.AdaptiveConfig, tracker *LatencyTracker, healthMon *HealthMonitor) *AdaptiveStrategy {
	learningReqs := cfg.LearningRequests
	if learningReqs <= 0 {
		learningReqs = 100
	}
	updateInterval := cfg.UpdateInterval
	if updateInterval <= 0 {
		updateInterval = 30 * time.Second
	}
	smoothing := cfg.SmoothingFactor
	if smoothing <= 0 || smoothing > 1 {
		smoothing = 0.3
	}
	minWeight := cfg.MinWeight
	if minWeight <= 0 {
		minWeight = 0.05
	}
	sigLat := cfg.SignalWeights.Latency
	if sigLat <= 0 {
		sigLat = 0.5
	}
	sigErr := cfg.SignalWeights.ErrorRate
	if sigErr <= 0 {
		sigErr = 0.4
	}

	a := &AdaptiveStrategy{
		learningReqs:    learningReqs,
		minWeight:       minWeight,
		smoothingFactor: smoothing,
		signalLatency:   sigLat,
		signalError:     sigErr,
		tracker:         tracker,
		healthMon:       healthMon,
		updateInterval:  updateInterval,
		stopCh:          make(chan struct{}),
	}
	a.weights.Store(&adaptiveWeightSnapshot{byProvider: make(map[string]float64)})

	go a.updateLoop()
	return a
}

func (a *AdaptiveStrategy) Name() string { return "adaptive" }

// Select picks a target using adaptive weights.
func (a *AdaptiveStrategy) Select(targets []RoutingTarget, tracker *LatencyTracker) (int, error) {
	a.requestCount.Add(1)

	// Learning phase: even distribution via round-robin.
	if a.phase.Load() == 0 {
		n := a.counter.Add(1) - 1
		return int(n % uint64(len(targets))), nil
	}
	if len(targets) == 1 {
		return 0, nil
	}

	// Active phase: weighted random selection.
	snapshot := a.weights.Load()
	var weights map[string]float64
	if snapshot != nil {
		weights = snapshot.byProvider
	}

	// First pass: total weight for available targets.
	var total float64
	for _, t := range targets {
		w := weights[t.ProviderID]
		if w < a.minWeight {
			w = a.minWeight
		}
		total += w
	}

	if total <= 0 {
		return 0, nil
	}

	// Weighted random selection.
	r := rand.Float64() * total
	var cumulative float64
	for i, t := range targets {
		w := weights[t.ProviderID]
		if w < a.minWeight {
			w = a.minWeight
		}
		cumulative += w
		if r <= cumulative {
			return i, nil
		}
	}
	return len(targets) - 1, nil
}

// IncrementRequestCount is called by the handler after each request.
func (a *AdaptiveStrategy) IncrementRequestCount() {
	// Already incremented in Select, but this is for explicit tracking.
}

// GetWeights returns a copy of current weights.
func (a *AdaptiveStrategy) GetWeights() map[string]float64 {
	snapshot := a.weights.Load()
	if snapshot == nil {
		return map[string]float64{}
	}
	cpy := make(map[string]float64, len(snapshot.byProvider))
	for k, v := range snapshot.byProvider {
		cpy[k] = v
	}
	return cpy
}

// GetPhase returns "learning" or "active".
func (a *AdaptiveStrategy) GetPhase() string {
	if a.phase.Load() == 0 {
		return "learning"
	}
	return "active"
}

// Stop shuts down the background update loop.
func (a *AdaptiveStrategy) Stop() {
	close(a.stopCh)
}

func (a *AdaptiveStrategy) updateLoop() {
	ticker := time.NewTicker(a.updateInterval)
	defer ticker.Stop()
	for {
		select {
		case <-ticker.C:
			if a.requestCount.Load() >= int64(a.learningReqs) {
				a.phase.Store(1)
				a.recalculateWeights()
			}
		case <-a.stopCh:
			return
		}
	}
}

func (a *AdaptiveStrategy) recalculateWeights() {
	if a.healthMon == nil {
		return
	}

	allHealth := a.healthMon.GetAllHealth()
	if len(allHealth) == 0 {
		return
	}

	// Find max latency for normalization.
	var maxLatency float64
	for _, h := range allHealth {
		lat := float64(h.LatencyEWMAMs)
		if lat > maxLatency {
			maxLatency = lat
		}
	}
	if maxLatency <= 0 {
		maxLatency = 1
	}

	// Compute raw scores.
	rawScores := make(map[string]float64, len(allHealth))
	var totalRaw float64
	for _, h := range allHealth {
		lat := float64(h.LatencyEWMAMs)
		normalizedLatency := 1.0 - (lat / maxLatency)
		// Clamp to [0, 1].
		normalizedLatency = math.Max(0, math.Min(1, normalizedLatency))

		successRate := h.SuccessRate
		if h.RequestCount == 0 {
			successRate = 1.0 // No data = assume good.
		}

		raw := a.signalLatency*normalizedLatency + a.signalError*successRate
		rawScores[h.ProviderID] = raw
		totalRaw += raw
	}

	if totalRaw <= 0 {
		return
	}

	// Normalize and smooth. The published snapshot is immutable, so it remains
	// safe for concurrent readers while the next one is being constructed.
	oldSnapshot := a.weights.Load()
	var oldWeights map[string]float64
	if oldSnapshot != nil {
		oldWeights = oldSnapshot.byProvider
	}

	newWeights := make(map[string]float64, len(rawScores))
	for pid, raw := range rawScores {
		calculated := raw / totalRaw
		old := oldWeights[pid]
		if old <= 0 {
			// First time: use calculated directly.
			newWeights[pid] = calculated
		} else {
			newWeights[pid] = a.smoothingFactor*calculated + (1-a.smoothingFactor)*old
		}
	}

	// Enforce minimum weight and re-normalize.
	var total float64
	for pid, w := range newWeights {
		if w < a.minWeight {
			newWeights[pid] = a.minWeight
		}
		total += newWeights[pid]
	}
	if total > 0 {
		for pid := range newWeights {
			newWeights[pid] /= total
		}
	}

	a.weights.Store(&adaptiveWeightSnapshot{byProvider: newWeights})
}
