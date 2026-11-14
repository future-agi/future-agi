package server

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/future-agi/future-agi/fi-collector/pkg/observedcatalog"
	"go.opentelemetry.io/collector/pdata/pcommon"
)

// Direct mode (Standalone, Helm): OTLP HTTP, the async source flusher, the
// fsync spool and a restart, then merged replay into the index as a writer
// with only the bootstrap's grants. The same spool records inserted one by
// one, as the Kafka consumer does, must give identical index contents.
// Authentication is a synthetic trusted result, as in the Kafka test.
func TestClickHouseDirectObservedCatalogFromOTLPHTTP(t *testing.T) {
	origin := observedIntegrationOrigin(t)
	admin, adminPassword := observedTestCredentials()
	ctx, cancel := context.WithTimeout(context.Background(), 45*time.Second)
	defer cancel()
	suffix := fmt.Sprint(time.Now().UnixNano())
	sourceDB, directDB, consumerDB := "observed_direct_source_"+suffix, "observed_direct_catalog_"+suffix, "observed_direct_consumer_"+suffix
	observedDatabases(t, origin, sourceDB, directDB, consumerDB)
	// The writer the Standalone and Helm bootstraps create: SELECT and INSERT
	// on the two index tables, nothing else.
	writer := directDB + "_writer"
	observedLocalSQL(t, origin, "default", "CREATE USER "+writer+" IDENTIFIED WITH sha256_password BY '"+suffix+"'")
	t.Cleanup(func() { observedLocalSQL(t, origin, "default", "DROP USER "+writer) })
	for _, table := range []string{observedcatalog.KeyTable, observedcatalog.ValueTable} {
		observedLocalSQL(t, origin, "default", "GRANT SELECT, INSERT ON "+directDB+"."+table+" TO "+writer)
	}

	smoke := postObservedOTLPSmoke(t, ctx, origin, sourceDB, "observed-direct-smoke", func(_, span pcommon.Map) {
		span.PutStr("gen_ai.request.model", "direct-model")
	})

	// What the Kafka consumer writes: each spool record, decoded and inserted.
	records, err := filepath.Glob(filepath.Join(smoke.spoolCfg.Directory, "observed-*.json"))
	if err != nil || len(records) == 0 {
		t.Fatal("no spooled observations", err)
	}
	consumer, err := observedcatalog.NewClickHouseSink(observedcatalog.ClickHouseConfig{URL: origin, Database: consumerDB, Username: admin, Password: adminPassword})
	if err != nil {
		t.Fatal(err)
	}
	for _, record := range records {
		raw, err := os.ReadFile(record)
		if err != nil {
			t.Fatal(err)
		}
		batch, err := observedcatalog.Decode(raw)
		if err != nil {
			t.Fatal(err)
		}
		if err := consumer.Insert(ctx, batch); err != nil {
			t.Fatal(err)
		}
	}

	// Restart, then replay as the collector does in direct mode.
	smoke.restartSpool(t)
	sink, err := observedcatalog.NewClickHouseSink(observedcatalog.ClickHouseConfig{URL: origin, Database: directDB, Username: writer, Password: suffix})
	if err != nil {
		t.Fatal(err)
	}
	if n, err := smoke.spool.ReplayMerged(ctx, sink); err != nil || n != len(records) {
		t.Fatalf("direct replay: %d of %d, %v", n, len(records), err)
	}
	if n, err := smoke.spool.ReplayMerged(ctx, sink); err != nil || n != 0 {
		t.Fatalf("acknowledged records retained: %d %v", n, err)
	}

	if got := observedLocalSQL(t, origin, directDB, "SELECT count() FROM observed_attribute_values WHERE startsWith(attribute_key, 'customer.')"); got != "6" {
		t.Fatal("custom values missing", got)
	}
	if got := observedLocalSQL(t, origin, directDB, "SELECT count() FROM observed_attribute_keys WHERE startsWith(attribute_key, 'customer.')"); got != "5" {
		t.Fatal("empty array key or typed key missing", got)
	}
	if got := observedLocalSQL(t, origin, directDB, "SELECT attribute_type, value_json FROM observed_attribute_values WHERE attribute_key = 'customer.score' OR attribute_key = 'customer.enabled' ORDER BY attribute_type"); got != "boolean\ttrue\nnumber\t1.5" {
		t.Fatal("scalar types lost", got)
	}
	if got := observedLocalSQL(t, origin, directDB, "SELECT source_kind, value_json FROM observed_attribute_values WHERE attribute_key = 'model'"); got != "system_attribute\t\"direct-model\"" {
		t.Fatal("system model suggestion missing", got)
	}
	smoke.requireTrustedScope(t, origin, directDB)
	for _, table := range []string{observedcatalog.KeyTable, observedcatalog.ValueTable} {
		logical := "SELECT * EXCEPT (first_seen, last_seen), min(first_seen), max(last_seen) FROM %s." + table + " GROUP BY ALL ORDER BY ALL FORMAT JSONEachRow"
		direct := observedLocalSQL(t, origin, "default", fmt.Sprintf(logical, directDB))
		viaConsumer := observedLocalSQL(t, origin, "default", fmt.Sprintf(logical, consumerDB))
		if direct == "" || direct != viaConsumer {
			t.Fatalf("%s differs from the Kafka consumer's rows:\n%s\n---\n%s", table, direct, viaConsumer)
		}
	}
}
