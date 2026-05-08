package routing

// RoutingTarget represents a single provider endpoint for load balancing.
type RoutingTarget struct {
	ProviderID    string
	Weight        int
	Priority      int
	ModelOverride string
	Healthy       bool
}
