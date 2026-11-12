package catalog

import (
	"encoding/json"
	"fmt"
	"testing"
	"time"
)

// pricedModels is n models, each priced at its index plus offset micros per
// prompt token.
func pricedModels(n int, offset float64) map[string]*Model {
	out := make(map[string]*Model, n)
	for i := 0; i < n; i++ {
		id := fmt.Sprintf("vendor/m%03d", i)
		out[id] = &Model{ID: id, Prices: Prices{Prompt: (float64(i) + offset) * 1e-6, Completion: 2e-6}}
	}
	return out
}

func withEndpointFixture(t *testing.T) map[string]*Model {
	t.Helper()
	models := fixtureModels(t)
	eps, err := ParseOpenRouterEndpoints("anthropic/claude-sonnet-5.5", fixture(t, "endpoints_claude.json"))
	if err != nil {
		t.Fatal(err)
	}
	models["anthropic/claude-sonnet-5.5"].Endpoints = eps
	return models
}

func TestSnapshotIDStableAndPriceSensitive(t *testing.T) {
	idOf := func(models map[string]*Model) string {
		s := NewStore()
		s.Put(&Snapshot{ProviderID: "or", CatalogSource: SourceOpenRouter, Models: models})
		return s.Load().ID()
	}
	base := idOf(withEndpointFixture(t))
	if len(base) != 12 || !snapshotIDPattern.MatchString(base) {
		t.Fatalf("snapshot id %q is not 12 hex characters", base)
	}
	if again := idOf(withEndpointFixture(t)); again != base {
		t.Fatalf("the same content gave ids %s and %s", base, again)
	}

	// One model price, one endpoint price, one tier: each is a new version.
	for name, mutate := range map[string]func(map[string]*Model){
		"model price": func(m map[string]*Model) { m["xiaomi/mimo-v2.5"].Prices.Completion = 1.7e-6 },
		"endpoint price": func(m map[string]*Model) {
			m["anthropic/claude-sonnet-5.5"].Endpoints[1].Prices.Prompt = 3.4e-6
		},
		"tier price": func(m map[string]*Model) {
			m["anthropic/claude-sonnet-5.5"].PriceTiers[0].Prices.Prompt = 7e-6
		},
		"model removed": func(m map[string]*Model) { delete(m, "xiaomi/mimo-v2.5") },
	} {
		models := withEndpointFixture(t)
		mutate(models)
		if idOf(models) == base {
			t.Errorf("%s changed but the snapshot id did not", name)
		}
	}

	// Live health, fetch time and description are not catalog content.
	models := withEndpointFixture(t)
	up := 12.5
	ep := &models["anthropic/claude-sonnet-5.5"].Endpoints[0]
	ep.Status, ep.UptimeLast30m, ep.LatencyP50Ms = -5, &up, &up
	models["xiaomi/mimo-v2.5"].Description = "rewritten"
	models["xiaomi/mimo-v2.5"].EndpointsFetchedAt = time.Now()
	if idOf(models) != base {
		t.Error("health figures and descriptions must not change the snapshot id")
	}
}

