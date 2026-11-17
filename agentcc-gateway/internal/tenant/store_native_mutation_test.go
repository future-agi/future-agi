package tenant

import (
	"errors"
	"github.com/futureagi/agentcc-gateway/internal/routebinding"
	"math"
	"testing"
)

func TestOwnedMutationAliasIsolation(t *testing.T) {
	for _, path := range []string{"input", "get", "all", "provider", "bulk", "fetch", "observer", "data"} {
		t.Run(path, func(t *testing.T) {
			s, g := boundStore(t)
			input := nativeSyntheticConfig()
			var observed *OrgConfig
			s.ObserveChanges(func(_ string, c *OrgConfig) { observed = c })
			configs := map[string]*OrgConfig{"org": input}
			switch path {
			case "bulk":
				requireMutation(t, s.LoadBulk(configs))
			case "fetch":
				requireMutation(t, s.MergeFetched(configs, map[string]struct{}{"org": {}}, 0))
			default:
				requireMutation(t, s.Set("org", input))
			}
			v := snapshot(t, s, g, "org")
			var alias *ProviderConfig
			switch path {
			case "get":
				alias = s.Get("org").Providers["synthetic"]
			case "all":
				alias = s.GetAll()["org"].Providers["synthetic"]
			case "provider":
				alias = s.GetProviderConfig("org", "synthetic")
			case "observer":
				alias = observed.Providers["synthetic"]
			case "data":
				alias = v.Data().Providers["synthetic"]
			default:
				alias = input.Providers["synthetic"]
			}
			alias.Weight = 7
			delete(configs, "org")
			if s.Get("org").Providers["synthetic"].Weight != 1 || v.Data().Providers["synthetic"].Weight != 1 || !current(s, g, "org", v) {
				t.Fatal("owned data borrowed")
			}
		})
	}
}
func unsupportedFixture() *OrgConfig {
	c := nativeSyntheticConfig()
	c.Routing = &RoutingConfig{ConditionalRoutes: []ConditionalRoute{{Value: func() {}}}}
	return c
}
func TestOwnedWholeBatchRejection(t *testing.T) {
	for _, kind := range []string{"set", "bulk", "fetch", "merge delegate"} {
		t.Run(kind, func(t *testing.T) {
			s, g := boundStore(t)
			requireMutation(t, s.Set("old", nativeSyntheticConfig()))
			old := snapshot(t, s, g, "old")
			gen := s.Generation()
			n := 0
			s.ObserveChanges(func(string, *OrgConfig) { n++ })
			s.SetOnChange(func(string) { n++ })
			batch := map[string]*OrgConfig{"first": nativeSyntheticConfig(), "last": unsupportedFixture()}
			var err error
			switch kind {
			case "set":
				err = s.Set("old", batch["last"])
			case "bulk":
				err = s.LoadBulk(batch)
			case "fetch":
				err = s.MergeFetched(batch, map[string]struct{}{"first": {}, "last": {}}, gen)
			default:
				err = s.MergeBulk(batch, map[string]struct{}{"first": {}, "last": {}})
			}
			if !errors.Is(err, ErrConfigGraph) || s.Generation() != gen || n != 0 || s.Count() != 1 || !current(s, g, "old", old) {
				t.Fatal("failed batch changed publication")
			}
		})
	}
}
func TestOwnedPushCounterExhaustionRevokesWithoutBatchCommit(t *testing.T) {
	s, g := boundStore(t)
	requireMutation(t, s.Set("old", nativeSyntheticConfig()))
	v := snapshot(t, s, g, "old")
	n := 0
	s.ObserveChanges(func(string, *OrgConfig) { n++ })
	requireMutation(t, g.Publish(func(w *routebinding.Write) error {
		return w.Use(g, func() error { s.mu.Lock(); defer s.mu.Unlock(); s.gen = math.MaxUint64; return nil })
	}))
	err := s.LoadBulk(map[string]*OrgConfig{"new": nativeSyntheticConfig()})
	if !errors.Is(err, ErrNativeLifecycle) || s.Count() != 1 || s.Get("old") == nil || s.Get("new") != nil || n != 0 || s.Generation() != math.MaxUint64 || current(s, g, "old", v) {
		t.Fatal("push counter exhaustion wrapped or published")
	}
}

func TestOwnedClosedMutationRefuses(t *testing.T) {
	for _, kind := range []string{"set", "delete", "bulk", "fetch", "merge delegate"} {
		t.Run(kind, func(t *testing.T) {
			s, g := boundStore(t)
			requireMutation(t, s.Set("org", nativeSyntheticConfig()))
			gen := s.Generation()
			n := 0
			s.ObserveChanges(func(string, *OrgConfig) { n++ })
			requireMutation(t, g.Close())
			var err error
			switch kind {
			case "set":
				err = s.Set("org", nativeSyntheticConfig())
			case "delete":
				err = s.Delete("org")
			case "bulk":
				err = s.LoadBulk(nil)
			case "fetch":
				err = s.MergeFetched(nil, nil, gen)
			default:
				err = s.MergeBulk(nil, nil)
			}
			if !errors.Is(err, ErrNativeLifecycle) || s.Generation() != gen || s.Count() != 1 || n != 0 {
				t.Fatal("closed mutation succeeded or changed state")
			}
		})
	}
}
