package main

import (
	"bytes"
	"context"
	"errors"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/future-agi/future-agi/fi-collector/pkg/catalogkafka"
	"github.com/future-agi/future-agi/fi-collector/pkg/catalogwriter"
)

func directRuntimeConfig(t *testing.T, url string) catalogwriter.RuntimeConfig {
	t.Helper()
	return catalogwriter.RuntimeConfig{
		Mode: catalogwriter.ModeDirect, Environment: "development", CatalogEpoch: 101,
		ProducerStream: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", SpoolDir: t.TempDir(),
		ClickHouse: catalogwriter.ClickHouseSinkConfig{
			URL: url, Database: "property_catalog_dev", Username: "catalog_dev",
			RequestTimeout: time.Second,
		},
	}
}

func kafkaRuntimeConfig(t *testing.T) catalogwriter.RuntimeConfig {
	t.Helper()
	return catalogwriter.RuntimeConfig{
		Mode: catalogwriter.ModeKafka, Environment: "development", CatalogEpoch: 102,
		ProducerStream: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", SpoolDir: t.TempDir(),
		Kafka: catalogwriter.KafkaRuntimeConfig{
			Brokers: []string{"kafka:9092"}, Topic: "span-attribute-catalog-dev",
		},
	}
}

type runtimePublisherStub struct{}

func (runtimePublisherStub) Publish(context.Context, catalogkafka.WireEnvelope) error { return nil }

func TestNewDirectCatalogWriterUsesTransportWALAndV3Publisher(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))
	defer server.Close()
	config := directRuntimeConfig(t, server.URL)
	writer, publisher, err := newDirectCatalogWriter(config)
	if err != nil || !writer.Enabled() || publisher == nil {
		t.Fatalf("writer=%v publisher=%v err=%v", writer, publisher, err)
	}
	if got := catalogV3WriterConfig(config); got.MaxJobProjects != 1 ||
		got.MaxJobEncodedBytes != catalogV3MaxJobEncodedBytes ||
		got.MaxChunkEncodedBytes != catalogV3MaxChunkEncodedBytes {
		t.Fatalf("v3 bounds=%+v", got)
	}
	if _, err := writer.Replay(context.Background()); err == nil || !strings.Contains(err.Error(), "ReplayTo") {
		t.Fatalf("direct runtime must use transport ReplayTo, err=%v", err)
	}
	if _, _, err := newDirectCatalogWriter(catalogwriter.RuntimeConfig{Mode: catalogwriter.ModeKafka}); err == nil {
		t.Fatal("direct constructor accepted Kafka mode")
	}
	if _, err := filepath.Abs(config.SpoolDir); err != nil {
		t.Fatal(err)
	}
}

func TestNewKafkaCatalogWriterIsProducerOnly(t *testing.T) {
	config := kafkaRuntimeConfig(t)
	writer, publisher, err := newKafkaCatalogWriter(config, runtimePublisherStub{})
	if err != nil || !writer.Enabled() || publisher == nil {
		t.Fatalf("writer=%v publisher=%v err=%v", writer, publisher, err)
	}
	if _, _, err := newKafkaCatalogWriter(config, nil); err == nil || !strings.Contains(err.Error(), "producer") {
		t.Fatalf("nil producer err=%v", err)
	}
	config.ClickHouse.URL = "http://forbidden:8123"
	if _, _, err := newKafkaCatalogWriter(config, runtimePublisherStub{}); err == nil ||
		!strings.Contains(err.Error(), "rejects ClickHouse") {
		t.Fatalf("Kafka privilege error=%v", err)
	}
}

func TestCatalogReplayStopsOnContext(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))
	defer server.Close()
	writer, publisher, err := newDirectCatalogWriter(directRuntimeConfig(t, server.URL))
	if err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	done := make(chan struct{})
	go func() {
		runDirectCatalogReplay(
			ctx, writer, publisher, time.Millisecond,
			slog.New(slog.NewTextHandler(&bytes.Buffer{}, nil)),
		)
		close(done)
	}()
	select {
	case <-done:
	case <-time.After(time.Second):
		t.Fatal("replay ignored cancellation")
	}
}

func TestLogCatalogSubmissionOverflowReportsCoalescedFailures(t *testing.T) {
	var output bytes.Buffer
	log := slog.New(slog.NewTextHandler(&output, nil))
	logCatalogSubmissionOverflow(log, catalogwriter.SubmissionGapOverflow{
		First: errors.New("first WAL failure"), Last: errors.New("last WAL failure"),
		Suppressed: 7,
	})
	for _, want := range []string{
		"attribute catalog WAL gap overflow",
		"suppressed_count=7",
		`first_err="first WAL failure"`,
		`last_err="last WAL failure"`,
	} {
		if !strings.Contains(output.String(), want) {
			t.Fatalf("log %q does not contain %q", output.String(), want)
		}
	}
}
