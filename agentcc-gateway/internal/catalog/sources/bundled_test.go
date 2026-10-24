package sources

import (
	"testing"
	"testing/fstest"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/catalog"
)

// TestBundledOpenAICompatibleSnapshot: a list source starts from the
// snapshot bundled for its upstream slug, at the time it was fetched, and
// a slug with none has no boot snapshot.
func TestBundledOpenAICompatibleSnapshot(t *testing.T) {
	fsys := fstest.MapFS{
		"snapshots/huggingface.json": {Data: []byte(`{"fetched_at":"2026-10-10T08:00:00Z","source_url":"https://example.test/v1/models",
			"data":[{"id":"deepseek-ai/DeepSeek-R1","context_length":163840,"pricing":{"prompt":"0.00000055","completion":"0.00000219"}},
			{"id":"Qwen/Qwen3-32B"}]}`)},
		"snapshots/broken.json": {Data: []byte(`{"data":[{"id":"m"}]}`)},
	}
	if bundledLoader(fsys, "hf", "together") != nil {
		t.Fatal("a slug with no bundled snapshot has a loader")
	}
	snap, err := bundledLoader(fsys, "hf", "huggingface")()
	if err != nil {
		t.Fatal(err)
	}
	if snap.ProviderID != "hf" || snap.Source != catalog.OriginBundled || snap.CatalogSource != catalog.SourceOpenAICompatible ||
		!snap.FetchedAt.Equal(time.Date(2026, 10, 10, 8, 0, 0, 0, time.UTC)) || len(snap.Models) != 2 {
		t.Fatalf("snapshot = %+v", snap)
	}
	if r1 := snap.Models["deepseek-ai/DeepSeek-R1"]; !r1.Priced() || len(r1.Endpoints) != 1 || r1.Endpoints[0].UpstreamProvider != "huggingface" {
		t.Errorf("priced model = %+v", r1)
	}
	if q := snap.Models["Qwen/Qwen3-32B"]; !q.Unpriced {
		t.Errorf("unpriced model = %+v", q)
	}
	if _, err := bundledLoader(fsys, "x", "broken")(); err == nil {
		t.Error("a bundled snapshot without fetched_at loaded")
	}

	// Seeded at boot like a price card, without a history entry.
	store := catalog.NewStore()
	catalog.SeedStatic(store, []catalog.Source{{ProviderID: "hf", CatalogSource: catalog.SourceOpenAICompatible,
		Bundled: bundledLoader(fsys, "hf", "huggingface")}})
	if !store.Load().Has("hf", "deepseek-ai/DeepSeek-R1") || len(store.History(0)) != 0 {
		t.Errorf("seeded: has %v, history %d", store.Load().Has("hf", "deepseek-ai/DeepSeek-R1"), len(store.History(0)))
	}
	// The gateway bundles none.
	if bundledLoader(bundledSnapshots, "hf", "huggingface") != nil {
		t.Error("an unexpected bundled snapshot; update this test and the README when one is added")
	}
}

// TestSeedStaticNeverRollsBack: a bundled list never replaces a fresher list
// of its kind (one restored from snapshot_dir), and a list of another kind
// never stays: OpenRouter's bundle, which Seed puts under an openrouter-type
// provider before Resolve names its catalog_source, is replaced by the
// source's bundle, or, with none bundled, by an empty list until the first
// fetch.
func TestSeedStaticNeverRollsBack(t *testing.T) {
	fsys := fstest.MapFS{"snapshots/huggingface.json": {Data: []byte(`{"fetched_at":"2026-10-10T08:00:00Z",
		"data":[{"id":"bundled-model","pricing":{"prompt":"0.000001","completion":"0.000002"}}]}`)}}
	hf := catalog.Source{ProviderID: "hf", CatalogSource: catalog.SourceOpenAICompatible, Bundled: bundledLoader(fsys, "hf", "huggingface")}
	restored := func(day int) *catalog.Snapshot {
		return &catalog.Snapshot{ProviderID: "hf", Source: catalog.OriginRestored, CatalogSource: catalog.SourceOpenAICompatible,
			FetchedAt: time.Date(2026, 10, day, 8, 0, 0, 0, time.UTC),
			Models:    map[string]*catalog.Model{"restored-model": {ID: "restored-model", InputPerToken: 1e-6}}}
	}
	for _, tc := range []struct {
		name         string
		day          int
		wantRestored bool
	}{{"a fresher restored list stays", 12, true}, {"an older restored list is replaced", 1, false}} {
		store := catalog.NewStore()
		store.Put(restored(tc.day))
		catalog.SeedStatic(store, []catalog.Source{hf})
		cat := store.Load()
		if cat.Has("hf", "restored-model") != tc.wantRestored || cat.Has("hf", "bundled-model") == tc.wantRestored {
			t.Errorf("%s: restored %v, bundled %v", tc.name, cat.Has("hf", "restored-model"), cat.Has("hf", "bundled-model"))
		}
	}

	// An openrouter-type provider read as another kind: Seed (main.go, before
	// Resolve) puts OpenRouter's bundle under it.
	orType := catalog.Source{ProviderID: "gen", Type: catalog.SourceOpenRouter}
	store := catalog.NewStore()
	catalog.Seed(store, []catalog.Source{orType})
	if len(store.Load().Snapshots()) != 1 || len(store.Load().Snapshots()[0].Models) == 0 {
		t.Fatal("Seed did not put OpenRouter's bundle in")
	}
	generic := orType
	generic.CatalogSource = catalog.SourceOpenAICompatible
	catalog.SeedStatic(store, []catalog.Source{generic})
	snaps := store.Load().Snapshots()
	if len(snaps) != 1 || snaps[0].CatalogSource != catalog.SourceOpenAICompatible || len(snaps[0].Models) != 0 {
		t.Fatalf("an openai_compatible source with nothing bundled kept %d models of kind %q", len(snaps[0].Models), snaps[0].CatalogSource)
	}
	st := store.Stats()
	if _, ok := st.PerSource[catalog.SourceOpenRouter]; ok || st.Endpoints != 0 {
		t.Errorf("stats after the empty seed = %+v", st)
	}
	// With a bundle of its own, the bundle replaces OpenRouter's, however new.
	store = catalog.NewStore()
	catalog.Seed(store, []catalog.Source{{ProviderID: "hf", Type: catalog.SourceOpenRouter}})
	withBundle := hf
	withBundle.Type = catalog.SourceOpenRouter
	catalog.SeedStatic(store, []catalog.Source{withBundle})
	if snap := store.Load().Snapshots()[0]; snap.CatalogSource != catalog.SourceOpenAICompatible || len(snap.Models) != 1 {
		t.Errorf("the source's bundle did not replace OpenRouter's: %d models of kind %q", len(snap.Models), snap.CatalogSource)
	}
}
