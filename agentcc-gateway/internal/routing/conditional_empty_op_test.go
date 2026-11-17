package routing

import (
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
)

// A conditional route whose condition lost its op (a file whose "$eq" was
// read as an empty environment variable) is refused by the router, and with
// it the whole list: NewConditionalRouter returns no router, so the gateway
// runs with no conditional routes at all (server.go logs the error and goes
// on). The route matches nothing, and neither do the valid routes beside it.
func TestARouteWithAnEmptyOpRefusesTheWholeRouteList(t *testing.T) {
	routes := []config.ConditionalRouteConfig{
		{Name: "valid", Priority: 10,
			Condition: config.ConditionConfig{Field: "model", Op: OpEq, Value: "gpt-4o"},
			Action:    config.RouteActionConfig{Provider: "openai"}},
		{Name: "emptied", Priority: 20,
			Condition: config.ConditionConfig{Field: "metadata.tier", Op: "", Value: "enterprise"},
			Action:    config.RouteActionConfig{Provider: "openai-dedicated"}},
	}
	router, err := NewConditionalRouter(routes)
	if err == nil {
		t.Fatalf("router accepted a route with an empty op (%d routes)", router.RouteCount())
	}
	if router != nil {
		t.Fatalf("router = %v, want none: the valid route must not run alone either", router)
	}
}
