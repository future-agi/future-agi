package main

import (
	"context"
	"encoding/hex"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"strings"
	"testing"
	"time"

	"github.com/future-agi/future-agi/fi-collector/pkg/observedcatalog"
)

// These tests deliberately require the isolated local harness. They cannot be
// pointed at an arbitrary production endpoint by copying a credentials file.
func localClickHouse(t *testing.T) (string, string) {
	t.Helper()
	origin := os.Getenv("OBS_TEST_CH_URL")
	if origin == "" {
		t.Skip("set OBS_TEST_CH_URL for isolated ClickHouse integration")
	}
	u, err := url.Parse(origin)
	if err != nil || u.Scheme != "http" || u.Hostname() != "127.0.0.1" || u.Port() == "" {
		t.Fatal("integration ClickHouse must be an explicitly ported loopback HTTP server")
	}
	database := fmt.Sprintf("observed_test_%d", time.Now().UnixNano())
	localSQL(t, origin, "CREATE DATABASE "+database)
	t.Cleanup(func() { localSQL(t, origin, "DROP DATABASE "+database) })
	return origin, database
}

func TestClickHouseStringParametersRoundTrip(t *testing.T) {
	origin, database := localClickHouse(t)
	reader, _ := newSourceReader(origin, database, "test", "test")
	for _, value := range []string{`a\nb`, "a\nb", `"a\nb"`, "quote'\"\\", "\t\r\x00\b\f", "Straße😀"} {
		rows, err := reader.selectRows(context.Background(), "SELECT lower(hex({value:String})) AS encoded", url.Values{"param_value": {value}})
		if err != nil || len(rows) != 1 || rows[0]["encoded"] != hex.EncodeToString([]byte(value)) {
			t.Fatalf("parameter bytes changed for %q: %v %v", value, rows, err)
		}
	}
}

func TestClickHouseMigratedStringOverflowBackfill(t *testing.T) {
	origin, database := localClickHouse(t)
	scope := exampleScope()
	localSQL(t, origin, `CREATE TABLE `+database+`.spans (
 project_id UUID, org_id Nullable(UUID), observation_type String, service_name String, trace_id String, id String,
 start_time DateTime64(6, 'UTC'), attrs_string Map(String,String), attrs_number Map(String,Float64),
 attrs_bool Map(String,UInt8), attributes_extra String, model String, _version UInt64, is_deleted UInt8)
 ENGINE=MergeTree ORDER BY (project_id, observation_type, service_name, toStartOfHour(start_time), trace_id, id)`)
	localSQL(t, origin, `INSERT INTO `+database+`.spans FORMAT JSONEachRow
{"project_id":"`+scope.ProjectID+`","org_id":"`+scope.OrganizationID+`","observation_type":"span","service_name":"s","trace_id":"t","id":"a","start_time":"2026-01-01 12:01:00","attrs_string":{},"attrs_number":{},"attrs_bool":{},"attributes_extra":"{\"items\":[1,true,\"001\"],\"empty\":[]}","model":"","_version":1,"is_deleted":0}`)
	reader, _ := newSourceReader(origin, database, "test", "test")
	hour := time.Date(2026, 1, 1, 12, 0, 0, 0, time.UTC)
	keys, err := reader.identityPage(context.Background(), scope.ProjectID, hour, physicalKey{}, 10)
	if err != nil {
		t.Fatal(err)
	}
	rows, err := reader.payloadPage(context.Background(), scope.ProjectID, hour, keys)
	if err != nil {
		t.Fatal(err)
	}
	batch, err := buildPage(rows, scope, hour, hour.Add(time.Hour), observedcatalog.DefaultLimits())
	if err != nil || len(batch.Keys) != 2 || len(batch.Values) != 3 {
		t.Fatalf("String overflow not normalized like live JSON: %v %v", batch, err)
	}
}

