package catalog

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"testing"
	"time"
)

// newTestRefresher is a refresher of one OpenRouter provider ("or") served by
// u, on clock, pacing requests with the fake clock's sleep.
func newTestRefresher(t *testing.T, u *fakeUpstream, clock *fakeClock, opts Options) (*Store, *Refresher, *int) {
	t.Helper()
	opts.Now = clock.Now
	s := NewStore()
	if err := s.Configure(opts); err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	t.Cleanup(cancel)
	updates := 0
	r := NewRefresher(ctx, s, []Source{u.source("or")}, func() { updates++ })
	r.pace = newPacer(s.opts.EndpointsRPS, clock.Now, clock.Sleep)
	return s, r, &updates
}

func TestRefreshKeepsSnapshotOnFailure(t *testing.T) {
	u := newFakeUpstream(t, fixture(t, "models.json"))
	s, r, updates := newTestRefresher(t, u, newFakeClock(), Options{})

	r.RefreshModels("or")
	if !s.Load().Has("or", "anthropic/claude-sonnet-5.5") || *updates != 1 {
		t.Fatalf("live refresh did not land (updates=%d)", *updates)
	}
	before := s.Load().ID()

	u.fail.Store(true)
	r.RefreshModels("or")
	if !s.Load().Has("or", "anthropic/claude-sonnet-5.5") || s.Load().ID() != before {
		t.Fatal("a failed refresh dropped or changed the previous snapshot")
	}
	if *updates != 1 || len(s.History(0)) != 1 {
		t.Fatalf("a failed refresh must not report an update or a version (updates=%d, history=%d)", *updates, len(s.History(0)))
	}

	// An empty catalog is a failed fetch too, never a wipe.
	u.fail.Store(false)
	u.set(func(u *fakeUpstream) { u.models = []byte(`{"data":[]}`) })
	r.RefreshModels("or")
	if !s.Load().Has("or", "anthropic/claude-sonnet-5.5") {
		t.Fatal("an empty model list wiped the catalog")
	}
}

func TestRefreshCarriesEndpointsAndZDR(t *testing.T) {
	u := newFakeUpstream(t, fixture(t, "models.json"))
	u.zdr = fixture(t, "zdr.json")
	u.endpoints["anthropic/claude-sonnet-5.5"] = fixture(t, "endpoints_claude.json")
	clock := newFakeClock()
	s, r, _ := newTestRefresher(t, u, clock, Options{})

	r.RefreshModels("or")
	if _, m, _ := s.ModelEndpoints(context.Background(), "or", "anthropic/claude-sonnet-5.5"); len(m.Endpoints) != 4 {
		t.Fatalf("lazy read got %d endpoints, want 4", len(m.Endpoints))
	}
	m, _ := s.Load().Get("or", "anthropic/claude-sonnet-5.5")
	if e := m.Endpoints[0]; e.ZDR == nil || !*e.ZDR || e.DataCollection != DataCollectionDeny {
		t.Fatalf("the ZDR list read with the model list must mark the direct endpoint: %+v", e)
	}

	// The next model-list refresh keeps the endpoints read, and a ZDR list
	// that cannot be read keeps the previous one.
	u.set(func(u *fakeUpstream) { u.zdr = nil })
	clock.Advance(time.Minute)
	r.RefreshModels("or")
	m, _ = s.Load().Get("or", "anthropic/claude-sonnet-5.5")
	if len(m.Endpoints) != 4 || m.Endpoints[0].ZDR == nil || !*m.Endpoints[0].ZDR {
		t.Fatalf("after a refresh the model has %d endpoints, first ZDR %v", len(m.Endpoints), m.Endpoints[0].ZDR)
	}
	if snap := s.Load().byProvider["or"]; snap.Source != OriginLive || !snap.FetchedAt.Equal(clock.Now()) {
		t.Fatalf("snapshot origin %q fetched %v", snap.Source, snap.FetchedAt)
	}
}

