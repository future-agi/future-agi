package tenant

import (
	"errors"
	"reflect"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/routebinding"
)

func overlapConfig(label string, weight float64) *OrgConfig {
	c := nativeSyntheticConfig()
	c.Providers["synthetic"].Weight = weight
	c.ModelMap = map[string]string{"synthetic": label}
	c.Routing = &RoutingConfig{DefaultModel: label}
	return c
}

func requireOverlapGraph(t *testing.T, got, want *OrgConfig) {
	t.Helper()
	a, errA := cloneConfigData(got)
	b, errB := cloneConfigData(want)
	if errA != nil || errB != nil || !reflect.DeepEqual(a, b) {
		t.Fatal("structural graph is incomplete or belongs to another publication")
	}
}

type overlapCopies struct {
	configs  []*OrgConfig
	provider *ProviderConfig
	view     NativeView
	key      LegacyConfigKey
	err      error
}

func collectOverlapCopies(s *Store, g *routebinding.Gate, expected NativeView) overlapCopies {
	out := overlapCopies{}
	get, all := s.Get("org"), s.GetAll()
	out.provider = s.GetProviderConfig("org", "synthetic")
	if get == nil || len(all) != 1 || all["org"] == nil || out.provider == nil {
		out.err = errors.New("reader lost complete current data")
		return out
	}
	out.err = g.Read(func(r *routebinding.Read) error {
		var err error
		out.view, err = s.NativeSnapshot(r, "org")
		if err != nil {
			return err
		}
		return s.CheckNativeView(r, "org", expected)
	})
	if out.err != nil {
		return out
	}
	var ok bool
	out.key, ok = get.LegacyCacheKey()
	if !ok {
		out.err = errors.New("reader lost provenance")
		return out
	}
	copied, ok, err := get.CopyForLegacyCache(out.key)
	if err != nil || !ok {
		out.err = errors.New("reader cache copy refused")
		return out
	}
	out.configs = []*OrgConfig{get, all["org"], out.view.Data(), copied}
	return out
}

