package catalog

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// --- shared test helpers ------------------------------------------------

// fakeClock is a clock tests move by hand; Sleep advances it.
type fakeClock struct {
	mu sync.Mutex
	t  time.Time
}

func newFakeClock() *fakeClock { return &fakeClock{t: time.Date(2026, 10, 15, 12, 0, 0, 0, time.UTC)} }

func (c *fakeClock) Now() time.Time {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.t
}

func (c *fakeClock) Advance(d time.Duration) {
	c.mu.Lock()
	c.t = c.t.Add(d)
	c.mu.Unlock()
}

func (c *fakeClock) Sleep(ctx context.Context, d time.Duration) error {
	c.Advance(d)
	return ctx.Err()
}

// mustConfigure configures s with opts, failing the test on an error.
func mustConfigure(t *testing.T, s *Store, opts Options) {
	t.Helper()
	if err := s.Configure(opts); err != nil {
		t.Fatal(err)
	}
}

// mustBlock sets s's runtime blocks, failing the test on an error.
func mustBlock(t *testing.T, s *Store, models []string, reason string) {
	t.Helper()
	if _, err := s.SetBlocks(models, reason); err != nil {
		t.Fatal(err)
	}
}

func fixture(t *testing.T, name string) []byte {
	t.Helper()
	b, err := os.ReadFile(filepath.Join("testdata", name))
	if err != nil {
		t.Fatal(err)
	}
	return b
}

func fixtureModels(t *testing.T) map[string]*Model {
	t.Helper()
	m, err := ParseOpenRouter(fixture(t, "models.json"))
	if err != nil {
		t.Fatal(err)
	}
	return m
}

// fakeUpstream serves an OpenRouter-shaped catalog under /api/v1 and
// records what it was asked for, on the clock when one is set.
type fakeUpstream struct {
	*httptest.Server
	mu        sync.Mutex
	models    []byte
	endpoints map[string][]byte // model id -> endpoints body
	zdr       []byte
	fail      atomic.Bool
	clock     *fakeClock
	requests  []upstreamRequest
}

type upstreamRequest struct {
	path string
	at   time.Time
}

func newFakeUpstream(t *testing.T, models []byte) *fakeUpstream {
	u := &fakeUpstream{models: models, endpoints: map[string][]byte{}}
	u.Server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		u.mu.Lock()
		at := time.Now()
		if u.clock != nil {
			at = u.clock.Now()
		}
		u.requests = append(u.requests, upstreamRequest{r.URL.Path, at})
		var body []byte
		switch {
		case r.URL.Path == "/api/v1/models":
			body = u.models
		case r.URL.Path == "/api/v1/endpoints/zdr":
			body = u.zdr
		case strings.HasPrefix(r.URL.Path, "/api/v1/models/") && strings.HasSuffix(r.URL.Path, "/endpoints"):
			body = u.endpoints[strings.TrimSuffix(strings.TrimPrefix(r.URL.Path, "/api/v1/models/"), "/endpoints")]
		}
		u.mu.Unlock()
		if u.fail.Load() || body == nil {
			http.Error(w, "down", http.StatusBadGateway)
			return
		}
		w.Write(body)
	}))
	t.Cleanup(u.Close)
	return u
}

func (u *fakeUpstream) source(id string) Source {
	return Source{ProviderID: id, Type: SourceOpenRouter, URL: u.URL + "/api/v1/models"}
}

func (u *fakeUpstream) set(f func(u *fakeUpstream)) {
	u.mu.Lock()
	defer u.mu.Unlock()
	f(u)
}

func (u *fakeUpstream) paths(prefix string) []upstreamRequest {
	u.mu.Lock()
	defer u.mu.Unlock()
	var out []upstreamRequest
	for _, r := range u.requests {
		if strings.HasPrefix(r.path, prefix) {
			out = append(out, r)
		}
	}
	return out
}

// --- parsing ----------------------------------------------------------------

func TestParseOpenRouterFullPriceCard(t *testing.T) {
	models := fixtureModels(t)
	if len(models) != 5 {
		t.Fatalf("got %d models, want 5", len(models))
	}
	m := models["anthropic/claude-sonnet-5.5"]
	want := Prices{Prompt: 3e-6, Completion: 15e-6, CacheRead: 0.3e-6, CacheWrite: 3.75e-6,
		Request: 0, Image: 0.0048, WebSearch: 0.01, Reasoning: 15e-6}
	if m.Prices != want {
		t.Errorf("prices = %+v, want %+v", m.Prices, want)
	}
	if m.InputPerToken != want.Prompt || m.OutputPerToken != want.Completion || m.CacheReadPerToken != want.CacheRead {
		t.Errorf("per-token fields %v/%v/%v do not mirror the card", m.InputPerToken, m.OutputPerToken, m.CacheReadPerToken)
	}
	if m.MaxOutputTokens != 64000 || m.ContextLength != 1000000 || m.CanonicalSlug != "anthropic/claude-sonnet-5.5-20260901" {
		t.Errorf("limits or slug wrong: %+v", m)
	}
	if m.Tokenizer != "Claude" || m.Modality != "text+image->text" || !m.Accepts("image") || !m.Supports("tools") {
		t.Errorf("architecture wrong: %+v", m)
	}
	var compact bytes.Buffer
	if err := json.Compact(&compact, m.Benchmarks); err != nil || compact.String() != `{"design_arena":[{"arena":"models","category":"3d","elo":1390}]}` {
		t.Errorf("benchmarks not kept verbatim: %s", m.Benchmarks)
	}
}

