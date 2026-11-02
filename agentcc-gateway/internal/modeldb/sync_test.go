package modeldb

import (
	"context"
	"fmt"
	"net/http"
	"net/http/httptest"
	"sync/atomic"
	"testing"
	"time"
)

// catalogueJSON is a LiteLLM-format catalogue of n chat models fetched-0..n-1
// that cost price USD per input token.
func catalogueJSON(n int, price float64) string {
	out := "{"
	for i := 0; i < n; i++ {
		if i > 0 {
			out += ","
		}
		out += fmt.Sprintf(`"fetched-%d":{"litellm_provider":"openai","mode":"chat","input_cost_per_token":%g,"output_cost_per_token":%g}`, i, price, price)
	}
	return out + "}"
}

type fakeCatalogue struct {
	*httptest.Server
	hits atomic.Int32
	body atomic.Value // string
	code atomic.Int32
}

func newFakeCatalogue(t *testing.T, body string) *fakeCatalogue {
	t.Helper()
	f := &fakeCatalogue{}
	f.body.Store(body)
	f.code.Store(http.StatusOK)
	f.Server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		f.hits.Add(1)
		w.WriteHeader(int(f.code.Load()))
		fmt.Fprint(w, f.body.Load().(string))
	}))
	t.Cleanup(f.Close)
	return f
}

// newTestSyncer syncs a fresh Catalogue from url, checking fetches against a
// bundled copy of bundledEntries entries.
func newTestSyncer(url string, bundledEntries int) (*Syncer, *atomic.Pointer[ModelDB], *Catalogue) {
	var ptr atomic.Pointer[ModelDB]
	c := NewCatalogue(&ptr, nil)
	s := NewSyncer(c, url, time.Hour)
	s.bundledEntries = bundledEntries
	return s, &ptr, c
}

func TestSync_GoodCatalogueReplacesBundled(t *testing.T) {
	f := newFakeCatalogue(t, catalogueJSON(60, 1e-6))
	s, ptr, c := newTestSyncer(f.URL, 100)

	if _, ok := ptr.Load().Get("gpt-4o"); !ok {
		t.Fatal("precondition: bundled gpt-4o missing")
	}
	s.syncOnce(context.Background(), time.Second)

	db := ptr.Load()
	if _, ok := db.Get("fetched-7"); !ok {
		t.Error("fetched model missing after a good sync")
	}
	if _, ok := db.Get("gpt-4o"); ok {
		t.Error("bundled-only model survived; the fetched catalogue should replace the bundled one")
	}
	info := c.Info()
	if info.Source != SourceFetched || info.URL != f.URL || info.Entries != 60 || info.LoadedAt.IsZero() {
		t.Errorf("info = %+v", info)
	}
}

func TestSync_RejectedFetchKeepsLastGood(t *testing.T) {
	cases := []struct {
		name string
		body string
		code int
	}{
		{"not json", "<html>nope</html>", http.StatusOK},
		{"fewer than 50 entries", catalogueJSON(49, 1e-6), http.StatusOK},
		{"under half of bundled", catalogueJSON(60, 1e-6), http.StatusOK},
		{"http error", catalogueJSON(200, 1e-6), http.StatusInternalServerError},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			f := newFakeCatalogue(t, catalogueJSON(150, 1e-6))
			s, ptr, c := newTestSyncer(f.URL, 150)
			s.syncOnce(context.Background(), time.Second)
			good := c.Info()
			if good.Source != SourceFetched {
				t.Fatalf("precondition: first sync did not load: %+v", good)
			}

			f.body.Store(tc.body)
			f.code.Store(int32(tc.code))
			s.syncOnce(context.Background(), time.Second)

			if _, ok := ptr.Load().Get("fetched-149"); !ok {
				t.Error("last good catalogue was dropped")
			}
			info := c.Info()
			if info.Entries != 150 || !info.LoadedAt.Equal(good.LoadedAt) {
				t.Errorf("catalogue changed: %+v", info)
			}
			if info.LastError == "" {
				t.Error("failed fetch left no last_error")
			}
		})
	}
}

func TestSync_FailedFetchFromStartKeepsBundled(t *testing.T) {
	f := newFakeCatalogue(t, "")
	f.Close() // connection refused
	s, ptr, c := newTestSyncer(f.URL, 100)

	s.syncOnce(context.Background(), time.Second)

	if _, ok := ptr.Load().Get("gpt-4o"); !ok {
		t.Error("bundled catalogue lost after an unreachable fetch")
	}
	if info := c.Info(); info.Source != SourceBundled || info.LastError == "" {
		t.Errorf("info = %+v", info)
	}
}

