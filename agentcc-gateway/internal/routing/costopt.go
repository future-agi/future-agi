package routing

import (
	"errors"
	"sync/atomic"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

var errNoTargets = errors.New("no routing targets to select from")

// CostOptimizedStrategy routes to the target that serves the model cheapest:
// by blended price, read from the price lookup when it selects (the
// gateway's lookup is catalog first, then modeldb), so a catalog refresh
// shows up without a config push. Equal prices go to the lowest priority
// value, then round-robin. Unpriced targets come after every priced one,
// ranked by priority, then round-robin; with no lookup at all every target
// is unpriced, which is the priority ranking.
type CostOptimizedStrategy struct {
	counter atomic.Uint64
	deps    strategyDeps
}

// NewCostOptimized creates a cost-optimized strategy with no price lookup:
// NewStrategy or BindStrategyDeps gives it one.
func NewCostOptimized() *CostOptimizedStrategy {
	return &CostOptimizedStrategy{}
}

func (s *CostOptimizedStrategy) Name() string { return StrategyCostOptimized }

func (s *CostOptimizedStrategy) strategyDepsCell() *strategyDeps { return &s.deps }

// BlendedPrice is a card's price per token for a typical request, which
// reads about three input tokens for each output token: (3·prompt +
// completion) / 4.
func BlendedPrice(card models.PriceCard) float64 {
	return (3*card.Prompt + card.Completion) / 4
}

// Select ranks targets by their own model overrides (ModelStrategy).
func (s *CostOptimizedStrategy) Select(targets []RoutingTarget, tracker *LatencyTracker) (int, error) {
	return s.SelectModel("", targets, tracker)
}

// SelectModel ranks targets by what serving model costs on each (a target's
// own override wins).
func (s *CostOptimizedStrategy) SelectModel(model string, targets []RoutingTarget, _ *LatencyTracker) (int, error) {
	if len(targets) == 0 {
		return 0, errNoTargets
	}
	prices := s.deps.get().Prices

	var buf [16]int
	best := buf[:0]
	var bestRank costRank
	for i, t := range targets {
		rank := costRank{priority: t.Priority}
		if prices != nil {
			if card, ok := prices(t.ProviderID, t.ModelFor(model), ""); ok {
				rank.priced, rank.price = true, BlendedPrice(card)
			}
		}
		switch {
		case len(best) == 0 || rank.before(bestRank):
			best, bestRank = append(best[:0], i), rank
		case !bestRank.before(rank):
			best = append(best, i)
		}
	}
	n := s.counter.Add(1) - 1
	return best[n%uint64(len(best))], nil
}

// costRank orders targets: priced before unpriced, then by blended price,
// then by priority value.
type costRank struct {
	priced   bool
	price    float64
	priority int
}

func (a costRank) before(b costRank) bool {
	if a.priced != b.priced {
		return a.priced
	}
	if a.priced && a.price != b.price {
		return a.price < b.price
	}
	return a.priority < b.priority
}
