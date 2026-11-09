package auth

import (
	"context"
	"testing"
)

// Independent requiring test for the sandbox-origin contract (733 row 11).
// Exercise both the initial route and the cached-project route through stamping.
func TestSandboxOriginCannotSurviveRoutingAndStamping(t *testing.T) {
	sim := &routeSim{project: hiddenProject()}
	a, _ := newRouter(t, sim)
	result := sandboxResult()
	a.cache.putPositive("origin-contract", result)
	for _, phase := range []string{"initial", "cached"} {
		t.Run(phase, func(t *testing.T) {
			traces := routeTraces("customer-prod", hiddenNamePrefix+"66666666-6666-4666-8666-666666666666")
			traces.ResourceSpans().At(0).Resource().Attributes().PutStr("fi.simulation.origin", "forged-origin")
			traces.ResourceSpans().At(1).Resource().Attributes().PutInt("fi.simulation.origin", 733)
			out, err := a.RouteSimulation(context.Background(), "origin-contract", traces, result)
			if err != nil || out.Hidden != routeHiddenID || out.DroppedSpans() != 0 {
				t.Fatalf("route: outcome=%+v err=%v", out, err)
			}
			if dropped, err := StampResourceAttrs(context.Background(), a, "origin-contract", traces, result); err != nil || dropped != 0 {
				t.Fatalf("stamp: dropped=%d err=%v", dropped, err)
			}
			if traces.ResourceSpans().Len() != 2 || traces.SpanCount() != 4 {
				t.Fatalf("routed payload changed size: resources=%d spans=%d", traces.ResourceSpans().Len(), traces.SpanCount())
			}
			for i := 0; i < traces.ResourceSpans().Len(); i++ {
				for key, want := range map[string]string{
					"project_name":         routeHiddenKey,
					"fi.project_id":        routeHiddenID,
					"fi.org_id":            routeOrg,
					"fi.simulation.origin": "simulation",
				} {
					if got, ok := resourceStr(t, traces, i, key); !ok || got != want {
						t.Errorf("resource %d %s=%q present=%v; want %q", i, key, got, ok, want)
					}
				}
			}
			if sim.callCount() != 1 {
				t.Fatalf("hidden project lookups=%d; want one across initial and cached requests", sim.callCount())
			}
		})
	}
}

// Independent requiring test for the reserved-names contract (733 row 25).
// A pre-existing reserved project must not let either principal stamp its spans.
func TestNonSandboxStampDropsExistingReservedProject(t *testing.T) {
	for _, principal := range []Principal{PrincipalPersonal, PrincipalPlatform} {
		t.Run(string(principal), func(t *testing.T) {
			resolver := &namesRecorder{countingProjectResolver: countingProjectResolver{
				found: map[string]string{routeHiddenKey: routeHiddenID, "customer-prod": routeProdID},
			}}
			a, _ := newRouter(t, nil)
			a.projects = resolver
			result := &ResolveResult{OrgID: routeOrg, WorkspaceID: routeWorkspace, Principal: principal, Projects: map[string]string{}}
			a.cache.putPositive("reserved-contract", result)
			traces := routeTraces(routeHiddenKey, "customer-prod")
			dropped, err := StampResourceAttrs(context.Background(), a, "reserved-contract", traces, result)
			if err != nil {
				t.Fatal(err)
			}
			if dropped != 1 || traces.ResourceSpans().Len() != 1 || traces.SpanCount() != 2 {
				t.Fatalf("reserved resource survived stamping: dropped=%d resources=%d spans=%d; want 1/1/2", dropped, traces.ResourceSpans().Len(), traces.SpanCount())
			}
			if len(resolver.asked) != 1 || len(resolver.asked[0]) != 1 || resolver.asked[0][0] != "customer-prod" {
				t.Errorf("project lookups=%v; want only customer-prod", resolver.asked)
			}
			if _, ok := result.GetProject(routeHiddenKey); ok {
				t.Error("reserved project leaked into request authorization map")
			}
			entry, _ := a.cache.get("reserved-contract")
			if entry == nil {
				t.Fatal("positive control cache entry missing")
			}
			if _, ok := entry.result.GetProject(routeHiddenKey); ok {
				t.Error("reserved project leaked into cached authorization map")
			}
			if len(resolver.created) != 0 {
				t.Errorf("unexpected project creation: %v", resolver.created)
			}
			for key, want := range map[string]string{"project_name": "customer-prod", "fi.project_id": routeProdID, "fi.org_id": routeOrg} {
				if got, ok := resourceStr(t, traces, 0, key); !ok || got != want {
					t.Errorf("ordinary resource %s=%q present=%v; want %q", key, got, ok, want)
				}
			}
		})
	}
}
