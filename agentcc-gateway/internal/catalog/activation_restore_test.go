package catalog

import (
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
)

func TestCAT2RestoreReappliesOperatorPolicy(t *testing.T) {
	cfg := cat2Config(t, "managed:\n  catalogue_families: [cat2]\n")
	cfg.Catalog.SnapshotDir = t.TempDir()
	s := cat2Store(t, cfg)
	id := "google/lyria-3-pro-preview"
	onID := s.Load().ID()
	if !s.Load().ServesManaged("openrouter", id) {
		t.Fatal("not activated")
	}
	cfg.Managed.CatalogueFamilies = nil
	off := NewStore()
	if err := off.Configure(OptionsFromConfig(cfg, []Source{{ProviderID: "openrouter", Type: SourceOpenRouter}})); err != nil {
		t.Fatal(err)
	}
	if _, exists := off.Load().Get("openrouter", id); !exists {
		t.Fatal("snapshot not restored")
	}
	if off.Load().ServesManaged("openrouter", id) {
		t.Fatal("persisted activation bypassed startup off")
	}
	if onID == off.Load().ID() {
		t.Fatal("activation did not change snapshot identity")
	}
	cfg.Managed.CatalogueFamilies = []string{config.CatalogueFamilyCAT2}
	on := NewStore()
	if err := on.Configure(OptionsFromConfig(cfg, []Source{{ProviderID: "openrouter", Type: SourceOpenRouter}})); err != nil {
		t.Fatal(err)
	}
	if !on.Load().ServesManaged("openrouter", id) {
		t.Fatal("restored catalogue lost startup on")
	}
	// OFF snapshots must retain the authoritative decimals for a later ON
	// restart with no source refresh. Redaction belongs only in API views.
	for _, snap := range s.Load().Snapshots() {
		for modelID, original := range snap.Models {
			if original.Inclusive == nil || original.Inclusive.Contract != InclusiveFamilyContract {
				continue
			}
			for name, restored := range map[string]*Store{"off": off, "on": on} {
				m, ok := restored.Load().Get(snap.ProviderID, modelID)
				if !ok || m.Inclusive == nil || *m.Inclusive != *original.Inclusive {
					t.Errorf("%s restart lost exact price for %s", name, modelID)
				}
			}
		}
	}
}

func TestCAT2ActivationKeepsExactSourceRates(t *testing.T) {
	cfg := cat2Config(t, "managed:\n  catalogue_families: [cat2]\n")
	s := cat2Store(t, cfg)
	all, _, err := BundledOpenRouter()
	if err != nil {
		t.Fatal(err)
	}
	for id, original := range all {
		if original.Inclusive == nil {
			continue
		}
		m, ok := s.Load().Get("openrouter", id)
		if !ok || m.Inclusive == nil || *original.Inclusive != *m.Inclusive {
			t.Fatalf("source decimal contract changed for %s", id)
		}
	}
	// The store owns a value, not the config slice or an Options pointer.
	opts := OptionsFromConfig(cfg, nil)
	if err := s.Configure(opts); err != nil {
		t.Fatal(err)
	}
	*opts.CAT2Enabled = false
	cfg.Managed.CatalogueFamilies[0] = "invalid"
	Seed(s, []Source{{ProviderID: "openrouter", Type: SourceOpenRouter}})
	if !s.Load().ServesManaged("openrouter", "google/lyria-3-pro-preview") {
		t.Fatal("caller mutated the startup policy")
	}
}
