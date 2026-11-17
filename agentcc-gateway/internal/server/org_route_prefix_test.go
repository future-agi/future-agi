package server

import "testing"

// A "<provider>/<model>" id reaches the org provider its prefix names when
// that provider serves it, ahead of another provider that lists the whole id
// as its own model name, whatever the ids' order. With no provider of that
// name serving it, a literal listing still matches as before, and an org
// model_map entry for the id still wins.

func TestOrgPrefixedModelPrefersTheProviderItNames(t *testing.T) {
	h := orgRouteHandlers()
	org := orgRouteConfig(map[string][]string{"aaa": {"zeta/x"}, "zeta": {"x"}})
	for _, model := range []string{"zeta/x", "Zeta/x"} {
		org := org
		if model == "Zeta/x" {
			org = orgRouteConfig(map[string][]string{"aaa": {"Zeta/x"}, "zeta": {"x"}})
		}
		for i := 0; i < 50; i++ {
			if id, upstream, _ := orgRoute(t, h, org, model); id != "zeta" || upstream != "x" {
				t.Fatalf("call %d: %q reached %q sent as %q, want zeta (named by the prefix) sent as x", i, model, id, upstream)
			}
		}
		if got := orgProviderServing(org, model); got != "zeta" {
			t.Errorf("orgProviderServing(%q) = %q, want zeta", model, got)
		}
	}
	// The named provider also wins when it lists the whole id itself.
	org = orgRouteConfig(map[string][]string{"aaa": {"zeta/x"}, "zeta": {"zeta/x"}})
	if id, _, _ := orgRoute(t, h, org, "zeta/x"); id != "zeta" {
		t.Errorf("zeta/x reached %q, want zeta, which lists it too", id)
	}
}

func TestOrgPrefixedModelWithNoProviderOfThatNameKeepsTheLiteralListing(t *testing.T) {
	h := orgRouteHandlers()
	for _, tc := range []struct {
		name    string
		listing map[string][]string
		want    string
	}{
		{"no provider zeta", map[string][]string{"bbb": {"zeta/x"}, "aaa": {"zeta/x"}}, "aaa"},
		{"provider zeta does not serve x", map[string][]string{"aaa": {"zeta/x"}, "zeta": {"y"}}, "aaa"},
	} {
		if id, upstream, _ := orgRoute(t, h, orgRouteConfig(tc.listing), "zeta/x"); id != tc.want || upstream != "zeta/x" {
			t.Errorf("%s: zeta/x reached %q sent as %q, want %s sent as zeta/x", tc.name, id, upstream, tc.want)
		}
	}
	// A disabled provider of that name does not take the id either.
	org := orgRouteConfig(map[string][]string{"aaa": {"zeta/x"}, "zeta": {"x"}})
	org.Providers["zeta"].Enabled = false
	if id, _, _ := orgRoute(t, h, org, "zeta/x"); id != "aaa" {
		t.Errorf("with zeta disabled, zeta/x reached %q, want aaa", id)
	}
}

func TestOrgModelMapStillWinsForAPrefixedModel(t *testing.T) {
	h := orgRouteHandlers()
	org := orgRouteConfig(map[string][]string{"aaa": {"zeta/x"}, "zeta": {"x"}})
	org.ModelMap = map[string]string{"zeta/x": "aaa"}
	if id, _, _ := orgRoute(t, h, org, "zeta/x"); id != "aaa" {
		t.Errorf("zeta/x reached %q, want aaa (the model_map entry)", id)
	}
}
