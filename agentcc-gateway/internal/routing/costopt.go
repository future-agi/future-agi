package routing

import "sync/atomic"

// CostOptimizedStrategy prefers targets with the lowest priority value,
// then round-robins within that priority group.
type CostOptimizedStrategy struct {
	counter atomic.Uint64
}

// NewCostOptimized creates a cost-optimized strategy.
func NewCostOptimized() *CostOptimizedStrategy {
	return &CostOptimizedStrategy{}
}

func (s *CostOptimizedStrategy) Name() string { return "cost-optimized" }

func (s *CostOptimizedStrategy) Select(targets []RoutingTarget, _ *LatencyTracker) (int, error) {
	// Find minimum priority.
	minPriority := targets[0].Priority
	for _, t := range targets[1:] {
		if t.Priority < minPriority {
			minPriority = t.Priority
		}
	}

	// Collect indices in the lowest priority group.
	group := make([]int, 0, len(targets))
	for i, t := range targets {
		if t.Priority == minPriority {
			group = append(group, i)
		}
	}

	// Round-robin within priority group.
	n := s.counter.Add(1) - 1
	return group[int(n%uint64(len(group)))], nil
}