func localSQL(t *testing.T, origin, sql string) string {
	t.Helper()
	req, err := http.NewRequest(http.MethodPost, origin, strings.NewReader(sql))
	if err != nil {
		t.Fatal(err)
	}
	req.SetBasicAuth("test", "test")
	response, err := (&http.Client{Timeout: 30 * time.Second}).Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer response.Body.Close()
	body, err := io.ReadAll(io.LimitReader(response.Body, 4<<20))
	if err != nil || response.StatusCode != 200 || strings.Contains(string(body), "DB::Exception") {
		t.Fatalf("local fixture SQL failed: %s (%v)", body, err)
	}
	return string(body)
}

func TestClickHouseSourceKeysetAndPhysicalVersions(t *testing.T) {
	origin, database := localClickHouse(t)
	localSQL(t, origin, `CREATE TABLE `+database+`.spans (
 project_id UUID, org_id Nullable(UUID), observation_type String, service_name String, trace_id String, id String,
 start_time DateTime64(6, 'UTC'), attrs_string Map(String, String), attrs_number Map(String, Float64),
 attrs_bool Map(String, UInt8), attributes_extra JSON, model String, _version UInt64, is_deleted UInt8)
 ENGINE=MergeTree ORDER BY (project_id, observation_type, service_name, toStartOfHour(start_time), trace_id, id)`)
	project := "00000000-0000-4000-8000-000000000001"
	localSQL(t, origin, `INSERT INTO `+database+`.spans FORMAT JSONEachRow
{"project_id":"`+project+`","observation_type":"span","service_name":"s","trace_id":"t","id":"a","start_time":"2026-01-01 12:01:00","attrs_string":{"id":"001"},"attrs_number":{"n":1.25},"attrs_bool":{"b":1},"attributes_extra":{"items":["a",2,true]},"model":"m","_version":1,"is_deleted":0}
{"project_id":"`+project+`","observation_type":"span","service_name":"s","trace_id":"t","id":"a","start_time":"2026-01-01 12:02:00","attrs_string":{"id":"002"},"attrs_number":{},"attrs_bool":{},"attributes_extra":{},"model":"m","_version":2,"is_deleted":0}
{"project_id":"`+project+`","observation_type":"span","service_name":"s","trace_id":"t","id":"b","start_time":"2026-01-01 12:03:00","attrs_string":{"deleted":"value"},"attrs_number":{},"attrs_bool":{},"attributes_extra":{},"model":"m","_version":3,"is_deleted":1}
{"project_id":"00000000-0000-4000-8000-000000000002","observation_type":"span","service_name":"s","trace_id":"t","id":"a","start_time":"2026-01-01 12:01:00","attrs_string":{"secret":"other-tenant"},"attrs_number":{},"attrs_bool":{},"attributes_extra":{},"model":"m","_version":1,"is_deleted":0}`)
	reader, _ := newSourceReader(origin, database, "test", "test")
	hour := time.Date(2026, 1, 1, 12, 0, 0, 0, time.UTC)
	keys, err := reader.identityPage(context.Background(), project, hour, physicalKey{}, 1)
	if err != nil || len(keys) != 1 || keys[0].Span != "a" {
		t.Fatalf("first page: %v %v", keys, err)
	}
	rows, err := reader.payloadPage(context.Background(), project, hour, keys)
	if err != nil || len(rows) != 1 || rows[0]["_version"] != "2" {
		t.Fatalf("latest hydration: %v %v", rows, err)
	}
	keys, err = reader.identityPage(context.Background(), project, hour, keys[0], 1)
	if err != nil || len(keys) != 1 || keys[0].Span != "b" {
		t.Fatalf("second page: %v %v", keys, err)
	}
	rows, err = reader.payloadPage(context.Background(), project, hour, keys)
	if err != nil || len(rows) != 1 || fmt.Sprint(rows[0]["is_deleted"]) != "1" {
		t.Fatalf("tombstone hydration: %v %v", rows, err)
	}
	empty, err := reader.identityPage(context.Background(), project, hour, keys[0], 1)
	if err != nil || len(empty) != 0 {
		t.Fatal("keyset did not terminate", err)
	}
	count := localSQL(t, origin, "SELECT count() FROM "+database+".spans")
	if strings.TrimSpace(count) != "4" {
		t.Fatal("source rows changed", count)
	}
}
