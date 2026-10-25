package routing

import (
	"math/rand/v2"
	"sync/atomic"

	"github.com/futureagi/agentcc-gateway/internal/config"
)

// Adaptive defaults, used where AdaptiveConfig leaves a field zero.
const (
	defaultAdaptiveLearning      = 20   // samples across the targets before ranking
	defaultAdaptiveExplore       = 0.05 // share of selections per other target
	defaultAdaptiveLatencyWeight = 0.5
	defaultAdaptiveErrorWeight   = 0.4
	maxAdaptiveExplore           = 0.5
	maxAdaptiveErrorOdds         = 99 // an error rate of 99% or more
)

// AdaptiveStrategy routes to the target that has recently served the model
// best: lowest latency, with a penalty for errors. It reads the signals when
// it selects, so it runs no background loop and keeps no state but a
// counter:
//
//   - a target's latency is its model's p50 from per-model health when that
//     has samples, else its provider's latency EWMA; its error rate comes
//     from the same source (per-model health, else the provider's health);
//   - its score is latency_weight × latency / the slowest target's latency,
//     plus error_rate_weight × the odds of an error (error rate / success
//     rate, capped at 99), from AdaptiveConfig.SignalWeights;
//   - until the targets have learning_requests samples between them it
//     round-robins, so each one is measured;
//   - then the lowest score wins, except that min_weight of the selections
//     per other target go to one of the others, so a target that recovers is
//     noticed. A target with no samples yet scores as the best known one.
//
// AdaptiveConfig's update_interval and smoothing_factor are not read: the
// scores are computed from the live EWMA at every selection.
type AdaptiveStrategy struct {
	counter       atomic.Uint64
	learning      int
	explore       float64
	latencyWeight float64
	errorWeight   float64
	deps          strategyDeps
}

// NewAdaptiveStrategy creates an adaptive strategy. With nil deps it
// round-robins until BindStrategyDeps gives it some.
func NewAdaptiveStrategy(cfg config.AdaptiveConfig, deps *StrategyDeps) *AdaptiveStrategy {
	a := &AdaptiveStrategy{
		learning:      cfg.LearningRequests,
		explore:       cfg.MinWeight,
		latencyWeight: cfg.SignalWeights.Latency,
		errorWeight:   cfg.SignalWeights.ErrorRate,
	}
	if a.learning <= 0 {
		a.learning = defaultAdaptiveLearning
	}
	if a.explore <= 0 || a.explore >= 1 {
		a.explore = defaultAdaptiveExplore
	}
	if a.latencyWeight <= 0 {
		a.latencyWeight = defaultAdaptiveLatencyWeight
	}
	if a.errorWeight <= 0 {
		a.errorWeight = defaultAdaptiveErrorWeight
	}
	a.deps.fix(deps)
	return a
}

// withDeps returns a new adaptive strategy with a's tuning that reads d.
func (a *AdaptiveStrategy) withDeps(d StrategyDeps) *AdaptiveStrategy {
	b := &AdaptiveStrategy{learning: a.learning, explore: a.explore, latencyWeight: a.latencyWeight, errorWeight: a.errorWeight}
	b.deps.fix(&d)
	return b
}

func (a *AdaptiveStrategy) Name() string { return StrategyAdaptive }

func (a *AdaptiveStrategy) strategyDepsCell() *strategyDeps { return &a.deps }

// targetSignal is what adaptive knows about one target.
type targetSignal struct {
	samples   int64
	latencyMs float64 // 0: unknown
	errorRate float64
}

// Select ranks targets by their own model overrides (ModelStrategy).
func (a *AdaptiveStrategy) Select(targets []RoutingTarget, tracker *LatencyTracker) (int, error) {
	return a.SelectModel("", targets, tracker)
}

// SelectModel ranks targets by how each has served model (a target's own
// override wins).
func (a *AdaptiveStrategy) SelectModel(model string, targets []RoutingTarget, tracker *LatencyTracker) (int, error) {
	n := len(targets)
	if n == 0 {
		return 0, errNoTargets
	}
	if n == 1 {
		return 0, nil
	}
	d := a.deps.get()
	if d.Latency == nil {
		d.Latency = tracker
	}

	var buf [16]targetSignal
	sig := buf[:0]
	if n > len(buf) {
		sig = make([]targetSignal, 0, n)
	}
	var total int64
	var slowest, fastest float64
	for _, t := range targets {
		s := signalFor(d, t.ProviderID, t.ModelFor(model))
		total += s.samples
		if s.latencyMs > 0 {
			slowest = max(slowest, s.latencyMs)
			if fastest == 0 || s.latencyMs < fastest {
				fastest = s.latencyMs
			}
		}
		sig = append(sig, s)
	}

	pick := a.counter.Add(1) - 1
	if total < int64(a.learning) {
		return int(pick % uint64(n)), nil
	}

	var tieBuf [16]int
	best := tieBuf[:0]
	var bestScore float64
	for i, s := range sig {
		score := a.score(s, fastest, slowest)
		switch {
		case len(best) == 0 || score < bestScore:
			best, bestScore = append(best[:0], i), score
		case score == bestScore:
			best = append(best, i)
		}
	}
	chosen := best[pick%uint64(len(best))]

	if explore := min(a.explore*float64(n-1), maxAdaptiveExplore); rand.Float64() < explore {
		other := rand.IntN(n - 1)
		if other >= chosen {
			other++
		}
		return other, nil
	}
	return chosen, nil
}

// score is a target's weighted latency plus its weighted error odds; lower
// is better. A target with no latency yet takes the fastest known one.
func (a *AdaptiveStrategy) score(s targetSignal, fastest, slowest float64) float64 {
	lat := s.latencyMs
	if lat == 0 {
		lat = fastest
	}
	var latency float64
	if slowest > 0 {
		latency = lat / slowest
	}
	odds := float64(maxAdaptiveErrorOdds)
	if s.errorRate < 0.99 {
		odds = s.errorRate / (1 - s.errorRate)
	}
	return a.latencyWeight*latency + a.errorWeight*odds
}

// signalFor reads one target's samples, latency and error rate: from
// per-model health when it has samples, else from the provider's.
func signalFor(d StrategyDeps, providerID, model string) targetSignal {
	if d.ModelHealth != nil {
		if snap := d.ModelHealth.Snapshot(providerID, model); snap.N > 0 {
			return targetSignal{samples: int64(snap.N), latencyMs: snap.P50Ms, errorRate: snap.ErrorRate}
		}
	}
	var s targetSignal
	if d.Latency != nil {
		s.latencyMs = d.Latency.Get(providerID)
	}
	if d.Health != nil {
		if h := d.Health.GetHealth(providerID); h.RequestCount > 0 {
			s.samples = h.RequestCount
			s.errorRate = 1 - h.SuccessRate
		}
	}
	return s
}
