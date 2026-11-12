package tenant

import "testing"

func TestLegacyKeyCheckedCopyAndVariant(t *testing.T) {
	s, g := boundStore(t)
	requireMutation(t, s.Set("org", rolloutFixture()))
	v := snapshot(t, s, g, "org")
	a, b := s.Get("org"), s.Get("org")
	k, ok := a.LegacyCacheKey()
	kb, okb := b.LegacyCacheKey()
	if !ok || !okb || k != kb {
		t.Fatal("same replacement not shared")
	}
	c := a.Candidate()
	ck, ok := c.LegacyCacheKey()
	if !ok || ck == k {
		t.Fatal("candidate identity missing or inherited")
	}
	if _, ok, err := c.CopyForLegacyCache(k); ok || err != nil {
		t.Fatal("wrong expected variant accepted")
	}
	if _, ok, err := a.CopyForLegacyCache(LegacyConfigKey{}); ok || err != nil {
		t.Fatal("zero key accepted")
	}
	owned, ok, err := a.CopyForLegacyCache(k)
	if !ok || err != nil {
		t.Fatal("private cache copy unavailable")
	}
	a.Providers["synthetic"].Models = []string{"changed"}
	a.Providers["synthetic"].Weight = 7
	if _, ok := a.LegacyCacheKey(); ok {
		t.Fatal("modified data key accepted")
	}
	if _, ok, err := a.CopyForLegacyCache(k); ok || err != nil {
		t.Fatal("modified data copy accepted")
	}
	if owned.Providers["synthetic"].Weight != 1 || !current(s, g, "org", v) {
		t.Fatal("cache copy borrowed or data became authority")
	}
	owned.Providers["synthetic"].Weight = 8
	if s.Get("org").Providers["synthetic"].Weight != 1 {
		t.Fatal("private build copy borrowed")
	}
	requireMutation(t, s.Set("next", b))
	fresh, ok := s.Get("next").LegacyCacheKey()
	if !ok || fresh == kb {
		t.Fatal("incoming provenance reused")
	}
	if _, ok := nativeSyntheticConfig().LegacyCacheKey(); ok {
		t.Fatal("untagged data acquired key")
	}
}
func TestLegacyKeyPrivateDifferenceInvalidates(t *testing.T) {
	s, _ := boundStore(t)
	c := rolloutFixture()
	c.Routing.ConditionalRoutes = []ConditionalRoute{{Value: privateScalar{Value: 1}}}
	requireMutation(t, s.Set("org", c))
	a := s.Get("org")
	k, ok := a.LegacyCacheKey()
	if !ok {
		t.Fatal("key missing")
	}
	a.Routing.ConditionalRoutes[0].Value = privateScalar{Value: 1, hidden: true}
	if _, ok := a.LegacyCacheKey(); ok {
		t.Fatal("private change accepted")
	}
	if _, ok, err := a.CopyForLegacyCache(k); ok || err != nil {
		t.Fatal("private modified copy accepted")
	}
}
