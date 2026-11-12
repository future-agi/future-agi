package routebinding_test

import (
	"errors"
	"math"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/routebinding"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

func ordinalConfig(weight float64) *tenant.OrgConfig {
	return &tenant.OrgConfig{Providers: map[string]*tenant.ProviderConfig{
		"synthetic": {Enabled: true, Weight: weight},
	}}
}

func ordinalRequire(t *testing.T, err error) {
	t.Helper()
	if err != nil {
		t.Fatal("fixture publication failed")
	}
}

func ordinalSnapshot(t *testing.T, s *tenant.Store, g *routebinding.Gate) tenant.NativeView {
	t.Helper()
	var v tenant.NativeView
	ordinalRequire(t, g.Read(func(r *routebinding.Read) error {
		var err error
		v, err = s.NativeSnapshot(r, "org")
		return err
	}))
	return v
}

func ordinalCurrent(s *tenant.Store, g *routebinding.Gate, v tenant.NativeView) bool {
	return g.Read(func(r *routebinding.Read) error { return s.CheckNativeView(r, "org", v) }) == nil
}

func ordinalLimitFixture(t *testing.T) (*tenant.Store, *routebinding.Gate, tenant.NativeView) {
	t.Helper()
	g, err := routebinding.NewGate()
	ordinalRequire(t, err)
	s := tenant.NewStore()
	ordinalRequire(t, s.BindNativeLifecycle(g)) // Fresh owner, before escape.
	ordinalRequire(t, s.Set("org", ordinalConfig(1)))
	ordinalRequire(t, routebinding.SeedPenultimateOrdinalForTest(g))
	before := s.Generation()
	ordinalRequire(t, s.Set("org", ordinalConfig(2))) // Consumes the real final stamp.
	atLimit, err := routebinding.OrdinalAtLimitForTest(g)
	if err != nil || !atLimit || s.Generation() != before+1 || s.Generation() == math.MaxUint64 {
		t.Fatal("last tenant allocation did not reach actual ordinal limit")
	}
	v := ordinalSnapshot(t, s, g)
	if !ordinalCurrent(s, g, v) || v.Data().Providers["synthetic"].Weight != 2 {
		t.Fatal("last allocation did not publish a current image")
	}
	gen := s.Generation()
	ordinalRequire(t, s.MergeFetched(map[string]*tenant.OrgConfig{"org": ordinalConfig(2)}, map[string]struct{}{"org": {}}, gen))
	if ordinalSnapshot(t, s, g) != v || !ordinalCurrent(s, g, v) || s.Generation() != gen {
		t.Fatal("equal fetch at limit allocated or replaced native image")
	}
	return s, g, v
}

func assertOrdinalOwnerRevoked(t *testing.T, s *tenant.Store, g *routebinding.Gate, v tenant.NativeView, gen uint64) {
	t.Helper()
	if s.Generation() != gen || s.Count() != 1 || s.Get("new") != nil || s.Get("org").Providers["synthetic"].Weight != 2 || v.Data().Providers["synthetic"].Weight != 2 {
		t.Fatal("refused publication changed last-good data or generation")
	}
	if ordinalCurrent(s, g, v) {
		t.Fatal("exhausted owner retained current native view")
	}
	ordinalRequire(t, g.Read(func(r *routebinding.Read) error {
		if _, err := s.NativeSnapshot(r, "org"); !errors.Is(err, tenant.ErrNativeLifecycle) {
			t.Error("revoked owner issued a snapshot")
		}
		return r.Use(g, func() error { return nil })
	}))
	ordinalRequire(t, g.Publish(func(w *routebinding.Write) error {
		if err := w.Use(g, func() error { return nil }); err != nil {
			return err
		}
		if _, err := w.Next(); !errors.Is(err, routebinding.ErrExhausted) {
			t.Error("gate was closed or ordinal allocation did not refuse")
		}
		return nil
	}))
	atLimit, err := routebinding.OrdinalAtLimitForTest(g)
	if err != nil || !atLimit {
		t.Fatal("exhaustion wrapped or closed the gate")
	}
}

func TestTenantPublicationAtActualOrdinalLimit(t *testing.T) {
	for _, kind := range []string{"Set", "Delete", "LoadBulk", "MergeFetched", "MergeBulk"} {
		t.Run(kind, func(t *testing.T) {
			s, g, v := ordinalLimitFixture(t)
			gen := s.Generation()
			observed, changed := 0, 0
			s.ObserveChanges(func(string, *tenant.OrgConfig) { observed++ })
			s.SetOnChange(func(string) { changed++ })
			batch := map[string]*tenant.OrgConfig{"org": ordinalConfig(3), "new": ordinalConfig(4)}
			ids := map[string]struct{}{"org": {}, "new": {}}
			var err error
			switch kind {
			case "Set":
				err = s.Set("org", batch["org"])
			case "Delete":
				err = s.Delete("org")
			case "LoadBulk":
				err = s.LoadBulk(batch)
			case "MergeFetched":
				err = s.MergeFetched(batch, ids, gen)
			case "MergeBulk":
				err = s.MergeBulk(batch, ids)
			}
			if !errors.Is(err, tenant.ErrNativeLifecycle) || observed != 0 || changed != 0 {
				t.Fatal("exhausted producer succeeded or notified")
			}
			assertOrdinalOwnerRevoked(t, s, g, v, gen)
		})
	}
	t.Run("InvalidInputDoesNotRevoke", func(t *testing.T) {
		s, g, v := ordinalLimitFixture(t)
		gen := s.Generation()
		notices := 0
		s.ObserveChanges(func(string, *tenant.OrgConfig) { notices++ })
		s.SetOnChange(func(string) { notices++ })
		invalid := ordinalConfig(3)
		invalid.Routing = &tenant.RoutingConfig{ConditionalRoutes: []tenant.ConditionalRoute{{Value: func() {}}}}
		if err := s.Set("org", invalid); !errors.Is(err, tenant.ErrConfigGraph) {
			t.Fatal("unsupported candidate did not refuse before allocation")
		}
		if ordinalSnapshot(t, s, g) != v || !ordinalCurrent(s, g, v) || gen != s.Generation() || notices != 0 {
			t.Fatal("invalid input revoked or changed coherent owner")
		}
		if err := s.Set("org", ordinalConfig(3)); !errors.Is(err, tenant.ErrNativeLifecycle) || notices != 0 {
			t.Fatal("supported change at limit did not refuse without notification")
		}
		assertOrdinalOwnerRevoked(t, s, g, v, gen)
	})
}
