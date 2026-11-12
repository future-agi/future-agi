package server

import (
	"bytes"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"reflect"
	"sort"
	"strconv"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/catalog"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/pricesfeed"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// This test intentionally uses only APIs present at the immutable API base.
// Enabling the proposed setting through JSON must fail there with HTTP 404,
// not an undefined symbol or a compile error.
func modelPricesGateway(t *testing.T, enabled bool, configure ...func(*config.Config)) (*Server, *atomic.Int32) {
	t.Helper()
	cfg := config.DefaultConfig()
	if err := json.Unmarshal([]byte(fmt.Sprintf(`{"public_price_feed":{"enabled":%t,"sources":[{"provider_id":"synthetic","catalog_source":"openrouter"}]}}`, enabled)), cfg); err != nil {
		t.Fatal(err)
	}
	cfg.Auth.Enabled = true
	cfg.LicenseAuth.Enabled = true
	for _, apply := range configure {
		apply(cfg)
	}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatal(err)
	}
	// No tenant store, credential store, ledger, logs or upstream provider.
	srv := New(cfg, "", registry, pipeline.NewEngine(), nil, nil, nil, nil, testModelDBPtr(), nil, nil)
	releaseTestServer(t, srv)
	all, err := catalog.ParseOpenRouter([]byte(`{"data":[{"id":"fixture/text","pricing":{"prompt":"0.000002","completion":"0.000010"}}]}`))
	if err != nil {
		t.Fatal(err)
	}
	srv.handlers.managedMarkupPercent = cfg.Managed.MarkupPercent
	srv.handlers.catalog = catalog.NewStore()
	srv.handlers.catalog.Put(&catalog.Snapshot{ProviderID: "synthetic", CatalogSource: catalog.SourceOpenRouter, Source: catalog.OriginBundled, FetchedAt: time.Date(2026, 10, 1, 0, 0, 0, 0, time.UTC), Models: all})
	calls := &atomic.Int32{}
	old := http.DefaultTransport
	http.DefaultTransport = priceFeedNoNetwork{calls}
	t.Cleanup(func() { http.DefaultTransport = old })
	return srv, calls
}

type priceFeedNoNetwork struct{ calls *atomic.Int32 }

func (p priceFeedNoNetwork) RoundTrip(*http.Request) (*http.Response, error) {
	p.calls.Add(1)
	return nil, fmt.Errorf("public feed must not access the network")
}

func TestModelPricesPublicContract(t *testing.T) {
	srv, calls := modelPricesGateway(t, true)
	w := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(w, httptest.NewRequest(http.MethodGet, "/public/model-prices/v1.json", nil))
	if w.Code != http.StatusOK {
		t.Fatalf("enabled public dimensional feed: got HTTP %d, want 200; body=%s", w.Code, w.Body)
	}
	var feed struct {
		SchemaVersion string `json:"schema_version"`
		Offers        []struct {
			ModelID    string            `json:"model_id"`
			Dimensions []json.RawMessage `json:"dimensions"`
		} `json:"offers"`
	}
	if err := json.Unmarshal(w.Body.Bytes(), &feed); err != nil {
		t.Fatal(err)
	}
	if feed.SchemaVersion != "model-prices-v1" || len(feed.Offers) != 1 || feed.Offers[0].ModelID != "fixture/text" || len(feed.Offers[0].Dimensions) != 7 {
		t.Fatalf("incomplete dimensional contract: %s", w.Body)
	}
	if calls.Load() != 0 {
		t.Fatalf("feed made %d upstream calls", calls.Load())
	}
}

func TestModelPricesDisabledByDefault(t *testing.T) {
	srv, calls := modelPricesGateway(t, false)
	// Replace the operator config with a true default, not enabled=false JSON.
	srv.cfg = config.DefaultConfig()
	w := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(w, httptest.NewRequest(http.MethodGet, "/public/model-prices/v1.json", nil))
	if w.Code != http.StatusNotFound {
		t.Fatalf("default feed: got %d, want 404", w.Code)
	}
	if calls.Load() != 0 {
		t.Fatalf("disabled feed made %d upstream calls", calls.Load())
	}
}

func feedRequest(srv *Server, etag string) *httptest.ResponseRecorder {
	r := httptest.NewRequest(http.MethodGet, "/public/model-prices/v1.json", nil)
	if etag != "" {
		r.Header.Set("If-None-Match", etag)
	}
	w := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(w, r)
	return w
}

