package tenant

import (
	"sync"
	"testing"
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
