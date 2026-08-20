package tenant

import (
	"fmt"
	"sync"
	"testing"
	"time"
)

func TestStoreGetSetDelete(t *testing.T) {
	s := NewStore()

	// Get non-existent returns nil
	if cfg := s.Get("org-1"); cfg != nil {
		t.Fatal("expected nil for non-existent org")
	}

	// Set and Get
	cfg := &OrgConfig{
		Providers: map[string]*ProviderConfig{
			"openai": {APIKey: "sk-test", Enabled: true},
		},
	}
	s.Set("org-1", cfg)

	got := s.Get("org-1")
	if got == nil {
		t.Fatal("expected config, got nil")
	}
	if got.Providers["openai"].APIKey != "sk-test" {
		t.Fatalf("expected sk-test, got %s", got.Providers["openai"].APIKey)
	}

	// Count
	if s.Count() != 1 {
		t.Fatalf("expected count 1, got %d", s.Count())
	}

	// Delete
	s.Delete("org-1")
	if cfg := s.Get("org-1"); cfg != nil {
		t.Fatal("expected nil after delete")
	}
	if s.Count() != 0 {
		t.Fatalf("expected count 0 after delete, got %d", s.Count())
	}
}

func TestStoreGetAll(t *testing.T) {
	s := NewStore()
	s.Set("org-1", &OrgConfig{})
	s.Set("org-2", &OrgConfig{})

	all := s.GetAll()
	if len(all) != 2 {
		t.Fatalf("expected 2 configs, got %d", len(all))
	}

	// Modifying the returned map shouldn't affect the store
	delete(all, "org-1")
	if s.Count() != 2 {
		t.Fatal("store was mutated by modifying GetAll result")
	}
}

func TestStoreLoadBulk(t *testing.T) {
	s := NewStore()
	s.Set("old-org", &OrgConfig{})

	bulk := map[string]*OrgConfig{
		"org-a": {},
		"org-b": {},
		"org-c": {},
	}
	s.LoadBulk(bulk)

	if s.Count() != 3 {
		t.Fatalf("expected 3 after bulk load, got %d", s.Count())
	}
	if s.Get("old-org") != nil {
		t.Fatal("old org should have been replaced by bulk load")
	}
}

func TestStoreGetProviderConfig(t *testing.T) {
	s := NewStore()

	// No config → nil
	if pc := s.GetProviderConfig("org-1", "openai"); pc != nil {
		t.Fatal("expected nil for non-existent org")
	}

	s.Set("org-1", &OrgConfig{
		Providers: map[string]*ProviderConfig{
			"openai": {APIKey: "sk-org1", Enabled: true},
		},
	})

	pc := s.GetProviderConfig("org-1", "openai")
	if pc == nil || pc.APIKey != "sk-org1" {
		t.Fatal("expected provider config with sk-org1")
	}

	// Non-existent provider
	if pc := s.GetProviderConfig("org-1", "anthropic"); pc != nil {
		t.Fatal("expected nil for non-existent provider")
	}
}

func TestStoreConcurrency(t *testing.T) {
	s := NewStore()
	var wg sync.WaitGroup

	// Concurrent writes
	for i := 0; i < 100; i++ {
		wg.Add(1)
		go func(id int) {
			defer wg.Done()
			orgID := "org-concurrent"
			s.Set(orgID, &OrgConfig{})
			s.Get(orgID)
			s.Count()
			s.GetAll()
		}(i)
	}
	wg.Wait()

	// Should not panic or race (run with -race flag)
}

func privacyConfig(pattern string) *OrgConfig {
	return &OrgConfig{Privacy: &PrivacyConfig{
		Enabled:  true,
		Mode:     "patterns",
		Patterns: []*RedactPatternConfig{{Name: "test", Pattern: pattern}},
	}}
}

