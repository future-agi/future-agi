package main

import (
	"bytes"
	"encoding/json"
	"log/slog"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// logLines parses newline-delimited slog JSON output into a slice of
// decoded records for easy assertions.
func logLines(t *testing.T, buf *bytes.Buffer) []map[string]any {
	t.Helper()
	var out []map[string]any
	for _, line := range strings.Split(strings.TrimSpace(buf.String()), "\n") {
		if line == "" {
			continue
		}
		var m map[string]any
		if err := json.Unmarshal([]byte(line), &m); err != nil {
			t.Fatalf("failed to parse log line %q: %v", line, err)
		}
		out = append(out, m)
	}
	return out
}

// TestLoadPriceTable_BadOverrideLogsWarnNotError proves a bad FI_PRICING_JSON
// override, followed by a successful embedded-snapshot fallback, logs at
// Warn — not Error. Pricing still works on this path, so an Error-level log
// would misreport a working fallback as a failure. The double-failure case
// (embedded snapshot itself unparseable) is not reachable in a test since
// the embedded snapshot is compiled in and always valid; that path keeps
// its Error log by inspection (see loadPriceTable).
func TestLoadPriceTable_BadOverrideLogsWarnNotError(t *testing.T) {
	badPath := filepath.Join(t.TempDir(), "bad.json")
	if err := os.WriteFile(badPath, []byte("not valid json"), 0o644); err != nil {
		t.Fatal(err)
	}

	var buf bytes.Buffer
	log := slog.New(slog.NewJSONHandler(&buf, nil))

	table := loadPriceTable(log, badPath)
	if table == nil {
		t.Fatal("want a non-nil table: the embedded snapshot fallback must succeed")
	}

	var sawWarnForOverride, sawErrorForOverride bool
	for _, rec := range logLines(t, &buf) {
		msg, _ := rec["msg"].(string)
		if !strings.Contains(msg, "FI_PRICING_JSON override load failed") {
			continue
		}
		switch rec["level"] {
		case "WARN":
			sawWarnForOverride = true
		case "ERROR":
			sawErrorForOverride = true
		}
	}
	if !sawWarnForOverride {
		t.Error("want a WARN log for the bad-override/successful-fallback path")
	}
	if sawErrorForOverride {
		t.Error("bad-override/successful-fallback path must not log at ERROR")
	}
}

// TestLoadPriceTable_SkippedEntriesWarns proves that when the loaded price
// table has skipped (malformed) entries, loadPriceTable logs a Warn with
// the skip count.
func TestLoadPriceTable_SkippedEntriesWarns(t *testing.T) {
	path := filepath.Join(t.TempDir(), "prices.json")
	body := `{
		"good-model": {"input_cost_per_token": 0.000001, "output_cost_per_token": 0.000002},
		"bad-model": {"input_cost_per_token": "not-a-number"}
	}`
	if err := os.WriteFile(path, []byte(body), 0o644); err != nil {
		t.Fatal(err)
	}

	var buf bytes.Buffer
	log := slog.New(slog.NewJSONHandler(&buf, nil))

	table := loadPriceTable(log, path)
	if table == nil {
		t.Fatal("want a non-nil table")
	}
	if table.Skipped != 1 {
		t.Fatalf("want Skipped=1, got %d", table.Skipped)
	}

	var sawSkippedWarn bool
	for _, rec := range logLines(t, &buf) {
		msg, _ := rec["msg"].(string)
		if msg == "pricing table loaded with skipped entries" && rec["level"] == "WARN" {
			sawSkippedWarn = true
			if skipped, ok := rec["skipped"].(float64); !ok || skipped != 1 {
				t.Errorf("want skipped=1 in log fields, got %v", rec["skipped"])
			}
		}
	}
	if !sawSkippedWarn {
		t.Error("want a WARN log reporting the skipped-entry count")
	}
}

func TestCatalogEnvironmentOverridesAreFailClosedAndExclusive(t *testing.T) {
	t.Setenv("FI_CATALOG_MODE", "direct")
	t.Setenv("FI_CATALOG_ENVIRONMENT", "development")
	t.Setenv("FI_CATALOG_EPOCH", "101")
	t.Setenv("FI_CATALOG_PRODUCER_STREAM_ID", "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
	t.Setenv("FI_CATALOG_SPOOL_DIR", t.TempDir())
	t.Setenv("FI_CATALOG_CH_URL", "http://clickhouse:8123")
	t.Setenv("FI_CATALOG_CH_DATABASE", "th7247_catalog_dev")
	t.Setenv("FI_CATALOG_CH_USERNAME", "catalog_dev")
	var cfg rootConfig
	if err := applyEnvOverrides(slog.Default(), &cfg); err != nil {
		t.Fatal(err)
	}
	if cfg.Catalog.Mode != "direct" || cfg.Catalog.CatalogEpoch != 101 ||
		cfg.Catalog.ClickHouse.Username != "catalog_dev" {
		t.Fatalf("catalog overrides=%+v", cfg.Catalog)
	}

	cfg.Writer.Username = "catalog_dev"
	if !sameClickHouseIdentity(cfg.Writer.Username, cfg.Catalog.ClickHouse.Username) {
		t.Fatal("shared canonical/catalog identity was not detected")
	}
	if !sameClickHouseIdentity("", "default") {
		t.Fatal("implicit canonical default identity was not detected")
	}
}

func TestCatalogEnvironmentRejectsInvalidEpochAndMixedModes(t *testing.T) {
	t.Setenv("FI_CATALOG_MODE", "direct")
	t.Setenv("FI_CATALOG_EPOCH", "not-a-number")
	if err := applyEnvOverrides(slog.Default(), &rootConfig{}); err == nil ||
		!strings.Contains(err.Error(), "FI_CATALOG_EPOCH") {
		t.Fatalf("epoch error=%v", err)
	}

	t.Setenv("FI_CATALOG_EPOCH", "101")
	t.Setenv("FI_CATALOG_ENVIRONMENT", "development")
	t.Setenv("FI_CATALOG_PRODUCER_STREAM_ID", "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
	t.Setenv("FI_CATALOG_SPOOL_DIR", t.TempDir())
	t.Setenv("FI_CATALOG_CH_URL", "http://clickhouse:8123")
	t.Setenv("FI_CATALOG_CH_DATABASE", "th7247_catalog_dev")
	t.Setenv("FI_CATALOG_CH_USERNAME", "catalog_dev")
	t.Setenv("FI_CATALOG_KAFKA_BROKERS", "kafka-a:9092,kafka-b:9092")
	t.Setenv("FI_CATALOG_KAFKA_TOPIC", "catalog")
	if err := applyEnvOverrides(slog.Default(), &rootConfig{}); err == nil ||
		!strings.Contains(err.Error(), "rejects Kafka") {
		t.Fatalf("mixed-mode error=%v", err)
	}
}

func TestKafkaCatalogEnvironmentRequiresOnlyProducerSettings(t *testing.T) {
	t.Setenv("FI_CATALOG_MODE", "kafka")
	t.Setenv("FI_CATALOG_ENVIRONMENT", "development")
	t.Setenv("FI_CATALOG_EPOCH", "102")
	t.Setenv("FI_CATALOG_PRODUCER_STREAM_ID", "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
	t.Setenv("FI_CATALOG_SPOOL_DIR", t.TempDir())
	t.Setenv("FI_CATALOG_KAFKA_BROKERS", " kafka-a:9092, kafka-b:9092 ")
	t.Setenv("FI_CATALOG_KAFKA_TOPIC", "span-attribute-catalog-dev")
	// Consumer group is owned by the standalone consumer and ignored here.
	t.Setenv("FI_CATALOG_KAFKA_CONSUMER_GROUP", "catalog-consumer")
	var cfg rootConfig
	if err := applyEnvOverrides(slog.Default(), &cfg); err != nil {
		t.Fatal(err)
	}
	if cfg.Catalog.Mode != "kafka" || cfg.Catalog.CatalogEpoch != 102 ||
		len(cfg.Catalog.Kafka.Brokers) != 2 || cfg.Catalog.Kafka.Brokers[0] != "kafka-a:9092" ||
		cfg.Catalog.Kafka.Topic != "span-attribute-catalog-dev" {
		t.Fatalf("Kafka catalog overrides=%+v", cfg.Catalog)
	}
	if cfg.Catalog.ClickHouse.URL != "" || cfg.Catalog.ClickHouse.Username != "" {
		t.Fatalf("Kafka producer carried ClickHouse access: %+v", cfg.Catalog.ClickHouse)
	}
}

func TestKafkaCatalogEnvironmentRejectsClickHouseAccess(t *testing.T) {
	t.Setenv("FI_CATALOG_MODE", "kafka")
	t.Setenv("FI_CATALOG_ENVIRONMENT", "development")
	t.Setenv("FI_CATALOG_EPOCH", "102")
	t.Setenv("FI_CATALOG_PRODUCER_STREAM_ID", "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
	t.Setenv("FI_CATALOG_SPOOL_DIR", t.TempDir())
	t.Setenv("FI_CATALOG_KAFKA_BROKERS", "kafka:9092")
	t.Setenv("FI_CATALOG_KAFKA_TOPIC", "span-attribute-catalog-dev")
	t.Setenv("FI_CATALOG_CH_URL", "http://forbidden:8123")
	if err := applyEnvOverrides(slog.Default(), &rootConfig{}); err == nil ||
		!strings.Contains(err.Error(), "rejects ClickHouse settings") {
		t.Fatalf("Kafka ClickHouse-access error=%v", err)
	}
}