func syntheticPriceModels(t *testing.T) map[string]*catalog.Model {
	t.Helper()
	all, err := catalog.ParseOpenRouter([]byte(`{"data":[
		{"id":"fixture/text","pricing":{"prompt":"0.000002","completion":"0.000010"}},
		{"id":"fixture/text:nitro","pricing":{"prompt":"0.000020","completion":"0.000100"}},
		{"id":"fixture/listed:free","pricing":{"prompt":"0","completion":"0"}},
		{"id":"fixture/request","pricing":{"request":"0.04"}},
		{"id":"fixture/missing"},
		{"id":"fixture/malformed","pricing":{"prompt":"bad","completion":"0.1"}},
		{"id":"fixture/unknown","pricing":{"prompt":"0.001","completion":"0.1","generation":"1"}},
		{"id":"fixture/dynamic","pricing":{"prompt":"-1","completion":"-1"}},
		{"id":"fixture/image","pricing":{"prompt":"0.001","completion":"0.01","image":"0.1"}},
		{"id":"fixture/tiered","pricing":{"prompt":"0.001","completion":"0.01","overrides":[{"min_prompt_tokens":100,"prompt":"0.002"}]}},
		{"id":"fixture/expired","expiration_date":"2026-10-01","pricing":{"prompt":"0.001","completion":"0.01"}},
		{"id":"fixture/blocked","pricing":{"prompt":"0.001","completion":"0.01"}}
	]}`))
	if err != nil {
		t.Fatal(err)
	}
	eps, err := catalog.ParseOpenRouterEndpoints("fixture/text", []byte(`{"data":{"id":"fixture/text","endpoints":[
		{"tag":"low","pricing":{"prompt":"0.000002","completion":"0.000010"}},
		{"tag":"high/variant","pricing":{"prompt":"0.000003","completion":"0.000020"}},
		{"tag":"down","status":-1,"pricing":{"prompt":"0.000001","completion":"0.000001"}},
		{"tag":"unknown","pricing":{}},
		{"tag":"dynamic","pricing":{"prompt":"-1","completion":"-1"}}
	]}}`))
	if err != nil {
		t.Fatal(err)
	}
	all["fixture/text"].Endpoints = eps
	all["fixture/text"].Name = "DO_NOT_PUBLISH_DESCRIPTION"
	all["fixture/text"].Description = "DO_NOT_PUBLISH_DESCRIPTION"
	return all
}