func TestHistoryKeepsFiftyWithDiffs(t *testing.T) {
	clock := newFakeClock()
	s := NewStore()
	mustConfigure(t, s, Options{HistorySize: 50, KeepSnapshots: 10, Now: clock.Now})
	for i := 0; i < 60; i++ {
		clock.Advance(time.Minute)
		models := pricedModels(3, float64(i))
		if i%2 == 1 {
			id := fmt.Sprintf("vendor/new%03d", i)
			models[id] = &Model{ID: id, Prices: Prices{Prompt: 1e-6}}
		}
		s.Put(&Snapshot{ProviderID: "or", Source: OriginLive, FetchedAt: clock.Now(), Models: models})
	}
	hist := s.History(0)
	if len(hist) != 50 {
		t.Fatalf("history has %d entries, want 50", len(hist))
	}
	newest := hist[0]
	if newest.ID != s.Load().ID() || newest.Provider != "or" || newest.Source != OriginLive {
		t.Fatalf("newest entry = %+v, want the current version", newest)
	}
	// Version 59 (odd) adds vendor/new059 and moves all three prices;
	// version 58 (even) dropped the vendor/new057 that version 57 added.
	if newest.Diff.PriceChangedCount != 3 || len(newest.Diff.Added) != 1 || newest.Diff.Added[0] != "vendor/new059" {
		t.Fatalf("newest diff = %+v, want 3 price changes and vendor/new059 added", newest.Diff)
	}
	if prev := hist[1]; len(prev.Diff.Removed) != 1 || prev.Diff.Removed[0] != "vendor/new057" {
		t.Fatalf("previous diff = %+v, want vendor/new057 removed", prev.Diff)
	}
	if newest.ModelCount != 4 || !newest.FetchedAt.Equal(clock.Now()) {
		t.Fatalf("newest entry counts = %+v", newest)
	}
	if got := s.History(5); len(got) != 5 || got[0].ID != newest.ID {
		t.Fatalf("History(5) = %d entries", len(got))
	}

	// Only the last 10 versions' content is kept.
	if _, ok := s.Snapshot(hist[0].ID); !ok {
		t.Fatal("the current version's content must be kept")
	}
	if _, ok := s.Snapshot(hist[9].ID); !ok {
		t.Fatal("the tenth-newest version's content must be kept")
	}
	if _, ok := s.Snapshot(hist[10].ID); ok {
		t.Fatal("content beyond keep_snapshots must be dropped")
	}
}

func TestHistorySkipsUnchangedRefreshesAndTheSeed(t *testing.T) {
	s := NewStore()
	s.Put(&Snapshot{ProviderID: "or", Source: OriginBundled, Models: pricedModels(2, 0)})
	s.Put(&Snapshot{ProviderID: "or", Source: OriginLive, Models: pricedModels(2, 1)})
	s.Put(&Snapshot{ProviderID: "or", Source: OriginLive, Models: pricedModels(2, 1)})
	hist := s.History(0)
	if len(hist) != 1 {
		t.Fatalf("history = %d entries, want 1: the seed and an unchanged refresh are not versions", len(hist))
	}
	if hist[0].Diff.PriceChangedCount != 2 {
		t.Fatalf("the first live version's diff is against the seed it replaced: %+v", hist[0].Diff)
	}
}

func TestDiffCapsLists(t *testing.T) {
	d := diffSnapshots(&Snapshot{Models: pricedModels(250, 0)}, &Snapshot{Models: pricedModels(250, 1)})
	if d.PriceChangedCount != 250 || len(d.PriceChanged) != maxDiffItems || d.PriceChanged[0] != "vendor/m000" {
		t.Fatalf("diff = %d listed of %d", len(d.PriceChanged), d.PriceChangedCount)
	}
	if d.Added == nil || d.Removed == nil {
		t.Fatal("empty diff lists must be [], not null")
	}
}

func TestSnapshotRetainsReplayableContent(t *testing.T) {
	s := NewStore()
	s.Put(&Snapshot{ProviderID: "or", Source: OriginLive, Models: pricedModels(2, 0)})
	first := s.Load().ID()
	s.Put(&Snapshot{ProviderID: "or", Source: OriginLive, Models: pricedModels(2, 5)})
	old, ok := s.Snapshot(first)
	if !ok {
		t.Fatal("the previous version must be retrievable")
	}
	m, _ := old.Get("or", "vendor/m001")
	if m.Prices.Prompt != 1e-6 {
		t.Fatalf("old version's price = %v, want the price it had", m.Prices.Prompt)
	}
	if _, ok := s.Snapshot("../../etc/x"); ok {
		t.Fatal("a malformed id must never be looked up")
	}
}

