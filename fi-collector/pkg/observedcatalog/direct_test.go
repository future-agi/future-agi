package observedcatalog

import (
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"reflect"
	"sort"
	"strings"
	"sync"
	"testing"
	"time"
)

func TestDirectModeReadsTheSinkConfigurationNotKafka(t *testing.T) {
	env := map[string]string{
		"FI_OBSERVED_CATALOG_MODE": "direct", "FI_OBSERVED_CATALOG_CH_URL": "http://clickhouse:8123",
		"FI_OBSERVED_CATALOG_CH_DATABASE": "property_catalog", "FI_OBSERVED_CATALOG_CH_USERNAME": "observed_catalog_writer",
		"FI_OBSERVED_CATALOG_CH_PASSWORD": "secret", "FI_OBSERVED_CATALOG_MAX_KEYS_PER_SPAN": "64",
		// Ignored without Kafka: a stale broker list must not be required or used.
		"FI_OBSERVED_CATALOG_KAFKA_BROKERS": "",
	}
	getenv := func(key string) string { return env[key] }
	r, err := RuntimeFromEnv(RuntimeConfig{}, getenv)
	if err != nil {
		t.Fatal(err)
	}
	want := ClickHouseConfig{URL: "http://clickhouse:8123", Database: "property_catalog", Username: "observed_catalog_writer", Password: "secret", Timeout: 10 * time.Second}
	if r.Mode != "direct" || r.ClickHouse != want || len(r.Kafka.Brokers) != 0 || r.Limits != (Limits{64, 256}) ||
		r.Spool.Directory != "/var/lib/fi-collector/observed-catalog" || r.Spool.MaxFiles != 10000 || r.Spool.MaxBytes != 512<<20 || r.ReplayInterval != time.Second {
		t.Fatalf("direct runtime: %+v", r)
	}
	if _, err := NewClickHouseSink(r.ClickHouse); err != nil {
		t.Fatal("direct configuration does not build the consumer's sink", err)
	}
	// YAML values stay unless the environment overrides them.
	r, err = RuntimeFromEnv(RuntimeConfig{Mode: "direct", ClickHouse: ClickHouseConfig{URL: "http://yaml:8123", Database: "yaml_catalog", Password: "yaml"}}, func(key string) string {
		return map[string]string{"FI_OBSERVED_CATALOG_CH_PASSWORD": "env"}[key]
	})
	if err != nil || r.ClickHouse.URL != "http://yaml:8123" || r.ClickHouse.Database != "yaml_catalog" || r.ClickHouse.Password != "env" {
		t.Fatalf("YAML overlay: %+v %v", r.ClickHouse, err)
	}
	for _, missing := range []string{"FI_OBSERVED_CATALOG_CH_URL", "FI_OBSERVED_CATALOG_CH_DATABASE"} {
		saved := env[missing]
		env[missing] = ""
		// deploy/standalone/bin/start detects direct-mode support by this text.
		if _, err := RuntimeFromEnv(RuntimeConfig{}, getenv); err == nil || !strings.Contains(err.Error(), "FI_OBSERVED_CATALOG_MODE=direct requires") {
			t.Fatal("direct mode without its index accepted", missing, err)
		}
		env[missing] = saved
	}
	for _, mode := range []string{"clickhouse", "Direct", "kafka-direct"} {
		env["FI_OBSERVED_CATALOG_MODE"] = mode
		if _, err := RuntimeFromEnv(RuntimeConfig{}, getenv); err == nil {
			t.Fatal("unknown mode accepted", mode)
		}
	}
	env["FI_OBSERVED_CATALOG_MODE"] = "disabled"
	if r, err := RuntimeFromEnv(RuntimeConfig{}, getenv); err != nil || r.Mode != "disabled" || r.ClickHouse != (ClickHouseConfig{}) {
		t.Fatal("disabled mode read index configuration", r, err)
	}
	env["FI_OBSERVED_CATALOG_MODE"], env["FI_OBSERVED_CATALOG_KAFKA_BROKERS"] = "kafka", "localhost:9092"
	if r, err := RuntimeFromEnv(RuntimeConfig{}, getenv); err != nil || r.ClickHouse != (ClickHouseConfig{}) || len(r.Kafka.Brokers) != 1 {
		t.Fatal("kafka mode changed", r, err)
	}
}

