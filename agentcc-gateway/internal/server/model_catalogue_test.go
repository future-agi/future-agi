package server

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/modeldb"
)

func newCatalogueTestServer(t *testing.T) (*Server, *atomic.Pointer[modeldb.ModelDB], *modeldb.Catalogue) {
	t.Helper()
	srv := createTestServer(t, "http://127.0.0.1:1")
	var ptr atomic.Pointer[modeldb.ModelDB]
	c := modeldb.NewCatalogue(&ptr, nil)
	srv.handlers.modelDB = &ptr
	srv.SetCatalogue(c)
	return srv, &ptr, c
}

func fetchedOnly() map[string]*modeldb.ModelInfo {
	return map[string]*modeldb.ModelInfo{
		"fetched-only": {ID: "fetched-only", Provider: "openai", Mode: modeldb.ModeChat,
			Pricing: modeldb.PricingInfo{InputPerToken: 1e-6, OutputPerToken: 2e-6}},
	}
}

// ReloadRouting used to rebuild the model database from the bundled models,
// which dropped a fetched catalogue on every config reload.
func TestReloadRoutingKeepsFetchedCatalogue(t *testing.T) {
	srv, ptr, c := newCatalogueTestServer(t)
	c.Overlay(fetchedOnly(), "https://catalogue.test/litellm.json")

	if err := srv.handlers.ReloadRouting(srv.cfg, srv.registry); err != nil {
		t.Fatalf("ReloadRouting: %v", err)
	}

	if _, ok := ptr.Load().Get("fetched-only"); !ok {
		t.Error("config reload dropped the fetched catalogue")
	}
	if c.Info().Source != modeldb.SourceFetched {
		t.Errorf("info = %+v", c.Info())
	}
}

func TestReloadRoutingAppliesNewOverridesOverFetched(t *testing.T) {
	srv, ptr, c := newCatalogueTestServer(t)
	c.Overlay(fetchedOnly(), "https://catalogue.test/litellm.json")

	price := 9e-6
	cfg := config.DefaultConfig()
	cfg.Providers = srv.cfg.Providers
	cfg.ModelDatabase.Overrides = map[string]config.ModelOverrideConfig{
		"fetched-only": {Pricing: &config.PricingOverrideConfig{InputPerToken: &price}},
	}
	if err := srv.handlers.ReloadRouting(cfg, srv.registry); err != nil {
		t.Fatalf("ReloadRouting: %v", err)
	}

	got, ok := ptr.Load().GetPricing("fetched-only")
	if !ok || got.InputPerToken != price || got.OutputPerToken != 2e-6 {
		t.Errorf("pricing = %+v ok=%v", got, ok)
	}
}

func TestCatalogueSourceEndpoint(t *testing.T) {
	srv, _, c := newCatalogueTestServer(t)
	srv.cfg.Admin.Token = "admin-secret"
	get := func(token string) *httptest.ResponseRecorder {
		req := httptest.NewRequest(http.MethodGet, "/-/model-catalogue/source", nil)
		if token != "" {
			req.Header.Set("Authorization", "Bearer "+token)
		}
		w := httptest.NewRecorder()
		srv.httpServer.Handler.ServeHTTP(w, req)
		return w
	}

	if w := get(""); w.Code != http.StatusUnauthorized {
		t.Errorf("no token: status %d, want 401", w.Code)
	}

	var info modeldb.CatalogueInfo
	w := get("admin-secret")
	if w.Code != http.StatusOK {
		t.Fatalf("status %d: %s", w.Code, w.Body.String())
	}
	if err := json.Unmarshal(w.Body.Bytes(), &info); err != nil || info.Source != modeldb.SourceBundled || info.Entries == 0 {
		t.Errorf("bundled: %+v err=%v", info, err)
	}

	c.Overlay(fetchedOnly(), "https://catalogue.test/litellm.json")
	w = get("admin-secret")
	if err := json.Unmarshal(w.Body.Bytes(), &info); err != nil ||
		info.Source != modeldb.SourceFetched || info.URL != "https://catalogue.test/litellm.json" || info.Entries != len(modeldb.BundledModels)+1 {
		t.Errorf("fetched: %+v err=%v", info, err)
	}
}

// The admin endpoint shows a failed sync's last_error, and a catalogue URL can
// carry a credential: in a signed query, or in userinfo that does not parse.
func TestCatalogueSourceEndpointRedactsTheCatalogueURL(t *testing.T) {
	for _, tc := range []struct{ name, url string }{
		{"signed query", "http://127.0.0.1:1/c.json?X-Amz-Signature=sigsecret&X-Amz-Credential=credsecret&X-Amz-Security-Token=toksecret&X-Goog-Signature=googsecret"},
		{"unparseable userinfo", "http://bob:hunter2@127.0.0.1:badport/c.json"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			srv, _, c := newCatalogueTestServer(t)
			srv.cfg.Admin.Token = "admin-secret"

			ctx, cancel := context.WithCancel(context.Background())
			defer cancel()
			go modeldb.NewSyncer(c, tc.url, time.Hour).Run(ctx)
			deadline := time.Now().Add(5 * time.Second)
			for c.Info().LastError == "" {
				if time.Now().After(deadline) {
					t.Fatal("the failed sync was not recorded")
				}
				time.Sleep(10 * time.Millisecond)
			}

			req := httptest.NewRequest(http.MethodGet, "/-/model-catalogue/source", nil)
			req.Header.Set("Authorization", "Bearer admin-secret")
			w := httptest.NewRecorder()
			srv.httpServer.Handler.ServeHTTP(w, req)
			if w.Code != http.StatusOK {
				t.Fatalf("status %d: %s", w.Code, w.Body.String())
			}
			for _, secret := range []string{"sigsecret", "credsecret", "toksecret", "googsecret", "hunter2"} {
				if strings.Contains(w.Body.String(), secret) {
					t.Errorf("the endpoint carries the credential %q: %s", secret, w.Body.String())
				}
			}
		})
	}
}