func TestSync_SlowFetchTimesOutAndKeepsBundled(t *testing.T) {
	release := make(chan struct{})
	slow := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		select {
		case <-release:
		case <-r.Context().Done():
		}
	}))
	defer slow.Close()
	defer close(release)
	s, _, c := newTestSyncer(slow.URL, 100)

	start := time.Now()
	s.syncOnce(context.Background(), 50*time.Millisecond)

	if time.Since(start) > 2*time.Second {
		t.Error("fetch ignored its timeout")
	}
	if c.Info().Source != SourceBundled {
		t.Error("a timed-out fetch changed the catalogue")
	}
}

func TestSync_ConfigReloadKeepsFetchedCatalogue(t *testing.T) {
	f := newFakeCatalogue(t, catalogueJSON(60, 1e-6))
	s, ptr, c := newTestSyncer(f.URL, 100)
	s.syncOnce(context.Background(), time.Second)

	c.SetOverrides(nil) // what a config reload does

	db := ptr.Load()
	if _, ok := db.Get("fetched-7"); !ok {
		t.Error("config reload dropped the fetched catalogue")
	}
	if _, ok := db.Get("gpt-4o"); ok {
		t.Error("config reload brought the bundled catalogue back")
	}
	if c.Info().Source != SourceFetched {
		t.Errorf("info = %+v", c.Info())
	}
}

func TestSync_OverridesApplyOverFetched(t *testing.T) {
	f := newFakeCatalogue(t, catalogueJSON(60, 1e-6))
	var ptr atomic.Pointer[ModelDB]
	price := 5e-6
	c := NewCatalogue(&ptr, map[string]ModelOverride{
		"fetched-7": {Pricing: &PricingOverride{InputPerToken: &price}},
	})
	s := NewSyncer(c, f.URL, time.Hour)
	s.bundledEntries = 100

	s.syncOnce(context.Background(), time.Second)

	pricing, ok := ptr.Load().GetPricing("fetched-7")
	if !ok || pricing.InputPerToken != price {
		t.Errorf("override lost over fetched: %+v ok=%v", pricing, ok)
	}
	if other, _ := ptr.Load().GetPricing("fetched-8"); other.InputPerToken != 1e-6 {
		t.Errorf("fetched price not used for a model with no override: %+v", other)
	}

	// A later fetch keeps the overrides too.
	f.body.Store(catalogueJSON(70, 2e-6))
	s.syncOnce(context.Background(), time.Second)
	if pricing, _ := ptr.Load().GetPricing("fetched-7"); pricing.InputPerToken != price {
		t.Errorf("override lost after a second fetch: %+v", pricing)
	}
}

func TestSync_EmptyURLTurnsSyncOff(t *testing.T) {
	f := newFakeCatalogue(t, catalogueJSON(60, 1e-6))
	var ptr atomic.Pointer[ModelDB]
	c := NewCatalogue(&ptr, nil)

	if s := NewSyncer(c, "", time.Hour); s != nil {
		t.Fatal("an empty URL must give no syncer")
	}
	if f.hits.Load() != 0 {
		t.Errorf("fetched %d times with the sync off", f.hits.Load())
	}
	if c.Info().Source != SourceBundled {
		t.Errorf("info = %+v", c.Info())
	}
}

func TestSync_RunFetchesAtStartup(t *testing.T) {
	f := newFakeCatalogue(t, catalogueJSON(60, 1e-6))
	s, _, c := newTestSyncer(f.URL, 100)
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan struct{})
	go func() { s.Run(ctx); close(done) }()

	deadline := time.Now().Add(2 * time.Second)
	for c.Info().Source != SourceFetched && time.Now().Before(deadline) {
		time.Sleep(10 * time.Millisecond)
	}
	cancel()
	<-done

	if c.Info().Source != SourceFetched {
		t.Error("Run did not fetch at startup")
	}
}

func TestNewSyncer_Interval(t *testing.T) {
	var ptr atomic.Pointer[ModelDB]
	c := NewCatalogue(&ptr, nil)
	for _, tc := range []struct{ in, want time.Duration }{
		{0, DefaultSyncInterval},
		{time.Minute, MinSyncInterval},
		{6 * time.Hour, 6 * time.Hour},
	} {
		if got := NewSyncer(c, "http://x", tc.in).interval; got != tc.want {
			t.Errorf("interval(%v) = %v, want %v", tc.in, got, tc.want)
		}
	}
}
