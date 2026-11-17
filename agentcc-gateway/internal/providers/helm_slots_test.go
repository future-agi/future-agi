package providers

import (
	"os"
	"testing"

	"gopkg.in/yaml.v3"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// Exercise the shipped chart, not a hand-written equivalent. No discovery or
// upstream call is made: both registry and org instances only resolve config.
func TestHelmProviderSlotDefaults(t *testing.T) {
	text, err := os.ReadFile("../../../deploy/helm/futureagi/values.yaml")
	if err != nil {
		t.Fatal(err)
	}
	for _, explicit := range []bool{false, true} {
		name, wantMax, wantPool := "shipped", 1024, 1024
		if explicit {
			name, wantMax, wantPool = "operator override", 7, 9
		}
		t.Run(name, func(t *testing.T) {
			var values struct {
				Gateway struct {
					Config config.Config `yaml:"config"`
				} `yaml:"agentccGateway"`
			}
			if err := yaml.Unmarshal(text, &values); err != nil {
				t.Fatal(err)
			}
			cfg := &values.Gateway.Config
			pc, ok := cfg.Providers["openai"]
			if !ok {
				t.Fatal("chart has no openai entry")
			}
			if explicit {
				pc.MaxConcurrent, pc.ConnPoolSize = wantMax, wantPool
			}
			// Fail closed to accidental discovery. No real credential is loaded.
			off := false
			pc.AutoDiscover, pc.APIKey = &off, "fixture-key"
			cfg.Providers = map[string]config.ProviderConfig{"openai": pc}
			r, err := NewRegistry(cfg)
			if err != nil {
				t.Fatal(err)
			}
			defer r.Close()
			resolved := r.configs["openai"]
			if resolved.MaxConcurrent != wantMax || resolved.ConnPoolSize != wantPool {
				t.Errorf("chart resolves max_concurrent=%d conn_pool_size=%d; want %d/%d", resolved.MaxConcurrent, resolved.ConnPoolSize, wantMax, wantPool)
			}
			p, ok := r.GetProvider("openai")
			if !ok || slotsOf(t, p).Cap() != wantMax {
				t.Errorf("chart instance does not have %d slots", wantMax)
			}
			cache := NewOrgProviderCache(cfg.Providers, false)
			defer cache.EvictAll()
			p, err = cache.GetOrCreateWithTenantConfig("helm-org", "openai", "org-fixture", &tenant.ProviderConfig{Enabled: true})
			if err != nil {
				t.Fatal(err)
			}
			if slotsOf(t, p).Cap() != wantMax {
				t.Errorf("chart org instance has %d slots; want %d", slotsOf(t, p).Cap(), wantMax)
			}
		})
	}
}