func TestBoundCopyReadPublicationOverlap(t *testing.T) {
	for _, kind := range []string{"Set", "MergeFetched"} {
		t.Run(kind, func(t *testing.T) {
			defer func() { retainedScratch = nil; serializerVisit = nil }()
			s, g := boundStore(t)
			var observed atomic.Pointer[OrgConfig]
			s.ObserveChanges(func(_ string, cfg *OrgConfig) { observed.Store(cfg) })
			a, b := overlapConfig("A", 1), overlapConfig("B", 2)
			c := overlapConfig("C", 3)
			c.Routing.ConditionalRoutes = []ConditionalRoute{{Value: &scratchSerializer{Values: []int{1}, Mode: "ok"}}}
			requireMutation(t, s.Set("org", a))
			viewA := snapshot(t, s, g, "org")
			initial := collectOverlapCopies(s, g, viewA)
			requireMutation(t, initial.err)
			observerA := observed.Load()
			fetchedAt := s.Generation()
			entered, release := make(chan struct{}), make(chan struct{})
			var once sync.Once
			unblock := func() { once.Do(func() { close(release) }) }
			var workers sync.WaitGroup
			start := func(fn func()) {
				workers.Add(1)
				go func() { defer workers.Done(); fn() }()
			}
			defer func() {
				unblock()
				joined := make(chan struct{})
				go func() { workers.Wait(); close(joined) }()
				select {
				case <-joined:
				case <-time.After(5 * time.Second):
					t.Fatal("watchdog: overlap workers did not join")
				}
			}()
			visits := 0 // Only C serializes; read only after its result is joined.
			serializerVisit = func() {
				visits++
				if visits == 1 {
					close(entered)
				}
				<-release
			}
			cDone := make(chan error, 1)
			start(func() {
				if kind == "Set" {
					cDone <- s.Set("org", c)
				} else {
					cDone <- s.MergeFetched(map[string]*OrgConfig{"org": c}, map[string]struct{}{"org": {}}, fetchedAt)
				}
			})
			select {
			case <-entered: // Real mutation reached sacrificial hashing after structural detach.
			case <-time.After(5 * time.Second):
				t.Fatal("watchdog: C preparation not reached")
			}
			readDone := make(chan overlapCopies, 1)
			start(func() { readDone <- collectOverlapCopies(s, g, viewA) })
			var during overlapCopies
			select {
			case during = <-readDone:
			case <-time.After(5 * time.Second):
				t.Fatal("watchdog: reader blocked by C preparation")
			}
			requireMutation(t, during.err)
			if during.view != viewA || !current(s, g, "org", viewA) {
				t.Fatal("C preparation changed A currentness")
			}
			for _, cfg := range append(initial.configs, during.configs...) {
				requireOverlapGraph(t, cfg, a)
			}
			requireOverlapGraph(t, observerA, a)
			if !reflect.DeepEqual(during.provider, a.Providers["synthetic"]) {
				t.Fatal("provider read was incomplete")
			}

			bDone := make(chan error, 1)
			start(func() { bDone <- s.Set("org", b) })
			select {
			case err := <-bDone:
				requireMutation(t, err)
			case <-time.After(5 * time.Second):
				t.Fatal("watchdog: B publication blocked by C preparation")
			}
			viewB := snapshot(t, s, g, "org")
			observerB := observed.Load()
			if current(s, g, "org", viewA) || !current(s, g, "org", viewB) || s.Generation() != fetchedAt+1 {
				t.Fatal("intervening publication did not change currentness")
			}
			requireOverlapGraph(t, viewA.Data(), a)
			requireOverlapGraph(t, viewB.Data(), b)
			requireOverlapGraph(t, s.Get("org"), b)
			requireOverlapGraph(t, observerB, b)
			// Copies are mutated only after their traversals have finished. B and C
			// caller inputs remain stable for each complete synchronous mutation.
			for _, cfg := range append(initial.configs, during.configs...) {
				cfg.Providers["synthetic"].Weight = 99
			}
			initial.provider.Weight, during.provider.Weight = 99, 99
			observerA.Providers["synthetic"].Weight = 99
			observerB.Providers["synthetic"].Weight = 99
			if _, ok := during.configs[0].LegacyCacheKey(); ok {
				t.Fatal("edited copy retained provenance")
			}
			if _, ok, err := during.configs[0].CopyForLegacyCache(during.key); ok || err != nil {
				t.Fatal("edited copy upgraded to cache data")
			}
			requireOverlapGraph(t, viewA.Data(), a)
			requireOverlapGraph(t, viewB.Data(), b)
			requireOverlapGraph(t, s.Get("org"), b)
			unblock()
			select {
			case err := <-cDone:
				requireMutation(t, err)
			case <-time.After(5 * time.Second):
				t.Fatal("watchdog: C did not finish")
			}
			if visits != 1 || retainedScratch == nil {
				t.Fatal("expected one sacrificial serializer visit")
			}
			retainedScratch.Values[0] = 8
			final := snapshot(t, s, g, "org")
			want := b
			if kind == "Set" {
				want = c
				if current(s, g, "org", viewB) || final == viewB || s.Generation() != fetchedAt+2 {
					t.Fatal("Set C did not publish last")
				}
			} else if final != viewB || !current(s, g, "org", viewB) || s.Generation() != fetchedAt+1 {
				t.Fatal("stale fetched C crossed intervening pushed B")
			}
			if current(s, g, "org", viewA) || !current(s, g, "org", final) {
				t.Fatal("final currentness invalid")
			}
			requireOverlapGraph(t, final.Data(), want)
			requireOverlapGraph(t, s.Get("org"), want)
			requireOverlapGraph(t, viewA.Data(), a)
			requireOverlapGraph(t, viewB.Data(), b)
			if c.Routing.ConditionalRoutes[0].Value.(*scratchSerializer).Values[0] != 1 {
				t.Fatal("serializer borrowed caller input")
			}
			// Caller inputs can be edited once their synchronous copy has returned.
			// Neither historical views nor the final publication may follow them.
			a.Providers["synthetic"].Weight = 99
			b.ModelMap["synthetic"] = "edited"
			c.Routing.ConditionalRoutes[0].Value.(*scratchSerializer).Values[0] = 99
			requireOverlapGraph(t, viewA.Data(), overlapConfig("A", 1))
			requireOverlapGraph(t, viewB.Data(), overlapConfig("B", 2))
			wantFinal := overlapConfig("B", 2)
			if kind == "Set" {
				wantFinal = overlapConfig("C", 3)
				wantFinal.Routing.ConditionalRoutes = []ConditionalRoute{{Value: &scratchSerializer{Values: []int{1}, Mode: "ok"}}}
			}
			requireOverlapGraph(t, final.Data(), wantFinal)
			requireOverlapGraph(t, s.Get("org"), wantFinal)
		})
	}
}
