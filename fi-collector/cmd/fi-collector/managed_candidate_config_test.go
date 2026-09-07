package main

import (
	"io"
	"log/slog"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/future-agi/future-agi/fi-collector/pkg/propertycatalog"
)

func managedCollectorEnvironment(t *testing.T) *slog.Logger {
	t.Helper()
	// Exercise the same loadConfig -> applyEnvOverrides path as main, without
	// inheriting any developer's unrelated FI_* overrides or connecting to IO.
	for _, entry := range os.Environ() {
		name, _, _ := strings.Cut(entry, "=")
		if strings.HasPrefix(name, "FI_") {
			t.Setenv(name, "")
		}
	}
	t.Setenv("FI_PROPERTY_CATALOG_MODE", "kafka")
	t.Setenv("FI_PROPERTY_CATALOG_ENVIRONMENT", "development")
	t.Setenv("FI_PROPERTY_CATALOG_DEV_ACK", propertycatalog.DevelopmentAcknowledgement)
	t.Setenv("FI_PROPERTY_CATALOG_KAFKA_BROKERS", "127.0.0.1:19092")
	t.Setenv("FI_PROPERTY_CATALOG_KAFKA_TOPIC", "futureagi.dev.property-catalog.candidates.v2")
	t.Setenv("FI_PROPERTY_CATALOG_EPOCH", "")
	t.Setenv("FI_PROPERTY_CATALOG_PROJECTION_VERSION", "")
	t.Setenv("FI_PROPERTY_CATALOG_PRODUCER_STREAM_ID", "")
	return slog.New(slog.NewTextHandler(io.Discard, nil))
}

func collectorConfigCandidateVersion(t *testing.T, cfg rootConfig) uint16 {
	t.Helper()
	rows := []propertycatalog.ScopedSpan{{
		OrganizationID: "11111111-1111-4111-8111-111111111111",
		WorkspaceID:    "22222222-2222-4222-8222-222222222222",
		Row: map[string]any{
			"org_id":       "11111111-1111-4111-8111-111111111111",
			"project_id":   "33333333-3333-4333-8333-333333333333",
			"start_time":   "2026-09-05 12:00:00.000000",
			"attrs_string": map[string]string{"plan": "Pro"},
			"attrs_number": map[string]float64{}, "attrs_bool": map[string]uint8{},
			"attributes_extra": map[string]any{},
		},
	}}
	candidates, err := propertycatalog.BuildCandidates(cfg.PropertyCatalog, rows)
	if err != nil || len(candidates) != 1 {
		t.Fatalf("effective collector candidate count=%d err=%v", len(candidates), err)
	}
	return candidates[0].Snapshot().Version
}

func TestCheckedInCollectorYAMLAndManagedEnvironmentProduceV2(t *testing.T) {
	log := managedCollectorEnvironment(t)
	path := filepath.Join("..", "..", "config", "collector.yaml")
	if _, err := os.Stat(path); err != nil {
		t.Fatal(err)
	}
	cfg := loadConfig(log, path)
	if err := applyEnvOverrides(log, &cfg); err != nil {
		t.Fatal(err)
	}
	if cfg.PropertyCatalog.CatalogEpoch != 0 || cfg.PropertyCatalog.ProjectionVersion != 0 || cfg.PropertyCatalog.ProducerStreamID != "" {
		t.Fatalf("checked-in YAML/defaults allocated unified catalog identity: %+v", cfg.PropertyCatalog)
	}
	if version := collectorConfigCandidateVersion(t, cfg); version != propertycatalog.CandidateManagedVersion {
		t.Fatalf("effective default collector emitted v%d, not v2", version)
	}
}

func TestCollectorLegacyYAMLIsNotClearedByAbsentOrEmptyEnvironment(t *testing.T) {
	for _, projection := range []string{"", "  projection_version: 1\n"} {
		t.Run("projection="+projection, func(t *testing.T) {
			log := managedCollectorEnvironment(t)
			path := filepath.Join(t.TempDir(), "collector.yaml")
			raw := "property_catalog:\n  catalog_epoch: 1\n" + projection
			if err := os.WriteFile(path, []byte(raw), 0o600); err != nil {
				t.Fatal(err)
			}
			cfg := loadConfig(log, path)
			err := applyEnvOverrides(log, &cfg)
			if projection == "" {
				if err == nil {
					t.Fatal("partial legacy YAML identity was silently defaulted")
				}
				return
			}
			if err != nil {
				t.Fatal(err)
			}
			if cfg.PropertyCatalog.CatalogEpoch != 1 || cfg.PropertyCatalog.ProjectionVersion != 1 ||
				collectorConfigCandidateVersion(t, cfg) != propertycatalog.CandidateVersion {
				t.Fatal("legacy YAML identity was silently changed by absent environment overrides")
			}
		})
	}
}
