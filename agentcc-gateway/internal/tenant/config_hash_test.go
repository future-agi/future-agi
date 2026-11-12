package tenant

import (
	"errors"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/routebinding"
)

type scratchSerializer struct {
	Values []int
	Mode   string
}

var retainedScratch *scratchSerializer
var serializerVisit func()

func (s *scratchSerializer) MarshalJSON() ([]byte, error) {
	retainedScratch = s
	if serializerVisit != nil {
		serializerVisit()
	}
	s.Values[0] = 9
	switch s.Mode {
	case "panic":
		panic("fixed serializer panic")
	case "error":
		return nil, errors.New("fixed serializer error")
	}
	return []byte(`0`), nil
}

type scratchText struct{ Values []int }

var retainedText *scratchText

func (s *scratchText) MarshalText() ([]byte, error) {
	retainedText = s
	s.Values[0] = 9
	return []byte("synthetic"), nil
}
func serializerConfig(mode string) *OrgConfig {
	c := rolloutFixture()
	c.Routing.ConditionalRoutes = []ConditionalRoute{{Value: &scratchSerializer{Values: []int{1}, Mode: mode}}}
	c.Routing.Rollout.Routing.ConditionalRoutes = c.Routing.ConditionalRoutes
	return c
}
func TestHashScratchIsolationAndReturnedError(t *testing.T) {
	defer func() { retainedScratch = nil; serializerVisit = nil }()
	for _, mode := range []string{"ok", "error"} {
		c := serializerConfig(mode)
		_, err := legacyHashForOwnedDraft(c)
		if err != nil || retainedScratch == nil {
			t.Fatal("supported serializer rejected")
		}
		retainedScratch.Values[0] = 8
		if c.Routing.ConditionalRoutes[0].Value.(*scratchSerializer).Values[0] != 1 {
			t.Fatal("hash scratch borrowed draft")
		}
	}
	c := &OrgConfig{Routing: &RoutingConfig{ConditionalRoutes: []ConditionalRoute{{Value: &scratchText{Values: []int{1}}}}}}
	_, err := legacyHashForOwnedDraft(c)
	if err != nil || retainedText == nil {
		t.Fatal("text serializer rejected")
	}
	retainedText.Values[0] = 8
	if c.Routing.ConditionalRoutes[0].Value.(*scratchText).Values[0] != 1 {
		t.Fatal("text scratch borrowed draft")
	}
	retainedText = nil
}
func TestOwnedSerializerScratchReentrantGet(t *testing.T) {
	defer func() { retainedScratch = nil; serializerVisit = nil }()
	for _, kind := range []string{"set", "fetch"} {
		t.Run(kind, func(t *testing.T) {
			s, g := boundStore(t)
			visits := 0
			serializerVisit = func() {
				visits++
				if !s.mu.TryRLock() {
					t.Error("serializer under store lock")
					return
				}
				s.mu.RUnlock()
				_ = s.Get("org")
			}
			c := serializerConfig("ok")
			if kind == "set" {
				requireMutation(t, s.Set("org", c))
			} else {
				requireMutation(t, s.MergeFetched(map[string]*OrgConfig{"org": c}, map[string]struct{}{"org": {}}, 0))
			}
			if visits == 0 || retainedScratch == nil {
				t.Fatal("serializer did not execute")
			}
			retainedScratch.Values[0] = 6
			v := snapshot(t, s, g, "org")
			a := s.Get("org")
			k, ok := a.LegacyCacheKey()
			if !ok {
				t.Fatal("expected provenance unavailable")
			}
			owned, ok, err := a.CopyForLegacyCache(k)
			if !ok || err != nil {
				t.Fatal("copy failed")
			}
			for _, data := range []*OrgConfig{c, a, v.Data(), owned, a.Candidate()} {
				if data.Routing.ConditionalRoutes[0].Value.(*scratchSerializer).Values[0] != 1 {
					t.Fatal("serializer changed non-scratch data")
				}
			}
			before := visits
			if !current(s, g, "org", v) {
				t.Fatal("currentness failed")
			}
			_, _ = a.LegacyCacheKey()
			if visits != before {
				t.Fatal("equality invoked callback")
			}
		})
	}
}
func TestOwnedSerializerPanicBatchNoCommit(t *testing.T) {
	defer func() { retainedScratch = nil; serializerVisit = nil }()
	for _, kind := range []string{"set", "fetch"} {
		t.Run(kind, func(t *testing.T) {
			s, g := boundStore(t)
			requireMutation(t, s.Set("old", nativeSyntheticConfig()))
			old := snapshot(t, s, g, "old")
			gen := s.Generation()
			n := 0
			s.ObserveChanges(func(string, *OrgConfig) { n++ })
			s.SetOnChange(func(string) { n++ })
			var err error
			if kind == "set" {
				err = s.Set("old", serializerConfig("panic"))
			} else {
				err = s.MergeFetched(map[string]*OrgConfig{"a": nativeSyntheticConfig(), "z": serializerConfig("panic")}, map[string]struct{}{"a": {}, "z": {}}, gen)
			}
			if !errors.Is(err, ErrConfigPreparation) || s.Count() != 1 || s.Generation() != gen || n != 0 || !current(s, g, "old", old) {
				t.Fatal("serializer panic partially published")
			}
		})
	}
}
func TestOwnedLaterHashFailureLeavesIndexesAndReentrantPublication(t *testing.T) {
	defer func() { retainedScratch = nil; serializerVisit = nil }()
	s, g := boundStore(t)
	original := nativeSyntheticConfig()
	original.Privacy = &PrivacyConfig{Enabled: true, Patterns: []*RedactPatternConfig{{Name: "synthetic", Pattern: "synthetic"}}}
	requireMutation(t, s.Set("old", original))
	old := snapshot(t, s, g, "old")
	redactor := s.Redactor("old")
	gen := s.Generation()
	hashes := len(s.configHash)
	pushes := len(s.pushedGen)
	notices := 0
	s.ObserveChanges(func(string, *OrgConfig) { notices++ })
	calls := 0
	serializerVisit = func() {
		calls++
		if calls == 2 {
			panic("fixed later serializer panic")
		}
	}
	a, b := serializerConfig("ok"), serializerConfig("ok")
	a.Routing.Rollout = nil
	b.Routing.Rollout = nil
	err := s.MergeFetched(map[string]*OrgConfig{"a": a, "b": b}, map[string]struct{}{"a": {}, "b": {}}, gen)
	if !errors.Is(err, ErrConfigPreparation) || calls != 2 || notices != 0 || s.Count() != 1 || s.Generation() != gen || len(s.configHash) != hashes || len(s.pushedGen) != pushes || s.Redactor("old") != redactor || !current(s, g, "old", old) {
		t.Fatal("later hash failure partially committed")
	}
	serializerVisit = func() {
		serializerVisit = nil
		requireMutation(t, s.Set("independent", nativeSyntheticConfig()))
		panic("fixed reentrant serializer panic")
	}
	err = s.Set("outer", serializerConfig("ok"))
	if !errors.Is(err, ErrConfigPreparation) || s.Get("independent") == nil || s.Get("outer") != nil || s.Generation() != gen+1 || !current(s, g, "old", old) {
		t.Fatal("outer failure rolled back independent publication")
	}
}

