package tenant

import "testing"

func TestOwnedObserverSeparatePayloadsAndOrdering(t *testing.T) {
	s, _ := boundStore(t)
	var seen []*OrgConfig
	for i := 0; i < 2; i++ {
		s.ObserveChanges(func(_ string, c *OrgConfig) {
			if s.mu.TryLock() {
				s.mu.Unlock()
				t.Error("observer outside write order")
			}
			if c != nil {
				if c.Providers["synthetic"].Weight != 1 {
					t.Error("observer saw another payload mutation")
				}
				seen = append(seen, c)
				c.Providers["synthetic"].Weight = 2
			}
		})
	}
	changed := 0
	s.SetOnChange(func(string) {
		changed++
		if !s.mu.TryLock() {
			t.Error("onChange locked")
			return
		}
		s.mu.Unlock()
	})
	requireMutation(t, s.Set("org", nativeSyntheticConfig()))
	if len(seen) != 2 || seen[0] == seen[1] || changed != 0 || s.Get("org").Providers["synthetic"].Weight != 1 {
		t.Fatal("observer isolation failed")
	}
	requireMutation(t, s.MergeFetched(nil, nil, s.Generation()))
	if changed != 1 {
		t.Fatal("fetch callback absent")
	}
}
func TestOwnedOnChangePanicRevokesAfterUnlock(t *testing.T) {
	s, g := boundStore(t)
	requireMutation(t, s.Set("org", nativeSyntheticConfig()))
	old := snapshot(t, s, g, "org")
	s.SetOnChange(func(string) { _ = s.Get("org"); panic("fixed onChange panic") })
	panicked := false
	func() { defer func() { panicked = recover() != nil }(); _ = s.MergeFetched(nil, nil, s.Generation()) }()
	if !panicked || s.Count() != 0 || current(s, g, "org", old) || s.Set("org", nativeSyntheticConfig()) == nil {
		t.Fatal("onChange panic certified publication")
	}
	requireMutation(t, g.Close())
}

func TestOwnedObserverPanicRevokesAfterPublication(t *testing.T) {
	s, g := boundStore(t)
	requireMutation(t, s.Set("org", nativeSyntheticConfig()))
	old := snapshot(t, s, g, "org")
	changed := 0
	s.SetOnChange(func(string) { changed++ })
	stop := s.ObserveChanges(func(string, *OrgConfig) { panic("fixed observer panic") })
	panicked := false
	func() {
		defer func() {
			if recover() != nil {
				panicked = true
			}
		}()
		c := nativeSyntheticConfig()
		c.Providers["synthetic"].Weight = 2
		_ = s.Set("org", c)
	}()
	stop()
	if !panicked || current(s, g, "org", old) || s.Get("org").Providers["synthetic"].Weight != 2 || changed != 0 {
		t.Fatal("observer panic was rolled back or certified")
	}
	if s.Set("org", nativeSyntheticConfig()) == nil {
		t.Fatal("revoked owner became ready")
	}
	// The panic must have released both fences; another owner operation completes.
	requireMutation(t, g.Close())
}
