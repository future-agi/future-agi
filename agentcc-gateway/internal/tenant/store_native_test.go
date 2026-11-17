package tenant

import "testing"

func nativeSyntheticConfig() *OrgConfig {
	return &OrgConfig{Providers: map[string]*ProviderConfig{
		"synthetic": {Enabled: true, Weight: 1},
	}}
}

// This is an ownership-blocker witness, not positive native qualification.
// A sidecar stamp advanced only by canonical mutation methods cannot account
// for these supported pointer escapes. Do not issue native snapshots until
// an agreed ownership boundary makes such changes impossible or rejects them.
func TestNativeFoundationBorrowedConfigBypassesPublication(t *testing.T) {
	for _, escape := range []string{"Set input", "Get", "GetAll", "GetProviderConfig", "observer"} {
		t.Run(escape, func(t *testing.T) {
			s := NewStore()
			input := nativeSyntheticConfig()
			var observed *OrgConfig
			notifications := 0
			s.ObserveChanges(func(_ string, cfg *OrgConfig) {
				observed = cfg
				notifications++
			})
			s.Set("synthetic-org", input)
			generation := s.Generation()
			var alias *ProviderConfig
			switch escape {
			case "Set input":
				alias = input.Providers["synthetic"]
			case "Get":
				alias = s.Get("synthetic-org").Providers["synthetic"]
			case "GetAll":
				alias = s.GetAll()["synthetic-org"].Providers["synthetic"]
			case "GetProviderConfig":
				alias = s.GetProviderConfig("synthetic-org", "synthetic")
			case "observer":
				alias = observed.Providers["synthetic"]
			}
			// Single-threaded witness: no data race or timing assumptions needed.
			alias.Weight = 2
			if s.Get("synthetic-org").Providers["synthetic"].Weight != 2 {
				t.Fatal("ownership escape changed; revisit native ownership blocker")
			}
			if s.Generation() != generation || notifications != 1 {
				t.Fatal("escape unexpectedly entered a canonical publication")
			}
		})
	}
}

func TestNativeFoundationBulkAndFetchAlsoRetainInputPointers(t *testing.T) {
	for _, publish := range []string{"LoadBulk", "MergeFetched"} {
		t.Run(publish, func(t *testing.T) {
			s := NewStore()
			cfg := nativeSyntheticConfig()
			configs := map[string]*OrgConfig{"synthetic-org": cfg}
			if publish == "LoadBulk" {
				s.LoadBulk(configs)
			} else {
				s.MergeFetched(configs, map[string]struct{}{"synthetic-org": {}}, s.Generation())
			}
			generation := s.Generation()
			cfg.Providers["synthetic"].Weight = 3
			if s.Get("synthetic-org") != cfg || s.GetProviderConfig("synthetic-org", "synthetic").Weight != 3 || s.Generation() != generation {
				t.Fatal("bulk/fetch pointer ownership changed; revisit blocker")
			}
		})
	}
}

func TestNativeFoundationNoChangeFetchReplacesCurrentPointer(t *testing.T) {
	s := NewStore()
	original, replacement := nativeSyntheticConfig(), nativeSyntheticConfig()
	observed, changed := 0, 0
	s.ObserveChanges(func(string, *OrgConfig) { observed++ })
	s.SetOnChange(func(string) { changed++ })
	s.Set("synthetic-org", original)
	generation := s.Generation()
	s.MergeFetched(map[string]*OrgConfig{"synthetic-org": replacement}, map[string]struct{}{"synthetic-org": {}}, generation)
	if s.Get("synthetic-org") != replacement || original == replacement {
		t.Fatal("no-change fetch did not replace current pointer")
	}
	if observed != 1 || changed != 0 || s.Generation() != generation {
		t.Fatal("no-change fetch changed notification/generation semantics")
	}
}

func TestNativeFoundationMutationAndNotificationSemantics(t *testing.T) {
	s := NewStore()
	observed, changed := 0, 0
	s.ObserveChanges(func(string, *OrgConfig) {
		observed++
		// Existing lifecycle observers run under tenant.mu. This is a lock
		// witness only; callbacks must not actually reenter the store.
		if s.mu.TryLock() {
			s.mu.Unlock()
			t.Error("observer was not ordered under the store lock")
		}
	})
	s.SetOnChange(func(string) {
		changed++
		if !s.mu.TryLock() {
			t.Error("public onChange ran under the store lock")
			return
		}
		s.mu.Unlock()
	})
	s.Set("synthetic-org", nativeSyntheticConfig())
	if s.Generation() != 1 || observed != 1 || changed != 0 {
		t.Fatal("Set semantics changed")
	}
	s.MergeFetched(nil, map[string]struct{}{"synthetic-org": {}}, s.Generation())
	if s.Get("synthetic-org") == nil || observed != 1 || changed != 0 {
		t.Fatal("malformed org was not preserved")
	}
	updated := nativeSyntheticConfig()
	updated.Providers["synthetic"].Weight = 2
	s.MergeFetched(map[string]*OrgConfig{"synthetic-org": updated}, map[string]struct{}{"synthetic-org": {}}, s.Generation())
	if s.Generation() != 1 || observed != 2 || changed != 1 {
		t.Fatal("changed fetch semantics changed")
	}
	s.MergeFetched(nil, nil, s.Generation())
	if s.Get("synthetic-org") != nil || s.Generation() != 1 || observed != 3 || changed != 2 {
		t.Fatal("fetch deletion semantics changed")
	}
	s.Delete("synthetic-org")
	if s.Generation() != 2 || observed != 4 || changed != 2 {
		t.Fatal("absent Delete semantics changed")
	}
	s.LoadBulk(map[string]*OrgConfig{"synthetic-org": nativeSyntheticConfig()})
	if s.Generation() != 3 || observed != 5 || changed != 2 {
		t.Fatal("LoadBulk semantics changed")
	}
	s.LoadBulk(nil)
	if s.Generation() != 4 || observed != 6 || changed != 2 || s.Count() != 0 {
		t.Fatal("bulk removal semantics changed")
	}
}

func TestNativeFoundationPushFetchGenerationOrdering(t *testing.T) {
	s := NewStore()
	before := s.Generation()
	pushed := nativeSyntheticConfig()
	s.Set("synthetic-org", pushed)
	s.MergeFetched(nil, nil, before)
	if s.Get("synthetic-org") != pushed {
		t.Fatal("stale fetch removed pushed config")
	}
	before = s.Generation()
	s.Delete("synthetic-org")
	s.MergeFetched(map[string]*OrgConfig{"synthetic-org": nativeSyntheticConfig()}, map[string]struct{}{"synthetic-org": {}}, before)
	if s.Get("synthetic-org") != nil {
		t.Fatal("stale fetch recreated deleted config")
	}
	before = s.Generation()
	s.LoadBulk(map[string]*OrgConfig{"synthetic-org": pushed})
	s.MergeFetched(nil, nil, before)
	if s.Get("synthetic-org") != pushed || s.Generation() != 3 {
		t.Fatal("stale fetch crossed bulk replacement")
	}
}