func serializerPrivacyConfig(mode, pattern string) *OrgConfig {
	c := serializerConfig(mode)
	c.Routing.Rollout = nil
	c.Privacy = &PrivacyConfig{Enabled: true, Patterns: []*RedactPatternConfig{{Name: "synthetic", Pattern: pattern}}}
	return c
}

// Accessors must inspect/copy structural data without invoking MarshalJSON.
func requireSerializerBoundaryData(t *testing.T, s *Store, g *routebinding.Gate, v NativeView, input *OrgConfig, visits *int, output string) LegacyConfigKey {
	t.Helper()
	before := *visits
	get := s.Get("org")
	key, ok := get.LegacyCacheKey()
	if !ok {
		t.Fatal("serializer fixture lost provenance")
	}
	owned, ok, err := get.CopyForLegacyCache(key)
	if !ok || err != nil {
		t.Fatal("serializer fixture cache copy refused")
	}
	for _, cfg := range []*OrgConfig{input, get, s.GetAll()["org"], v.Data(), owned} {
		if cfg == nil || cfg.Routing.ConditionalRoutes[0].Value.(*scratchSerializer).Values[0] != 1 {
			t.Fatal("scratch serializer changed retained structural data")
		}
	}
	if !current(s, g, "org", v) {
		t.Fatal("serializer fixture view not current")
	}
	redactor := s.Redactor("org")
	if redactor == nil || redactor.Redact("alpha beta") != output {
		t.Fatal("privacy output differs from current policy")
	}
	if *visits != before {
		t.Fatal("data, provenance, currentness or redactor accessor serialized")
	}
	return key
}

