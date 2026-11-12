package tenant

import "testing"

type privateScalar struct {
	Value  int
	hidden bool
}

func TestOwnedEqualFetchRetainsNativeReplacesLegacy(t *testing.T) {
	s, g := boundStore(t)
	requireMutation(t, s.Set("org", rolloutFixture()))
	v := snapshot(t, s, g, "org")
	a := s.Get("org")
	k, ok := a.LegacyCacheKey()
	if !ok {
		t.Fatal("legacy key missing")
	}
	n := 0
	s.ObserveChanges(func(string, *OrgConfig) { n++ })
	s.SetOnChange(func(string) { n++ })
	gen := s.Generation()
	requireMutation(t, s.MergeFetched(map[string]*OrgConfig{"org": rolloutFixture()}, map[string]struct{}{"org": {}}, gen))
	b := snapshot(t, s, g, "org")
	next, ok := s.Get("org").LegacyCacheKey()
	if v != b || !current(s, g, "org", v) || !ok || k == next || n != 0 || gen != s.Generation() {
		t.Fatal("equal fetch changed native identity or retained legacy identity")
	}
}
func TestOwnedRepeatedMarshalErrorRetainsNativeImage(t *testing.T) {
	defer func() { retainedScratch = nil; serializerVisit = nil }()
	s, g := boundStore(t)
	visits := 0
	serializerVisit = func() { visits++ }
	initial := serializerPrivacyConfig("error", "alpha")
	requireMutation(t, s.LoadBulk(map[string]*OrgConfig{"org": initial}))
	if _, hashed := s.configHash["org"]; hashed || visits != 0 {
		t.Fatal("LoadBulk seed unexpectedly hashed")
	}
	view := snapshot(t, s, g, "org")
	gen := s.Generation()
	key := requireSerializerBoundaryData(t, s, g, view, initial, &visits, "[REDACTED:synthetic] beta")
	observed, changed := 0, 0
	s.ObserveChanges(func(string, *OrgConfig) { observed++ })
	s.SetOnChange(func(string) { changed++ })
	var scratches []*scratchSerializer
	for i := 0; i < 2; i++ {
		oldHash, oldHashed := s.configHash["org"]
		oldRedactor := s.Redactor("org")
		beforeObserved, beforeChanged := observed, changed
		input := serializerPrivacyConfig("error", "alpha")
		requireMutation(t, s.MergeFetched(map[string]*OrgConfig{"org": input}, map[string]struct{}{"org": {}}, gen))
		if visits != i+1 || retainedScratch == nil {
			t.Fatal("returned-error serializer did not run exactly once")
		}
		if snapshot(t, s, g, "org") != view || !current(s, g, "org", view) || s.Generation() != gen {
			t.Fatal("returned marshal error changed exact native image or stamp")
		}
		newHash, hashed := s.configHash["org"]
		if !hashed {
			t.Fatal("returned-error fallback hash missing")
		}
		// Address/time fallback hashes are not guaranteed unique. The initial
		// missing hash deterministically invalidates; later calls follow the
		// observed hash relation, independently of native identity retention.
		invalidated := !oldHashed || oldHash != newHash
		wantNotices := 0
		if invalidated {
			wantNotices = 1
		}
		if observed-beforeObserved != wantNotices || changed-beforeChanged != wantNotices {
			t.Fatal("legacy invalidation did not follow observed hash relation")
		}
		if i == 0 && (!invalidated || observed != 1 || changed != 1) {
			t.Fatal("missing-hash first invalidation not witnessed")
		}
		scratches = append(scratches, retainedScratch)
		for _, scratch := range scratches {
			scratch.Values[0] = 8
		}
		nextKey := requireSerializerBoundaryData(t, s, g, view, input, &visits, "[REDACTED:synthetic] beta")
		if nextKey == key {
			t.Fatal("returned-error fetch retained legacy replacement identity")
		}
		if (s.Redactor("org") != oldRedactor) != invalidated {
			t.Fatal("redactor rebuild disagrees with legacy invalidation")
		}
		key = nextKey
	}
	if initial.Routing.ConditionalRoutes[0].Value.(*scratchSerializer).Values[0] != 1 || view.Data().Routing.ConditionalRoutes[0].Value.(*scratchSerializer).Values[0] != 1 {
		t.Fatal("repeated fallback mutated original or historical data")
	}
}

func TestOwnedHashEqualPrivateAndDynamicChange(t *testing.T) {
	for _, kind := range []string{"private", "dynamic", "nil-empty", "fallback"} {
		t.Run(kind, func(t *testing.T) {
			s, g := boundStore(t)
			a := nativeSyntheticConfig()
			b := nativeSyntheticConfig()
			a.Routing = &RoutingConfig{ConditionalRoutes: []ConditionalRoute{{Value: privateScalar{Value: 1}}}}
			b.Routing = &RoutingConfig{ConditionalRoutes: []ConditionalRoute{{Value: privateScalar{Value: 1, hidden: true}}}}
			if kind == "dynamic" {
				a.Routing.ConditionalRoutes[0].Value = int(1)
				b.Routing.ConditionalRoutes[0].Value = int64(1)
			}
			if kind == "nil-empty" {
				a.ModelMap = nil
				b.ModelMap = map[string]string{}
				b.Routing = a.Routing
			}
			if kind == "fallback" {
				a.Routing = &RoutingConfig{}
				b.Routing = &RoutingConfig{fallbackOff: true}
			}
			requireMutation(t, s.Set("org", a))
			v := snapshot(t, s, g, "org")
			observed, changed := 0, 0
			s.ObserveChanges(func(string, *OrgConfig) { observed++ })
			s.SetOnChange(func(string) { changed++ })
			requireMutation(t, s.MergeFetched(map[string]*OrgConfig{"org": b}, map[string]struct{}{"org": {}}, s.Generation()))
			if current(s, g, "org", v) || observed != 1 {
				t.Fatal("full graph difference retained native identity")
			}
			if kind != "fallback" && changed != 0 {
				t.Fatal("native-only change altered legacy classification")
			}
		})
	}
}
func TestOwnedFetchGuardsAndRetention(t *testing.T) {
	s, g := boundStore(t)
	before := s.Generation()
	requireMutation(t, s.Set("org", nativeSyntheticConfig()))
	v := snapshot(t, s, g, "org")
	requireMutation(t, s.MergeFetched(nil, nil, before))
	if !current(s, g, "org", v) {
		t.Fatal("fetch crossed push")
	}
	requireMutation(t, s.MergeFetched(nil, map[string]struct{}{"org": {}}, s.Generation()))
	if !current(s, g, "org", v) {
		t.Fatal("malformed entry lost")
	}
	before = s.Generation()
	requireMutation(t, s.Delete("org"))
	tomb := snapshot(t, s, g, "org")
	requireMutation(t, s.MergeFetched(map[string]*OrgConfig{"org": nativeSyntheticConfig()}, map[string]struct{}{"org": {}}, before))
	if !current(s, g, "org", tomb) {
		t.Fatal("fetch crossed delete")
	}
	before = s.Generation()
	requireMutation(t, s.LoadBulk(map[string]*OrgConfig{"org": nativeSyntheticConfig()}))
	v = snapshot(t, s, g, "org")
	requireMutation(t, s.MergeFetched(nil, nil, before))
	if !current(s, g, "org", v) {
		t.Fatal("fetch crossed bulk")
	}
	requireMutation(t, s.MergeFetched(nil, nil, s.Generation()))
	if current(s, g, "org", v) || s.Count() != 0 {
		t.Fatal("eligible fetch removal absent")
	}
}
