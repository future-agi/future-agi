package main

import (
	"os"
	"path/filepath"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/catalog"
	"github.com/futureagi/agentcc-gateway/internal/config"
)

func TestCAT2StartupWiring(t *testing.T) {
	for _, families := range []string{"[]", "[cat2]"} {
		t.Run(families, func(t *testing.T) {
			path := filepath.Join(t.TempDir(), "gateway.yaml")
			if err := os.WriteFile(path, []byte("managed:\n  catalogue_families: "+families+"\n"), 0600); err != nil {
				t.Fatal(err)
			}
			cfg, err := config.Load(path)
			if err != nil {
				t.Fatal(err)
			}
			sources := []catalog.Source{{ProviderID: "openrouter", Type: catalog.SourceOpenRouter}}
			d := &wiringDeps{cfg: cfg, catalogStore: catalog.NewStore(), catalogSources: sources}
			// Match main: bundled catalogue is seeded before initCatalog applies
			// operator policy. Never start the refresher or contact a provider.
			catalog.Seed(d.catalogStore, sources)
			if err := initCatalog(d); err != nil {
				t.Fatal(err)
			}
			card, priced := d.prices("openrouter", "google/lyria-3-pro-preview", "")
			if want := families == "[cat2]"; (priced && card.Billable()) != want || (want && (card.Inclusive == nil || card.Inclusive.Generation != "0.08")) {
				t.Fatalf("families=%s priced=%t card=%+v", families, priced, card)
			}
			if !d.catalogStore.Load().MixedCatalogueDisabled() {
				t.Fatal("startup enabled mixed chains")
			}
		})
	}
}