func TestOwnedSerializerAndRedactorBoundaries(t *testing.T) {
	defer func() { retainedScratch = nil; serializerVisit = nil }()
	s, g := boundStore(t)
	visits := 0
	serializerVisit = func() { visits++ }
	original := serializerPrivacyConfig("ok", "alpha")
	requireMutation(t, s.Set("org", original))
	if visits != 1 || retainedScratch == nil {
		t.Fatal("initial successful serializer missing")
	}
	retainedScratch.Values[0] = 8
	v := snapshot(t, s, g, "org")
	key := requireSerializerBoundaryData(t, s, g, v, original, &visits, "[REDACTED:synthetic] beta")
	redactor := s.Redactor("org")
	gen := s.Generation()
	oldHash := s.configHash["org"]
	observed, changed := 0, 0
	s.ObserveChanges(func(string, *OrgConfig) { observed++ })
	s.SetOnChange(func(string) { changed++ })
	equal := serializerPrivacyConfig("ok", "alpha")
	requireMutation(t, s.MergeFetched(map[string]*OrgConfig{"org": equal}, map[string]struct{}{"org": {}}, gen))
	if visits != 2 || snapshot(t, s, g, "org") != v || s.Generation() != gen || observed != 0 || changed != 0 || s.configHash["org"] != oldHash || s.Redactor("org") != redactor {
		t.Fatal("successful equal serialization changed native or legacy classification")
	}
	retainedScratch.Values[0] = 8
	nextKey := requireSerializerBoundaryData(t, s, g, v, equal, &visits, "[REDACTED:synthetic] beta")
	if nextKey == key {
		t.Fatal("equal fetch did not rotate legacy replacement identity")
	}
	changedPolicy := serializerPrivacyConfig("ok", "beta")
	requireMutation(t, s.MergeFetched(map[string]*OrgConfig{"org": changedPolicy}, map[string]struct{}{"org": {}}, gen))
	newView := snapshot(t, s, g, "org")
	if visits != 3 || newView == v || current(s, g, "org", v) || observed != 1 || changed != 1 || s.Generation() != gen || s.Redactor("org") == redactor {
		t.Fatal("genuine privacy change retained prior publication or redactor")
	}
	if redactor.Redact("alpha beta") != "[REDACTED:synthetic] beta" || v.Data().Privacy.Patterns[0].Pattern != "alpha" {
		t.Fatal("historical policy or redactor was mutated")
	}
	retainedScratch.Values[0] = 8
	workingKey := requireSerializerBoundaryData(t, s, g, newView, changedPolicy, &visits, "alpha [REDACTED:synthetic]")
	workingRedactor := s.Redactor("org")
	workingHash := s.configHash["org"]
	err := s.MergeFetched(map[string]*OrgConfig{"org": serializerPrivacyConfig("panic", "alpha")}, map[string]struct{}{"org": {}}, gen)
	if !errors.Is(err, ErrConfigPreparation) || visits != 4 || snapshot(t, s, g, "org") != newView || s.Generation() != gen || observed != 1 || changed != 1 || s.configHash["org"] != workingHash || s.Redactor("org") != workingRedactor {
		t.Fatal("serializer panic changed working publication")
	}
	if requireSerializerBoundaryData(t, s, g, newView, changedPolicy, &visits, "alpha [REDACTED:synthetic]") != workingKey {
		t.Fatal("panic rotated legacy identity")
	}
}

func TestLegacyUnboundHashBorrowWitness(t *testing.T) {
	defer func() { retainedScratch = nil; serializerVisit = nil }()
	c := serializerConfig("ok")
	_ = hashConfig(c)
	if c.Routing.ConditionalRoutes[0].Value.(*scratchSerializer).Values[0] != 9 {
		t.Fatal("legacy mechanism changed; revisit witness")
	}
}
