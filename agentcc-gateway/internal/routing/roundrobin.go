package routing

import "sync/atomic"

// RoundRobinStrategy cycles through targets sequentially.
type RoundRobinStrategy struct {
	counter atomic.Uint64
}

// NewRoundRobin creates a round-robin strategy.
func NewRoundRobin() *RoundRobinStrategy {
	return &RoundRobinStrategy{}
}

func (s *RoundRobinStrategy) Name() string { return "round-robin" }

func (s *RoundRobinStrategy) Select(targets []RoutingTarget, _ *LatencyTracker) (int, error) {
	n := s.counter.Add(1) - 1 // zero-based
	return int(n % uint64(len(targets))), nil
}
