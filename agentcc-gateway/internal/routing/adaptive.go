package routing

import (
	"math"
	"math/rand"
	"sync"
	"sync/atomic"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
)

// AdaptiveStrategy dynamically adjusts provider weights based on real-time metrics.
type AdaptiveStrategy struct {
	mu              sync.RWMutex
	weights         map[string]float64
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
		weights:         make(map[string]float64),
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

	// Active phase: weighted random selection.
	a.mu.RLock()
	defer a.mu.RUnlock()

	// Compute total weight for available targets.
	type tw struct {
		idx    int
		weight float64
	}
	tws := make([]tw, 0, len(targets))
	var total float64
	for i, t := range targets {
		w := a.weights[t.ProviderID]
		if w < a.minWeight {
			w = a.minWeight
		}
		tws = append(tws, tw{idx: i, weight: w})
		total += w
	}

	if total <= 0 {
		return 0, nil
	}

	// Weighted random selection.
	r := rand.Float64() * total
	var cumulative float64
	for _, entry := range tws {
		cumulative += entry.weight
		if r <= cumulative {
			return entry.idx, nil
		}
	}
	return tws[len(tws)-1].idx, nil
}

// IncrementRequestCount is called by the handler after each request.
func (a *AdaptiveStrategy) IncrementRequestCount() {
	// Already incremented in Select, but this is for explicit tracking.
}

// GetWeights returns a copy of current weights.
func (a *AdaptiveStrategy) GetWeights() map[string]float64 {
	a.mu.RLock()
	defer a.mu.RUnlock()
	cpy := make(map[string]float64, len(a.weights))
	for k, v := range a.weights {
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

	// Normalize and smooth.
	a.mu.Lock()
	defer a.mu.Unlock()

	newWeights := make(map[string]float64, len(rawScores))
	for pid, raw := range rawScores {
		calculated := raw / totalRaw
		old := a.weights[pid]
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

	a.weights = newWeights
}
