package main

import (
	"bytes"
	"encoding/json"
	"log/slog"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/future-agi/future-agi/fi-collector/pkg/propertycatalog"
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
	t.Setenv("FI_CATALOG_CH_DATABASE", "property_catalog_dev")
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
	t.Setenv("FI_CATALOG_CH_DATABASE", "property_catalog_dev")
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

func setUnifiedPropertyCatalogEnv(t *testing.T) {
	t.Helper()
	t.Setenv("FI_PROPERTY_CATALOG_MODE", "kafka")
	t.Setenv("FI_PROPERTY_CATALOG_ENVIRONMENT", "development")
	t.Setenv("FI_PROPERTY_CATALOG_DEV_ACK", propertycatalog.DevelopmentAcknowledgement)
	t.Setenv("FI_PROPERTY_CATALOG_EPOCH", "3")
	t.Setenv("FI_PROPERTY_CATALOG_PROJECTION_VERSION", "1")
	t.Setenv("FI_PROPERTY_CATALOG_PRODUCER_STREAM_ID", "44444444-4444-4444-8444-444444444444")
	t.Setenv(envPropertyCatalogWorkspaceScopeMode, "")
	t.Setenv("FI_PROPERTY_CATALOG_WORKSPACE_ALLOWLIST", "22222222-2222-4222-8222-222222222222")
	t.Setenv("FI_PROPERTY_CATALOG_REVISION_FENCE_FILE", filepath.Join(t.TempDir(), "revision-fence.json"))
	t.Setenv("FI_PROPERTY_CATALOG_SPOOL_DIR", t.TempDir())
	t.Setenv("FI_PROPERTY_CATALOG_KAFKA_BROKERS", "kafka:9092")
	t.Setenv("FI_PROPERTY_CATALOG_KAFKA_TOPIC", "property-catalog-v1-dev")
}

func TestUnifiedPropertyCatalogEnvironmentIsExplicitAndDefaultsToDevClientIdentity(t *testing.T) {
	setUnifiedPropertyCatalogEnv(t)
	var cfg rootConfig
	if err := applyEnvOverrides(slog.Default(), &cfg); err != nil {
		t.Fatal(err)
	}
	if cfg.PropertyCatalog.Mode != propertycatalog.RuntimeKafka ||
		cfg.PropertyCatalog.Environment != propertycatalog.DevelopmentEnvironment ||
		cfg.PropertyCatalog.WorkspaceScopeMode != propertycatalog.WorkspaceScopeStatic ||
		cfg.PropertyCatalog.ProjectionVersion != 1 || len(cfg.PropertyCatalog.WorkspaceAllowlist) != 1 ||
		cfg.PropertyCatalog.Kafka.ClientID != "fi-collector-property-candidate-v1-dev" {
		t.Fatalf("property catalog config=%+v", cfg.PropertyCatalog)
	}
}

func TestUnifiedPropertyCatalogEnvironmentParsesFenceScopedDevelopmentMode(t *testing.T) {
	setUnifiedPropertyCatalogEnv(t)
	t.Setenv("FI_PROPERTY_CATALOG_MODE", string(propertycatalog.RuntimeDirectKafkaDevelopment))
	t.Setenv(envPropertyCatalogWorkspaceScopeMode, string(propertycatalog.WorkspaceScopeRevisionFence))
	t.Setenv("FI_PROPERTY_CATALOG_WORKSPACE_ALLOWLIST", "")
	var cfg rootConfig
	if err := applyEnvOverrides(slog.Default(), &cfg); err != nil {
		t.Fatal(err)
	}
	if cfg.PropertyCatalog.WorkspaceScopeMode != propertycatalog.WorkspaceScopeRevisionFence ||
		len(cfg.PropertyCatalog.WorkspaceAllowlist) != 0 {
		t.Fatalf("revision-fence environment config=%+v", cfg.PropertyCatalog)
	}
}

func TestUnifiedPropertyCatalogEnvironmentRejectsUnsafeFenceScope(t *testing.T) {
	for _, test := range []struct {
		name          string
		scopeMode     string
		production    bool
		keepAllowlist bool
	}{
		{name: "unknown mode", scopeMode: "dynamic"},
		{name: "mixed static allowlist", scopeMode: string(propertycatalog.WorkspaceScopeRevisionFence), keepAllowlist: true},
		{name: "production", scopeMode: string(propertycatalog.WorkspaceScopeRevisionFence), production: true},
	} {
		t.Run(test.name, func(t *testing.T) {
			setUnifiedPropertyCatalogEnv(t)
			t.Setenv(envPropertyCatalogWorkspaceScopeMode, test.scopeMode)
			t.Setenv("FI_PROPERTY_CATALOG_MODE", string(propertycatalog.RuntimeDirectKafkaDevelopment))
			if !test.keepAllowlist {
				t.Setenv("FI_PROPERTY_CATALOG_WORKSPACE_ALLOWLIST", "")
			}
			if test.production {
				t.Setenv("FI_PROPERTY_CATALOG_ENVIRONMENT", propertycatalog.ProductionEnvironment)
				t.Setenv("FI_PROPERTY_CATALOG_DEV_ACK", "")
				t.Setenv("FI_PROPERTY_CATALOG_PROD_ACK", propertycatalog.ProductionAcknowledgement)
				t.Setenv("FI_PROPERTY_CATALOG_KAFKA_TOPIC", "futureagi.prod.property-catalog.v1")
			}
			if err := applyEnvOverrides(slog.Default(), &rootConfig{}); err == nil {
				t.Fatal("unsafe fence-scoped environment was accepted")
			}
		})
	}
}

func TestUnifiedPropertyCatalogAcceptsOnlyExactProductionGate(t *testing.T) {
	setUnifiedPropertyCatalogEnv(t)
	t.Setenv("FI_PROPERTY_CATALOG_ENVIRONMENT", propertycatalog.ProductionEnvironment)
	t.Setenv("FI_PROPERTY_CATALOG_DEV_ACK", "")
	t.Setenv("FI_PROPERTY_CATALOG_PROD_ACK", propertycatalog.ProductionAcknowledgement)
	t.Setenv("FI_PROPERTY_CATALOG_KAFKA_TOPIC", "futureagi.prod.property-catalog.v1")
	var cfg rootConfig
	if err := applyEnvOverrides(slog.Default(), &cfg); err != nil {
		t.Fatal(err)
	}
	if cfg.PropertyCatalog.Environment != propertycatalog.ProductionEnvironment ||
		cfg.PropertyCatalog.ProductionAcknowledgement != propertycatalog.ProductionAcknowledgement ||
		cfg.PropertyCatalog.Kafka.ClientID != "fi-collector-property-candidate-v1-prod" {
		t.Fatalf("production property catalog config=%+v", cfg.PropertyCatalog)
	}
}

func TestUnifiedPropertyCatalogOperationalLimitsAreEnvironmentOverridable(t *testing.T) {
	setUnifiedPropertyCatalogEnv(t)
	t.Setenv(envPropertyCatalogReplayInterval, "2s")
	t.Setenv(envPropertyCatalogShutdownTimeout, "8s")
	t.Setenv(envPropertyCatalogQueueDepth, "32")
	t.Setenv(envPropertyCatalogMaxSpansPerBatch, "4000")
	t.Setenv(envPropertyCatalogMaxKeysPerSpan, "64")
	t.Setenv(envPropertyCatalogMaxArrayMembersPerSpan, "96")
	t.Setenv(envPropertyCatalogMaxEncodedBytesPerSpan, "32768")
	t.Setenv(envPropertyCatalogMaxChunkRows, "1000")
	t.Setenv(envPropertyCatalogMaxChunkBytes, "131072")
	t.Setenv(envPropertyCatalogMaxSpoolFiles, "5000")
	t.Setenv(envPropertyCatalogMaxSpoolBytes, "268435456")
	t.Setenv(envPropertyCatalogMaxCandidateSpans, "256")
	t.Setenv(envPropertyCatalogMaxCandidateBytes, "262144")
	t.Setenv(envPropertyCatalogKafkaDeliveryTimeout, "4s")

	var cfg rootConfig
	if err := applyEnvOverrides(slog.Default(), &cfg); err != nil {
		t.Fatal(err)
	}
	property := cfg.PropertyCatalog
	if property.ReplayInterval != 2*time.Second ||
		property.ShutdownTimeout != 8*time.Second || property.QueueDepth != 32 ||
		property.MaxSpansPerBatch != 4000 || property.MaxKeysPerSpan != 64 ||
		property.MaxArrayMembersPerSpan != 96 ||
		property.MaxEncodedBytesPerSpan != 32768 || property.MaxChunkRows != 1000 ||
		property.MaxChunkBytes != 131072 || property.MaxSpoolFiles != 5000 ||
		property.MaxSpoolBytes != 268435456 || property.MaxCandidateSpans != 256 ||
		property.MaxCandidateBytes != 262144 ||
		property.Kafka.DeliveryTimeout != 4*time.Second {
		t.Fatalf("property catalog limits=%+v", property)
	}
}

func TestUnifiedPropertyCatalogOperationalLimitsRejectInvalidEnvironment(t *testing.T) {
	for name, value := range map[string]string{
		envPropertyCatalogQueueDepth:           "0",
		envPropertyCatalogShutdownTimeout:      "121s",
		envPropertyCatalogMaxChunkRows:         "not-a-number",
		envPropertyCatalogMaxSpoolBytes:        "-1",
		envPropertyCatalogKafkaDeliveryTimeout: "11s",
	} {
		t.Run(name, func(t *testing.T) {
			setUnifiedPropertyCatalogEnv(t)
			t.Setenv(name, value)
			if err := applyEnvOverrides(slog.Default(), &rootConfig{}); err == nil ||
				!strings.Contains(err.Error(), name) {
				t.Fatalf("%s=%q error=%v", name, value, err)
			}
		})
	}
}

func TestUnifiedPropertyCatalogRejectsMismatchedProductionGateAndLegacyCoactivation(t *testing.T) {
	setUnifiedPropertyCatalogEnv(t)
	t.Setenv("FI_PROPERTY_CATALOG_ENVIRONMENT", "production")
	if err := applyEnvOverrides(slog.Default(), &rootConfig{}); err == nil ||
		!strings.Contains(err.Error(), "production acknowledgement") {
		t.Fatalf("production error=%v", err)
	}

	t.Setenv("FI_PROPERTY_CATALOG_ENVIRONMENT", "development")
	t.Setenv("FI_PROPERTY_CATALOG_DEV_ACK", propertycatalog.DevelopmentAcknowledgement)
	t.Setenv("FI_PROPERTY_CATALOG_PROD_ACK", "")
	t.Setenv("FI_CATALOG_MODE", "kafka")
	t.Setenv("FI_CATALOG_ENVIRONMENT", "development")
	t.Setenv("FI_CATALOG_EPOCH", "102")
	t.Setenv("FI_CATALOG_PRODUCER_STREAM_ID", "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
	t.Setenv("FI_CATALOG_SPOOL_DIR", t.TempDir())
	t.Setenv("FI_CATALOG_KAFKA_BROKERS", "kafka:9092")
	t.Setenv("FI_CATALOG_KAFKA_TOPIC", "legacy-catalog-dev")
	if err := applyEnvOverrides(slog.Default(), &rootConfig{}); err == nil ||
		!strings.Contains(err.Error(), "cannot both be enabled") {
		t.Fatalf("coactivation error=%v", err)
	}
}
