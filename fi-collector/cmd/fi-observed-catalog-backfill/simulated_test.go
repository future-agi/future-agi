package main

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"reflect"
	"strings"
	"testing"
	"time"

	"github.com/future-agi/future-agi/fi-collector/pkg/observedcatalog"
)

func simulatedPageRow(id string, flag any, attrs map[string]string) map[string]any {
	row := policyRow()
	row["id"], row[simulatedResourceColumn], row["attrs_string"] = id, flag, attrs
	return row
}

func hasBatchKey(batch observedcatalog.Batch, key string) bool {
	for _, row := range batch.Keys {
		if row.AttributeKey == key {
			return true
		}
	}
	return false
}

func TestBuildPageSkipsSimulatedRows(t *testing.T) {
	start := time.Date(2026, 1, 1, 12, 0, 0, 0, time.UTC)
	limits := observedcatalog.DefaultLimits()
	resource := simulatedPageRow("resource", 1, map[string]string{"only_resource": "x"})
	span := simulatedPageRow("span", 0, map[string]string{"fi.simulator.call_execution_id": "call-1", "only_span": "y"})
	plain := simulatedPageRow("plain", 0, map[string]string{"real": "z"})

	want, _, err := buildPage([]map[string]any{plain}, exampleScope(), start, start.Add(time.Hour), limits)
	if err != nil || len(want.Keys) == 0 || len(want.Values) == 0 {
		t.Fatalf("baseline: %v", err)
	}
	got, report, err := buildPage([]map[string]any{resource, span, plain}, exampleScope(), start, start.Add(time.Hour), limits)
	if err != nil || !reflect.DeepEqual(got, want) || report.PolicyExclusions != 0 || len(report.Quarantined) != 0 {
		t.Fatalf("marked rows reached the catalog: %+v %v", report, err)
	}
	if hasBatchKey(got, "only_resource") || hasBatchKey(got, "only_span") || !hasBatchKey(got, "real") {
		t.Fatalf("wrong keys: %+v", got.Keys)
	}

	empty, report, err := buildPage([]map[string]any{resource, span}, exampleScope(), start, start.Add(time.Hour), limits)
	if err != nil || len(empty.Keys) != 0 || len(empty.Values) != 0 || report.PolicyExclusions != 0 {
		t.Fatalf("all-marked page: %+v %+v %v", empty, report, err)
	}
}

func TestBuildPageAcceptsDecodedFlagForms(t *testing.T) {
	start := time.Date(2026, 1, 1, 12, 0, 0, 0, time.UTC)
	// selectRows decodes numbers with UseNumber, so this is the only form a
	// stored flag arrives in.
	for _, flag := range []any{json.Number("1")} {
		row := simulatedPageRow("resource", flag, map[string]string{"only_resource": "x"})
		batch, _, err := buildPage([]map[string]any{row}, exampleScope(), start, start.Add(time.Hour), observedcatalog.DefaultLimits())
		if err != nil || len(batch.Keys) != 0 || len(batch.Values) != 0 {
			t.Fatalf("flag %#v not read as marked: %v", flag, err)
		}
	}
}

func TestBuildPageRejectsMissingOrInvalidFlag(t *testing.T) {
	start := time.Date(2026, 1, 1, 12, 0, 0, 0, time.UTC)
	for name, mutate := range map[string]func(map[string]any){
		"missing": func(r map[string]any) { delete(r, simulatedResourceColumn) },
		"null":    func(r map[string]any) { r[simulatedResourceColumn] = nil },
		"two":     func(r map[string]any) { r[simulatedResourceColumn] = 2 },
		"negative": func(r map[string]any) {
			r[simulatedResourceColumn] = -1
		},
		"text":   func(r map[string]any) { r[simulatedResourceColumn] = "true" },
		"empty":  func(r map[string]any) { r[simulatedResourceColumn] = "" },
		"number": func(r map[string]any) { r[simulatedResourceColumn] = json.Number("2") },
	} {
		t.Run(name, func(t *testing.T) {
			row := simulatedPageRow("plain", 0, map[string]string{"real": "z"})
			mutate(row)
			if _, _, err := buildPage([]map[string]any{row}, exampleScope(), start, start.Add(time.Hour), observedcatalog.DefaultLimits()); err == nil {
				t.Fatal("row without a valid simulation flag was accepted")
			}
		})
	}
}

func TestBuildPageValidatesMarkedRowsLikeUnmarkedOnes(t *testing.T) {
	start := time.Date(2026, 1, 1, 12, 0, 0, 0, time.UTC)
	for name, mutate := range map[string]func(map[string]any){
		"missing typed map": func(r map[string]any) { delete(r, "attrs_number") },
		"invalid overflow":  func(r map[string]any) { r["attributes_extra"] = "not json" },
	} {
		t.Run(name, func(t *testing.T) {
			plain := simulatedPageRow("plain", 0, map[string]string{"real": "z"})
			marked := simulatedPageRow("marked", 1, map[string]string{"real": "z"})
			mutate(plain)
			mutate(marked)
			_, _, plainErr := buildPage([]map[string]any{plain}, exampleScope(), start, start.Add(time.Hour), observedcatalog.DefaultLimits())
			_, _, markedErr := buildPage([]map[string]any{marked}, exampleScope(), start, start.Add(time.Hour), observedcatalog.DefaultLimits())
			if plainErr == nil || markedErr == nil || plainErr.Error() != markedErr.Error() {
				t.Fatalf("marked row validated differently: %v vs %v", markedErr, plainErr)
			}
		})
	}
}