// fakeIndex is a ClickHouse HTTP endpoint that records every JSONEachRow row.
type fakeIndex struct {
	mu      sync.Mutex
	inserts map[string]int
	keys    []KeyRow
	values  []ValueRow
	fail    bool
}

func (f *fakeIndex) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	query := r.URL.Query().Get("query")
	if strings.HasPrefix(query, "SELECT ") {
		fmt.Fprint(w, `{"engine":"AggregatingMergeTree"}`)
		return
	}
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.fail {
		w.WriteHeader(http.StatusServiceUnavailable)
		return
	}
	table := strings.Fields(query)[2]
	f.inserts[table]++
	scanner := bufio.NewScanner(r.Body)
	scanner.Buffer(make([]byte, MaxRecordBytes), MaxRecordBytes)
	for scanner.Scan() {
		var err error
		if table == KeyTable {
			var row KeyRow
			err = json.Unmarshal(scanner.Bytes(), &row)
			f.keys = append(f.keys, row)
		} else {
			var row ValueRow
			err = json.Unmarshal(scanner.Bytes(), &row)
			f.values = append(f.values, row)
		}
		if err != nil {
			w.WriteHeader(http.StatusBadRequest)
			return
		}
	}
}

func directFixture(t *testing.T) (*Writer, *fakeIndex, *ClickHouseSink, []Batch) {
	t.Helper()
	w, err := NewWriter(SpoolConfig{Directory: t.TempDir()}, DefaultLimits())
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { w.Close() })
	index := &fakeIndex{inserts: map[string]int{}}
	server := httptest.NewServer(index)
	t.Cleanup(server.Close)
	sink, err := NewClickHouseSink(ClickHouseConfig{URL: server.URL, Database: "property_catalog", Username: "observed_catalog_writer", Password: "secret"})
	if err != nil {
		t.Fatal(err)
	}
	// Three records: a repeated identity seen later, and a new value.
	later, other := testSpan(), testSpan()
	later.Row["start_time"] = "2026-09-09 01:02:03.123456"
	other.Row["attrs_string"] = map[string]string{"a": "world"}
	var batches []Batch
	for _, span := range []ScopedSpan{testSpan(), later, other} {
		b, _, err := Extract(span, DefaultLimits())
		if err != nil {
			t.Fatal(err)
		}
		if err := w.Enqueue(context.Background(), b); err != nil {
			t.Fatal(err)
		}
		batches = append(batches, b)
	}
	if len(w.files) != 3 {
		t.Fatal("fixture records", len(w.files))
	}
	return w, index, sink, batches
}

func TestReplayMergedWritesEachIndexTableOncePerReplay(t *testing.T) {
	w, index, sink, batches := directFixture(t)
	n, err := w.ReplayMerged(context.Background(), sink)
	if err != nil || n != 3 || len(w.files) != 0 || w.bytes != 0 {
		t.Fatalf("merged replay %d %v, %d files left", n, err, len(w.files))
	}
	if !reflect.DeepEqual(index.inserts, map[string]int{KeyTable: 1, ValueTable: 1}) {
		t.Fatal("one insert per table expected", index.inserts)
	}
	// The same logical rows the Kafka consumer writes record by record.
	if got, want := Merge(Batch{Keys: index.keys, Values: index.values}), Merge(batches...); !reflect.DeepEqual(got, want) {
		t.Fatal("merged replay changed the observations")
	}
	if n, err := w.ReplayMerged(context.Background(), sink); err != nil || n != 0 || index.inserts[KeyTable] != 1 {
		t.Fatal("empty spool wrote again", n, err)
	}
}

func TestReplayKeepsOnePublicationPerRecordForKafka(t *testing.T) {
	w, index, sink, _ := directFixture(t)
	if n, err := w.Replay(context.Background(), sink); err != nil || n != 3 {
		t.Fatal(n, err)
	}
	if !reflect.DeepEqual(index.inserts, map[string]int{KeyTable: 3, ValueTable: 3}) {
		t.Fatal("Replay must publish record by record", index.inserts)
	}
}

