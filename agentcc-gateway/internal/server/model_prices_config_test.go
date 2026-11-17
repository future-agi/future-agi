package server

import (
	"bytes"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/pricesfeed"
)

// Check the wire shape, including explicit false/empty values, rather than
// decoding an absent field into a zero-value Config and accidentally passing.
func assertModelPricesConfig(t *testing.T, srv *Server, enabled bool, sources []config.PublicPriceSource) {
	t.Helper()
	out, raw := r7GetConfig(t, srv)
	var publication map[string]json.RawMessage
	if err := json.Unmarshal(out["public_price_feed"], &publication); err != nil {
		t.Fatalf("missing public_price_feed in authenticated node config: %v", err)
	}
	if len(publication) != 2 || string(publication["enabled"]) != fmt.Sprint(enabled) {
		t.Fatalf("publication must contain only enabled and sources: %s", out["public_price_feed"])
	}
	var got []config.PublicPriceSource
	if err := json.Unmarshal(publication["sources"], &got); err != nil || got == nil || !reflect.DeepEqual(got, sources) {
		t.Fatalf("publication sources = %s, want %+v (error %v)", publication["sources"], sources, err)
	}
	var entries []map[string]json.RawMessage
	if err := json.Unmarshal(publication["sources"], &entries); err != nil {
		t.Fatal(err)
	}
	for _, entry := range entries {
		if len(entry) != 2 || entry["provider_id"] == nil || entry["catalog_source"] == nil {
			t.Fatalf("source exposes fields beyond the publication pair: %v", entry)
		}
	}
	for _, secret := range []string{r7ConfigAdmin, r7ConfigCPToken, r7ConfigWebhook} {
		if strings.Contains(raw, secret) {
			t.Fatal("node config exposed a credential")
		}
	}
}

func modelPricesConfigAuth(cfg *config.Config) {
	cfg.Admin.Token = r7ConfigAdmin
	cfg.ControlPlane.AdminToken = r7ConfigCPToken
	cfg.ControlPlane.WebhookSecret = r7ConfigWebhook
}

func TestModelPricesConfigDefaultOff(t *testing.T) {
	srv, calls := modelPricesGateway(t, false, modelPricesConfigAuth, func(cfg *config.Config) {
		cfg.PublicPriceFeed = config.DefaultConfig().PublicPriceFeed
	})
	assertModelPricesConfig(t, srv, false, []config.PublicPriceSource{})
	if w := feedRequest(srv, ""); w.Code != http.StatusNotFound {
		t.Fatalf("default public feed = %d, want 404", w.Code)
	}
	if calls.Load() != 0 {
		t.Fatalf("config/feed made %d network calls", calls.Load())
	}
}

func TestModelPricesConfigEnabledAndAuthenticated(t *testing.T) {
	srv, calls := modelPricesGateway(t, true, modelPricesConfigAuth)
	assertModelPricesConfig(t, srv, true, []config.PublicPriceSource{{ProviderID: "synthetic", CatalogSource: "openrouter"}})
	if w := feedRequest(srv, ""); w.Code != http.StatusOK {
		t.Fatalf("enabled public feed = %d, want 200", w.Code)
	}
	for _, token := range []string{"", "not-the-admin"} {
		req := httptest.NewRequest(http.MethodGet, "/-/config", nil)
		if token != "" {
			req.Header.Set("Authorization", "Bearer "+token)
		}
		w := httptest.NewRecorder()
		srv.httpServer.Handler.ServeHTTP(w, req)
		if w.Code != http.StatusUnauthorized || strings.Contains(w.Body.String(), "public_price_feed") {
			t.Fatalf("unauthenticated config = %d %s", w.Code, w.Body)
		}
	}
	if calls.Load() != 0 {
		t.Fatalf("config/feed made %d network calls", calls.Load())
	}
}

