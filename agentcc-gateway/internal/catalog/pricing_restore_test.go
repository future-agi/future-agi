package catalog

import (
	"compress/gzip"
	"encoding/json"
	"os"
	"testing"
	"time"
)

func TestCompletePricingLegacySnapshotNotRestoredForServing(t *testing.T) {
	dir := t.TempDir()
	s := NewStore()
	if err := s.Configure(Options{SnapshotDir: dir, Providers: []string{"openrouter"}}); err != nil {
		t.Fatal(err)
	}
	s.Put(&Snapshot{ProviderID: "openrouter", CatalogSource: SourceOpenRouter, Source: OriginLive, FetchedAt: time.Now(), Models: map[string]*Model{"acme/old": {ID: "acme/old", Prices: Prices{Prompt: 0.000002, Completion: 0.00001}}}})
	cat := s.Load()
	// Legacy files stored only the normalized catalogue and cannot prove that
	// the old parser preserved every price dimension from the upstream.
	b, err := json.Marshal(cat)
	if err != nil {
		t.Fatal(err)
	}
	p, err := newPersister(dir)
	if err != nil {
		t.Fatal(err)
	}
	f, err := os.Create(p.snapshotPath(cat.ID()))
	if err != nil {
		t.Fatal(err)
	}
	z := gzip.NewWriter(f)
	if _, err := z.Write(b); err != nil {
		t.Fatal(err)
	}
	if err := z.Close(); err != nil {
		t.Fatal(err)
	}
	f.Close()
	restarted := NewStore()
	if err := restarted.Configure(Options{SnapshotDir: dir, Providers: []string{"openrouter"}}); err != nil {
		t.Fatal(err)
	}
	if restarted.Load().ServesManaged("openrouter", "acme/old") {
		t.Fatal("legacy price classification restored into serving")
	}
	// History remains readable for audit, not activated as fresh pricing.
	if _, ok := restarted.Snapshot(cat.ID()); !ok {
		t.Fatal("legacy audit snapshot must remain readable")
	}
}