func TestCrawlerRateLimited(t *testing.T) {
	models := map[string]any{}
	var data []map[string]any
	u := newFakeUpstream(t, nil)
	for i := 0; i < 12; i++ {
		id := fmt.Sprintf("vendor/m%02d", i)
		data = append(data, map[string]any{"id": id, "created": 1790000000 + i,
			"pricing": map[string]any{"prompt": "0.000001", "completion": "0.000002"}})
		u.endpoints[id] = []byte(fmt.Sprintf(`{"data":{"id":%q,"endpoints":[{"name":"V | %s","provider_name":"V","tag":"v",`+
			`"context_length":1000,"pricing":{"prompt":"0.0000011","completion":"0.000002"},"status":0}]}}`, id, id))
	}
	models["data"] = data
	body, _ := json.Marshal(models)
	u.models = body
	clock := newFakeClock()
	u.clock = clock
	s, r, _ := newTestRefresher(t, u, clock, Options{EndpointsRPS: 4})
	r.RefreshModels("or")

	start := clock.Now()
	r.Crawl()
	reqs := u.paths("/api/v1/models/")
	if len(reqs) != 12 {
		t.Fatalf("crawled %d models, want all 12", len(reqs))
	}
	for i := 1; i < len(reqs); i++ {
		if gap := reqs[i].at.Sub(reqs[i-1].at); gap < 250*time.Millisecond {
			t.Fatalf("requests %d and %d are %v apart, want at least 250ms at 4 requests a second", i-1, i, gap)
		}
	}
	if took := clock.Now().Sub(start); took < 11*250*time.Millisecond {
		t.Fatalf("12 requests at 4/s took %v of clock time", took)
	}
	// The crawl is one catalog change, priced from the endpoints read.
	hist := s.History(0)
	if len(hist) != 2 || hist[0].Source != originEndpoints || hist[0].Diff.PriceChangedCount != 0 || hist[0].EndpointCount != 12 {
		t.Fatalf("history after the crawl = %+v", hist)
	}
	m, _ := s.Load().Get("or", "vendor/m00")
	if !m.EndpointsFetchedAt.After(start) || len(m.Endpoints) != 1 {
		t.Fatalf("crawled model = %+v", m)
	}
}

func TestCrawlSelection(t *testing.T) {
	s := NewStore()
	models := fixtureModels(t)
	for i := 0; i < 60; i++ {
		id := fmt.Sprintf("vendor/m%02d", i)
		models[id] = &Model{ID: id, Created: int64(1700000000 + i), Prices: Prices{Prompt: 1e-6}}
	}
	models["vendor/old-blocked"] = &Model{ID: "vendor/old-blocked", Created: 1799999999, Prices: Prices{Prompt: 1e-6}}
	s.Put(&Snapshot{ProviderID: "or", Models: models})
	mustBlock(t, s, []string{"vendor/old-blocked"}, "x")
	got := crawlSelection(s.Load(), "or", crawlModels)
	if len(got) != crawlModels {
		t.Fatalf("selected %d, want %d", len(got), crawlModels)
	}
	for _, id := range got {
		switch id {
		case "openrouter/auto", "qwen/qwen3.7-flash:free", "vendor/old-blocked":
			t.Fatalf("selected %s: dynamic, variant and blocked models are not crawled", id)
		}
	}
	if got[0] != "anthropic/claude-sonnet-5.5" {
		t.Fatalf("newest first: got %s", got[0])
	}
}

func TestModelEndpointsLazyRefresh(t *testing.T) {
	u := newFakeUpstream(t, fixture(t, "models.json"))
	u.endpoints["anthropic/claude-sonnet-5.5"] = fixture(t, "endpoints_claude.json")
	clock := newFakeClock()
	s, r, _ := newTestRefresher(t, u, clock, Options{})
	r.RefreshModels("or")
	reads := func() int { return len(u.paths("/api/v1/models/anthropic/")) }

	if _, _, ok := s.ModelEndpoints(context.Background(), "", "no/such-model"); ok {
		t.Fatal("an unlisted model must not be found")
	}
	provider, m, ok := s.ModelEndpoints(context.Background(), "", "anthropic/claude-sonnet-5.5")
	if !ok || provider != "or" || len(m.Endpoints) != 4 || reads() != 1 {
		t.Fatalf("first lookup: provider %q, %d endpoints, %d reads", provider, len(m.Endpoints), reads())
	}
	clock.Advance(29 * time.Minute)
	s.ModelEndpoints(context.Background(), "or", "anthropic/claude-sonnet-5.5")
	if reads() != 1 {
		t.Fatal("endpoints younger than 30 minutes must be served without a read")
	}
	clock.Advance(2 * time.Minute)
	u.fail.Store(true)
	defer u.fail.Store(false)
	_, m, _ = s.ModelEndpoints(context.Background(), "or", "anthropic/claude-sonnet-5.5")
	if reads() != 2 || len(m.Endpoints) != 4 {
		t.Fatalf("a stale lookup must re-read (reads=%d), and a failed read serve what it had (%d endpoints)", reads(), len(m.Endpoints))
	}
}

