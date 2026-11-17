package catalog

import "testing"

// A token quote is not a bound on a router's target universe or fan-out.
// These tests use the existing billability entry points so the initial red
// is behavioral, not a reference to an unimplemented contract type.
func TestRouterCreditsRequireEnforcedTargets(t *testing.T) {
	all, _, err := BundledOpenRouter()
	if err != nil {
		t.Fatal(err)
	}
	eps, err := BundledEndpoints()
	if err != nil {
		t.Fatal(err)
	}
	ids := []string{"typesafe/jev-router", "nvidia/switchyard", "openrouter/auto-beta", "openrouter/fusion", "openrouter/pareto-code", "openrouter/bodybuilder", "openrouter/auto"}
	count := 0
	for _, m := range all {
		if m.DynamicPrice {
			count++
		}
	}
	if count != len(ids) {
		t.Fatalf("dynamic router inventory changed: got %d, want %d", count, len(ids))
	}
	for _, id := range ids {
		t.Run(id, func(t *testing.T) {
			m := all[id]
			if m == nil || !m.DynamicPrice || len(eps.ByModel[id]) != 0 {
				t.Fatal("expected a dynamic bundled listing with no endpoint/target envelope")
			}
			if m.ManagedBillable(SourceOpenRouter) {
				t.Fatal("dynamic sentinel became a free credit offer")
			}
			for _, source := range []string{SourceOpenRouter, SourcePriceCard} {
				t.Run(source, func(t *testing.T) {
					// A refresh/override assigning a scalar quote must not make
					// an unconstrained router a bounded single-model invocation.
					quoted := Model{ID: id, Prices: Prices{Prompt: 1e-6, Completion: 2e-6}}
					if quoted.ManagedBillable(source) {
						t.Error("a scalar quote enabled a router without an enforced target set")
					}
					ep := Endpoint{Source: source, Prices: quoted.Prices}
					quoted.Endpoints = []Endpoint{ep}
					if ep.ManagedBillable(id) || quoted.ManagedBillable(source) {
						t.Error("provider endpoint pinning was mistaken for a model target bound")
					}
				})
			}
		})
	}
}

func TestRouterCreditsKeepFixedModelsAndFreeOffers(t *testing.T) {
	for _, m := range []*Model{
		{ID: "vendor/fixed", Prices: Prices{Prompt: 1e-6, Completion: 2e-6}},
		{ID: "openrouter/free"},
		{ID: "vendor/fixed:free"},
	} {
		if !m.ManagedBillable(SourceOpenRouter) {
			t.Errorf("non-router %s lost its supported offer", m.ID)
		}
	}
}
