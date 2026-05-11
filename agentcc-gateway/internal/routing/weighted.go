package routing

import (
	"errors"
	"math/rand/v2"
)

// WeightedStrategy selects targets proportional to their weights.
//
// Hot-path design notes:
//   - Uses math/rand/v2 which is lock-free (goroutine-local PCG state).
//     The legacy math/rand.Intn took a global mutex and dominated the
//     per-call cost on multi-core workloads.
//   - Single-target fast path skips the RNG entirely.
//   - Weight <= 0 is treated as 1 in one branch per target — predictable
//     and well-predicted under CPU branch predictors when weights are
//     uniformly positive.
type WeightedStrategy struct{}

func (s *WeightedStrategy) Name() string { return "weighted" }

func (s *WeightedStrategy) Select(targets []RoutingTarget, _ *LatencyTracker) (int, error) {
	n := len(targets)
	switch n {
	case 0:
		return 0, errors.New("no targets with positive weight")
	case 1:
		return 0, nil
	}

	// First pass: total weight.
	totalWeight := 0
	for i := 0; i < n; i++ {
		w := targets[i].Weight
		if w <= 0 {
			w = 1
		}
		totalWeight += w
	}
	if totalWeight == 0 {
		return 0, errors.New("no targets with positive weight")
	}

	// Second pass: cumulative pick. rand/v2 IntN is lock-free (PCG).
	r := rand.IntN(totalWeight)
	cumulative := 0
	for i := 0; i < n; i++ {
		w := targets[i].Weight
		if w <= 0 {
			w = 1
		}
		cumulative += w
		if r < cumulative {
			return i, nil
		}
	}
	return n - 1, nil
}
