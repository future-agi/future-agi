package providers

import (
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/providers/admission"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// PROVIDER-CONCURRENCY-1: unset max_concurrent and conn_pool_size are 1024 on
// a remote entry and 10 on a local one, for every instance the gateway
// builds; queue_timeout falls back to provider_defaults.queue_timeout, then
// 5s. A value the config states always wins.

func slotsOf(t *testing.T, p Provider) *admission.Limiter {
	t.Helper()
	sh, ok := p.(SlotHolder)
	if !ok {
		t.Fatalf("%T does not hold upstream slots", p)
	}
	return sh.Admission()
}

func TestApplyConcurrencyDefaults(t *testing.T) {
	no := false
	for _, tc := range []struct {
		name              string
		cfg               config.ProviderConfig
		global            time.Duration
		wantMax, wantPool int
		wantQueue         time.Duration
	}{
		{"remote", config.ProviderConfig{BaseURL: "https://api.example.invalid"}, 0, 1024, 1024, 0},
		{"remote with the global wait", config.ProviderConfig{BaseURL: "https://api.example.invalid"}, 3 * time.Second, 1024, 1024, 3 * time.Second},
		{"local", config.ProviderConfig{BaseURL: "http://127.0.0.1:8000"}, 0, 10, 10, 0},
		{"loopback forced remote", config.ProviderConfig{BaseURL: "http://127.0.0.1:8000", Local: &no}, 0, 1024, 1024, 0},
		{"explicit wins", config.ProviderConfig{BaseURL: "https://api.example.invalid", MaxConcurrent: 7, ConnPoolSize: 9, QueueTimeout: 2 * time.Second},
			3 * time.Second, 7, 9, 2 * time.Second},
		{"explicit local wins", config.ProviderConfig{BaseURL: "http://localhost:11434", MaxConcurrent: 64}, 0, 64, 10, 0},
	} {
		t.Run(tc.name, func(t *testing.T) {
			cfg := tc.cfg
			applyConcurrencyDefaults(&cfg, tc.global)
			if cfg.MaxConcurrent != tc.wantMax || cfg.ConnPoolSize != tc.wantPool || cfg.QueueTimeout != tc.wantQueue {
				t.Fatalf("max %d pool %d queue %v; want %d %d %v", cfg.MaxConcurrent, cfg.ConnPoolSize, cfg.QueueTimeout, tc.wantMax, tc.wantPool, tc.wantQueue)
			}
		})
	}
}

// TestRegistrySlotDefaults: the registry's instances, and a key rotation's
// candidate built from the resolved config, carry the defaults.
func TestRegistrySlotDefaults(t *testing.T) {
	off := false
	cfg := config.DefaultConfig()
	cfg.ProviderDefaults.QueueTimeout = 3 * time.Second
	cfg.Providers = map[string]config.ProviderConfig{
		"slotdef-remote": {BaseURL: "https://api.example.invalid", APIFormat: "openai", Models: []string{"m-remote"}, AutoDiscover: &off},
		"slotdef-local":  {BaseURL: "http://127.0.0.1:9", APIFormat: "openai", Models: []string{"m-local"}, AutoDiscover: &off},
		"slotdef-set": {BaseURL: "https://api.example.invalid", APIFormat: "anthropic", Models: []string{"m-set"},
			MaxConcurrent: 7, ConnPoolSize: 9, QueueTimeout: time.Second, AutoDiscover: &off},
	}
	r, err := NewRegistry(cfg)
	if err != nil {
		t.Fatal(err)
	}
	defer r.Close()
	for id, want := range map[string]struct {
		max, pool int
		queue     time.Duration
	}{
		"slotdef-remote": {1024, 1024, 3 * time.Second},
		"slotdef-local":  {10, 10, 3 * time.Second},
		"slotdef-set":    {7, 9, time.Second},
	} {
		got := r.configs[id]
		if got.MaxConcurrent != want.max || got.ConnPoolSize != want.pool || got.QueueTimeout != want.queue {
			t.Errorf("%s config: max %d pool %d queue %v; want %+v", id, got.MaxConcurrent, got.ConnPoolSize, got.QueueTimeout, want)
		}
		p, _ := r.GetProvider(id)
		if l := slotsOf(t, p); l.Cap() != want.max || l.QueueTimeout() != want.queue || l.Entry() != id {
			t.Errorf("%s instance: cap %d queue %v entry %q", id, l.Cap(), l.QueueTimeout(), l.Entry())
		}
	}
	candidate, _, _, err := r.RotationCandidate("slotdef-remote", "rotated-key")
	if err != nil {
		t.Fatal(err)
	}
	defer candidate.Close()
	if l := slotsOf(t, candidate); l.Cap() != 1024 || l.QueueTimeout() != 3*time.Second {
		t.Fatalf("rotation candidate: cap %d queue %v", l.Cap(), l.QueueTimeout())
	}
}

// TestOrgInstanceSlotDefaults: an org's instance inherits the entry's stated
// values, else gets the defaults (never the fixed 100 an org's own base_url
// used to get); the org's own values win.
func TestOrgInstanceSlotDefaults(t *testing.T) {
	stubLookupIP(t, map[string][]string{"llm.example.com": {"203.0.113.7"}})
	c := NewOrgProviderCache(map[string]config.ProviderConfig{
		"slotdef-base": {BaseURL: "https://api.example.invalid", APIFormat: "openai"},
		"slotdef-set":  {BaseURL: "https://api.example.invalid", APIFormat: "openai", MaxConcurrent: 3, QueueTimeout: 250 * time.Millisecond},
	}, false)
	c.SetDefaultQueueTimeout(2 * time.Second)
	defer c.EvictAll()
	for _, tc := range []struct {
		name, org, provider string
		tenantCfg           *tenant.ProviderConfig
		max                 int
		queue               time.Duration
	}{
		{"inherited entry, defaults", "org-a", "slotdef-base", nil, 1024, 2 * time.Second},
		{"inherited entry, its values", "org-a", "slotdef-set", nil, 3, 250 * time.Millisecond},
		{"the org's own value", "org-b", "slotdef-base", &tenant.ProviderConfig{MaxConcurrent: 5}, 5, 2 * time.Second},
		{"the org's own base_url", "org-c", "slotdef-own", &tenant.ProviderConfig{BaseURL: "https://llm.example.com", APIFormat: "openai"}, 1024, 2 * time.Second},
	} {
		t.Run(tc.name, func(t *testing.T) {
			p, err := c.GetOrCreateWithTenantConfig(tc.org, tc.provider, "org-key", tc.tenantCfg)
			if err != nil {
				t.Fatal(err)
			}
			l := slotsOf(t, p)
			if l.Cap() != tc.max || l.QueueTimeout() != tc.queue || l.Entry() != orgInstanceID(tc.provider, tc.org) {
				t.Fatalf("cap %d queue %v entry %q; want %d %v", l.Cap(), l.QueueTimeout(), l.Entry(), tc.max, tc.queue)
			}
		})
	}
}
