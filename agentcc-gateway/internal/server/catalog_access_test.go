package server

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"sort"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/catalog"
	"github.com/futureagi/agentcc-gateway/internal/catalog/sources"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/modeldb"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	creditsplugin "github.com/futureagi/agentcc-gateway/internal/plugins/credits"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

var accessNow = time.Date(2026, 10, 16, 12, 0, 0, 0, time.UTC)

// accessCard is a direct Anthropic price card listing Claude Sonnet 5.5
// under Anthropic's own id, at the same list price as OpenRouter's.
const accessCard = `{"provider":"anthropic-direct","upstream_slug":"anthropic","upstream_fee_bps":0,
	"as_of":"2026-10-01","source_url":"https://example.test/pricing","data_collection":"deny",
	"models":[{"id":"claude-sonnet-5-5-20260928","context_length":1000000,"max_output":128000,
		"prices":{"prompt":"0.000002","completion":"0.00001"}}]}`

type accessGateway struct {
	srv   *Server
	store *catalog.Store
	orgs  *tenant.Store
}

// newAccessGateway is a gateway with two managed sources, OpenRouter and a
// direct price card, and the given org configs. withCard false leaves the
// price-card source out.
func newAccessGateway(t *testing.T, withCard bool, orgs map[string]*tenant.OrgConfig) *accessGateway {
	t.Helper()
	off := false
	cfg := config.DefaultConfig()
	cfg.Admin.Token = adminToken
	cfg.Catalog.BlockedModels = []string{"openai/gpt-6-luna"}
	cfg.Providers["openrouter"] = config.ProviderConfig{BaseURL: "https://openrouter.example.test/api", APIKey: "fagi-openrouter-key",
		APIFormat: "openai", Type: "openrouter", Managed: true, AutoDiscover: &off}
	if withCard {
		cfg.Providers["anthropic-direct"] = config.ProviderConfig{BaseURL: "https://anthropic.example.test", APIKey: "fagi-direct-key",
			APIFormat: "openai", Managed: true, CatalogSource: catalog.SourcePriceCard, AutoDiscover: &off}
	}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatal(err)
	}
	srcs := ManagedCatalogSources(registry)
	opts := catalog.OptionsFromConfig(cfg, srcs)
	opts.Now = func() time.Time { return accessNow }
	store := catalog.NewStore()
	if err := store.Configure(opts); err != nil {
		t.Fatal(err)
	}
	store.Put(&catalog.Snapshot{ProviderID: "openrouter", Source: catalog.OriginLive, CatalogSource: catalog.SourceOpenRouter,
		FetchedAt: accessNow, Models: map[string]*catalog.Model{
			"anthropic/claude-sonnet-5.5": {Name: "Anthropic: Claude Sonnet 5.5", ContextLength: 1000000,
				Prices: catalog.Prices{Prompt: 2e-6, Completion: 1e-5}},
			"openai/gpt-6-sol":  {Name: "OpenAI: GPT-6 Sol", ContextLength: 400000, Prices: catalog.Prices{Prompt: 5e-6, Completion: 25e-6}},
			"openai/gpt-6-luna": {Name: "OpenAI: GPT-6 Luna", Prices: catalog.Prices{Prompt: 1e-6, Completion: 4e-6}},
			"x-ai/grok-4.6":     {Name: "xAI: Grok 4.6", Prices: catalog.Prices{Prompt: 3e-6, Completion: 15e-6}},
			"vendor/unpriced":   {Unpriced: true},
		}})
	if withCard {
		card, err := sources.ParsePriceCard([]byte(accessCard))
		if err != nil {
			t.Fatal(err)
		}
		store.Put(card.Snapshot("anthropic-direct", accessNow))
	}
	ts := tenant.NewStore()
	for id, c := range orgs {
		ts.Set(id, c)
	}
	var mdb atomic.Pointer[modeldb.ModelDB]
	mdb.Store(BuildModelDB(nil, store))
	srv := New(cfg, "", registry, pipeline.NewEngine(), auth.NewKeyStore(cfg.Auth), nil, nil, nil, &mdb, ts, nil)
	srv.EnableManaged(ManagedSettings{Catalog: store, Ledger: creditsplugin.NewOrgLedger(nil), MarkupPercent: 5})
	srv.ready.Store(true)
	return &accessGateway{srv: srv, store: store, orgs: ts}
}