func TestReplayMergedRetainsEveryRecordUntilTheIndexConfirms(t *testing.T) {
	w, index, sink, batches := directFixture(t)
	index.fail = true
	if n, err := w.ReplayMerged(context.Background(), sink); err == nil || n != 0 || len(w.files) != 3 {
		t.Fatal("failed index write lost records", n, err, len(w.files))
	}
	w.Close()
	// Restart: the next owner delivers everything the failed replay held.
	w, err := NewWriter(w.cfg, DefaultLimits())
	if err != nil {
		t.Fatal(err)
	}
	defer w.Close()
	index.fail = false
	if n, err := w.ReplayMerged(context.Background(), sink); err != nil || n != 3 || len(w.files) != 0 {
		t.Fatal("restart replay", n, err)
	}
	if got, want := Merge(Batch{Keys: index.keys, Values: index.values}), Merge(batches...); !reflect.DeepEqual(got, want) {
		t.Fatal("retried observations changed")
	}
}

func TestReplayMergedDeliversRecordsBeforeACorruptOne(t *testing.T) {
	w, _, _, _ := directFixture(t)
	names := make([]string, 0, len(w.files))
	for name := range w.files {
		names = append(names, name)
	}
	sort.Strings(names)
	if err := os.WriteFile(filepath.Join(w.cfg.Directory, names[1]), []byte(`{}`), 0600); err != nil {
		t.Fatal(err)
	}
	var published []Batch
	n, err := w.ReplayMerged(context.Background(), publisherFunc(func(_ context.Context, b Batch) error {
		published = append(published, b)
		return nil
	}))
	if err == nil || n != 1 || len(published) != 1 || len(w.files) != 2 {
		t.Fatal("corrupt record skipped or earlier record held back", n, err, len(published), len(w.files))
	}
	if _, kept := w.files[names[1]]; !kept {
		t.Fatal("corrupt record removed")
	}
}

func TestReplayMergedBoundsEachPublication(t *testing.T) {
	w, err := NewWriter(SpoolConfig{Directory: t.TempDir()}, DefaultLimits())
	if err != nil {
		t.Fatal(err)
	}
	defer w.Close()
	scope := Scope{OrganizationID: "11111111-1111-4111-8111-111111111111", WorkspaceID: "22222222-2222-4222-8222-222222222222", ProjectID: "33333333-3333-4333-8333-333333333333"}
	const records, rowsPerRecord = 5, 400
	for r := range records {
		b := Batch{}
		for i := range rowsPerRecord {
			key := fmt.Sprintf("key-%d-%d", r, i)
			b.Keys = append(b.Keys, KeyRow{Scope: scope, SourceKind: "custom_attribute", AttributeKey: key, AttributeType: "string", KeyFolded: key, FirstSeen: "2026-09-08 00:00:00.000000", LastSeen: "2026-09-08 00:00:00.000000"})
		}
		if err := w.Enqueue(context.Background(), b); err != nil {
			t.Fatal(err)
		}
	}
	var sizes []int
	n, err := w.ReplayMerged(context.Background(), publisherFunc(func(_ context.Context, b Batch) error {
		sizes = append(sizes, len(b.Keys)+len(b.Values))
		return nil
	}))
	if err != nil || n != records {
		t.Fatal(n, err)
	}
	// Records join a publication until it holds MaxRecordRows rows: 3 + 2.
	if !reflect.DeepEqual(sizes, []int{3 * rowsPerRecord, 2 * rowsPerRecord}) {
		t.Fatal("unbounded or unmerged publications", sizes)
	}
}

func TestReplayMergedStopsOnCancellationWithoutLosingRecords(t *testing.T) {
	w, _, _, _ := directFixture(t)
	ctx, cancel := context.WithCancel(context.Background())
	n, err := w.ReplayMerged(ctx, publisherFunc(func(ctx context.Context, _ Batch) error {
		cancel()
		return errors.Join(errors.New("index write interrupted"), ctx.Err())
	}))
	if err == nil || n != 0 || len(w.files) != 3 {
		t.Fatal("interrupted write acknowledged", n, err, len(w.files))
	}
}
