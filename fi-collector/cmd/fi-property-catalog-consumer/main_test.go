package main

import (
	"context"
	"strings"
	"testing"
)

func TestConsumerRejectsLegacyModeWithoutContactingKafka(t *testing.T) {
	err := run(context.Background(), func(key string) string {
		if key == "FI_PROPERTY_CATALOG_MODE" {
			return "sequencer"
		}
		return ""
	})
	if err == nil || !strings.Contains(err.Error(), "obsolete") {
		t.Fatal(err)
	}
}

func TestConsumerRequiresExplicitDestination(t *testing.T) {
	err := run(context.Background(), func(key string) string {
		if key == "FI_OBSERVED_CATALOG_KAFKA_BROKERS" {
			return "localhost:9092"
		}
		return ""
	})
	if err == nil || !strings.Contains(err.Error(), "ClickHouse URL") {
		t.Fatal(err)
	}
}
