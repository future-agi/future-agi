package catalog

import (
	"os"
	"path/filepath"
	"reflect"
	"sort"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
)

func cat2Config(t *testing.T, text string) *config.Config {
	t.Helper()
	path := filepath.Join(t.TempDir(), "gateway.yaml")
	if err := os.WriteFile(path, []byte(text), 0600); err != nil {
		t.Fatal(err)
	}
	cfg, err := config.Load(path)
	if err != nil {
		t.Fatal(err)
	}
	return cfg
}

func cat2Store(t *testing.T, cfg *config.Config) *Store {
	t.Helper()
	s := NewStore()
	sources := []Source{{ProviderID: "openrouter", Type: SourceOpenRouter}}
	if err := s.Configure(OptionsFromConfig(cfg, sources)); err != nil {
		t.Fatal(err)
	}
	Seed(s, sources)
	return s
}

func TestCAT2OperatorActivation(t *testing.T) {
	off := cat2Store(t, cat2Config(t, "{}"))
	on := cat2Store(t, cat2Config(t, "managed:\n  catalogue_families: [cat2]\n"))
	var changed, reverse []string
	for _, snap := range off.Load().Snapshots() {
		for id, m := range snap.Models {
			a := off.Load().ServesManaged(snap.ProviderID, id)
			b := on.Load().ServesManaged(snap.ProviderID, id)
			if _, exists := on.Load().Get(snap.ProviderID, id); !exists {
				t.Errorf("ON lost model %s", id)
			}
			if a && !b {
				reverse = append(reverse, id)
			}
			if !cat2FamilyMember(id) {
				before, beforeOK := off.Load().PriceCard(snap.ProviderID, id, "")
				after, afterOK := on.Load().PriceCard(snap.ProviderID, id, "")
				before.SnapshotID, after.SnapshotID = "", ""
				if a != b || beforeOK != afterOK || !reflect.DeepEqual(before, after) {
					t.Errorf("non-family service or card changed: %s", id)
				}
			}
			if !m.Servable(time.Date(2026, 10, 27, 12, 0, 0, 0, time.UTC)) || id == "google/gemini-2.5-flash-image" || a || !b {
				continue
			}
			changed = append(changed, id)
			if card, ok := off.Load().PriceCard("openrouter", id, ""); ok && card.Billable() {
				t.Errorf("off priced %s", id)
			}
			card, ok := on.Load().PriceCard("openrouter", id, "")
			if !ok || card.Inclusive == nil {
				t.Fatalf("on unpriced %s", id)
			}
			if id == "google/lyria-3-pro-preview" && card.Inclusive.Generation != "0.08" {
				t.Fatal("Lyria pro price changed")
			}
			if id == "google/lyria-3-clip-preview" && card.Inclusive.Generation != "0.04" {
				t.Fatal("Lyria clip price changed")
			}
		}
	}
	// Pin identities, not just a count: a different model cannot substitute for
	// one of the 28 callable listings. Routers and retired IDs stay out.
	want := []string{
		"google/gemini-2.5-flash-lite:batch",
		"google/gemini-2.5-pro-preview",
		"google/gemini-3-flash-preview",
		"google/gemini-3-flash-preview:batch",
		"google/gemini-3-pro-image",
		"google/gemini-3-pro-image-preview",
		"google/gemini-3.1-flash-lite",
		"google/gemini-3.1-flash-lite-preview",
		"google/gemini-3.1-flash-lite:batch",
		"google/gemini-3.1-pro-preview",
		"google/gemini-3.1-pro-preview-customtools",
		"google/gemini-3.1-pro-preview:batch",
		"google/gemini-3.5-flash",
		"google/gemini-3.5-flash-lite",
		"google/gemini-3.5-flash-lite:batch",
		"google/gemini-3.5-flash:batch",
		"google/gemini-3.6-flash",
		"google/gemini-3.6-flash:batch",
		"google/gemini-3.7-flash",
		"google/gemini-3.7-flash:batch",
		"google/gemini-3.8-flash",
		"google/gemini-3.8-flash:batch",
		"google/gemini-4-argon",
		"google/gemini-4-argon:batch",
		"google/lyria-3-clip-preview",
		"google/lyria-3-pro-preview",
		"~google/gemini-flash-latest",
		"~google/gemini-pro-latest",
	}
	sort.Strings(changed)
	sort.Strings(reverse)
	if !reflect.DeepEqual(changed, want) {
		t.Errorf("activated=%v want exact sorted set %v", changed, want)
	}
	if len(reverse) != 0 {
		t.Errorf("ON disabled models: %v", reverse)
	}
	for _, snap := range on.Load().Snapshots() {
		for id := range snap.Models {
			if _, exists := off.Load().Get(snap.ProviderID, id); !exists {
				t.Errorf("ON added model instead of changing availability: %s", id)
			}
		}
	}
	if !off.Load().MixedCatalogueDisabled() || !on.Load().MixedCatalogueDisabled() {
		t.Fatal("operator activation enabled mixed chains")
	}
	if on.Load().ServesManaged("openrouter", "openrouter/fusion") {
		t.Fatal("Fusion activated")
	}
}

func TestCAT2ActivationRestartAndRefresh(t *testing.T) {
	cfg := cat2Config(t, "managed:\n  catalogue_families: []\n")
	old := cat2Store(t, cfg)
	t.Setenv("AGENTCC_MANAGED_CATALOGUE_FAMILIES", "cat2")
	newCfg := cat2Config(t, "{}")
	fresh := cat2Store(t, newCfg)
	id := "google/lyria-3-pro-preview"
	Seed(old, []Source{{ProviderID: "openrouter", Type: SourceOpenRouter}})
	if old.Load().ServesManaged("openrouter", id) {
		t.Fatal("environment hot-reloaded without restart")
	}
	if !fresh.Load().ServesManaged("openrouter", id) {
		t.Fatal("restart did not activate")
	}
	Seed(fresh, []Source{{ProviderID: "openrouter", Type: SourceOpenRouter}})
	if !fresh.Load().ServesManaged("openrouter", id) {
		t.Fatal("refresh lost startup activation")
	}
	t.Setenv("AGENTCC_MANAGED_CATALOGUE_FAMILIES", "")
	disabled := cat2Store(t, cat2Config(t, "managed:\n  catalogue_families: [cat2]\n"))
	if disabled.Load().ServesManaged("openrouter", id) {
		t.Fatal("empty environment did not override file to off")
	}
}
