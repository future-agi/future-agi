package tenant

import (
	"errors"
	"github.com/futureagi/agentcc-gateway/internal/routebinding"
	"reflect"
	"testing"
)

func boundStore(t *testing.T) (*Store, *routebinding.Gate) {
	t.Helper()
	g, err := routebinding.NewGate()
	if err != nil {
		t.Fatal("gate setup failed")
	}
	s := NewStore()
	if s.BindNativeLifecycle(g) != nil {
		t.Fatal("native ownership unavailable")
	}
	return s, g
}
func requireMutation(t *testing.T, err error) {
	t.Helper()
	if err != nil {
		t.Fatal("publication failed")
	}
}
func snapshot(t *testing.T, s *Store, g *routebinding.Gate, org string) NativeView {
	t.Helper()
	var v NativeView
	err := g.Read(func(r *routebinding.Read) error { var e error; v, e = s.NativeSnapshot(r, org); return e })
	if err != nil {
		t.Fatal("snapshot unavailable")
	}
	return v
}
func current(s *Store, g *routebinding.Gate, org string, v NativeView) bool {
	return g.Read(func(r *routebinding.Read) error { return s.CheckNativeView(r, org, v) }) == nil
}
func TestOwnedViewTokensAndIdentity(t *testing.T) {
	s, g := boundStore(t)
	requireMutation(t, s.Set("org", nativeSyntheticConfig()))
	v := snapshot(t, s, g, "org")
	copyView := v
	if !current(s, g, "org", copyView) || current(s, g, "other", v) || current(s, g, "org", NativeView{}) {
		t.Fatal("view identity validation failed")
	}
	other, og := boundStore(t)
	if current(other, og, "org", v) {
		t.Fatal("foreign owner accepted")
	}
	var retained *routebinding.Read
	requireMutation(t, g.Read(func(r *routebinding.Read) error {
		retained = r
		copied := *r
		if s.CheckNativeView(&copied, "org", v) == nil {
			t.Error("copied token accepted")
		}
		return nil
	}))
	if s.CheckNativeView(retained, "org", v) == nil {
		t.Fatal("ended token accepted")
	}
	requireMutation(t, g.Publish(func(w *routebinding.Write) error { return s.CheckNativeViewForPublish(w, "org", v) }))
	requireMutation(t, s.Set("org", nativeSyntheticConfig()))
	if current(s, g, "org", v) {
		t.Fatal("replaced view accepted")
	}
	requireMutation(t, g.Close())
	if current(s, g, "org", snapshotDataOnly(v)) {
		t.Fatal("closed owner accepted")
	}
}
func snapshotDataOnly(v NativeView) NativeView { _ = v.Data(); return v }
func TestOwnedViewAbsenceNilTombstoneRecreate(t *testing.T) {
	s, g := boundStore(t)
	absent := snapshot(t, s, g, "org")
	if absent.Data() != nil || s.Count() != 0 {
		t.Fatal("absence changed data")
	}
	requireMutation(t, s.Set("org", nil))
	present := snapshot(t, s, g, "org")
	all := s.GetAll()
	if _, ok := all["org"]; !ok || s.Count() != 1 || current(s, g, "org", absent) {
		t.Fatal("nil presence lost")
	}
	requireMutation(t, s.Delete("org"))
	tomb := snapshot(t, s, g, "org")
	if s.Count() != 0 || current(s, g, "org", present) || current(s, g, "org", absent) {
		t.Fatal("delete identity lost")
	}
	requireMutation(t, s.Delete("org"))
	if current(s, g, "org", tomb) {
		t.Fatal("absent delete reused tombstone")
	}
	requireMutation(t, s.Set("org", nil))
	if current(s, g, "org", present) {
		t.Fatal("recreation reused identity")
	}
}
func rolloutFixture() *OrgConfig {
	c := nativeSyntheticConfig()
	c.Routing = &RoutingConfig{DefaultModel: "stable", Rollout: &RolloutConfig{Version: "candidate", Percent: 50, Routing: &RoutingConfig{DefaultModel: "candidate"}}}
	return c
}
func TestOwnedViewVariantSameRoot(t *testing.T) {
	s, g := boundStore(t)
	requireMutation(t, s.Set("org", rolloutFixture()))
	base := snapshot(t, s, g, "org")
	var variant NativeView
	requireMutation(t, g.Read(func(r *routebinding.Read) error {
		var err error
		variant, err = s.NativeVariant(r, "org", base, PolicyVariantCandidate)
		if err != nil {
			return err
		}
		if _, e := s.NativeVariant(r, "org", base, "invalid"); e == nil {
			t.Error("unknown variant accepted")
		}
		if _, e := s.NativeVariant(r, "org", variant, PolicyVariantCandidate); e == nil {
			t.Error("effective view upgraded to root")
		}
		return s.CheckNativeView(r, "org", variant)
	}))
	data := variant.Data()
	data.Routing.DefaultModel = "changed"
	if variant.Data().Routing.DefaultModel != "candidate" {
		t.Fatal("variant data borrowed")
	}
	requireMutation(t, s.Set("org", rolloutFixture()))
	if current(s, g, "org", base) || current(s, g, "org", variant) {
		t.Fatal("root pair survived replacement")
	}
}
func TestOwnedBindingRefusesMigrationRebindAndCopies(t *testing.T) {
	g, err := routebinding.NewGate()
	requireMutation(t, err)
	for _, kind := range []string{"populated", "deleted", "empty bulk", "empty fetch", "copied", "zero"} {
		t.Run(kind, func(t *testing.T) {
			s := NewStore()
			switch kind {
			case "populated":
				requireMutation(t, s.Set("org", nil))
			case "deleted":
				requireMutation(t, s.Delete("org"))
			case "empty bulk":
				requireMutation(t, s.LoadBulk(nil))
			case "empty fetch":
				requireMutation(t, s.MergeFetched(nil, nil, 0))
			case "copied":
				c := new(Store)
				reflect.ValueOf(c).Elem().Set(reflect.ValueOf(s).Elem())
				s = c
			case "zero":
				s = &Store{}
			}
			if !errors.Is(s.BindNativeLifecycle(g), ErrNativeLifecycle) {
				t.Fatal("nonfresh owner bound")
			}
		})
	}
	s, _ := boundStore(t)
	if s.BindNativeLifecycle(g) == nil {
		t.Fatal("rebind accepted")
	}
	requireMutation(t, g.Close())
	if NewStore().BindNativeLifecycle(g) == nil {
		t.Fatal("closed gate bound")
	}
}
