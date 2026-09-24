package catalogwriter

import (
	"strings"
	"testing"
	"time"
)

func validRuntimeMode(mode Mode) RuntimeConfig {
	cfg := RuntimeConfig{
		Mode: mode, Environment: "development", CatalogEpoch: 101,
		ProducerStream: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", SpoolDir: "/catalog-wal",
	}
	if mode == ModeDirect {
		cfg.ClickHouse = ClickHouseSinkConfig{
			URL: "http://clickhouse:8123", Database: "property_catalog_dev", Username: "catalog_dev",
		}
	}
	return cfg
}

func TestCatalogModeDefaultsDisabledAndIsClosed(t *testing.T) {
	if mode, err := (RuntimeConfig{}).SelectedMode(); err != nil || mode != ModeDisabled {
		t.Fatalf("zero mode=%q err=%v", mode, err)
	}
	invalid := validRuntimeMode("both")
	if _, err := invalid.SelectedMode(); err == nil || !strings.Contains(err.Error(), "invalid catalog mode") {
		t.Fatalf("invalid mode err=%v", err)
	}
}

func TestCatalogModesAreMutuallyExclusiveAndDevelopmentOnly(t *testing.T) {
	direct := validRuntimeMode(ModeDirect)
	if mode, err := direct.SelectedMode(); err != nil || mode != ModeDirect {
		t.Fatalf("direct mode=%q err=%v", mode, err)
	}
	direct.Kafka = KafkaRuntimeConfig{Brokers: []string{"kafka:9092"}, Topic: "catalog"}
	if _, err := direct.SelectedMode(); err == nil || !strings.Contains(err.Error(), "rejects Kafka") {
		t.Fatalf("mixed direct err=%v", err)
	}
	kafka := validRuntimeMode(ModeKafka)
	kafka.Kafka = KafkaRuntimeConfig{Brokers: []string{"kafka:9092"}, Topic: "catalog"}
	if mode, err := kafka.SelectedMode(); err != nil || mode != ModeKafka {
		t.Fatalf("Kafka mode=%q err=%v", mode, err)
	}
	kafka.Environment = "production"
	if _, err := kafka.SelectedMode(); err == nil || !strings.Contains(err.Error(), "development-only") {
		t.Fatalf("production err=%v", err)
	}
	direct = validRuntimeMode(ModeDirect)
	direct.ReplayInterval = 31 * time.Second
	if _, err := direct.SelectedMode(); err == nil || !strings.Contains(err.Error(), "replay interval") {
		t.Fatalf("replay interval err=%v", err)
	}
}

func TestKafkaProducerModeRejectsClickHouseAndNeedsNoConsumerGroup(t *testing.T) {
	kafka := validRuntimeMode(ModeKafka)
	kafka.Kafka = KafkaRuntimeConfig{Brokers: []string{"kafka:9092"}, Topic: "catalog"}
	if mode, err := kafka.SelectedMode(); err != nil || mode != ModeKafka {
		t.Fatalf("producer-only Kafka mode=%q err=%v", mode, err)
	}
	kafka.ClickHouse.URL = "http://clickhouse:8123"
	if _, err := kafka.SelectedMode(); err == nil || !strings.Contains(err.Error(), "rejects ClickHouse") {
		t.Fatalf("Kafka ClickHouse privilege error=%v", err)
	}
}