func TestModelPricesExactOffers(t *testing.T) {
	srv, calls := modelPricesGateway(t, true)
	now := time.Date(2026, 10, 27, 12, 0, 0, 0, time.UTC)
	store := catalog.NewStore()
	if err := store.Configure(catalog.Options{Now: func() time.Time { return now }, BlockedModels: []string{"fixture/blocked"}}); err != nil {
		t.Fatal(err)
	}
	store.Put(&catalog.Snapshot{ProviderID: "synthetic", CatalogSource: catalog.SourceOpenRouter, Source: catalog.OriginBundled, FetchedAt: now, Models: syntheticPriceModels(t)})
	store.Put(&catalog.Snapshot{ProviderID: "not-allowlisted", CatalogSource: catalog.SourceOpenRouter, Source: catalog.OriginBundled, Models: syntheticPriceModels(t)})
	srv.handlers.catalog = store
	w := feedRequest(srv, "")
	if w.Code != 200 {
		t.Fatalf("%d %s", w.Code, w.Body)
	}
	var doc pricesfeed.Document
	if err := json.Unmarshal(w.Body.Bytes(), &doc); err != nil {
		t.Fatal(err)
	}
	var got, want []string
	cards := map[string]catalog.Prices{}
	allowFree := true
	policy := &tenant.OrgConfig{ManagedModels: &tenant.ManagedModelsConfig{Enabled: true, AllowFree: &allowFree}}
	for _, snap := range store.Load().Snapshots() {
		if snap.ProviderID != "synthetic" {
			continue
		}
		for _, m := range snap.Sorted() {
			l := catalog.Listing{ProviderID: snap.ProviderID, Source: snap.CatalogSource, Model: m}
			v, ok := managedOffer(l, policy, now, true)
			if !ok {
				continue
			}
			for _, e := range offerEndpoints(l, &v) {
				want = append(want, m.ID+"|"+e.ID+"|"+strconv.FormatFloat(e.Prices.Prompt, 'f', -1, 64))
				cards[m.ID+"|"+e.ID] = e.Prices
			}
		}
	}
	for _, o := range doc.Offers {
		if o.ProviderID != "synthetic" {
			t.Fatalf("unallowlisted source: %+v", o)
		}
		got = append(got, o.ModelID+"|"+o.EndpointID+"|"+*o.Dimensions[0].CardRateUSD)
		p, ok := cards[o.ModelID+"|"+o.EndpointID]
		if !ok {
			t.Fatalf("unexpected exact endpoint %s/%s", o.ModelID, o.EndpointID)
		}
		rates := map[string]float64{"uncached_input": p.Prompt, "output": p.Completion,
			"cache_read": p.CacheRead, "cache_write": p.CacheWrite, "reasoning": p.Reasoning,
			"request": p.Request, "web_search": p.WebSearch}
		for _, d := range o.Dimensions {
			if d.Name == "web_search" && p.WebSearch == 0 {
				if d.CardRateUSD != nil || d.Rule != "not_quoted" {
					t.Fatal("missing endpoint search price became a quote")
				}
				continue
			}
			if d.CardRateUSD == nil || *d.CardRateUSD != strconv.FormatFloat(rates[d.Name], 'f', -1, 64) {
				t.Fatalf("wrong exact endpoint dimension %s/%s/%s", o.ModelID, o.EndpointID, d.Name)
			}
		}
	}
	sort.Strings(got)
	sort.Strings(want)
	if !reflect.DeepEqual(got, want) || len(got) != 5 {
		t.Fatalf("exact offers got=%v want=%v", got, want)
	}
	for _, forbidden := range []string{"DO_NOT_PUBLISH_DESCRIPTION", "upstream_fee", "processing_fee", "not-allowlisted", "fixture/expired", "fixture/blocked", "fixture/missing", "fixture/malformed", "fixture/unknown", "fixture/image", "fixture/tiered", "fixture/dynamic", ":down:"} {
		if strings.Contains(w.Body.String(), forbidden) {
			t.Fatalf("feed disclosed/exposed %q", forbidden)
		}
	}
	if calls.Load() != 0 {
		t.Fatalf("projection made %d upstream calls", calls.Load())
	}
}

func TestModelPricesAllowlist(t *testing.T) {
	for _, tc := range []struct{ name, source string }{{"empty", ""}, {"wrong_kind", "price_card"}} {
		t.Run(tc.name, func(t *testing.T) {
			srv, calls := modelPricesGateway(t, true, func(c *config.Config) {
				c.PublicPriceFeed.Sources = nil
				if tc.source != "" {
					c.PublicPriceFeed.Sources = []config.PublicPriceSource{{ProviderID: "synthetic", CatalogSource: tc.source}}
				}
			})
			w := feedRequest(srv, "")
			var d pricesfeed.Document
			if w.Code != 200 || json.Unmarshal(w.Body.Bytes(), &d) != nil || len(d.Offers) != 0 {
				t.Fatalf("allowlist: %d %s", w.Code, w.Body)
			}
			if calls.Load() != 0 {
				t.Fatal("allowlist lookup made an upstream request")
			}
		})
	}
}

