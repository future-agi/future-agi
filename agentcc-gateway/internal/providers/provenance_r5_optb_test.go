package providers

import (
	"reflect"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
)

// INT-R1-03: two real instances have exactly the same ID. Only the object
// constructed by the org cache may qualify for the BYOK publication exception.
func TestOptBR5OrgProvenanceRejectsGlobalNameCollision(t *testing.T) {
	cfg := config.DefaultConfig()
	base := config.ProviderConfig{APIFormat: "openai", BaseURL: "http://127.0.0.1:1", APIKey: "synthetic-platform", Models: []string{"fixture"}}
	cfg.Providers = map[string]config.ProviderConfig{"edge_org_acme": base}
	r, err := NewRegistry(cfg)
	if err != nil {
		t.Fatal(err)
	}
	defer r.Close()
	global, ok := r.GetProvider("edge_org_acme")
	if !ok {
		t.Fatal("global provider missing")
	}
	cache := NewOrgProviderCache(map[string]config.ProviderConfig{"edge": base}, false)
	org, err := cache.GetOrCreateWithTenantConfig("acme", "edge", "synthetic-tenant", nil)
	if err != nil {
		t.Fatal(err)
	}
	defer org.Close()
	if org.ID() != global.ID() {
		t.Fatal("fixture is not a true ID collision")
	}
	if IsOrgInstance(global, "acme") {
		t.Error("global platform instance was trusted by its ID suffix")
	}
	if !IsOrgInstance(org, "acme") {
		t.Error("org-cache construction lost its provenance")
	}
	if IsOrgInstance(org, "other") || IsOrgInstance(org, "") || IsOrgInstance(nil, "acme") {
		t.Error("provenance did not bind the exact org")
	}
	cached, err := cache.GetOrCreate("acme", "edge", "ignored-on-hit")
	if err != nil || cached != org || !IsOrgInstance(cached, "acme") {
		t.Error("cache hit lost instance identity or provenance")
	}
	// Adding provenance must not mask optional adapter capabilities. Compare
	// every exported method, not just speech, so future native interfaces stay.
	globalType, orgType := reflect.TypeOf(global), reflect.TypeOf(org)
	for i := 0; i < globalType.NumMethod(); i++ {
		m := globalType.Method(i)
		if _, ok := orgType.MethodByName(m.Name); !ok {
			t.Errorf("org instance lost adapter method %s", m.Name)
		}
	}
}