func (g *accessGateway) get(t *testing.T, path string, admin bool) *httptest.ResponseRecorder {
	t.Helper()
	req := httptest.NewRequest(http.MethodGet, path, nil)
	if admin {
		req.Header.Set("Authorization", "Bearer "+adminToken)
	}
	w := httptest.NewRecorder()
	g.srv.httpServer.Handler.ServeHTTP(w, req)
	return w
}

// accessDoc is GET /-/orgs/{org_id}/catalog/access, decoded.
type accessDoc struct {
	Data []struct {
		ID                      string            `json:"id"`
		Name                    string            `json:"name"`
		Access                  string            `json:"access"`
		BYOKProviders           []string          `json:"byok_providers"`
		Managed                 bool              `json:"managed"`
		ManagedAvailableOnOptIn bool              `json:"managed_available_on_opt_in"`
		ManagedSources          []string          `json:"managed_sources"`
		State                   string            `json:"state"`
		Pricing                 map[string]string `json:"pricing"`
		ContextLength           int               `json:"context_length"`
	} `json:"data"`
	Stats struct {
		CanonicalModels int `json:"canonical_models"`
		BYOKOnly        int `json:"byok_only"`
		ManagedOnly     int `json:"managed_only"`
		Both            int `json:"both"`
	} `json:"stats"`
}

func (g *accessGateway) access(t *testing.T, org string) (accessDoc, map[string]int) {
	t.Helper()
	w := g.get(t, "/-/orgs/"+org+"/catalog/access", true)
	if w.Code != http.StatusOK {
		t.Fatalf("access = %d %s", w.Code, w.Body)
	}
	var doc accessDoc
	if err := json.Unmarshal(w.Body.Bytes(), &doc); err != nil {
		t.Fatalf("access body %s: %v", w.Body, err)
	}
	idx := map[string]int{}
	for i, row := range doc.Data {
		if _, dup := idx[row.ID]; dup {
			t.Fatalf("model %s listed twice", row.ID)
		}
		idx[row.ID] = i
	}
	return doc, idx
}

func accessOrg(managed *tenant.ManagedModelsConfig) *tenant.OrgConfig {
	return &tenant.OrgConfig{
		ManagedModels: managed,
		Providers: map[string]*tenant.ProviderConfig{
			// Anthropic's native id for the model both managed sources offer.
			"anthropic": {APIKey: "org-anthropic-key", Enabled: true, Models: []string{"claude-sonnet-5-5-20260928"}},
			// A model no managed source offers.
			"openai": {APIKey: "org-openai-key", Enabled: true, Models: []string{"gpt-4o"}},
			// No credentials: offers nothing.
			"mistral": {Enabled: true, Models: []string{"mistral-large-latest"}},
		},
	}
}

