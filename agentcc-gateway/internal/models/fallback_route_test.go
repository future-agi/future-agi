package models

import (
	"slices"
	"testing"
)

// A request's fallback route is fixed once and never changes afterwards: the
// first route given is kept, a later one and the zero route are refused, the
// declared list is copied in and out, Release clears the route for the next
// request, and an async job's clone resolves on the same route.
func TestFallbackRouteFixedOnce(t *testing.T) {
	declared := []string{"spare"}
	first := NewFallbackRoute("alias", "first", "first", true, declared)
	declared[0] = "changed"
	if got := first.Declared(); !slices.Equal(got, []string{"spare"}) {
		t.Fatalf("declared = %v, want the list as built", got)
	}
	out := first.Declared()
	out[0] = "changed"
	if got := first.Declared(); !slices.Equal(got, []string{"spare"}) {
		t.Fatalf("declared = %v after its copy changed", got)
	}

	rc := AcquireRequestContext()
	if rc.FallbackRoute().Routed() {
		t.Fatal("a new request has a fallback route")
	}
	if rc.FixFallbackRoute(FallbackRoute{}) || rc.FallbackRoute().Routed() {
		t.Fatal("the zero route was fixed")
	}
	if !rc.FixFallbackRoute(first) {
		t.Fatal("the first route was refused")
	}
	if rc.FixFallbackRoute(NewFallbackRoute("alias", "last", "last", true, []string{"first", "spare"})) {
		t.Fatal("a second route replaced the first")
	}
	r := rc.FallbackRoute()
	if r.Requested() != "alias" || r.Hop() != "first" || r.Inherit() != "first" || !r.Declares() || !slices.Equal(r.Declared(), []string{"spare"}) {
		t.Fatalf("route = %+v, want the first one", r)
	}
	clone := rc.CloneRequest()
	if c := clone.FallbackRoute(); c.Hop() != "first" || !slices.Equal(c.Declared(), []string{"spare"}) {
		t.Fatalf("clone route = %+v, want the request's", c)
	}
	clone.Release()
	rc.Release()
	if rc.FallbackRoute().Routed() {
		t.Fatal("Release kept the request's fallback route")
	}

	// No models[]: Declares is false and the plan comes from a configured chain.
	undeclared := NewFallbackRoute("alias", "first", "first", false, nil)
	if undeclared.Declares() || undeclared.Declared() != nil {
		t.Fatalf("undeclared route = %+v", undeclared)
	}
}