// The redactor cache lives on the store so that no config-change path can
// forget to drop it. A stale redactor means content is exported using an org's
// previous patterns after it tightened them, which is a privacy failure rather
// than a staleness annoyance — so every mutator is covered here.
func TestStoreRedactorInvalidatedOnEveryConfigChange(t *testing.T) {
	const orgID = "org-1"

	redactsWith := func(t *testing.T, s *Store, secret string) bool {
		t.Helper()
		r := s.Redactor(orgID)
		if r == nil {
			t.Fatal("no redactor for an org with privacy enabled")
		}
		return r.Redact("value "+secret) != "value "+secret
	}

	t.Run("Set", func(t *testing.T) {
		s := NewStore()
		s.Set(orgID, privacyConfig("alpha"))
		if !redactsWith(t, s, "alpha") {
			t.Fatal("initial pattern not applied")
		}
		s.Set(orgID, privacyConfig("beta"))
		if !redactsWith(t, s, "beta") {
			t.Error("Set did not drop the stale redactor")
		}
	})

	t.Run("MergeBulk", func(t *testing.T) {
		s := NewStore()
		s.Set(orgID, privacyConfig("alpha"))
		_ = redactsWith(t, s, "alpha") // prime the cache
		s.MergeBulk(map[string]*OrgConfig{orgID: privacyConfig("beta")},
			map[string]struct{}{orgID: {}})
		if !redactsWith(t, s, "beta") {
			t.Error("MergeBulk did not drop the stale redactor")
		}
	})

	// LoadBulk replaces every config and fires no onChange, so a cache living
	// in a plugin behind that callback would never hear about this one.
	t.Run("LoadBulk", func(t *testing.T) {
		s := NewStore()
		s.Set(orgID, privacyConfig("alpha"))
		_ = redactsWith(t, s, "alpha")
		s.LoadBulk(map[string]*OrgConfig{orgID: privacyConfig("beta")})
		if !redactsWith(t, s, "beta") {
			t.Error("LoadBulk did not drop the stale redactor")
		}
	})

	t.Run("Delete", func(t *testing.T) {
		s := NewStore()
		s.Set(orgID, privacyConfig("alpha"))
		_ = redactsWith(t, s, "alpha")
		s.Delete(orgID)
		if r := s.Redactor(orgID); r != nil {
			t.Error("redactor survived the org being deleted")
		}
	})
}

// A redactor built from a config the store has already replaced must never
// reach the cache. Invalidation cannot fix this after the fact: the delete runs
// while nothing is cached, so it hits nothing, and the stale build lands after
// it and stays until some unrelated write to the same org.
//
// The many-pattern config is the point — it makes the build slow enough that
// Set reliably lands inside the window a released lock would open. -race does
// not flag this: both the configs map and the cache are synchronized, it is the
// gap between them that is wrong.
func TestStoreRedactorNeverCachesAConfigTheStoreHasReplaced(t *testing.T) {
	const orgID = "org-1"

	slow := &OrgConfig{Privacy: &PrivacyConfig{Enabled: true, Mode: "patterns"}}
	for i := 0; i < 400; i++ {
		slow.Privacy.Patterns = append(slow.Privacy.Patterns,
			&RedactPatternConfig{Name: fmt.Sprintf("p%d", i), Pattern: fmt.Sprintf("alpha%d[0-9a-f]+", i)})
	}

	s := NewStore()
	s.Set(orgID, slow)

	var wg sync.WaitGroup
	wg.Add(1)
	go func() {
		defer wg.Done()
		s.Redactor(orgID) // reads the config, then compiles 400 patterns
	}()

	time.Sleep(2 * time.Millisecond) // let the build get past reading the config
	s.Set(orgID, privacyConfig("beta"))
	wg.Wait()

	r := s.Redactor(orgID)
	if r == nil {
		t.Fatal("no redactor after the config was replaced")
	}
	if got := r.Redact("value beta"); got == "value beta" {
		t.Errorf("cached a redactor built from the replaced config: %q left unredacted", got)
	}
}

func TestStoreRedactorAbsentWithoutPrivacyConfig(t *testing.T) {
	s := NewStore()
	if r := s.Redactor("unknown-org"); r != nil {
		t.Error("unknown org should have no redactor")
	}
	s.Set("org-2", &OrgConfig{})
	if r := s.Redactor("org-2"); r != nil {
		t.Error("org without privacy config should have no redactor")
	}
	s.Set("org-3", &OrgConfig{Privacy: &PrivacyConfig{Enabled: false}})
	if r := s.Redactor("org-3"); r != nil {
		t.Error("org with privacy disabled should have no redactor")
	}
	if r := s.Redactor(""); r != nil {
		t.Error("empty org id should have no redactor")
	}
}
