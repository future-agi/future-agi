package main

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/future-agi/future-agi/fi-collector/pkg/observedcatalog"
)

func TestReplicatedObservedSinkSingleReplicaWithRestrictedWriter(t *testing.T) {
	origin := strings.Split(os.Getenv("OBS_TEST_REPLICA_URLS"), ",")[0]
	testObservedRestrictedWriter(t, origin, true)
}

func TestClickHouseObservedSinkPlainWithRestrictedWriter(t *testing.T) {
	testObservedRestrictedWriter(t, os.Getenv("OBS_TEST_CH_URL"), false)
}

func testObservedRestrictedWriter(t *testing.T, origin string, replicated bool) {
	t.Helper()
	if origin == "" {
		t.Skip("set the isolated ClickHouse harness URL")
	}
	u, err := url.Parse(origin)
	if err != nil || u.Scheme != "http" || u.Hostname() != "127.0.0.1" || u.Port() == "" {
		t.Fatal("replica must be an explicitly ported loopback endpoint")
	}
	database := fmt.Sprintf("observed_single_test_%d", time.Now().UnixNano())
	localSQL(t, origin, "CREATE DATABASE "+database)
	t.Cleanup(func() { localSQL(t, origin, "DROP DATABASE "+database) })
	ddl, err := os.ReadFile(filepath.Join("..", "..", "..", "futureagi", "tracer", "services", "clickhouse", "v2", "observed_catalog", "schema.sql"))
	if err != nil {
		t.Fatal(err)
	}
	for _, statement := range strings.Split(string(ddl), ";\n") {
		if strings.TrimSpace(statement) != "" {
			if replicated {
				statement = strings.ReplaceAll(statement, "ENGINE = AggregatingMergeTree", "ENGINE = ReplicatedAggregatingMergeTree('/clickhouse/observed-test/"+database+"/{table}', '{replica}')")
			}
			localSQL(t, origin+"?database="+database, statement)
		}
	}
	user := database + "_writer"
	localSQL(t, origin, "CREATE USER "+user+" IDENTIFIED BY 'test'")
	t.Cleanup(func() { localSQL(t, origin, "DROP USER "+user) })
	for _, table := range []string{observedcatalog.KeyTable, observedcatalog.ValueTable} {
		localSQL(t, origin, "GRANT SELECT, INSERT ON "+database+"."+table+" TO "+user)
	}
	if replicated {
		localSQL(t, origin, "GRANT SELECT(database, table, total_replicas) ON system.replicas TO "+user)
	}
	sink, err := observedcatalog.NewClickHouseSink(observedcatalog.ClickHouseConfig{URL: origin, Database: database, Username: user, Password: "test", Timeout: 2 * time.Second})
	if err != nil {
		t.Fatal(err)
	}
	batch, _, err := observedcatalog.Extract(exampleSpan(exampleScope()), observedcatalog.DefaultLimits())
	if err != nil {
		t.Fatal(err)
	}
	if replicated {
		// Reproduce the paused pilot: automatic quorum stored keys but did not
		// acknowledge them. The fixed sink must replay that exact partial batch.
		var body bytes.Buffer
		for _, key := range batch.Keys {
			if err := json.NewEncoder(&body).Encode(key); err != nil {
				t.Fatal(err)
			}
		}
		q := url.Values{"database": {database}, "query": {"INSERT INTO " + observedcatalog.KeyTable + " FORMAT JSONEachRow"},
			"insert_quorum": {"auto"}, "insert_quorum_timeout": {"500"}, "wait_end_of_query": {"1"}}
		req, err := http.NewRequest(http.MethodPost, origin+"?"+q.Encode(), &body)
		if err != nil {
			t.Fatal(err)
		}
		req.SetBasicAuth(user, "test")
		response, err := (&http.Client{Timeout: 3 * time.Second}).Do(req)
		if err != nil {
			t.Fatal(err)
		}
		result, err := io.ReadAll(io.LimitReader(response.Body, 4096))
		response.Body.Close()
		if err != nil || response.StatusCode == http.StatusOK || !strings.Contains(string(result), "UNKNOWN_STATUS_OF_INSERT") {
			t.Fatalf("automatic single-replica quorum failure not reproduced: %d %s %v", response.StatusCode, result, err)
		}
		if localSQL(t, origin, "SELECT count() FROM "+database+"."+observedcatalog.KeyTable) == "0\n" {
			t.Fatal("fixture did not preserve the ambiguous key write")
		}
	}
	for range 2 {
		if err := sink.Insert(context.Background(), batch); err != nil {
			t.Fatal("single replica insert/replay failed", err)
		}
	}
	for _, table := range []string{observedcatalog.KeyTable, observedcatalog.ValueTable} {
		count := localSQL(t, origin, "SELECT count() FROM (SELECT * FROM "+database+"."+table+" GROUP BY ALL)")
		if strings.TrimSpace(count) != "8" {
			t.Fatal("single replica replay changed logical rows", table, count)
		}
	}
}

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

