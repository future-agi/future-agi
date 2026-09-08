package main

import (
	"context"
	"io"
	"log/slog"
	"testing"
	"time"

	"github.com/future-agi/future-agi/fi-collector/pkg/observedcatalog"
)

func TestObservedConfigurationIsSeparateAndUsesSharedLimits(t *testing.T) {
	t.Setenv("FI_CATALOG_MODE", "disabled")
	t.Setenv("FI_PROPERTY_CATALOG_MODE", "disabled")
	t.Setenv("FI_OBSERVED_CATALOG_MODE", "kafka")
	t.Setenv("FI_OBSERVED_CATALOG_KAFKA_BROKERS", "localhost:9092")
	t.Setenv("FI_OBSERVED_CATALOG_KAFKA_TOPIC", "custom-observed")
	t.Setenv("FI_OBSERVED_CATALOG_KAFKA_GROUP", "custom-consumer")
	t.Setenv("FI_OBSERVED_CATALOG_MAX_KEYS_PER_SPAN", "256")
	t.Setenv("FI_OBSERVED_CATALOG_MAX_ARRAY_MEMBERS_PER_SPAN", "512")
	cfg := rootConfig{}
	if err := applyEnvOverrides(slog.Default(), &cfg); err != nil {
		t.Fatal(err)
	}
	if cfg.Observed.Kafka.Topic != "custom-observed" || cfg.Observed.Limits.MaxKeysPerSpan != 256 || cfg.Observed.Limits.MaxArrayMembersPerSpan != 512 {
		t.Fatal("configuration lost")
	}
	t.Setenv("FI_CATALOG_MODE", "kafka")
	if err := applyEnvOverrides(slog.Default(), &rootConfig{}); err == nil {
		t.Fatal("legacy mode accepted")
	}
	t.Setenv("FI_CATALOG_MODE", "disabled")
	cfg.Writer.AsyncInsert = true
	if err := applyEnvOverrides(slog.Default(), &cfg); err == nil {
		t.Fatal("unconfirmed async insert mode accepted")
	}
}

type noPublish struct{}

func (noPublish) Publish(context.Context, observedcatalog.Batch) error { return nil }

func TestReplayStopsWhileFinalSynchronousHandoffRemainsAvailable(t *testing.T) {
	w, err := observedcatalog.NewWriter(observedcatalog.SpoolConfig{Directory: t.TempDir()}, observedcatalog.DefaultLimits())
	if err != nil {
		t.Fatal(err)
	}
	defer w.Close()
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan struct{})
	go func() {
		defer close(done)
		runObservedReplay(ctx, w, noPublish{}, time.Millisecond, slog.New(slog.NewTextHandler(io.Discard, nil)))
	}()
	cancel()
	select {
	case <-done:
	case <-time.After(time.Second):
		t.Fatal("replay did not stop")
	}
	scope := observedcatalog.Scope{OrganizationID: "11111111-1111-4111-8111-111111111111", WorkspaceID: "22222222-2222-4222-8222-222222222222", ProjectID: "33333333-3333-4333-8333-333333333333"}
	batch := observedcatalog.Batch{Keys: []observedcatalog.KeyRow{{Scope: scope, SourceKind: "custom_attribute", AttributeKey: "k", AttributeType: "string", KeyFolded: "k", FirstSeen: "2026-09-08 00:00:00.000000", LastSeen: "2026-09-08 00:00:00.000000"}}}
	if err := w.Enqueue(context.Background(), batch); err != nil {
		t.Fatal("signal prematurely closed spool")
	}
	if n, err := w.Replay(context.Background(), noPublish{}); err != nil || n != 1 {
		t.Fatal("final drain did not persist observations", n, err)
	}
}
