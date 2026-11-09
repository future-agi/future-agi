package providers

import (
	"sync"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/providers/openai"
)

func r3ProvenanceCache() *OrgProviderCache {
	return NewOrgProviderCache(map[string]config.ProviderConfig{
		"edge": {APIFormat: "openai", BaseURL: "http://127.0.0.1:1", APIKey: "synthetic-platform"},
	}, false)
}

func TestOptBR3ProvenanceRejectsTypedNil(t *testing.T) {
	var p *openai.Provider
	if identityKeyed(p) {
		t.Fatal("typed nil is not an instance identity")
	}
	if IsOrgInstance(p, "acme") {
		t.Fatal("typed nil acquired org provenance")
	}
}

func TestOptBR3ProvenanceFreshAfterEviction(t *testing.T) {
	for _, all := range []bool{false, true} {
		cache := r3ProvenanceCache()
		first, err := cache.GetOrCreate("acme", "edge", "synthetic-first")
		if err != nil {
			t.Fatal(err)
		}
		if !IsOrgInstance(first, "acme") {
			t.Fatal("record absent before publication")
		}
		platform, err := createProvider(first.ID(), config.ProviderConfig{APIFormat: "openai", BaseURL: "http://127.0.0.1:1"})
		if err != nil {
			t.Fatal(err)
		}
		if IsOrgInstance(platform, "acme") {
			t.Fatal("_org_<org> platform id forged provenance")
		}
		platform.Close()
		if all {
			cache.EvictAll()
		} else {
			cache.Evict("acme")
		}
		second, err := cache.GetOrCreate("acme", "edge", "synthetic-second")
		if err != nil {
			t.Fatal(err)
		}
		if first == second || first.ID() != second.ID() || IsOrgInstance(first, "acme") || !IsOrgInstance(second, "acme") {
			t.Fatal("recreation must have a fresh identity record, never revive the old one")
		}
		cache.EvictAll()
		if IsOrgInstance(second, "acme") {
			t.Fatal("EvictAll retained the replacement record")
		}
	}
}

// Inspect map publication and provenance under the instance-map lock while
// readers, creators and both eviction paths contend. Retain all retired
// pointers so address reuse cannot disguise resurrection. Run with -count=50.
func TestOptBR3ProvenanceConcurrentPublicationEviction(t *testing.T) {
	cache := r3ProvenanceCache()
	defer cache.EvictAll()
	start := make(chan struct{})
	var wg sync.WaitGroup
	var seenMu sync.Mutex
	seen := make(map[Provider]bool)
	for worker := 0; worker < 8; worker++ {
		wg.Add(1)
		go func(worker int) {
			defer wg.Done()
			<-start
			for iteration := 0; iteration < 32; iteration++ {
				if worker == 0 {
					cache.Evict("acme")
					continue
				}
				if worker == 1 {
					cache.EvictAll()
					continue
				}
				p, err := cache.GetOrCreate("acme", "edge", "synthetic-tenant")
				if err != nil {
					t.Error(err)
					return
				}
				seenMu.Lock()
				seen[p] = true
				seenMu.Unlock()
				_ = cache.Get("acme", "edge")
				cache.mu.RLock()
				published := cache.providers["acme:edge"]
				if published != nil && !IsOrgInstance(published, "acme") {
					t.Error("published instance has no provenance")
				}
				if p != published && IsOrgInstance(p, "acme") {
					t.Error("removed instance retained provenance")
				}
				if IsOrgInstance(p, "other") {
					t.Error("record has wrong org")
				}
				cache.mu.RUnlock()
			}
		}(worker)
	}
	close(start)
	wg.Wait()
	cache.EvictAll()
	for p := range seen {
		if IsOrgInstance(p, "acme") {
			t.Fatal("retired instance survived final eviction")
		}
	}
	fresh, err := cache.GetOrCreate("acme", "edge", "synthetic-new")
	if err != nil {
		t.Fatal(err)
	}
	if seen[fresh] || !IsOrgInstance(fresh, "acme") {
		t.Fatal("fresh record was not attached to a new instance")
	}
}