func TestPayloadSelectReadsTheSimulationFlag(t *testing.T) {
	var sql string
	row := policyRow()
	delete(row, simulatedResourceColumn)
	row["_version"] = "1"
	payload, _ := json.Marshal(row)
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		sql = string(body)
		w.Write(payload)
	}))
	defer server.Close()
	reader, err := newSourceReader(server.URL, "source", "readonly", "test")
	if err != nil {
		t.Fatal(err)
	}
	key, _ := rowKey(row)
	hour := time.Date(2026, 1, 1, 12, 0, 0, 0, time.UTC)
	rows, err := reader.payloadPage(context.Background(), exampleScope().ProjectID, hour, []physicalKey{key})
	if err != nil {
		t.Fatal(err)
	}
	for _, want := range []string{"resource_attrs.fi.simulation.origin IS NOT NULL", "AS resource_simulation_origin_present"} {
		if !strings.Contains(sql, want) {
			t.Fatalf("payload SELECT does not read the simulation flag: missing %q", want)
		}
	}
	// The flag selects a presence value; it must not filter rows, or the
	// identity checks would see simulated spans as vanished.
	_, filters, _ := strings.Cut(sql, " FROM ")
	if strings.Contains(filters, "resource_attrs") || strings.Contains(filters, simulatedResourceColumn) {
		t.Fatal("payload SELECT filters on the resource mark")
	}
	// A source that does not return the flag column is a changed query, never
	// an unmarked row.
	start := time.Date(2026, 1, 1, 12, 0, 0, 0, time.UTC)
	if _, _, err := buildPage(rows, exampleScope(), start, start.Add(time.Hour), observedcatalog.DefaultLimits()); err == nil {
		t.Fatal("row without the flag column was treated as unmarked")
	}
}

func TestClickHouseFlagReadsAStoredResourceMark(t *testing.T) {
	origin, database := localClickHouse(t)
	scope := exampleScope()
	localSQL(t, origin, `CREATE TABLE `+database+`.spans (
 project_id UUID, org_id Nullable(UUID), observation_type String, service_name String, trace_id String, id String,
 start_time DateTime64(6, 'UTC'), attrs_string Map(String, String), attrs_number Map(String, Float64),
 attrs_bool Map(String, UInt8), attributes_extra JSON, model String, _version UInt64, is_deleted UInt8,
 resource_attrs JSON(max_dynamic_paths=512))
 ENGINE=MergeTree ORDER BY (project_id, observation_type, service_name, toStartOfHour(start_time), trace_id, id)`)
	localSQL(t, origin, `INSERT INTO `+database+`.spans FORMAT JSONEachRow
{"project_id":"`+scope.ProjectID+`","org_id":"`+scope.OrganizationID+`","observation_type":"span","service_name":"s","trace_id":"t","id":"marked","start_time":"2026-01-01 12:01:00","attrs_string":{"only_marked":"x"},"attrs_number":{},"attrs_bool":{},"attributes_extra":{},"model":"","_version":1,"is_deleted":0,"resource_attrs":{"fi.simulation.origin":"simulation","service.name":"s"}}
{"project_id":"`+scope.ProjectID+`","org_id":"`+scope.OrganizationID+`","observation_type":"span","service_name":"s","trace_id":"t","id":"plain","start_time":"2026-01-01 12:02:00","attrs_string":{"real":"z"},"attrs_number":{},"attrs_bool":{},"attributes_extra":{},"model":"","_version":1,"is_deleted":0,"resource_attrs":{"service.name":"s"}}`)
	reader, _ := newSourceReader(origin, database, "test", "test")
	hour := time.Date(2026, 1, 1, 12, 0, 0, 0, time.UTC)
	keys, err := reader.identityPage(context.Background(), scope.ProjectID, hour, physicalKey{}, 10)
	if err != nil || len(keys) != 2 {
		t.Fatalf("identity page: %v %v", keys, err)
	}
	rows, err := reader.payloadPage(context.Background(), scope.ProjectID, hour, keys)
	if err != nil || len(rows) != 2 {
		t.Fatalf("payload page: %v %v", rows, err)
	}
	for _, row := range rows {
		want := map[string]string{"marked": "1", "plain": "0"}[row["id"].(string)]
		if fmt.Sprint(row[simulatedResourceColumn]) != want {
			t.Fatalf("row %v: flag %#v, want %s", row["id"], row[simulatedResourceColumn], want)
		}
	}
	batch, _, err := buildPage(rows, scope, hour, hour.Add(time.Hour), observedcatalog.DefaultLimits())
	if err != nil || hasBatchKey(batch, "only_marked") || !hasBatchKey(batch, "real") {
		t.Fatalf("stored marked span reached the catalog: %+v %v", batch.Keys, err)
	}
}
