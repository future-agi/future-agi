package routing

import "testing"

// A breaker key with neither a model nor an endpoint is the provider id;
// TestBreakerKeyPerModelAndEndpoint covers the per-model and per-endpoint
// keys.
func TestBreakerKeyWithoutModelIsProvider(t *testing.T) {
	if got := BreakerKey("p", "", ""); got != "p" {
		t.Errorf("BreakerKey(p, \"\", \"\") = %q, want the provider id", got)
	}
	if s := NoModelHealth.Snapshot("openai", "gpt-4o"); s != (ModelHealthSnapshot{}) {
		t.Errorf("NoModelHealth snapshot = %+v, want empty", s)
	}
}

func TestRoutingTargetModelFor(t *testing.T) {
	if got := (RoutingTarget{ProviderID: "a"}).ModelFor("gpt-4o"); got != "gpt-4o" {
		t.Errorf("no override: %q", got)
	}
	if got := (RoutingTarget{ProviderID: "a", ModelOverride: "gpt-4o-2024"}).ModelFor("gpt-4o"); got != "gpt-4o-2024" {
		t.Errorf("override: %q", got)
	}
}
