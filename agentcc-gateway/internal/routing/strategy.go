package routing

import "fmt"

// Strategy selects a target from a list of healthy routing targets.
type Strategy interface {
	// Name returns the strategy identifier.
	Name() string

	// Select picks a target index from the given slice.
	// The slice contains only healthy targets.
	// Returns the index into the slice, or an error if no valid target.
	Select(targets []RoutingTarget, tracker *LatencyTracker) (int, error)
}

// NewStrategy creates a strategy by name.
// Note: "adaptive" requires additional parameters — use NewAdaptiveStrategy directly.
// "fastest" is handled at the handler level via RaceExecutor, not as a Strategy.
func NewStrategy(name string) (Strategy, error) {
	switch name {
	case "round-robin", "":
		return NewRoundRobin(), nil
	case "weighted":
		return &WeightedStrategy{}, nil
	case "least-latency":
		return &LeastLatencyStrategy{}, nil
	case "cost-optimized":
		return NewCostOptimized(), nil
	case "adaptive":
		// Adaptive requires tracker + health monitor; return round-robin as placeholder.
		// The actual adaptive strategy is injected by the server during init.
		return NewRoundRobin(), nil
	case "fastest":
		// Fastest (race) mode is handled by RaceExecutor, not the Strategy interface.
		// Use round-robin for any fallback routing within race mode.
		return NewRoundRobin(), nil
	default:
		return nil, fmt.Errorf("unknown routing strategy %q (supported: round-robin, weighted, least-latency, cost-optimized, adaptive, fastest)", name)
	}
}