func TestStaleAfterThreeIntervals(t *testing.T) {
	clock := newFakeClock()
	s := NewStore()
	mustConfigure(t, s, Options{RefreshInterval: time.Hour, Now: clock.Now})
	if s.Stale() {
		t.Fatal("an empty catalog is not stale")
	}
	s.Put(&Snapshot{ProviderID: "or", Source: OriginLive, FetchedAt: clock.Now(), Models: pricedModels(1, 0)})
	clock.Advance(3 * time.Hour)
	if s.Stale() || s.Age() != 3*time.Hour {
		t.Fatalf("stale at exactly three intervals (age %v)", s.Age())
	}
	clock.Advance(time.Second)
	if !s.Stale() {
		t.Fatal("not stale past three intervals")
	}
	// An unchanged refresh is fresh data: it resets the age.
	s.Put(&Snapshot{ProviderID: "or", Source: OriginLive, FetchedAt: clock.Now(), Models: pricedModels(1, 0)})
	if s.Stale() {
		t.Fatal("an unchanged but successful refresh must clear staleness")
	}
}

func TestStatsCounts(t *testing.T) {
	s := NewStore()
	s.Put(&Snapshot{ProviderID: "openrouter", CatalogSource: SourceOpenRouter, Models: withEndpointFixture(t)})
	s.Put(&Snapshot{ProviderID: "plain", CatalogSource: SourceOpenAICompatible, Models: map[string]*Model{
		"anthropic/claude-sonnet-5.5": {ID: "anthropic/claude-sonnet-5.5", Unpriced: true},
	}})
	st := s.Stats()
	// Five OpenRouter models (one free, one dynamic) and a plain listing of
	// one of them: five canonical models, since the canonical id merges a
	// listing whose upstream slug is dated with the same model elsewhere.
	// The dynamic-price endpoint is not priced.
	if st.Endpoints != 4 || st.PricedEndpoints != 3 || st.FreeModels != 1 {
		t.Fatalf("stats = %+v", st)
	}
	or := st.PerSource[SourceOpenRouter]
	if or.Models != 5 || or.Endpoints != 4 || or.PricedEndpoints != 3 || or.FreeModels != 1 {
		t.Fatalf("openrouter stats = %+v", or)
	}
	if st.PerSource[SourceOpenAICompatible].Models != 1 || st.PerSource[SourceOpenAICompatible].FreeModels != 0 {
		t.Fatalf("plain stats = %+v; an unpriced model is not free", st.PerSource[SourceOpenAICompatible])
	}
	if st.CanonicalModels != 5 {
		t.Fatalf("canonical models = %d, want 5", st.CanonicalModels)
	}
	if or.CanonicalModels != 5 || st.PerSource[SourceOpenAICompatible].CanonicalModels != 1 {
		t.Fatalf("per-source canonical models = openrouter %d, plain %d", or.CanonicalModels, st.PerSource[SourceOpenAICompatible].CanonicalModels)
	}
}

func TestSnapshotIDSurvivesPersistRoundTrip(t *testing.T) {
	zdr, err := ParseZDR(fixture(t, "zdr.json"))
	if err != nil {
		t.Fatal(err)
	}
	fixtureStore := NewStore()
	fixtureStore.Put(&Snapshot{ProviderID: "or", Source: OriginLive, CatalogSource: SourceOpenRouter, Models: withEndpointFixture(t), ZDR: zdr})
	bundledStore := NewStore()
	Seed(bundledStore, []Source{{ProviderID: "openrouter", Type: SourceOpenRouter}})

	for name, before := range map[string]*Catalog{"fixtures": fixtureStore.Load(), "bundled": bundledStore.Load()} {
		b, err := json.Marshal(before)
		if err != nil {
			t.Fatal(err)
		}
		var after Catalog
		if err := json.Unmarshal(b, &after); err != nil {
			t.Fatal(err)
		}
		if got := snapshotID(after.byProvider); got != before.ID() {
			t.Fatalf("%s: a persisted version reads back as %s, not %s: a restart would mint a new version of unchanged content",
				name, got, before.ID())
		}
	}
}