func TestCatalogAccessBothByokManaged(t *testing.T) {
	g := newAccessGateway(t, true, map[string]*tenant.OrgConfig{
		"org-both": accessOrg(&tenant.ManagedModelsConfig{Enabled: true, DenyVendors: []string{"x-ai"}}),
	})
	if w := g.get(t, "/-/orgs/org-both/catalog/access", false); w.Code != http.StatusUnauthorized {
		t.Fatalf("without the admin token = %d", w.Code)
	}
	doc, idx := g.access(t, "org-both")

	row := func(id string) int {
		i, ok := idx[id]
		if !ok {
			t.Fatalf("%s is not listed: %+v", id, doc.Data)
		}
		return i
	}
	// One canonical model, reached through the org's key and two managed
	// sources, priced at what the org pays (list price plus the 5% markup).
	both := doc.Data[row("anthropic/claude-sonnet-5.5")]
	if both.Access != "both" || !both.Managed || both.ManagedAvailableOnOptIn ||
		strings.Join(both.BYOKProviders, ",") != "anthropic" ||
		strings.Join(both.ManagedSources, ",") != "anthropic-direct,openrouter" ||
		both.State != catalog.StateActive || both.ContextLength != 1000000 || both.Name != "Anthropic: Claude Sonnet 5.5" {
		t.Errorf("claude row = %+v", both)
	}
	if both.Pricing["prompt"] != "0.0000021" || both.Pricing["completion"] != "0.0000105" {
		t.Errorf("claude pricing = %v, want the billed price", both.Pricing)
	}
	// Only through the org's own key: no managed source, no price, no state.
	byok := doc.Data[row("openai/gpt-4o")]
	if byok.Access != "byok" || byok.Managed || byok.ManagedAvailableOnOptIn || len(byok.ManagedSources) != 0 ||
		strings.Join(byok.BYOKProviders, ",") != "openai" || byok.Pricing != nil || byok.State != "unknown" {
		t.Errorf("gpt-4o row = %+v", byok)
	}
	// Only managed.
	sol := doc.Data[row("openai/gpt-6-sol")]
	if sol.Access != "managed" || !sol.Managed || len(sol.BYOKProviders) != 0 ||
		strings.Join(sol.ManagedSources, ",") != "openrouter" || sol.Pricing["prompt"] != "0.00000525" {
		t.Errorf("gpt-6-sol row = %+v", sol)
	}
	// A blocked model is listed with its state, and is not reachable.
	luna := doc.Data[row("openai/gpt-6-luna")]
	if luna.Access != "managed" || luna.Managed || luna.ManagedAvailableOnOptIn || luna.State != catalog.StateBlocked {
		t.Errorf("blocked row = %+v", luna)
	}
	// Never offered: an unpriced source model and a vendor the org denies;
	// a provider the org holds no credentials for reaches nothing.
	for _, id := range []string{"vendor/unpriced", "x-ai/grok-4.6"} {
		if _, ok := idx[id]; ok {
			t.Errorf("%s is listed", id)
		}
	}
	for _, r := range doc.Data {
		if strings.Contains(strings.Join(r.BYOKProviders, ","), "mistral") {
			t.Errorf("%s lists a provider without credentials: %v", r.ID, r.BYOKProviders)
		}
	}
	if s := doc.Stats; s.CanonicalModels != len(doc.Data) || s.CanonicalModels != 4 || s.Both != 1 || s.BYOKOnly != 1 || s.ManagedOnly != 2 {
		t.Errorf("stats = %+v over %d rows", s, len(doc.Data))
	}
}

func TestCatalogAccessOptInHint(t *testing.T) {
	g := newAccessGateway(t, true, map[string]*tenant.OrgConfig{
		"org-byok": accessOrg(nil),
		"org-off":  accessOrg(&tenant.ManagedModelsConfig{Enabled: false, AllowModels: []string{"openai/*"}}),
		"org-on":   accessOrg(&tenant.ManagedModelsConfig{Enabled: true}),
	})

	// Not opted in: what managed access would add is shown as available on
	// opt-in, never as managed.
	doc, idx := g.access(t, "org-byok")
	claude := doc.Data[idx["anthropic/claude-sonnet-5.5"]]
	if claude.Access != "both" || claude.Managed || !claude.ManagedAvailableOnOptIn {
		t.Errorf("claude row = %+v", claude)
	}
	sol := doc.Data[idx["openai/gpt-6-sol"]]
	if sol.Access != "managed" || sol.Managed || !sol.ManagedAvailableOnOptIn {
		t.Errorf("gpt-6-sol row = %+v", sol)
	}
	if gpt4o := doc.Data[idx["openai/gpt-4o"]]; gpt4o.ManagedAvailableOnOptIn {
		t.Errorf("a model no managed source offers is available on opt-in: %+v", gpt4o)
	}
	if luna := doc.Data[idx["openai/gpt-6-luna"]]; luna.ManagedAvailableOnOptIn {
		t.Errorf("a blocked model is available on opt-in: %+v", luna)
	}

	// The hint follows the org's own allow list even before it opts in.
	doc, idx = g.access(t, "org-off")
	if row := doc.Data[idx["openai/gpt-6-sol"]]; !row.ManagedAvailableOnOptIn {
		t.Errorf("allowed model = %+v", row)
	}
	if row := doc.Data[idx["anthropic/claude-sonnet-5.5"]]; row.Access != "byok" || row.ManagedAvailableOnOptIn {
		t.Errorf("a model outside the allow list = %+v", row)
	}

	// Opted in: managed now, so never "available on opt-in".
	doc, _ = g.access(t, "org-on")
	for _, row := range doc.Data {
		if row.ManagedAvailableOnOptIn || row.Access != accessBYOK && row.State == catalog.StateActive && !row.Managed {
			t.Errorf("opted-in org row = %+v", row)
		}
	}

	// An org with no config: every offered model is available on opt-in.
	doc, _ = g.access(t, "org-unknown")
	if len(doc.Data) == 0 || doc.Stats.BYOKOnly != 0 || doc.Stats.Both != 0 {
		t.Fatalf("unknown org = %+v", doc.Stats)
	}
	for _, row := range doc.Data {
		if row.Access != "managed" || row.Managed || row.ManagedAvailableOnOptIn != (row.State == catalog.StateActive) {
			t.Errorf("unknown org row = %+v", row)
		}
	}
}

