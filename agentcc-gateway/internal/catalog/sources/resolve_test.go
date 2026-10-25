package sources

import (
	"context"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/catalog"
	"github.com/futureagi/agentcc-gateway/internal/config"
)

func intPtr(v int) *int { return &v }

// writeCard writes the fixture card for provider id into dir, as of asOf.
func writeCard(t *testing.T, dir, provider, asOf string) string {
	t.Helper()
	body := strings.Replace(string(fixture(t, "pricecard_direct.json")), `"anthropic-direct"`, `"`+provider+`"`, 1)
	body = strings.Replace(body, `"as_of": "2026-10-01"`, `"as_of": "`+asOf+`"`, 1)
	path := filepath.Join(dir, provider+".json")
	if err := os.WriteFile(path, []byte(body), 0o600); err != nil {
		t.Fatal(err)
	}
	return path
}

type clock struct {
	mu sync.Mutex
	t  time.Time
}

func (c *clock) Now() time.Time { c.mu.Lock(); defer c.mu.Unlock(); return c.t }
func (c *clock) Advance(d time.Duration) {
	c.mu.Lock()
	c.t = c.t.Add(d)
	c.mu.Unlock()
}

// TestResolveAttachesSources: each managed provider's catalog_source picks
// its reader, and a price-card source's refresh re-reads the card on the
// store's clock, never fetching the provider's URL.
func TestResolveAttachesSources(t *testing.T) {
	var fetched atomic.Int32
	up := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		fetched.Add(1)
		w.Write(fixture(t, "bare_array.json"))
	}))
	t.Cleanup(up.Close)

	dir := t.TempDir()
	cardPath := writeCard(t, dir, "direct", "2026-07-20")
	cfg := config.DefaultConfig()
	cfg.Providers = map[string]config.ProviderConfig{
		"direct":   {Managed: true, CatalogSource: catalog.SourcePriceCard, PriceCardFile: cardPath, UpstreamFeeBps: intPtr(0)},
		"together": {Managed: true, Type: "together", CatalogSource: catalog.SourceOpenAICompatible},
		"or":       {Managed: true, Type: "openrouter"},
	}
	in := []catalog.Source{
		{ProviderID: "direct", URL: up.URL + "/v1/models"},
		{ProviderID: "together", Type: "together", URL: up.URL + "/v1/models"},
		{ProviderID: "or", Type: "openrouter", URL: up.URL + "/api/v1/models"},
	}
	got, err := Resolve(cfg, in)
	if err != nil {
		t.Fatal(err)
	}
	byID := map[string]catalog.Source{}
	for _, s := range got {
		byID[s.ProviderID] = s
	}
	if d := byID["direct"]; d.Kind() != catalog.SourcePriceCard || d.Load == nil || d.Parse != nil || d.UpstreamSlug != "anthropic" {
		t.Errorf("direct = %+v", d)
	}
	if tg := byID["together"]; tg.Kind() != catalog.SourceOpenAICompatible || tg.Parse == nil || tg.Load != nil || tg.UpstreamSlug != "together" {
		t.Errorf("together = %+v", tg)
	}
	if or := byID["or"]; or.Kind() != catalog.SourceOpenRouter || or.Parse != nil || or.Load != nil {
		t.Errorf("openrouter = %+v", or)
	}

	// The card is seeded at boot as of the store's clock: 88 days old, fresh.
	clk := &clock{t: time.Date(2026, 10, 16, 0, 0, 0, 0, time.UTC)}
	store := catalog.NewStore()
	opts := catalog.OptionsFromConfig(cfg, got)
	opts.Now = clk.Now
	if err := store.Configure(opts); err != nil {
		t.Fatal(err)
	}
	catalog.SeedStatic(store, got)
	snap := store.Load().Snapshots()[0]
	if snap.ProviderID != "direct" || snap.Source != catalog.OriginPriceCard || snap.CatalogSource != catalog.SourcePriceCard {
		t.Fatalf("seeded = %+v", snap)
	}
	if !store.Load().Has("direct", "claude-3-5-sonnet-20241022") || len(store.History(0)) != 0 {
		t.Fatalf("seed: has %v, history %d (the seed is not a refresh)",
			store.Load().Has("direct", "claude-3-5-sonnet-20241022"), len(store.History(0)))
	}

	// Three days on the card is 91 days old: the next refresh re-reads it and
	// its models drop out, recorded as a new version. The URL is never read.
	ctx, cancel := context.WithCancel(context.Background())
	t.Cleanup(cancel)
	r := catalog.NewRefresher(ctx, store, got, nil)
	clk.Advance(3 * 24 * time.Hour)
	r.RefreshModels("direct")
	if store.Load().Has("direct", "claude-3-5-sonnet-20241022") {
		t.Error("the refresh did not re-evaluate the card's age")
	}
	if m, _ := store.Load().Get("direct", "claude-3-5-sonnet-20241022"); m == nil || !m.StalePrice {
		t.Errorf("after the refresh = %+v", m)
	}
	if h := store.History(0); len(h) != 1 || h[0].Source != catalog.OriginPriceCard {
		t.Errorf("history = %+v", h)
	}
	if n := fetched.Load(); n != 0 {
		t.Fatalf("a price-card refresh fetched the provider URL %d times", n)
	}

	// The generic source refreshes through its parser.
	r.RefreshModels("together")
	if n := fetched.Load(); n != 1 {
		t.Fatalf("together fetched %d times", n)
	}
	if m, ok := store.Load().Get("together", "meta-llama/Llama-3.3-70B-Instruct-Turbo"); !ok || !m.Unpriced ||
		m.Canonical != "meta-llama/llama-3.3-70b-instruct" {
		t.Errorf("together model = %+v, %v", m, ok)
	}

	// A card that becomes unreadable keeps the previous snapshot.
	before := store.Load().ID()
	if err := os.WriteFile(cardPath, []byte("{"), 0o600); err != nil {
		t.Fatal(err)
	}
	r.RefreshModels("direct")
	if store.Load().ID() != before {
		t.Error("an unreadable card replaced the catalog")
	}
}

