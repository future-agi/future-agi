package main

import (
	"context"
	"encoding/json"
	"fmt"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/future-agi/future-agi/fi-collector/pkg/observedcatalog"
)

func TestReplicatedObservedSinkUsesSameRowsAndMajorityWrites(t *testing.T) {
	raw := os.Getenv("OBS_TEST_REPLICA_URLS")
	if raw == "" {
		t.Skip("set OBS_TEST_REPLICA_URLS for the isolated three-replica harness")
	}
	origins := strings.Split(raw, ",")
	if len(origins) != 3 {
		t.Fatal("three distinct replica URLs required")
	}
	seen := map[string]bool{}
	for _, origin := range origins {
		u, err := url.Parse(origin)
		if err != nil || u.Scheme != "http" || u.Hostname() != "127.0.0.1" || u.Port() == "" || seen[origin] {
			t.Fatal("replicas must be distinct explicitly ported loopback endpoints")
		}
		seen[origin] = true
	}
	database := fmt.Sprintf("observed_test_%d", time.Now().UnixNano())
	localSQL(t, origins[0], "CREATE DATABASE "+database+" ON CLUSTER observed_test_cluster")
	t.Cleanup(func() { localSQL(t, origins[0], "DROP DATABASE "+database+" ON CLUSTER observed_test_cluster") })
	render := exec.Command("python3", filepath.Join("..", "..", "test", "observed-catalog", "render_schema.py"))
	ddl, err := render.Output()
	if err != nil {
		t.Fatal("application topology rendering failed", err)
	}
	var statements []string
	if err := json.Unmarshal(ddl, &statements); err != nil || len(statements) != 2 {
		t.Fatal("invalid rendered schema", err)
	}
	for _, statement := range statements {
		localSQL(t, origins[0]+"?database="+database, statement)
	}
	for _, origin := range origins {
		engine := localSQL(t, origin, "SELECT groupUniqArray(engine) FROM system.tables WHERE database='"+database+"'")
		if strings.TrimSpace(engine) != "['ReplicatedAggregatingMergeTree']" {
			t.Fatal("wrong topology", engine)
		}
	}
	batch, _, err := observedcatalog.Extract(exampleSpan(exampleScope()), observedcatalog.DefaultLimits())
	if err != nil {
		t.Fatal(err)
	}
	for _, origin := range origins[:2] {
		sink, err := observedcatalog.NewClickHouseSink(observedcatalog.ClickHouseConfig{URL: origin, Database: database, Username: "test", Password: "test", Timeout: 10 * time.Second})
		if err != nil {
			t.Fatal(err)
		}
		if err := sink.Insert(context.Background(), batch); err != nil {
			t.Fatal("replicated insert/replay", err)
		}
	}
	for _, origin := range origins {
		for _, table := range []string{observedcatalog.KeyTable, observedcatalog.ValueTable} {
			localSQL(t, origin, "SYSTEM SYNC REPLICA "+database+"."+table)
		}
		count := localSQL(t, origin, `SELECT uniqExact(tuple(attribute_key,attribute_type,value_json)) FROM `+database+`.observed_attribute_values`)
		if strings.TrimSpace(count) != "8" {
			t.Fatal("replica did not converge", count)
		}
		keyCount := localSQL(t, origin, `SELECT uniqExact(tuple(attribute_key,attribute_type)) FROM `+database+`.observed_attribute_keys`)
		if strings.TrimSpace(keyCount) != "8" {
			t.Fatal("replica missing key-only observation", keyCount)
		}
	}

	// Fail only this fixture table's replication, not containers or unrelated
	// services. No Kafka acknowledgement should be allowed for a non-quorum write.
	for _, origin := range origins[1:] {
		localSQL(t, origin, "SYSTEM STOP FETCHES "+database+"."+observedcatalog.KeyTable)
	}
	restore := func() {
		for _, origin := range origins[1:] {
			localSQL(t, origin, "SYSTEM START FETCHES "+database+"."+observedcatalog.KeyTable)
		}
	}
	t.Cleanup(restore)
	later := exampleSpan(exampleScope())
	later.Row["attrs_string"].(map[string]string)["quorum_retry"] = "after-recovery"
	retry, _, err := observedcatalog.Extract(later, observedcatalog.DefaultLimits())
	if err != nil {
		t.Fatal(err)
	}
	sink, err := observedcatalog.NewClickHouseSink(observedcatalog.ClickHouseConfig{URL: origins[0], Database: database, Username: "test", Password: "test", Timeout: 2 * time.Second})
	if err != nil {
		t.Fatal(err)
	}
	if err := sink.Insert(context.Background(), retry); err == nil {
		t.Fatal("acknowledged without majority replication")
	}
	restore()
	if err := sink.Insert(context.Background(), retry); err != nil {
		t.Fatal("quorum replay failed after recovery", err)
	}
	for _, origin := range origins {
		for _, table := range []string{observedcatalog.KeyTable, observedcatalog.ValueTable} {
			localSQL(t, origin, "SYSTEM SYNC REPLICA "+database+"."+table)
		}
		count := localSQL(t, origin, "SELECT uniqExact(tuple(attribute_key,value_json)) FROM "+database+"."+observedcatalog.ValueTable)
		if strings.TrimSpace(count) != "9" {
			t.Fatal("quorum failure/replay lost observations", count)
		}
	}
}