// TestCatalogStatsPerSource: /-/catalog/stats counts each catalog source
// from its own snapshot, so a source added is a source counted; nothing is
// a hard-coded number.
func TestCatalogStatsPerSource(t *testing.T) {
	type sourceStats struct {
		Models          int `json:"models"`
		Endpoints       int `json:"endpoints"`
		PricedEndpoints int `json:"priced_endpoints"`
		CanonicalModels int `json:"canonical_models"`
	}
	stats := func(g *accessGateway) (int, map[string]sourceStats) {
		w := g.get(t, "/-/catalog/stats", true)
		if w.Code != http.StatusOK {
			t.Fatalf("stats = %d %s", w.Code, w.Body)
		}
		var doc struct {
			CanonicalModels int                    `json:"canonical_models"`
			PerSource       map[string]sourceStats `json:"per_source"`
		}
		if err := json.Unmarshal(w.Body.Bytes(), &doc); err != nil {
			t.Fatal(err)
		}
		return doc.CanonicalModels, doc.PerSource
	}
	// want is each source's counts, read from the store's own snapshots.
	want := func(g *accessGateway) map[string]sourceStats {
		out := map[string]sourceStats{}
		canon := map[string]map[string]bool{}
		for _, snap := range g.store.Load().Snapshots() {
			s := out[snap.CatalogSource]
			if canon[snap.CatalogSource] == nil {
				canon[snap.CatalogSource] = map[string]bool{}
			}
			for _, m := range snap.Models {
				s.Models++
				key := m.Canonical
				if key == "" {
					key = m.ID
				}
				if !canon[snap.CatalogSource][key] {
					canon[snap.CatalogSource][key] = true
					s.CanonicalModels++
				}
				for _, e := range m.Endpoints {
					s.Endpoints++
					if !e.DynamicPrice {
						s.PricedEndpoints++
					}
				}
			}
			out[snap.CatalogSource] = s
		}
		return out
	}

	_, only := stats(newAccessGateway(t, false, nil))
	if _, ok := only[catalog.SourcePriceCard]; ok || len(only) != 1 {
		t.Fatalf("without a card per_source = %v", only)
	}

	g := newAccessGateway(t, true, nil)
	canonical, per := stats(g)
	expected := want(g)
	keys := make([]string, 0, len(per))
	for k := range per {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	if strings.Join(keys, ",") != catalog.SourceOpenRouter+","+catalog.SourcePriceCard {
		t.Fatalf("per_source sources = %v", keys)
	}
	for k, s := range per {
		if s != expected[k] {
			t.Errorf("per_source[%s] = %+v, want %+v from the snapshot", k, s, expected[k])
		}
	}
	// The card's priced model is one endpoint; the OpenRouter listings were
	// never crawled, so they have none.
	if per[catalog.SourcePriceCard].PricedEndpoints != 1 || per[catalog.SourceOpenRouter].Endpoints != 0 {
		t.Errorf("per_source = %+v", per)
	}
	// The top-level canonical_models counts each canonical id once across
	// sources, and per_source counts each source's distinct canonical ids
	// (checked above).
	if canonical != len(g.store.Load().CanonicalModels()) {
		t.Errorf("canonical_models = %d, want the %d canonical models", canonical, len(g.store.Load().CanonicalModels()))
	}
}
