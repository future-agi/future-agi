package main

import (
	"context"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/future-agi/future-agi/fi-collector/pkg/observedcatalog"
)

func localCatalog(t *testing.T) (string, string, *observedcatalog.ClickHouseSink) {
	t.Helper()
	origin, database := localClickHouse(t)
	ddl, err := os.ReadFile(filepath.Join("..", "..", "..", "futureagi", "tracer", "services", "clickhouse", "v2", "observed_catalog", "schema.sql"))
	if err != nil {
		t.Fatal(err)
	}
	for _, statement := range strings.Split(string(ddl), ";\n") {
		if strings.TrimSpace(statement) != "" {
			localSQL(t, origin+"?database="+database, statement)
		}
	}
	sink, err := observedcatalog.NewClickHouseSink(observedcatalog.ClickHouseConfig{URL: origin, Database: database, Username: "test", Password: "test"})
	if err != nil {
		t.Fatal(err)
	}
	return origin, database, sink
}

func exampleScope() observedcatalog.Scope {
	return observedcatalog.Scope{OrganizationID: "00000000-0000-4000-8000-000000000010", WorkspaceID: "00000000-0000-4000-8000-000000000020", ProjectID: "00000000-0000-4000-8000-000000000030"}
}

func exampleSpan(scope observedcatalog.Scope) observedcatalog.ScopedSpan {
	return observedcatalog.ScopedSpan{OrganizationID: scope.OrganizationID, WorkspaceID: scope.WorkspaceID, Row: map[string]any{
		"project_id": scope.ProjectID, "org_id": scope.OrganizationID, "start_time": "2026-01-01 12:01:00.000000",
		"attrs_string": map[string]string{"company_id": "001", "unicode": "Straße"},
		"attrs_number": map[string]float64{"latency": 1.25}, "attrs_bool": map[string]uint8{"enabled": 1},
		"attributes_extra": map[string]any{"array": []any{"one", true, 2}, "empty": []any{}, "map": map[string]any{"a": 1}}, "model": "m",
	}}
}

func TestClickHouseObservedIndexReplayBeforeMerges(t *testing.T) {
	origin, database, sink := localCatalog(t)
	for _, table := range []string{observedcatalog.KeyTable, observedcatalog.ValueTable} {
		localSQL(t, origin, "SYSTEM STOP MERGES "+database+"."+table)
	}
	batch, report, err := observedcatalog.Extract(exampleSpan(exampleScope()), observedcatalog.DefaultLimits())
	if err != nil || !report.Complete {
		t.Fatalf("fixture extraction: %v %v", report, err)
	}
	if err := sink.Insert(context.Background(), batch); err != nil {
		t.Fatal(err)
	}
	if err := sink.Insert(context.Background(), batch); err != nil {
		t.Fatal(err)
	}
	later := exampleSpan(exampleScope())
	later.Row["start_time"] = "2026-01-02 12:01:00.000000"
	newer, _, err := observedcatalog.Extract(later, observedcatalog.DefaultLimits())
	if err != nil {
		t.Fatal(err)
	}
	if err := sink.Insert(context.Background(), newer); err != nil {
		t.Fatal(err)
	}
	result := localSQL(t, origin, `SELECT count(), min(first_seen), max(last_seen) FROM (
 SELECT organization_id, workspace_id, project_id, source_kind, attribute_key, attribute_type, value_fingerprint, value_json,
 min(first_seen) AS first_seen, max(last_seen) AS last_seen FROM `+database+`.observed_attribute_values
 GROUP BY organization_id, workspace_id, project_id, source_kind, attribute_key, attribute_type, value_fingerprint, value_json)`)
	if !strings.HasPrefix(result, "8\t2026-01-01 12:01:00.000000\t2026-01-02 12:01:00.000000") {
		t.Fatal("replay changed logical values/times", result)
	}
	keys := localSQL(t, origin, `SELECT count() FROM (SELECT attribute_key FROM `+database+`.observed_attribute_keys GROUP BY attribute_key)`)
	if strings.TrimSpace(keys) != "8" {
		t.Fatal("key-only properties missing", keys)
	}
	foreign := exampleScope()
	foreign.WorkspaceID = "00000000-0000-4000-8000-000000000099"
	other, _, _ := observedcatalog.Extract(exampleSpan(foreign), observedcatalog.DefaultLimits())
	if err := sink.Insert(context.Background(), other); err != nil {
		t.Fatal(err)
	}
	count := localSQL(t, origin, `SELECT uniqExact(tuple(attribute_key, value_json)) FROM `+database+`.observed_attribute_values WHERE workspace_id='`+exampleScope().WorkspaceID+`'`)
	if strings.TrimSpace(count) != "8" {
		t.Fatal("scope leaked", count)
	}
	for _, table := range []string{observedcatalog.KeyTable, observedcatalog.ValueTable} {
		localSQL(t, origin, "SYSTEM START MERGES "+database+"."+table)
	}
}

func TestPersistedRowsUseTheLiveExtractor(t *testing.T) {
	scope := exampleScope()
	span := exampleSpan(scope)
	row := map[string]any{}
	for key, value := range span.Row {
		row[key] = value
	}
	row["is_deleted"] = 0
	direct, _, err := observedcatalog.Extract(span, observedcatalog.DefaultLimits())
	if err != nil {
		t.Fatal(err)
	}
	start := time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC)
	backfilled, err := buildPage([]map[string]any{row}, scope, start, start.Add(24*time.Hour), observedcatalog.DefaultLimits())
	if err != nil {
		t.Fatal(err)
	}
	if len(direct.Keys) != len(backfilled.Keys) || len(direct.Values) != len(backfilled.Values) {
		t.Fatal("different extraction cardinality")
	}
	merged := observedcatalog.Merge(direct, backfilled)
	if len(merged.Keys) != len(direct.Keys) || len(merged.Values) != len(direct.Values) {
		t.Fatal("live/backfill payload mismatch")
	}
}
