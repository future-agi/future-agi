package main

import (
	"log/slog"
	"testing"
	"time"

	"github.com/future-agi/future-agi/fi-collector/pkg/pricing"
)

func envOf(env map[string]string) func(string) (string, bool) {
	return func(k string) (string, bool) { v, ok := env[k]; return v, ok }
}

func TestCatalogueSyncSettings(t *testing.T) {
	for _, tc := range []struct {
		name         string
		env          map[string]string
		wantURL      string
		wantInterval time.Duration
	}{
		{"unset syncs from the default", nil, pricing.DefaultCatalogueURL, 0},
		{"empty URL turns it off", map[string]string{"FI_PRICING_CATALOGUE_URL": ""}, "", 0},
		{"explicit URL", map[string]string{"FI_PRICING_CATALOGUE_URL": "http://x/p.json"}, "http://x/p.json", 0},
		{"a pinned file is not replaced by the default", map[string]string{"FI_PRICING_JSON": "/p.json"}, "", 0},
		{"a pinned file with an explicit URL syncs", map[string]string{"FI_PRICING_JSON": "/p.json", "FI_PRICING_CATALOGUE_URL": "http://x/p.json"}, "http://x/p.json", 0},
		{"interval", map[string]string{"FI_PRICING_SYNC_INTERVAL": "6h"}, pricing.DefaultCatalogueURL, 6 * time.Hour},
		{"bad interval falls back to the default", map[string]string{"FI_PRICING_SYNC_INTERVAL": "soon"}, pricing.DefaultCatalogueURL, 0},
	} {
		t.Run(tc.name, func(t *testing.T) {
			url, interval := catalogueSyncSettings(slog.Default(), envOf(tc.env))
			if url != tc.wantURL || interval != tc.wantInterval {
				t.Errorf("got (%q, %v), want (%q, %v)", url, interval, tc.wantURL, tc.wantInterval)
			}
		})
	}
}