func TestParsePricingDynamicAndTiers(t *testing.T) {
	models := fixtureModels(t)

	tiers := models["anthropic/claude-sonnet-5.5"].PriceTiers
	if len(tiers) != 1 || tiers[0].MinPromptTokens != 200000 {
		t.Fatalf("tiers = %+v, want one from 200000 prompt tokens", tiers)
	}
	if p := tiers[0].Prices; p.Prompt != 6e-6 || p.Completion != 22.5e-6 || p.CacheWrite != 3.75e-6 {
		t.Errorf("a tier must overlay the lines it lists on the base card, got %+v", p)
	}

	router := models["openrouter/auto"]
	if !router.DynamicPrice || router.Priced() || router.Prices != (Prices{}) || router.InputPerToken != 0 {
		t.Errorf("a negative price must mark a dynamic, unpriced model: %+v", router)
	}
	store := NewStore()
	store.Put(&Snapshot{ProviderID: "or", Source: OriginLive, Models: models})
	if _, ok := store.PriceCard("or", "openrouter/auto", ""); ok {
		t.Error("a dynamic-price model must have no price card")
	}

	// A negative line in a tier also makes the whole card dynamic.
	if _, _, dyn := parsePricing(map[string]any{"prompt": "0.000001", "completion": "0.000002",
		"overrides": []any{map[string]any{"min_prompt_tokens": 1000.0, "prompt": "-1"}}}); !dyn {
		t.Error("a negative tier price must make the card dynamic")
	}

	free := models["qwen/qwen3.7-flash:free"]
	if free.DynamicPrice || !free.Priced() {
		t.Errorf("a zero price is a free model, priced at zero: %+v", free)
	}
	if card, ok := store.PriceCard("or", "qwen/qwen3.7-flash:free", ""); !ok || card.Prompt != 0 {
		t.Errorf("free model card = %+v %v, want a zero card", card, ok)
	}
}

func TestParseOpenRouterRejectsEmpty(t *testing.T) {
	if _, err := ParseOpenRouter([]byte(`{"data":[]}`)); err == nil {
		t.Fatal("an empty catalog must be an error, or a bad fetch would wipe the snapshot")
	}
}

func TestBundledSnapshotParses(t *testing.T) {
	models, fetchedAt, err := BundledOpenRouter()
	if err != nil {
		t.Fatal(err)
	}
	if len(models) < 100 {
		t.Fatalf("bundled snapshot has only %d models", len(models))
	}
	if fetchedAt.IsZero() {
		t.Fatal("the bundled snapshot must say when it was fetched, or its age is unknowable")
	}
	full := 0
	for _, m := range models {
		if m.Prices.CacheWrite > 0 || m.Prices.WebSearch > 0 || m.Prices.Reasoning > 0 {
			full++
		}
	}
	if full == 0 {
		t.Error("no bundled model carries a cache-write, web-search or reasoning price: the snapshot lacks the full card")
	}
}

func TestModelInfosKeyByProviderAndSkipUnpriced(t *testing.T) {
	store := NewStore()
	store.Put(&Snapshot{ProviderID: "openrouter", Models: fixtureModels(t)})
	store.Put(&Snapshot{ProviderID: "plain", Models: map[string]*Model{"listed-only": {ID: "listed-only", Unpriced: true}}})

	infos := store.Load().ModelInfos()
	info, ok := infos["openrouter/anthropic/claude-sonnet-5.5"]
	if !ok {
		t.Fatalf("missing provider-qualified entry; have %v", keys(infos))
	}
	if !info.Capabilities.FunctionCalling || !info.Capabilities.Vision || !info.Capabilities.ResponseSchema ||
		!info.Capabilities.Reasoning || !info.Capabilities.PromptCaching {
		t.Errorf("capabilities = %+v", info.Capabilities)
	}
	if info.MaxInputTokens != 1000000-64000 {
		t.Errorf("max input = %d", info.MaxInputTokens)
	}
	if info.Pricing.CachedInputPerToken != 0.3e-6 || info.Pricing.InputPerImage != 0.0048 {
		t.Errorf("model database pricing = %+v, want the cache-read and image prices", info.Pricing)
	}
	if _, ok := infos["openrouter/openrouter/auto"]; ok {
		t.Error("a dynamic-price model must not get a price entry")
	}
	if _, ok := infos["plain/listed-only"]; ok {
		t.Error("a model whose source lists no price must not get a price entry")
	}
	if _, ok := infos["openrouter/qwen/qwen3.7-flash:free"]; !ok {
		t.Error("a free model is still a priced (at zero) model")
	}
}