func TestModelPricesCacheContract(t *testing.T) {
	srv, calls := modelPricesGateway(t, true)
	now := time.Date(2026, 10, 27, 23, 59, 58, 0, time.UTC)
	store := catalog.NewStore()
	if err := store.Configure(catalog.Options{Now: func() time.Time { return now }}); err != nil {
		t.Fatal(err)
	}
	put := func(price float64, blocked bool) {
		all := syntheticPriceModels(t)
		m := all["fixture/text:nitro"]
		m.ExpirationDate = "2026-10-28"
		m.Prices.Prompt = price
		store.Put(&catalog.Snapshot{ProviderID: "synthetic", CatalogSource: catalog.SourceOpenRouter, Source: catalog.OriginBundled, FetchedAt: now, Models: map[string]*catalog.Model{m.ID: m}})
		var blocks []string
		if blocked {
			blocks = []string{m.ID}
		}
		if _, err := store.SetBlocks(blocks, "synthetic block"); err != nil {
			t.Fatal(err)
		}
	}
	put(20e-6, false)
	srv.handlers.catalog = store
	first := feedRequest(srv, "")
	if first.Code != 200 || first.Header().Get("ETag") == "" || first.Header().Get("Cache-Control") != "public, max-age=2, must-revalidate" {
		t.Fatalf("cache headers: %d %v", first.Code, first.Header())
	}
	put(20e-6, false)
	again := feedRequest(srv, "")
	if !bytes.Equal(first.Body.Bytes(), again.Body.Bytes()) || first.Header().Get("ETag") != again.Header().Get("ETag") {
		t.Fatal("unchanged content reversioned")
	}
	now = now.Add(time.Second)
	conditional := feedRequest(srv, first.Header().Get("ETag"))
	if conditional.Code != 304 || conditional.Body.Len() != 0 || conditional.Header().Get("Cache-Control") != "public, max-age=1, must-revalidate" {
		t.Fatalf("conditional response: %d %v %s", conditional.Code, conditional.Header(), conditional.Body)
	}
	for _, etag := range []string{"W/" + first.Header().Get("ETag"), "\"other\", " + first.Header().Get("ETag"), "*"} {
		if w := feedRequest(srv, etag); w.Code != 304 || w.Body.Len() != 0 {
			t.Fatalf("conditional %s: %d", etag, w.Code)
		}
	}
	put(30e-6, false)
	if w := feedRequest(srv, first.Header().Get("ETag")); w.Code != 200 || w.Header().Get("ETag") == first.Header().Get("ETag") {
		t.Fatal("price change kept ETag")
	}
	put(20e-6, false)
	srv.handlers.managedMarkupPercent = 6
	if w := feedRequest(srv, first.Header().Get("ETag")); w.Code != 200 || w.Header().Get("ETag") == first.Header().Get("ETag") {
		t.Fatal("effective markup change kept ETag")
	}
	srv.handlers.managedMarkupPercent = 5
	put(20e-6, true)
	if w := feedRequest(srv, first.Header().Get("ETag")); w.Code != 200 || w.Header().Get("ETag") == first.Header().Get("ETag") {
		t.Fatal("eligibility change kept ETag")
	}
	put(20e-6, false)
	now = now.Add(time.Second)
	w := feedRequest(srv, first.Header().Get("ETag"))
	var d pricesfeed.Document
	if w.Code != 200 || json.Unmarshal(w.Body.Bytes(), &d) != nil || len(d.Offers) != 0 {
		t.Fatalf("expiry still quoted: %d %s", w.Code, w.Body)
	}
	if calls.Load() != 0 {
		t.Fatalf("generation/revalidation made %d HTTP calls", calls.Load())
	}
	t.Run("endpoint_eligibility_and_unpublished_sources", func(t *testing.T) {
		srv, calls := modelPricesGateway(t, true)
		clock := time.Date(2026, 10, 27, 12, 0, 0, 0, time.UTC)
		store := catalog.NewStore()
		if err := store.Configure(catalog.Options{Now: func() time.Time { return clock }}); err != nil {
			t.Fatal(err)
		}
		put := func(provider string, lowStatus int) {
			all := syntheticPriceModels(t)
			m := all["fixture/text"]
			m.Endpoints[0].Status = lowStatus
			store.Put(&catalog.Snapshot{ProviderID: provider, CatalogSource: catalog.SourceOpenRouter,
				Source: catalog.OriginBundled, FetchedAt: clock, Models: map[string]*catalog.Model{m.ID: m}})
		}
		put("synthetic", 0)
		srv.handlers.catalog = store
		first := feedRequest(srv, "")
		var before pricesfeed.Document
		if first.Code != 200 || json.Unmarshal(first.Body.Bytes(), &before) != nil || len(before.Offers) != 2 {
			t.Fatalf("initial endpoints: %d %s", first.Code, first.Body)
		}
		put("not-allowlisted", 0)
		if w := feedRequest(srv, first.Header().Get("ETag")); w.Code != 304 || w.Body.Len() != 0 {
			t.Fatal("unpublished source changed public representation")
		}
		put("synthetic", -1)
		changed := feedRequest(srv, first.Header().Get("ETag"))
		var after pricesfeed.Document
		if changed.Code != 200 || json.Unmarshal(changed.Body.Bytes(), &after) != nil || len(after.Offers) != 1 ||
			changed.Header().Get("ETag") == first.Header().Get("ETag") || after.ContentVersion == before.ContentVersion ||
			after.Sources[0].SnapshotID == before.Sources[0].SnapshotID {
			t.Fatalf("endpoint eligibility kept stale representation: %d %s", changed.Code, changed.Body)
		}
		if calls.Load() != 0 {
			t.Fatalf("endpoint revalidation made %d HTTP calls", calls.Load())
		}
	})
}