func TestRequestRefreshRateLimited(t *testing.T) {
	u := newFakeUpstream(t, fixture(t, "models.json"))
	clock := newFakeClock()
	s, _, _ := newTestRefresher(t, u, clock, Options{})

	started, _, err := s.RequestRefresh(nil)
	if err != nil || len(started) != 1 || started[0] != "or" {
		t.Fatalf("first refresh: %v %v", started, err)
	}
	clock.Advance(20 * time.Second)
	started, wait, err := s.RequestRefresh([]string{"or"})
	if err != nil || len(started) != 0 || wait != 40*time.Second {
		t.Fatalf("second refresh within the minute: started %v, wait %v, err %v", started, wait, err)
	}
	clock.Advance(40 * time.Second)
	if started, _, _ := s.RequestRefresh([]string{"or"}); len(started) != 1 {
		t.Fatal("a refresh a minute later must start")
	}
	if _, _, err := s.RequestRefresh([]string{"nope"}); !errors.Is(err, ErrUnknownProvider) {
		t.Fatalf("unknown provider: %v", err)
	}
	if _, _, err := NewStore().RequestRefresh(nil); err == nil {
		t.Fatal("a store with no refresher must refuse")
	}
}

func TestBundledEndpointsAreReReadLive(t *testing.T) {
	u := newFakeUpstream(t, fixture(t, "models.json"))
	u.endpoints["anthropic/claude-sonnet-5.5"] = fixture(t, "endpoints_claude.json")
	clock := newFakeClock()
	s, _, _ := newTestRefresher(t, u, clock, Options{})
	bundled := fixtureModels(t)
	bundled["anthropic/claude-sonnet-5.5"].Endpoints = []Endpoint{{ID: "openrouter:old:anthropic/claude-sonnet-5.5", Tag: "old"}}
	bundled["anthropic/claude-sonnet-5.5"].EndpointsFetchedAt = clock.Now() // a bundle fetched a moment ago
	bundled["anthropic/claude-sonnet-5.5"].EndpointsOrigin = OriginBundled
	s.Put(&Snapshot{ProviderID: "or", Source: OriginBundled, CatalogSource: SourceOpenRouter, Models: bundled})

	_, m, _ := s.ModelEndpoints(context.Background(), "or", "anthropic/claude-sonnet-5.5")
	if len(m.Endpoints) != 4 || m.EndpointsOrigin != OriginLive {
		t.Fatalf("bundled endpoints must be re-read live however young: %d endpoints, origin %q", len(m.Endpoints), m.EndpointsOrigin)
	}
}

func TestFirstCrawlWaitsForTheModelLists(t *testing.T) {
	u := newFakeUpstream(t, fixture(t, "models.json"))
	u.endpoints["anthropic/claude-sonnet-5.5"] = fixture(t, "endpoints_claude.json")
	clock := newFakeClock()
	s := NewStore()
	mustConfigure(t, s, Options{EndpointsRefresh: time.Hour, Now: clock.Now})
	s.Put(&Snapshot{ProviderID: "or", Source: OriginBundled, CatalogSource: SourceOpenRouter, Models: map[string]*Model{
		"bundled/only": {ID: "bundled/only", Created: 1799999999, Prices: Prices{Prompt: 1e-6}},
	}})
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	r := NewRefresher(ctx, s, []Source{u.source("or")}, nil)
	r.pace = newPacer(5, clock.Now, clock.Sleep)

	go r.RunEndpoints()
	time.Sleep(50 * time.Millisecond)
	if n := len(u.paths("/api/v1/models/")); n != 0 {
		t.Fatalf("the crawler read %d endpoints before the first model-list refresh", n)
	}
	go r.Run(time.Hour)
	deadline := time.Now().Add(5 * time.Second)
	for len(u.paths("/api/v1/models/anthropic/")) == 0 {
		if time.Now().After(deadline) {
			t.Fatal("the crawler never ran after the model lists were refreshed")
		}
		time.Sleep(5 * time.Millisecond)
	}
	if n := len(u.paths("/api/v1/models/bundled/")); n != 0 {
		t.Fatal("the first crawl must pick from the live lists, not the bundled one they replaced")
	}
}