func TestModelPricesConfigStartupSnapshot(t *testing.T) {
	srv, calls := modelPricesGateway(t, true, modelPricesConfigAuth)
	before := feedRequest(srv, "")
	// Mutate the constructor's input, including the source slice's backing
	// array. Neither endpoint may follow these changes without a restart.
	srv.cfg.PublicPriceFeed.Enabled = false
	srv.cfg.PublicPriceFeed.Sources[0].ProviderID = "changed"
	srv.cfg.PublicPriceFeed.Sources[0].CatalogSource = "price_card"
	assertModelPricesConfig(t, srv, true, []config.PublicPriceSource{{ProviderID: "synthetic", CatalogSource: "openrouter"}})
	after := feedRequest(srv, "")
	if before.Code != http.StatusOK || after.Code != before.Code || !bytes.Equal(before.Body.Bytes(), after.Body.Bytes()) || before.Header().Get("ETag") != after.Header().Get("ETag") {
		t.Fatal("constructor input mutation changed the startup feed")
	}
	if calls.Load() != 0 {
		t.Fatalf("config/feed made %d network calls", calls.Load())
	}
}

func TestModelPricesConfigRoutingReloadAndRestart(t *testing.T) {
	for _, tc := range []struct {
		name          string
		before, after bool
		source        string
		wantOffers    int
	}{
		{"enable_on_restart", false, true, "openrouter", 1},
		{"disable_on_restart", true, false, "openrouter", 0},
		{"allowlist_on_restart", true, true, "price_card", 0},
	} {
		t.Run(tc.name, func(t *testing.T) {
			srv, calls := modelPricesGateway(t, tc.before, modelPricesConfigAuth)
			path := filepath.Join(t.TempDir(), "config.yaml")
			writeConfig := func(enabled bool, source, strategy string) {
				t.Helper()
				text := fmt.Sprintf("admin:\n  token: %s\nrouting:\n  default_strategy: %s\npublic_price_feed:\n  enabled: %t\n  sources:\n    - provider_id: synthetic\n      catalog_source: %s\n", r7ConfigAdmin, strategy, enabled, source)
				if err := os.WriteFile(path, []byte(text), 0o600); err != nil {
					t.Fatal(err)
				}
			}
			writeConfig(tc.before, "openrouter", "round-robin")
			srv.configPath = path
			before := feedRequest(srv, "")
			writeConfig(tc.after, tc.source, "least-latency")
			req := httptest.NewRequest(http.MethodPost, "/-/reload", nil)
			req.Header.Set("Authorization", "Bearer "+r7ConfigAdmin)
			w := httptest.NewRecorder()
			srv.httpServer.Handler.ServeHTTP(w, req)
			if w.Code != http.StatusOK || srv.cfg.Routing.DefaultStrategy != "least-latency" || srv.cfg.PublicPriceFeed.Enabled != tc.after || srv.cfg.PublicPriceFeed.Sources[0].CatalogSource != tc.source {
				t.Fatalf("changed-file routing reload did not apply: %d %s", w.Code, w.Body)
			}
			assertModelPricesConfig(t, srv, tc.before, []config.PublicPriceSource{{ProviderID: "synthetic", CatalogSource: "openrouter"}})
			after := feedRequest(srv, "")
			if after.Code != before.Code || !bytes.Equal(before.Body.Bytes(), after.Body.Bytes()) || before.Header().Get("ETag") != after.Header().Get("ETag") {
				t.Fatal("routing reload changed startup publication")
			}
			loaded, err := config.Load(path)
			if err != nil {
				t.Fatal(err)
			}
			restarted, restartCalls := modelPricesGateway(t, tc.after, func(cfg *config.Config) { *cfg = *loaded })
			assertModelPricesConfig(t, restarted, tc.after, []config.PublicPriceSource{{ProviderID: "synthetic", CatalogSource: tc.source}})
			feed := feedRequest(restarted, "")
			if !tc.after {
				if feed.Code != http.StatusNotFound {
					t.Fatalf("restarted disabled feed = %d, want 404", feed.Code)
				}
			} else {
				var doc pricesfeed.Document
				if feed.Code != http.StatusOK || json.Unmarshal(feed.Body.Bytes(), &doc) != nil || len(doc.Offers) != tc.wantOffers {
					t.Fatalf("restarted feed disagrees with reported publication: %d %s", feed.Code, feed.Body)
				}
			}
			if calls.Load() != 0 || restartCalls.Load() != 0 {
				t.Fatal("reload/restart config or feed made a network call")
			}
		})
	}
}
