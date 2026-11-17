package routing

import (
	"sort"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
)

// The config loader keeps config.ConditionOperators as written and refuses
// any other op, so the list must be exactly the operators the router applies.
func TestConfigConditionOperatorsAreTheRoutersOperators(t *testing.T) {
	var router []string
	for op := range validOps {
		router = append(router, op)
	}
	loader := append([]string(nil), config.ConditionOperators...)
	sort.Strings(router)
	sort.Strings(loader)
	if len(router) != len(loader) {
		t.Fatalf("config.ConditionOperators = %q, router applies %q", loader, router)
	}
	for i := range router {
		if router[i] != loader[i] {
			t.Fatalf("config.ConditionOperators = %q, router applies %q", loader, router)
		}
	}
}