func TestPerTokenFieldsFillThePriceCard(t *testing.T) {
	store := NewStore()
	store.Put(&Snapshot{ProviderID: "openrouter", Models: map[string]*Model{
		"vendor/m": {ID: "vendor/m", InputPerToken: 1e-6, OutputPerToken: 2e-6},
	}})
	card, ok := store.PriceCard("openrouter", "vendor/m", "")
	if !ok || card.Prompt != 1e-6 || card.Completion != 2e-6 {
		t.Fatalf("card = %+v %v, want the per-token prices", card, ok)
	}
}

// --- price lookup ---------------------------------------------------------

func TestPriceCardCatalogFirstWithFallback(t *testing.T) {
	store := NewStore()
	snap := &Snapshot{ProviderID: "openrouter", Models: fixtureModels(t)}
	eps, err := ParseOpenRouterEndpoints("anthropic/claude-sonnet-5.5", fixture(t, "endpoints_claude.json"))
	if err != nil {
		t.Fatal(err)
	}
	snap.Models["anthropic/claude-sonnet-5.5"].Endpoints = eps
	store.Put(snap)

	var fellBack []string
	lookup := store.PriceLookup(func(providerID, model, endpointID string) (models.PriceCard, bool) {
		fellBack = append(fellBack, providerID+"|"+model)
		return models.PriceCard{Prompt: 9, Source: models.PriceSourceModelDB}, true
	})

	card, ok := lookup("openrouter", "anthropic/claude-sonnet-5.5", "")
	if !ok || card.Source != models.PriceSourceCatalog || card.SnapshotID != store.Load().ID() ||
		card.CacheWrite != 3.75e-6 || card.WebSearch != 0.01 || card.Reasoning != 15e-6 || card.Image != 0.0048 {
		t.Fatalf("model card = %+v %v, want the catalog's full card with its snapshot id", card, ok)
	}
	vertex := EndpointID(SourceOpenRouter, "google-vertex/europe", "anthropic/claude-sonnet-5.5")
	if card, ok := lookup("openrouter", "anthropic/claude-sonnet-5.5", vertex); !ok || card.Prompt != 3.3e-6 {
		t.Fatalf("endpoint card = %+v %v, want the Vertex endpoint's price", card, ok)
	}
	if card, ok := lookup("openrouter", "anthropic/claude-sonnet-5.5", "openrouter:gone:anthropic/claude-sonnet-5.5"); !ok || card.Prompt != 3e-6 {
		t.Fatalf("an endpoint the catalog does not list gets the model's card, got %+v %v", card, ok)
	}
	if _, ok := store.PriceCard("openrouter", "anthropic/claude-sonnet-5.5", EndpointID(SourceOpenRouter, "router", "anthropic/claude-sonnet-5.5")); ok {
		t.Fatal("a dynamic-price endpoint must not get a catalog card")
	}
	fellBack = nil
	if card, _ := lookup("operator", "gpt-4o", ""); card.Source != models.PriceSourceModelDB || len(fellBack) != 1 {
		t.Fatalf("a model the catalog does not list must fall back to the model database, got %+v (%v)", card, fellBack)
	}
	fellBack = nil
	if card, _ := lookup("openrouter", "openrouter/auto", ""); card.Source != models.PriceSourceModelDB || len(fellBack) != 1 {
		t.Fatalf("a dynamic-price model must fall back, got %+v", card)
	}
	if _, ok := store.PriceLookup(nil)("operator", "gpt-4o", ""); ok {
		t.Fatal("with no fallback, an unlisted model has no card")
	}
}

// --- seeding ---------------------------------------------------------------

func TestSeedUsesBundledSnapshotAndEndpoints(t *testing.T) {
	store := NewStore()
	Seed(store, []Source{{ProviderID: "or", Type: "openrouter"}, {ProviderID: "plain", Type: ""}})
	snaps := store.Load().Snapshots()
	if len(snaps) != 1 || snaps[0].ProviderID != "or" || snaps[0].Source != OriginBundled {
		t.Fatalf("seed = %+v", snaps)
	}
	if snaps[0].FetchedAt.IsZero() || store.Age() <= 0 {
		t.Fatal("the seeded snapshot must carry the bundle's fetch time")
	}
	if store.Stats().Endpoints < 1000 {
		t.Fatalf("seeded endpoints = %d, want the bundled endpoints attached", store.Stats().Endpoints)
	}
	if len(store.History(0)) != 0 {
		t.Fatal("the bundled seed is not a runtime change and must not enter the history")
	}
}

func keys[V any](m map[string]V) []string {
	out := make([]string, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	return out
}

func mustJSON(t *testing.T, v any) string {
	t.Helper()
	b, err := json.Marshal(v)
	if err != nil {
		t.Fatal(err)
	}
	return string(b)
}
