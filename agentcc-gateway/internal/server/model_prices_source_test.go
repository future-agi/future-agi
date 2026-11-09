package server

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/catalog"
	"github.com/futureagi/agentcc-gateway/internal/catalog/sources"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/pricesfeed"
)

// Use the shipped local-source resolver, not an invented catalogue type or a
// renamed bundled vendor snapshot. The fixed clock keeps this synthetic card
// eligible without changing the production price-card freshness policy.
func TestModelPricesResolvedSyntheticPriceCard(t *testing.T) {
	const provider = "e2e-synthetic"
	const card = `{
  "provider": "e2e-synthetic", "upstream_slug": "e2e-synthetic",
  "upstream_fee_bps": 0, "as_of": "2026-10-01", "data_collection": "deny",
  "source_url": "http://127.0.0.1:9/synthetic-card-never-fetched",
  "notes": "Synthetic test fixture, not a vendor quote",
  "models": [{"id": "fixture/text", "canonical": "fixture/text",
    "prices": {"prompt": "0.000002", "completion": "0.000010"},
    "modalities": {"input": ["text"], "output": ["text"]}}]
}`
	path := filepath.Join(t.TempDir(), "synthetic-price-card.json")
	if err := os.WriteFile(path, []byte(card), 0o600); err != nil {
		t.Fatal(err)
	}
	cfg := config.DefaultConfig()
	cfg.Providers = map[string]config.ProviderConfig{
		provider: {CatalogSource: catalog.SourcePriceCard, PriceCardFile: path},
	}
	resolved, err := sources.Resolve(cfg, []catalog.Source{{ProviderID: provider}})
	if err != nil || len(resolved) != 1 || resolved[0].Load == nil {
		t.Fatalf("real price-card source did not resolve: %v", err)
	}
	now := time.Date(2026, 10, 29, 12, 0, 0, 0, time.UTC)
	snapshot, err := resolved[0].Load(now)
	if err != nil || snapshot.ProviderID != provider || snapshot.CatalogSource != catalog.SourcePriceCard || snapshot.Source != catalog.OriginPriceCard {
		t.Fatalf("source loader did not return the synthetic price-card snapshot: %v", err)
	}
	store := catalog.NewStore()
	if err := store.Configure(catalog.Options{Now: func() time.Time { return now }}); err != nil {
		t.Fatal(err)
	}
	store.Put(snapshot)
	// A second global source is present but must not be published.
	other := *snapshot
	other.ProviderID = "not-allowlisted"
	store.Put(&other)

	for _, kind := range []string{catalog.SourcePriceCard, catalog.SourceOpenRouter} {
		t.Run(kind, func(t *testing.T) {
			srv, calls := modelPricesGateway(t, true, modelPricesConfigAuth, func(c *config.Config) {
				c.PublicPriceFeed.Sources = []config.PublicPriceSource{{ProviderID: provider, CatalogSource: kind}}
			})
			srv.handlers.catalog = store
			assertModelPricesConfig(t, srv, true, []config.PublicPriceSource{{ProviderID: provider, CatalogSource: kind}})
			before := feedRequest(srv, "")
			var doc pricesfeed.Document
			if before.Code != http.StatusOK || json.Unmarshal(before.Body.Bytes(), &doc) != nil {
				t.Fatalf("synthetic feed = %d %s", before.Code, before.Body)
			}
			if kind != catalog.SourcePriceCard {
				if len(doc.Offers) != 0 || len(doc.Sources) != 0 {
					t.Fatal("provider-only match ignored the catalog_source allowlist")
				}
			} else {
				if len(doc.Sources) != 1 || doc.Sources[0].ProviderID != provider || doc.Sources[0].CatalogSource != kind || len(doc.Offers) != 1 {
					t.Fatalf("not the exact synthetic source/offer set: %s", before.Body)
				}
				offer := doc.Offers[0]
				if offer.ProviderID != provider || offer.CatalogSource != kind || offer.ModelID != "fixture/text" || offer.UpstreamProvider != provider || offer.EndpointID != catalog.EndpointID(kind, provider, "fixture/text") || len(offer.Dimensions) != 7 {
					t.Fatalf("synthetic offer identity changed: %+v", offer)
				}
				for name, want := range map[string]string{"uncached_input": "0.000002", "output": "0.00001"} {
					found := false
					for _, d := range offer.Dimensions {
						if d.Name == name {
							found = d.Unit == "token" && d.ProviderRateUSD != nil && *d.ProviderRateUSD == want
						}
					}
					if !found {
						t.Fatalf("missing exact per-token %s price %s", name, want)
					}
				}
			}
			// The public read cannot depend on a caller credential or reload the
			// local file. Corrupting only the file must not change this snapshot.
			if err := os.WriteFile(path, []byte("not a price card"), 0o600); err != nil {
				t.Fatal(err)
			}
			defer func() {
				if err := os.WriteFile(path, []byte(card), 0o600); err != nil {
					t.Error(err)
				}
			}()
			req := httptest.NewRequest(http.MethodGet, "/public/model-prices/v1.json", nil)
			req.Header.Set("Authorization", "Bearer synthetic-caller-not-a-real-key")
			after := httptest.NewRecorder()
			srv.httpServer.Handler.ServeHTTP(after, req)
			if after.Code != before.Code || !bytes.Equal(after.Body.Bytes(), before.Body.Bytes()) || after.Header().Get("ETag") != before.Header().Get("ETag") {
				t.Fatal("public read depended on caller credentials or reloaded the source")
			}
			if _, err := resolved[0].Load(now); err == nil {
				t.Fatal("control: the real source loader did not observe the corrupted file")
			}
			for _, excluded := range []string{"not-allowlisted", "synthetic-caller-not-a-real-key", "source_url", "price_card_file", "Synthetic test fixture"} {
				if strings.Contains(before.Body.String(), excluded) {
					t.Fatalf("feed disclosed %q", excluded)
				}
			}
			if calls.Load() != 0 {
				t.Fatalf("config/feed made %d HTTP calls", calls.Load())
			}
		})
	}

	t.Run("unknown_data_policy_stays_excluded", func(t *testing.T) {
		// Direct price cards must attest no data collection. Unlike an
		// aggregator source, unknown cannot enforce that policy upstream.
		unknown := strings.Replace(card, `"data_collection": "deny"`, `"data_collection": "unknown"`, 1)
		if err := os.WriteFile(path, []byte(unknown), 0o600); err != nil {
			t.Fatal(err)
		}
		unattested, err := resolved[0].Load(now)
		if err != nil {
			t.Fatal(err)
		}
		store.Put(unattested)
		srv, calls := modelPricesGateway(t, true, func(c *config.Config) {
			c.PublicPriceFeed.Sources = []config.PublicPriceSource{{ProviderID: provider, CatalogSource: catalog.SourcePriceCard}}
		})
		srv.handlers.catalog = store
		response := feedRequest(srv, "")
		var doc pricesfeed.Document
		if response.Code != http.StatusOK || json.Unmarshal(response.Body.Bytes(), &doc) != nil || len(doc.Offers) != 0 || len(doc.Sources) != 0 {
			t.Fatalf("unattested direct source became publishable: %d %s", response.Code, response.Body)
		}
		if calls.Load() != 0 {
			t.Fatal("data-policy refusal fetched a provider")
		}
	})
}
