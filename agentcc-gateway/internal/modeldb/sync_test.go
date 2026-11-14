package modeldb

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
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

func TestSync_GoodCatalogueOverlaysBundled(t *testing.T) {
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
	if _, ok := db.Get("gpt-4o"); !ok {
		t.Error("bundled-only model dropped; a fetch must not remove bundled models")
	}
	info := c.Info()
	if info.Source != SourceFetched || info.URL != f.URL || info.Fetched != 60 ||
		info.Entries != len(BundledModels)+60 || info.LoadedAt.IsZero() {
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
		{"wrong schema: objects with no price fields", wrongSchemaJSON(500), http.StatusOK},
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
			if info.Fetched != 150 || !info.LoadedAt.Equal(good.LoadedAt) {
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
	if _, ok := db.Get("gpt-4o"); !ok {
		t.Error("config reload dropped the bundled models")
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

// eventually waits up to two seconds for cond.
func eventually(t *testing.T, what string, cond func() bool) {
	t.Helper()
	deadline := time.Now().Add(2 * time.Second)
	for !cond() {
		if time.Now().After(deadline) {
			t.Fatalf("timed out waiting for %s", what)
		}
		time.Sleep(5 * time.Millisecond)
	}
}

// Run fetches again on every interval: a price changed after startup reaches
// the live catalogue without a restart.
func TestSync_RunPublishesAScheduledRefresh(t *testing.T) {
	f := newFakeCatalogue(t, catalogueJSON(60, 1e-6))
	s, ptr, _ := newTestSyncer(f.URL, 100)
	s.interval = 20 * time.Millisecond
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan struct{})
	go func() { s.Run(ctx); close(done) }()
	defer func() { cancel(); <-done }()

	price := func() float64 {
		p, ok := ptr.Load().GetPricing("fetched-7")
		if !ok {
			return 0
		}
		return p.InputPerToken
	}
	eventually(t, "the startup fetch", func() bool { return price() == 1e-6 })
	f.body.Store(catalogueJSON(60, 3e-6))
	eventually(t, "a scheduled refresh to publish the new price", func() bool { return price() == 3e-6 })
	if hits := f.hits.Load(); hits < 2 {
		t.Errorf("catalogue fetched %d times, want a startup fetch and a scheduled one", hits)
	}
}

// A fetch and a config reload can land at once. Both rebuild under the
// Catalogue's lock, so the result holds the fetched models and the overrides,
// and a reader always sees a whole catalogue.
func TestCatalogue_ConcurrentOverlayAndSetOverrides(t *testing.T) {
	var ptr atomic.Pointer[ModelDB]
	c := NewCatalogue(&ptr, nil)
	fetched, err := parseLiteLLM([]byte(catalogueJSON(60, 1e-6)), true)
	if err != nil {
		t.Fatal(err)
	}
	price := 5e-6
	overrides := map[string]ModelOverride{"fetched-7": {Pricing: &PricingOverride{InputPerToken: &price}}}

	start, stop := make(chan struct{}), make(chan struct{})
	var readers, writers sync.WaitGroup
	for i := 0; i < 4; i++ {
		readers.Add(1)
		go func() {
			defer readers.Done()
			<-start
			for {
				select {
				case <-stop:
					return
				default:
				}
				if _, ok := ptr.Load().Get("gpt-4o"); !ok {
					t.Error("a reader saw a catalogue without the bundled gpt-4o")
					return
				}
			}
		}()
	}
	for i := 0; i < 50; i++ {
		writers.Add(2)
		go func() { defer writers.Done(); <-start; c.Overlay(fetched, "http://catalogue.test") }()
		go func() { defer writers.Done(); <-start; c.SetOverrides(overrides) }()
	}
	close(start)
	writers.Wait()
	close(stop)
	readers.Wait()

	db := ptr.Load()
	if p, ok := db.GetPricing("fetched-7"); !ok || p.InputPerToken != price {
		t.Errorf("fetched-7 = %+v (ok %v), want the override's %v", p, ok, price)
	}
	if p, ok := db.GetPricing("fetched-8"); !ok || p.InputPerToken != 1e-6 {
		t.Errorf("fetched-8 = %+v (ok %v), want the fetched 1e-6", p, ok)
	}
	if _, ok := db.Get("gpt-4o"); !ok {
		t.Error("bundled gpt-4o missing")
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

func wrongSchemaJSON(n int) string {
	out := "{"
	for i := 0; i < n; i++ {
		if i > 0 {
			out += ","
		}
		out += fmt.Sprintf(`"fetched-%d":{"description":"wrong schema: no price fields"}`, i)
	}
	return out + "}"
}

// An older but valid snapshot lacks models the binary ships. The fetch must
// not remove them, and a matching key takes the fetched price.
func TestSync_OlderSnapshotKeepsBundledOnlyModels(t *testing.T) {
	var bundledOnly string
	for id := range BundledModels {
		bundledOnly = id
		break
	}
	body := catalogueJSON(60, 1e-6)
	body = body[:len(body)-1] + fmt.Sprintf(`,%q:{"litellm_provider":"openai","mode":"chat","input_cost_per_token":7e-6,"output_cost_per_token":7e-6}}`, "gpt-4o")
	f := newFakeCatalogue(t, body)
	s, ptr, c := newTestSyncer(f.URL, 100)
	s.syncOnce(context.Background(), time.Second)

	for step := 0; step < 2; step++ { // second pass: after a config reload
		db := ptr.Load()
		if bundledOnly != "gpt-4o" {
			if _, ok := db.Get(bundledOnly); !ok {
				t.Errorf("step %d: bundled-only %q was dropped", step, bundledOnly)
			}
		}
		if p, ok := db.GetPricing("gpt-4o"); !ok || p.InputPerToken != 7e-6 {
			t.Errorf("step %d: fetched price did not win on a matching key: %+v", step, p)
		}
		c.SetOverrides(nil)
	}
}

// A partly wrong download adds its usable entries and leaves the rest of the
// catalogue alone: the objects with no prices must not zero a priced model.
func TestSync_UnpricedObjectsDoNotOverwriteBundledPrices(t *testing.T) {
	body := catalogueJSON(60, 1e-6)
	body = body[:len(body)-1] + `,"gpt-4o":{"description":"no price fields"}}`
	f := newFakeCatalogue(t, body)
	s, ptr, _ := newTestSyncer(f.URL, 100)
	want, _ := ptr.Load().GetPricing("gpt-4o")
	s.syncOnce(context.Background(), time.Second)

	got, ok := ptr.Load().GetPricing("gpt-4o")
	if !ok || got.InputPerToken != want.InputPerToken || got.InputPerToken == 0 {
		t.Errorf("gpt-4o price = %+v, want the bundled %+v", got, want)
	}
}

// One negative rate drops the entry, whichever rate it is: the bundled price
// stays and a catalogue of such entries is rejected.
func TestSync_NegativeRateDropsTheEntry(t *testing.T) {
	cases := map[string]string{
		"negative input, non-negative output": `{"litellm_provider":"openai","mode":"chat","input_cost_per_token":-0.00001,"output_cost_per_token":0.000001}`,
		"non-negative input, negative output": `{"litellm_provider":"openai","mode":"chat","input_cost_per_token":0.000001,"output_cost_per_token":-0.00001}`,
		"negative cache rate":                 `{"litellm_provider":"openai","mode":"chat","input_cost_per_token":0.000001,"output_cost_per_token":0.000001,"cache_read_input_token_cost":-0.00001}`,
		"negative non-token rate":             `{"litellm_provider":"openai","mode":"image_generation","output_cost_per_image":-0.04}`,
	}
	for name, entry := range cases {
		t.Run(name, func(t *testing.T) {
			body := catalogueJSON(60, 1e-6)
			body = body[:len(body)-1] + fmt.Sprintf(`,"gpt-4o":%s}`, entry)
			f := newFakeCatalogue(t, body)
			s, ptr, _ := newTestSyncer(f.URL, 100)
			want, _ := ptr.Load().GetPricing("gpt-4o")

			s.syncOnce(context.Background(), time.Second)

			if _, ok := ptr.Load().Get("fetched-7"); !ok {
				t.Fatal("the good entries were not loaded")
			}
			got, _ := ptr.Load().GetPricing("gpt-4o")
			if *got != *want || got.InputPerToken < 0 || got.OutputPerToken < 0 {
				t.Errorf("gpt-4o pricing = %+v, want the bundled %+v", got, want)
			}
		})
	}
}

func TestSync_AllNegativeCatalogueRejected(t *testing.T) {
	out := "{"
	for i := 0; i < 500; i++ {
		if i > 0 {
			out += ","
		}
		out += fmt.Sprintf(`"fetched-%d":{"litellm_provider":"openai","mode":"chat","input_cost_per_token":-0.00001,"output_cost_per_token":0.000001}`, i)
	}
	f := newFakeCatalogue(t, out+"}")
	s, _, c := newTestSyncer(f.URL, 100)

	s.syncOnce(context.Background(), time.Second)

	if info := c.Info(); info.Source != SourceBundled || info.LastError == "" {
		t.Errorf("info = %+v, want bundled with a last_error", info)
	}
}

// An explicit 0 is a free price, and an absent optional rate is fine.
func TestSync_ExplicitFreeAndAbsentOptionalRatesAccepted(t *testing.T) {
	body := catalogueJSON(60, 1e-6)
	body = body[:len(body)-1] + `,"free-image":{"litellm_provider":"openai","mode":"image_generation","output_cost_per_image":0}}`
	f := newFakeCatalogue(t, body)
	s, ptr, _ := newTestSyncer(f.URL, 100)

	s.syncOnce(context.Background(), time.Second)

	if _, ok := ptr.Load().Get("free-image"); !ok {
		t.Error("an explicit free price was dropped")
	}
	if _, ok := ptr.Load().Get("fetched-7"); !ok {
		t.Error("an entry with no optional rates was dropped")
	}
}

// bundledFileWithout is the bundled catalogue file with the given keys removed.
func bundledFileWithout(t *testing.T, keys ...string) string {
	t.Helper()
	var raw map[string]json.RawMessage
	if err := json.Unmarshal(liteLLMData, &raw); err != nil {
		t.Fatal(err)
	}
	for _, k := range keys {
		if _, ok := BundledModels[k]; !ok {
			t.Fatalf("precondition: %q is not bundled", k)
		}
		delete(raw, k)
	}
	b, err := json.Marshal(raw)
	if err != nil {
		t.Fatal(err)
	}
	return string(b)
}

// The bundled file without the seven models it gained since the previous
// snapshot is a valid, older catalogue. Synced through the real entry floor,
// it must keep those models and the decision models, also after a config reload.
func TestSync_OlderBundledFileKeepsModelsAddedSince(t *testing.T) {
	added := []string{
		"perplexity/deepseek/deepseek-v4-flash-0731",
		"perplexity/google/gemini-4-argon",
		"perplexity/moonshot-ai/kimi-k2.7-code",
		"perplexity/moonshot-ai/kimi-k3",
		"perplexity/nvidia/nemotron-3-super-120b-a12b",
		"perplexity/perplexity/nemotron-3.5-super-120b-a12b",
		"vertex_ai/gemini-embedding-001",
	}
	f := newFakeCatalogue(t, bundledFileWithout(t, added...))
	var ptr atomic.Pointer[ModelDB]
	c := NewCatalogue(&ptr, nil)

	NewSyncer(c, f.URL, time.Hour).syncOnce(context.Background(), 5*time.Second)

	if info := c.Info(); info.Source != SourceFetched || info.Entries != len(BundledModels) {
		t.Fatalf("info = %+v, want fetched with all %d bundled entries", info, len(BundledModels))
	}
	for step := 0; step < 2; step++ { // second pass: after a config reload
		for _, id := range added {
			if m, ok := ptr.Load().Get(id); !ok || m.ID != id {
				t.Errorf("step %d: %q was dropped", step, id)
			}
		}
		if _, ok := ptr.Load().DecisionPricing("jev-1.13.0"); !ok {
			t.Errorf("step %d: a decision model was dropped", step)
		}
		c.SetOverrides(nil)
	}
}

// An entry that states no rate the gateway reads cannot be priced from, in any
// mode. In a good download it must not zero a bundled price, and a download of
// only such entries is rejected however many there are.
func TestSync_EntriesWithNoRateDoNotCount(t *testing.T) {
	const rateless = `{"litellm_provider":"openai","mode":"image_generation","description":"wrong schema: no price fields"}`

	t.Run("in a good catalogue", func(t *testing.T) {
		body := catalogueJSON(60, 1e-6)
		body = body[:len(body)-1] + `,"dall-e-3":` + rateless + `}`
		f := newFakeCatalogue(t, body)
		s, ptr, _ := newTestSyncer(f.URL, 100)
		want, _ := ptr.Load().GetPricing("dall-e-3")

		s.syncOnce(context.Background(), time.Second)

		got, _ := ptr.Load().GetPricing("dall-e-3")
		if got.InputPerImage != want.InputPerImage || got.InputPerImage == 0 {
			t.Errorf("dall-e-3 pricing = %+v, want the bundled %+v", got, want)
		}
	})

	t.Run("as every bundled key", func(t *testing.T) {
		wrong := make(map[string]json.RawMessage, len(BundledModels))
		for id := range BundledModels {
			wrong[id] = json.RawMessage(rateless)
		}
		body, err := json.Marshal(wrong)
		if err != nil {
			t.Fatal(err)
		}
		f := newFakeCatalogue(t, string(body))
		var ptr atomic.Pointer[ModelDB]
		c := NewCatalogue(&ptr, nil)

		NewSyncer(c, f.URL, time.Hour).syncOnce(context.Background(), 5*time.Second)

		if info := c.Info(); info.Source != SourceBundled || info.LastError == "" {
			t.Errorf("info = %+v, want bundled with a last_error", info)
		}
		if p, _ := ptr.Load().GetPricing("dall-e-3"); p.InputPerImage == 0 {
			t.Errorf("dall-e-3 pricing = %+v, want the bundled price", p)
		}
	})
}

// credentialURL rewrites a test server URL to carry a password and a token in
// the query string, the two places a catalogue URL holds a credential.
func credentialURL(serverURL string) string {
	return "http://bob:hunter2@" + strings.TrimPrefix(serverURL, "http://") + "/catalogue.json?token=s3cret"
}

// captureLogs sends the default logger to a buffer for one test.
func captureLogs(t *testing.T) *bytes.Buffer {
	t.Helper()
	var buf bytes.Buffer
	prev := slog.Default()
	slog.SetDefault(slog.New(slog.NewTextHandler(&buf, nil)))
	t.Cleanup(func() { slog.SetDefault(prev) })
	return &buf
}

func assertNoCredential(t *testing.T, where, s string) {
	t.Helper()
	for _, secret := range []string{"hunter2", "s3cret"} {
		if strings.Contains(s, secret) {
			t.Errorf("%s carries the credential %q: %s", where, secret, s)
		}
	}
}

func TestSync_CredentialsNeverReachLogsOrInfo(t *testing.T) {
	t.Run("good fetch", func(t *testing.T) {
		logs := captureLogs(t)
		f := newFakeCatalogue(t, catalogueJSON(60, 1e-6))
		s, _, c := newTestSyncer(credentialURL(f.URL), 100)

		s.syncOnce(context.Background(), time.Second)

		info := c.Info()
		if info.Source != SourceFetched {
			t.Fatalf("precondition: fetch did not load: %+v", info)
		}
		assertNoCredential(t, "info.url", info.URL)
		assertNoCredential(t, "log", logs.String())
	})
	t.Run("unreachable fetch", func(t *testing.T) {
		logs := captureLogs(t)
		f := newFakeCatalogue(t, "")
		f.Close() // connection refused: the error carries the URL
		s, _, c := newTestSyncer(credentialURL(f.URL), 100)

		s.syncOnce(context.Background(), time.Second)

		info := c.Info()
		if info.LastError == "" {
			t.Fatal("precondition: the fetch did not fail")
		}
		assertNoCredential(t, "info.last_error", info.LastError)
		assertNoCredential(t, "log", logs.String())
	})
}

// "At least half" of an odd count rounds up: 61 of 121, not 60.
func TestSync_HalfOfBundledRoundsUp(t *testing.T) {
	f := newFakeCatalogue(t, catalogueJSON(60, 1e-6))
	s, _, c := newTestSyncer(f.URL, 121)

	s.syncOnce(context.Background(), time.Second)

	if info := c.Info(); info.Source != SourceBundled || info.LastError == "" {
		t.Errorf("a fetch of 60 against 121 bundled was accepted: %+v", info)
	}
}
