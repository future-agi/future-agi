package main

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/future-agi/future-agi/fi-collector/pkg/observedcatalog"
)

// Installs without Kafka (Standalone, Helm) run the collector with
// FI_OBSERVED_CATALOG_MODE=direct; the same environment makes apply write the
// index instead of requiring brokers.
func TestDirectApplyWritesTheIndexWithoutKafka(t *testing.T) {
	env := testEnvironment()
	delete(env, "FI_OBSERVED_CATALOG_KAFKA_BROKERS")
	getenv := func(key string) string { return env[key] }
	args := []string{"--project", exampleScope().ProjectID, "--since", "2026-01-01T00:00:00Z", "--until", "2026-01-02T00:00:00Z", "--apply", "--checkpoint", filepath.Join(t.TempDir(), "progress.json")}
	if _, err := parseOptions(args, getenv); err == nil {
		t.Fatal("Kafka apply accepted without brokers")
	}
	env["FI_OBSERVED_CATALOG_MODE"] = "direct"
	if _, err := parseOptions(args, getenv); err == nil {
		t.Fatal("direct apply accepted without its index")
	}
	env["FI_OBSERVED_CATALOG_CH_URL"] = "http://clickhouse:8123"
	env["FI_OBSERVED_CATALOG_CH_DATABASE"] = "property_catalog"
	env["FI_OBSERVED_CATALOG_CH_USERNAME"] = "observed_catalog_writer"
	cfg, err := parseOptions(args, getenv)
	if err != nil || cfg.index.Database != "property_catalog" || len(cfg.kafka.Brokers) != 0 {
		t.Fatal("direct apply", cfg.index, err)
	}
	other := cfg
	other.index.Database = "other_catalog"
	if scanBinding(cfg, exampleScope()) == scanBinding(other, exampleScope()) {
		t.Fatal("a checkpoint could resume against another index")
	}
	env["FI_OBSERVED_CATALOG_MODE"] = "disabled"
	if _, err := parseOptions(args, getenv); err == nil {
		t.Fatal("apply accepted with the catalog disabled")
	}
	// Preview needs neither Kafka nor the index.
	env["FI_OBSERVED_CATALOG_MODE"] = "direct"
	delete(env, "FI_OBSERVED_CATALOG_CH_URL")
	if _, err := parseOptions(args[:6], getenv); err != nil {
		t.Fatal("preview requires a destination", err)
	}
}

func TestKafkaCheckpointBindingIsUnchanged(t *testing.T) {
	env := testEnvironment()
	cfg, err := parseOptions([]string{"--project", exampleScope().ProjectID, "--since", "2026-01-01T00:00:00Z", "--until", "2026-01-02T00:00:00Z", "--apply", "--checkpoint", "/tmp/progress.json"}, func(k string) string { return env[k] })
	if err != nil {
		t.Fatal(err)
	}
	scope := exampleScope()
	data, _ := json.Marshal([]any{2, cfg.mode, cfg.source.url, cfg.source.database, scope, cfg.since, cfg.until, cfg.kafka.Brokers, cfg.kafka.Topic, cfg.legacyEpoch, cfg.legacyRevision, cfg.legacyBuild, cfg.limits})
	digest := sha256.Sum256(data)
	if scanBinding(cfg, scope) != hex.EncodeToString(digest[:]) {
		t.Fatal("existing Kafka checkpoints would no longer resume")
	}
}

func TestDirectApplyScanWritesBothIndexTables(t *testing.T) {
	scope := exampleScope()
	row := exampleSpan(scope).Row
	row["is_deleted"], row["_version"] = 0, "1"
	row["observation_type"], row["service_name"], row["trace_id"], row["id"] = "span", "s", "t", "a"
	payload, _ := json.Marshal(row)
	source := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Query().Get("param_limit") != "" {
			w.Write([]byte(`{"observation_type":"span","service_name":"s","trace_id":"t","id":"a"}`))
			return
		}
		w.Write(payload)
	}))
	defer source.Close()
	inserted := map[string]int{}
	index := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		query := r.URL.Query().Get("query")
		if strings.HasPrefix(query, "SELECT ") {
			fmt.Fprint(w, `{"engine":"AggregatingMergeTree"}`)
			return
		}
		var body bytes.Buffer
		body.ReadFrom(r.Body)
		inserted[strings.Fields(query)[2]] += strings.Count(body.String(), "\n")
	}))
	defer index.Close()
	reader, _ := newSourceReader(source.URL, "source", "readonly", "test")
	sink, err := observedcatalog.NewClickHouseSink(observedcatalog.ClickHouseConfig{URL: index.URL, Database: "property_catalog", Username: "observed_catalog_writer"})
	if err != nil {
		t.Fatal(err)
	}
	start := time.Date(2026, 1, 1, 12, 0, 0, 0, time.UTC)
	cfg := options{project: scope.ProjectID, source: reader, since: start, until: start.Add(time.Hour), apply: true, checkpointPath: filepath.Join(t.TempDir(), "progress.json"), maxPages: 5, pageSize: 2, limits: observedcatalog.DefaultLimits(), index: observedcatalog.ClickHouseConfig{URL: index.URL, Database: "property_catalog"}}
	if err := runSpans(context.Background(), cfg, &testScopeReader{scope: scope}, sink, &bytes.Buffer{}); err != nil {
		t.Fatal(err)
	}
	live, _, _ := observedcatalog.Extract(exampleSpan(scope), observedcatalog.DefaultLimits())
	if inserted[observedcatalog.KeyTable] != len(live.Keys) || inserted[observedcatalog.ValueTable] != len(live.Values) {
		t.Fatal("backfill wrote different observations than live ingestion", inserted, len(live.Keys), len(live.Values))
	}
}