// The delete is read back on replicas 2 and 3 right after replica 1 returns:
// no sleep and no SYSTEM SYNC REPLICA between them.
func TestReplicatedDeleteProjectLeavesNoRowsOnAnyReplica(t *testing.T) {
	raw := os.Getenv("OBS_TEST_REPLICA_URLS")
	if raw == "" {
		t.Skip("set OBS_TEST_REPLICA_URLS for the isolated three-replica harness")
	}
	origins := strings.Split(raw, ",")
	if len(origins) != 3 {
		t.Fatal("three distinct replica URLs required")
	}
	for i, origin := range origins {
		u, err := url.Parse(origin)
		if err != nil || u.Scheme != "http" || u.Hostname() != "127.0.0.1" || u.Port() == "" || (i > 0 && origin == origins[i-1]) {
			t.Fatal("replicas must be distinct explicitly ported loopback endpoints")
		}
	}
	database := fmt.Sprintf("observed_delete_test_%d", time.Now().UnixNano())
	localSQL(t, origins[0], "CREATE DATABASE "+database+" ON CLUSTER observed_test_cluster")
	t.Cleanup(func() { localSQL(t, origins[0], "DROP DATABASE "+database+" ON CLUSTER observed_test_cluster") })
	ddl, err := exec.Command("python3", filepath.Join("..", "..", "test", "observed-catalog", "render_schema.py")).Output()
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
	sink, err := observedcatalog.NewClickHouseSink(observedcatalog.ClickHouseConfig{URL: origins[0], Database: database, Username: "test", Password: "test", Timeout: 10 * time.Second})
	if err != nil {
		t.Fatal(err)
	}
	a := exampleScope()
	b := a
	b.ProjectID = "00000000-0000-4000-8000-000000000031"
	seedCatalog(t, sink, a, "2026-01-01 12:00:00.000000", map[string]string{"a_only": "x", "shared": "z"})
	seedCatalog(t, sink, b, "2026-01-01 12:00:00.000000", map[string]string{"b_only": "x", "shared": "z"})

	// Before the delete every replica holds both projects (this wait is the
	// setup; none follows the delete).
	for _, origin := range origins {
		for _, table := range []string{observedcatalog.KeyTable, observedcatalog.ValueTable} {
			localSQL(t, origin, "SYSTEM SYNC REPLICA "+database+"."+table)
		}
	}
	beforeB := catalogListing(t, origins[0], database, b)
	if beforeB == "--\n" || catalogListing(t, origins[0], database, a) == "--\n" {
		t.Fatal("fixture did not seed both projects")
	}
	for _, origin := range origins {
		if catalogListing(t, origin, database, b) != beforeB {
			t.Fatal("replicas differ before the delete")
		}
	}

	if err := sink.DeleteProject(context.Background(), a); err != nil {
		t.Fatal("replicated delete failed", err)
	}
	for _, origin := range []string{origins[1], origins[2], origins[0]} {
		if got := catalogListing(t, origin, database, a); got != "--\n" {
			t.Fatalf("project A still has rows on %s right after the delete:\n%s", origin, got)
		}
		if got := catalogListing(t, origin, database, b); got != beforeB {
			t.Fatalf("project B changed on %s:\n%s", origin, got)
		}
	}
}

// A user with only SELECT and INSERT, like the live writer, cannot delete: the
// rebuild stops before publishing and records no purge.
func TestDeleteProjectWithoutTheDeleteGrantFailsClosed(t *testing.T) {
	origin, database, admin := localCatalog(t)
	user := database + "_writer"
	localSQL(t, origin, "CREATE USER "+user+" IDENTIFIED BY 'test'")
	t.Cleanup(func() { localSQL(t, origin, "DROP USER "+user) })
	for _, table := range []string{observedcatalog.KeyTable, observedcatalog.ValueTable} {
		localSQL(t, origin, "GRANT SELECT, INSERT ON "+database+"."+table+" TO "+user)
	}
	restricted, err := observedcatalog.NewClickHouseSink(observedcatalog.ClickHouseConfig{URL: origin, Database: database, Username: user, Password: "test", Timeout: 5 * time.Second})
	if err != nil {
		t.Fatal(err)
	}
	a := exampleScope()
	seedCatalog(t, admin, a, "2026-01-01 12:01:00.000000", map[string]string{"real": "z"})
	before := catalogListing(t, origin, database, a)
	if before == "--\n" {
		t.Fatal("fixture did not seed the catalog")
	}

	if err := restricted.DeleteProject(context.Background(), a); err == nil {
		t.Fatal("a user without the delete grant deleted")
	}
	log := &eventLog{}
	cfg, _, publisher := replaceOptions(t, replaceSource(t, oneSpan, false), log)
	cfg.purger = restricted
	err = runSpans(context.Background(), cfg, &testScopeReader{scope: a}, publisher, &bytes.Buffer{})
	if err == nil || !strings.Contains(err.Error(), "nothing published") {
		t.Fatalf("rebuild did not stop at the failed delete: %v", err)
	}
	if log.count("publish") != 0 {
		t.Fatal("published after a failed delete")
	}
	if _, statErr := os.Stat(cfg.checkpointPath); !os.IsNotExist(statErr) || purgeRecorded(cfg.checkpointPath) {
		t.Fatal("checkpoint records a purge that did not happen")
	}
	if got := catalogListing(t, origin, database, a); got != before {
		t.Fatalf("rows changed without the grant:\n%s", got)
	}

	// The same call with a user that may delete succeeds, so the failure above
	// is the grant and not the setup.
	if err := admin.DeleteProject(context.Background(), a); err != nil {
		t.Fatal(err)
	}
	if got := catalogListing(t, origin, database, a); got != "--\n" {
		t.Fatalf("delete left rows:\n%s", got)
	}
}