func TestResolveRefusesInconsistentCards(t *testing.T) {
	dir := t.TempDir()
	cardPath := writeCard(t, dir, "direct", "2026-10-01")
	base := func() *config.Config {
		cfg := config.DefaultConfig()
		cfg.Providers = map[string]config.ProviderConfig{
			"direct": {Managed: true, CatalogSource: catalog.SourcePriceCard, PriceCardFile: cardPath},
		}
		return cfg
	}
	src := []catalog.Source{{ProviderID: "direct"}}

	// upstream_fee_bps unset is 0 for a price card, as the card says.
	if _, err := Resolve(base(), src); err != nil {
		t.Fatalf("a consistent card: %v", err)
	}
	// The cost plugin reads the config's fee and endpoint ranking reads the endpoint's:
	// the two must agree, or landed cost and source choice disagree.
	cfg := base()
	pc := cfg.Providers["direct"]
	pc.UpstreamFeeBps = intPtr(300)
	cfg.Providers["direct"] = pc
	if _, err := Resolve(cfg, src); err == nil || !strings.Contains(err.Error(), "upstream_fee_bps") {
		t.Errorf("a fee mismatch = %v", err)
	}
	// A card that does not load fails startup.
	cfg = base()
	pc = cfg.Providers["direct"]
	pc.PriceCardFile = filepath.Join(dir, "missing.json")
	cfg.Providers["direct"] = pc
	if _, err := Resolve(cfg, src); err == nil {
		t.Error("a missing card file resolved")
	}
	// The config's upstream_slug must name the card's upstream.
	cfg = base()
	pc = cfg.Providers["direct"]
	pc.UpstreamSlug = "someone-else"
	cfg.Providers["direct"] = pc
	if _, err := Resolve(cfg, src); err == nil || !strings.Contains(err.Error(), "upstream_slug") {
		t.Errorf("an upstream_slug mismatch = %v", err)
	}
}
