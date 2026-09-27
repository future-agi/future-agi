package main

import (
	"context"
	"crypto/sha256"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"path/filepath"
	"reflect"
	"slices"
	"strings"
	"testing"

	"github.com/future-agi/future-agi/fi-collector/pkg/attributecatalog"
	"github.com/future-agi/future-agi/fi-collector/pkg/observedcatalog"
	"golang.org/x/text/cases"
)

var legacyTestSelection = legacySelection{
	Scope: observedcatalog.Scope{
		OrganizationID: "10000000-0000-4000-8000-000000000001",
		WorkspaceID:    "20000000-0000-4000-8000-000000000002",
		ProjectID:      "30000000-0000-4000-8000-000000000003",
	},
	Epoch: 7, Revision: 42, BuildToken: "40000000-0000-4000-8000-000000000004",
}

const legacyTestFirst = "2026-01-01 01:02:03.000004"
const legacyTestLast = "2026-04-01 02:03:04.000005"

type legacySelectFunc func(context.Context, string, url.Values) ([]map[string]any, error)

func (f legacySelectFunc) selectRows(ctx context.Context, sql string, params url.Values) ([]map[string]any, error) {
	return f(ctx, sql, params)
}

func legacyTestScopeRow() map[string]any {
	s := legacyTestSelection
	return map[string]any{
		"organization_id": s.Scope.OrganizationID, "workspace_id": s.Scope.WorkspaceID, "project_id": s.Scope.ProjectID,
		"catalog_epoch": json.Number("7"), "catalog_revision": "42", "build_token": s.BuildToken,
	}
}

func legacyTestDefinition(key, binding string, types ...string) map[string]any {
	resolved := "json"
	if len(types) == 1 {
		resolved = types[0]
	}
	// source_adapters._span_attribute_record + canonicalize_definition shape.
	payload := map[string]any{
		"category": "custom_attribute", "category_rank": 3, "definition_source": "span_attribute_value_catalog",
		"display_name": key, "name": key, "output_type": resolved, "primary_source": "traces",
		"property_id": "custom_attribute:" + key, "property_kind": "custom_attribute", "role": "dimension",
		"source_rank": 0, "source_tokens": []string{"attribute", "span", "traces"},
		"value_adapter": "span_attribute_value", "value_type": resolved,
		"details": map[string]any{"allowed_aggregations": []string{"count", "count_distinct"},
			"attribute_types": types, "attribute_types_exact": true, "data_type": resolved},
	}
	raw, _ := json.Marshal(payload)
	row := legacyTestScopeRow()
	row["binding_id"] = strings.Repeat(binding, 64)
	row["states"] = []any{[]any{key, "custom_attribute", string(raw), legacyTestHash(raw), legacyTestFirst, legacyTestLast,
		json.Number("0"), nil, strings.Repeat("a", 64), strings.Repeat("b", 64), json.Number("1"), key, "custom_attribute:" + key}}
	return row
}

func legacyTestHash(raw []byte) string {
	return fmt.Sprintf("%x", sha256.Sum256(raw))
}

func legacyTestValue(t *testing.T, key, typ string, scalarValue any) map[string]any {
	t.Helper()
	scalar, err := attributecatalog.EncodeScalar(scalarValue)
	if err != nil {
		t.Fatal(err)
	}
	row := legacyTestScopeRow()
	row["source_kind"] = "custom_attribute"
	row["attribute_key"], row["attribute_type"] = key, typ
	row["value_fingerprint"], row["value_json"] = scalar.Fingerprint, scalar.ValueJSON
	row["searches"] = []any{cases.Fold().String(scalar.SearchText)}
	row["first_seen"], row["last_seen"] = legacyTestFirst, legacyTestLast
	return row
}

func legacyAssertQuery(t *testing.T, sql string, params url.Values) {
	t.Helper()
	if !strings.HasPrefix(sql, "SELECT ") || strings.Contains(sql, ";") ||
		strings.Contains(sql, " FINAL") || strings.Contains(sql, " OFFSET") || strings.Contains(sql, "FROM spans") ||
		strings.Contains(sql, "argMax") || strings.Contains(sql, "anyLast") {
		t.Fatalf("unsafe legacy query: %s", sql)
	}
	for key, expected := range legacyTestSelection.params() {
		if !reflect.DeepEqual(params[key], expected) {
			t.Fatalf("selection %s = %v, want %v", key, params[key], expected)
		}
	}
	for _, clause := range []string{"catalog_epoch = {epoch:UInt16}", "catalog_revision = {revision:UInt64}",
		"build_token = {build:UUID}", "LIMIT {limit:UInt32}"} {
		if !strings.Contains(sql, clause) {
			t.Fatalf("query missing %s", clause)
		}
	}
}

func TestLegacyDefinitionOnlyPages(t *testing.T) {
	pages := [][]map[string]any{
		{legacyTestDefinition("empty", "1", "array"), legacyTestDefinition("Straße", "2", "json", "map")},
		{legacyTestDefinition("nested", "3", "map")},
		{},
	}
	calls := 0
	reader := legacySelectFunc(func(_ context.Context, sql string, params url.Values) ([]map[string]any, error) {
		legacyAssertQuery(t, sql, params)
		if calls < 2 {
			if !strings.Contains(sql, "max(source_version)") || !strings.Contains(sql, "groupUniqArray(2)") ||
				!strings.Contains(sql, "visibility_id = {project:UUID}") || strings.Contains(sql, "is_deleted = 0") {
				t.Fatalf("definition conflict/tombstone safeguards missing: %s", sql)
			}
			if calls == 1 && params.Get("param_binding") != strings.Repeat("2", 64) {
				t.Fatal("definition cursor did not advance")
			}
		} else if !strings.Contains(sql, "FROM span_attribute_value_catalog") {
			t.Fatal("did not transition to values")
		}
		page := pages[calls]
		calls++
		return page, nil
	})
	cursor := legacyCursor{}
	var collected observedcatalog.Batch
	for !cursor.Done {
		batch, next, err := importLegacyPage(context.Background(), reader, legacyTestSelection, cursor, 2)
		if err != nil {
			t.Fatal(err)
		}
		collected = observedcatalog.Merge(collected, batch)
		cursor = next
	}
	if calls != 3 || len(collected.Keys) != 4 || len(collected.Values) != 0 {
		t.Fatalf("key-only import lost types: calls=%d batch=%+v", calls, collected)
	}
	for _, key := range collected.Keys {
		if key.FirstSeen != legacyTestFirst || key.LastSeen != legacyTestLast || key.Scope != legacyTestSelection.Scope ||
			(key.AttributeKey == "Straße" && key.KeyFolded != "strasse") {
			t.Fatalf("incorrect key metadata: %+v", key)
		}
	}
	_, done, err := importLegacyPage(context.Background(), reader, legacyTestSelection, cursor, 2)
	if err != nil || done != cursor || calls != 3 {
		t.Fatal("completed cursor performed another read")
	}
}

func TestLegacyDefinitionRejectsUnsafeStates(t *testing.T) {
	tests := map[string]func(map[string]any){
		"deleted":            func(r map[string]any) { r["states"].([]any)[0].([]any)[6] = json.Number("1") },
		"deleted_at":         func(r map[string]any) { r["states"].([]any)[0].([]any)[7] = legacyTestLast },
		"missing_deleted":    func(r map[string]any) { r["states"].([]any)[0].([]any)[6] = nil },
		"conflict":           func(r map[string]any) { r["states"] = append(r["states"].([]any), r["states"].([]any)[0]) },
		"erased":             func(r map[string]any) { r["states"].([]any)[0].([]any)[2] = "" },
		"missing_time":       func(r map[string]any) { r["states"].([]any)[0].([]any)[4] = nil },
		"bad_interval":       func(r map[string]any) { r["states"].([]any)[0].([]any)[4] = "2027-01-01 00:00:00" },
		"bad_hash":           func(r map[string]any) { r["states"].([]any)[0].([]any)[3] = strings.Repeat("0", 64) },
		"name_mismatch":      func(r map[string]any) { r["states"].([]any)[0].([]any)[0] = "other" },
		"wrong_org":          func(r map[string]any) { r["organization_id"] = legacyTestSelection.Scope.ProjectID },
		"wrong_workspace":    func(r map[string]any) { r["workspace_id"] = legacyTestSelection.Scope.ProjectID },
		"wrong_project":      func(r map[string]any) { r["project_id"] = legacyTestSelection.Scope.WorkspaceID },
		"wrong_epoch":        func(r map[string]any) { r["catalog_epoch"] = "8" },
		"wrong_revision":     func(r map[string]any) { r["catalog_revision"] = "41" },
		"wrong_build":        func(r map[string]any) { r["build_token"] = legacyTestSelection.Scope.ProjectID },
		"malformed_revision": func(r map[string]any) { r["catalog_revision"] = "42junk" },
		"missing_states":     func(r map[string]any) { delete(r, "states") },
		"bad_binding":        func(r map[string]any) { r["binding_id"] = "bad" },
	}
	for name, mutate := range tests {
		t.Run(name, func(t *testing.T) {
			row := legacyTestDefinition("x", "1", "map")
			mutate(row)
			reader := legacySelectFunc(func(context.Context, string, url.Values) ([]map[string]any, error) {
				return []map[string]any{row}, nil
			})
			before := legacyCursor{}
			batch, after, err := importLegacyPage(context.Background(), reader, legacyTestSelection, before, 1)
			if err == nil || !batch.Empty() || after != before {
				t.Fatalf("unsafe definition imported: batch=%+v after=%+v err=%v", batch, after, err)
			}
		})
	}
}

func TestLegacyUnsupportedDefinitionJSON(t *testing.T) {
	for _, variant := range []string{"inexact", "missing_types", "unsupported_type", "duplicate_type", "wrong_union", "wrong_source", "wrong_adapter"} {
		t.Run(variant, func(t *testing.T) {
			row := legacyTestDefinition("x", "1", "map")
			state := row["states"].([]any)[0].([]any)
			var payload map[string]any
			if err := json.Unmarshal([]byte(state[2].(string)), &payload); err != nil {
				t.Fatal(err)
			}
			details := payload["details"].(map[string]any)
			switch variant {
			case "inexact":
				details["attribute_types_exact"] = false
			case "missing_types":
				delete(details, "attribute_types")
			case "unsupported_type":
				details["attribute_types"] = []string{"binary"}
			case "duplicate_type":
				details["attribute_types"] = []string{"map", "map"}
			case "wrong_union":
				payload["value_type"] = "number"
			case "wrong_source":
				payload["definition_source"] = "other"
			case "wrong_adapter":
				payload["value_adapter"] = "other"
			}
			raw, _ := json.Marshal(payload)
			state[2], state[3] = string(raw), legacyTestHash(raw)
			if _, _, err := legacyDefinition(row, legacyTestSelection); err == nil {
				t.Fatal("unsupported definition accepted")
			}
		})
	}
}

func TestLegacyValueImportAndExactKeyset(t *testing.T) {
	for _, tc := range []struct {
		name, typ string
		value     any
	}{{"unicode", "string", "Straße"}, {"precise", "number", json.Number("123456789012345678901234567890.125")},
		{"boolean", "boolean", false}, {"member", "array", ""}} {
		t.Run(tc.name, func(t *testing.T) {
			value := legacyTestValue(t, "a'\\\nkey", tc.typ, tc.value)
			calls := 0
			reader := legacySelectFunc(func(_ context.Context, sql string, params url.Values) ([]map[string]any, error) {
				legacyAssertQuery(t, sql, params)
				calls++
				if calls == 1 {
					for _, fragment := range []string{"value_fingerprint, value_json", "{json:String}", "min(first_seen)", "max(last_seen)"} {
						if !strings.Contains(sql, fragment) {
							t.Fatalf("value identity/min/max missing: %s", fragment)
						}
					}
					return []map[string]any{value}, nil
				}
				if calls == 2 {
					if params.Get("param_key0") != "a'\\\nkey" || strings.Contains(sql, "a'\\\nkey") ||
						!strings.Contains(sql, "binding_id IN (SELECT binding_id") {
						t.Fatal("definition lookup did not parameterize exact key or resolve full binding")
					}
					return []map[string]any{legacyTestDefinition("a'\\\nkey", "1", tc.typ)}, nil
				}
				if params.Get("param_json") != value["value_json"] || params.Get("param_fingerprint") != value["value_fingerprint"] ||
					params.Get("param_key") != value["attribute_key"] || params.Get("param_type") != tc.typ {
					t.Fatal("resume cursor lost exact scalar identity")
				}
				return nil, nil
			})
			batch, cursor, err := importLegacyPage(context.Background(), reader, legacyTestSelection, legacyCursor{Values: true}, 1)
			if err != nil || len(batch.Keys) != 1 || len(batch.Values) != 1 || cursor.Done {
				t.Fatalf("value import failed: %+v %+v %v", batch, cursor, err)
			}
			if batch.Values[0].ValueJSON != value["value_json"] || batch.Values[0].FirstSeen != legacyTestFirst || batch.Values[0].LastSeen != legacyTestLast {
				t.Fatalf("value bytes or interval changed: %+v", batch)
			}
			_, cursor, err = importLegacyPage(context.Background(), reader, legacyTestSelection, cursor, 1)
			if err != nil || !cursor.Done || calls != 3 {
				t.Fatalf("value page did not complete: %+v %v", cursor, err)
			}
		})
	}
}

func TestLegacyValueRejectsInvalidPayloads(t *testing.T) {
	for name, mutate := range map[string]func(map[string]any){
		"noncanonical":       func(r map[string]any) { r["value_json"] = "1.0" },
		"trailing_json":      func(r map[string]any) { r["value_json"] = "1 2" },
		"object":             func(r map[string]any) { r["value_json"] = "{}" },
		"null":               func(r map[string]any) { r["value_json"] = "null" },
		"fingerprint":        func(r map[string]any) { r["value_fingerprint"] = strings.Repeat("0", 64) },
		"search":             func(r map[string]any) { r["searches"] = []any{"wrong"} },
		"search_conflict":    func(r map[string]any) { r["searches"] = []any{"1", "wrong"} },
		"type":               func(r map[string]any) { r["attribute_type"] = "string" },
		"oversized":          func(r map[string]any) { r["value_json"] = strings.Repeat("1", observedcatalog.MaxValueBytes+1) },
		"revision":           func(r map[string]any) { r["catalog_revision"] = "43" },
		"unsupported_system": func(r map[string]any) { r["source_kind"] = "system_attribute" },
	} {
		t.Run(name, func(t *testing.T) {
			row := legacyTestValue(t, "x", "number", 1)
			mutate(row)
			calls := 0
			reader := legacySelectFunc(func(context.Context, string, url.Values) ([]map[string]any, error) {
				calls++
				return []map[string]any{row}, nil
			})
			before := legacyCursor{Values: true}
			batch, after, err := importLegacyPage(context.Background(), reader, legacyTestSelection, before, 1)
			if err == nil || !batch.Empty() || after != before || calls != 1 {
				t.Fatalf("bad scalar accepted or cursor advanced: %+v %+v %v", batch, after, err)
			}
		})
	}
}

func TestLegacyValuesCannotResurrectDefinitions(t *testing.T) {
	for _, variant := range []string{"missing", "erased", "tombstone", "conflict", "type_missing", "ambiguous", "other_key"} {
		t.Run(variant, func(t *testing.T) {
			def := legacyTestDefinition("x", "1", "string")
			defs := []map[string]any{def}
			switch variant {
			case "missing":
				defs = nil
			case "erased":
				def["states"].([]any)[0].([]any)[2] = ""
			case "tombstone":
				def["states"].([]any)[0].([]any)[6] = "1"
			case "conflict":
				def["states"] = append(def["states"].([]any), def["states"].([]any)[0])
			case "type_missing":
				defs = []map[string]any{legacyTestDefinition("x", "1", "map")}
			case "ambiguous":
				defs = append(defs, def)
			case "other_key":
				defs = []map[string]any{legacyTestDefinition("other", "1", "string")}
			}
			calls := 0
			reader := legacySelectFunc(func(context.Context, string, url.Values) ([]map[string]any, error) {
				calls++
				if calls == 1 {
					return []map[string]any{legacyTestValue(t, "x", "string", "a")}, nil
				}
				return defs, nil
			})
			before := legacyCursor{Values: true}
			batch, after, err := importLegacyPage(context.Background(), reader, legacyTestSelection, before, 1)
			if err == nil || !batch.Empty() || after != before {
				t.Fatalf("unsafe value resurrected a key: %+v %+v %v", batch, after, err)
			}
		})
	}
}

func TestLegacyBoundsAndOrdering(t *testing.T) {
	for _, size := range []int{-1, 0, 1001} {
		_, _, err := importLegacyPage(context.Background(), legacySelectFunc(func(context.Context, string, url.Values) ([]map[string]any, error) {
			t.Fatal("invalid size made source request")
			return nil, nil
		}), legacyTestSelection, legacyCursor{}, size)
		if err == nil {
			t.Fatalf("accepted size %d", size)
		}
	}
	for _, values := range []bool{false, true} {
		for _, over := range []bool{false, true} {
			row := legacyTestDefinition("x", "1", "string")
			if values {
				row = legacyTestValue(t, "x", "string", "a")
			}
			reader := legacySelectFunc(func(context.Context, string, url.Values) ([]map[string]any, error) {
				return []map[string]any{row, row}, nil
			})
			size := 2
			if over {
				size = 1
			}
			before := legacyCursor{Values: values}
			batch, after, err := importLegacyPage(context.Background(), reader, legacyTestSelection, before, size)
			if err == nil || !batch.Empty() || after != before {
				t.Fatal("oversized or unordered page accepted")
			}
		}
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	reader := legacySelectFunc(func(context.Context, string, url.Values) ([]map[string]any, error) {
		t.Fatal("canceled request read source")
		return nil, nil
	})
	if _, _, err := importLegacyPage(ctx, reader, legacyTestSelection, legacyCursor{}, 1); !errors.Is(err, context.Canceled) {
		t.Fatalf("lost cancellation: %v", err)
	}
}

func TestLegacyRequiresExplicitSelection(t *testing.T) {
	for _, field := range []string{"org", "workspace", "project", "epoch", "revision", "build"} {
		t.Run(field, func(t *testing.T) {
			selection := legacyTestSelection
			switch field {
			case "org":
				selection.Scope.OrganizationID = ""
			case "workspace":
				selection.Scope.WorkspaceID = ""
			case "project":
				selection.Scope.ProjectID = ""
			case "epoch":
				selection.Epoch = 0
			case "revision":
				selection.Revision = 0
			case "build":
				selection.BuildToken = "00000000-0000-0000-0000-000000000000"
			}
			reader := legacySelectFunc(func(context.Context, string, url.Values) ([]map[string]any, error) {
				t.Fatal("invalid selection read source")
				return nil, nil
			})
			if _, _, err := importLegacyPage(context.Background(), reader, selection, legacyCursor{}, 1); err == nil {
				t.Fatal("implicit selection accepted")
			}
		})
	}
}

func TestLegacyUsesSeparateReadOnlySource(t *testing.T) {
	row := legacyTestValue(t, "model", "string", "GPT")
	row["source_kind"] = "system_attribute"
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Query().Get("database") != "old_verified_catalog" || r.URL.Query().Get("readonly") != "1" ||
			r.URL.Query().Get("max_threads") != "1" || r.URL.Query().Get("param_revision") != "42" {
			t.Error("legacy reader used wrong source or missing read limits")
		}
		sql, _ := io.ReadAll(r.Body)
		if !strings.HasPrefix(string(sql), "SELECT ") || !strings.HasSuffix(string(sql), " FORMAT JSONEachRow") {
			t.Errorf("wrong HTTP query: %s", sql)
		}
		if err := json.NewEncoder(w).Encode(row); err != nil {
			t.Error(err)
		}
	}))
	defer server.Close()
	reader, err := newSourceReader(server.URL, "old_verified_catalog", "readonly_user", "local_test")
	if err != nil {
		t.Fatal(err)
	}
	batch, cursor, err := importLegacyPage(context.Background(), reader, legacyTestSelection, legacyCursor{Values: true}, 2)
	if err != nil || len(batch.Values) != 1 || !cursor.Done || batch.Values[0].ValueSearchTextFolded != "gpt" {
		t.Fatalf("HTTP source adapter failed: %+v %+v %v", batch, cursor, err)
	}
}

func legacyInsertDefinition(t *testing.T, origin, database string, row map[string]any, version uint64) {
	t.Helper()
	state := row["states"].([]any)[0].([]any)
	payload := make(map[string]any, len(row)+len(state))
	for key, value := range row {
		if key != "states" && key != "project_id" {
			payload[key] = value
		}
	}
	for i, field := range []string{"source_entity_id", "property_kind", "definition_json", "definition_sha256",
		"first_seen", "last_seen", "is_deleted", "deleted_at", "state_sha256", "source_fingerprint", "projection_version", "name", "property_id"} {
		payload[field] = state[i]
	}
	payload["source_adapter"], payload["visibility_scope"], payload["visibility_id"] = "span_attribute", "project", row["project_id"]
	payload["source_version"] = version
	raw, err := json.Marshal(payload)
	if err != nil {
		t.Fatal(err)
	}
	localSQL(t, origin, "INSERT INTO "+database+".property_definition_catalog FORMAT JSONEachRow\n"+string(raw))
}

func legacyInsertValue(t *testing.T, origin, database string, row map[string]any) {
	t.Helper()
	payload := make(map[string]any, len(row))
	for key, value := range row {
		if key == "searches" {
			payload["value_search_text_folded"] = value.([]any)[0]
		} else {
			payload[key] = value
		}
	}
	raw, err := json.Marshal(payload)
	if err != nil {
		t.Fatal(err)
	}
	localSQL(t, origin, "INSERT INTO "+database+".span_attribute_value_catalog FORMAT JSONEachRow\n"+string(raw))
}

func TestLegacyClickHouseEscapedValuePagination(t *testing.T) {
	origin, database := localClickHouse(t)
	ddl, err := os.ReadFile(filepath.Join("..", "..", "..", "futureagi", "tracer", "services", "clickhouse", "v2", "schema", "025_property_catalog_data.sql"))
	if err != nil {
		t.Fatal(err)
	}
	for _, statement := range strings.Split(string(ddl), ";\n") {
		if strings.TrimSpace(statement) != "" {
			localSQL(t, origin+"?database="+database, statement)
		}
	}
	// Deliberately insert out of lexical order. Literal escape sequences and
	// control bytes must stay distinct in definition lookups and keyset params.
	keys := []string{`key\n`, "key\\\t", "key\n", "a"}
	values := []string{`line\nend`, "line\nend", "line\\\nend", "plain"}
	var want []observedcatalog.ValueRow
	for i, key := range keys {
		legacyInsertDefinition(t, origin, database, legacyTestDefinition(key, fmt.Sprint(i+1), "string"), 1)
		for _, value := range values {
			row := legacyTestValue(t, key, "string", value)
			legacyInsertValue(t, origin, database, row)
			want = append(want, observedcatalog.ValueRow{
				Scope: legacyTestSelection.Scope, SourceKind: "custom_attribute", AttributeKey: key, AttributeType: "string",
				ValueFingerprint: row["value_fingerprint"].(string), ValueJSON: row["value_json"].(string),
				ValueSearchTextFolded: row["searches"].([]any)[0].(string), FirstSeen: legacyTestFirst, LastSeen: legacyTestLast,
			})
		}
	}
	slices.SortFunc(want, func(a, b observedcatalog.ValueRow) int {
		return slices.Compare(
			[]string{a.SourceKind, a.AttributeKey, a.AttributeType, a.ValueFingerprint, a.ValueJSON},
			[]string{b.SourceKind, b.AttributeKey, b.AttributeType, b.ValueFingerprint, b.ValueJSON})
	})
	reader, err := newSourceReader(origin, database, "test", "test")
	if err != nil {
		t.Fatal(err)
	}
	var got []observedcatalog.ValueRow
	definitions := make(map[string]int)
	cursor := legacyCursor{}
	for page := 0; !cursor.Done; page++ {
		if page >= len(keys)+len(want)+2 {
			t.Fatal("escaped keyset did not terminate")
		}
		batch, next, err := importLegacyPage(context.Background(), reader, legacyTestSelection, cursor, 1)
		if err != nil {
			t.Fatalf("page %d (cursor %#v): %v", page, cursor, err)
		}
		if !cursor.Values {
			for _, key := range batch.Keys {
				definitions[key.AttributeKey]++
			}
		}
		if len(batch.Values) > 1 {
			t.Fatalf("page %d exceeded one value: %+q", page, batch.Values)
		}
		for _, value := range batch.Values {
			if len(got) >= len(want) || value != want[len(got)] {
				t.Fatalf("page %d: missing, repeated or byte-altered value at index %d: %+q", page, len(got), value)
			}
			if next.AttributeKey != value.AttributeKey || next.ValueFingerprint != value.ValueFingerprint || next.ValueJSON != value.ValueJSON {
				t.Fatalf("page %d: cursor lost exact value identity: %#v", page, next)
			}
			got = append(got, value) // Do not Merge: it would hide repeated pages.
		}
		// Exercise the persisted-cursor representation at every page boundary.
		saved, err := json.Marshal(next)
		if err != nil {
			t.Fatal(err)
		}
		if err := json.Unmarshal(saved, &cursor); err != nil || cursor != next {
			t.Fatalf("cursor round trip changed bytes: %s: %v", saved, err)
		}
	}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("escaped values missing: got %d, want %d", len(got), len(want))
	}
	for _, key := range keys {
		if definitions[key] != 1 {
			t.Fatalf("definition key %q imported %d times, want once", key, definitions[key])
		}
	}
}

func TestLegacyClickHouseImport(t *testing.T) {
	origin, database := localClickHouse(t)
	ddl, err := os.ReadFile(filepath.Join("..", "..", "..", "futureagi", "tracer", "services", "clickhouse", "v2", "schema", "025_property_catalog_data.sql"))
	if err != nil {
		t.Fatal(err)
	}
	for _, statement := range strings.Split(string(ddl), ";\n") {
		if strings.TrimSpace(statement) != "" {
			localSQL(t, origin+"?database="+database, statement)
		}
	}
	definitions := []map[string]any{
		legacyTestDefinition("empty", "1", "array"), legacyTestDefinition("metadata", "2", "json", "map"),
		legacyTestDefinition("size", "3", "number"), legacyTestDefinition("label", "4", "string"),
		legacyTestDefinition("members", "5", "array"),
	}
	for _, row := range definitions {
		legacyInsertDefinition(t, origin, database, row, 2)
		legacyInsertDefinition(t, origin, database, row, 2) // delivery replay
	}
	// Stale disagreement must not taint or replace a unique latest definition.
	legacyInsertDefinition(t, origin, database, legacyTestDefinition("size", "3", "string"), 1)
	legacyInsertDefinition(t, origin, database, legacyTestDefinition("size", "3", "map"), 1)
	for _, field := range []string{"catalog_epoch", "catalog_revision", "build_token", "organization_id", "workspace_id", "project_id"} {
		row := legacyTestDefinition("foreign", "6", "map")
		if field == "catalog_epoch" || field == "catalog_revision" {
			row[field] = "99"
		} else {
			row[field] = "90000000-0000-4000-8000-000000000009"
		}
		legacyInsertDefinition(t, origin, database, row, 99)
	}
	values := []map[string]any{
		legacyTestValue(t, "size", "number", json.Number("123456789012345678901234567890.125")),
		legacyTestValue(t, "label", "string", "Straße"), legacyTestValue(t, "members", "array", true),
		legacyTestValue(t, "members", "array", ""), legacyTestValue(t, "model", "string", "model-v1"),
	}
	values[4]["source_kind"] = "system_attribute"
	for _, row := range values {
		legacyInsertValue(t, origin, database, row)
	}
	duplicate := legacyTestValue(t, "label", "string", "Straße")
	duplicate["first_seen"], duplicate["last_seen"] = "2025-12-01 00:00:00", "2026-06-01 00:00:00.000006"
	legacyInsertValue(t, origin, database, duplicate)
	for _, field := range []string{"catalog_epoch", "catalog_revision", "build_token", "organization_id", "workspace_id", "project_id"} {
		row := legacyTestValue(t, "foreign", "string", "must-not-import")
		if field == "catalog_epoch" || field == "catalog_revision" {
			row[field] = "99"
		} else {
			row[field] = "90000000-0000-4000-8000-000000000009"
		}
		legacyInsertValue(t, origin, database, row)
	}
	reader, err := newSourceReader(origin, database, "test", "test")
	if err != nil {
		t.Fatal(err)
	}
	var all observedcatalog.Batch
	cursor := legacyCursor{}
	for pages := 0; !cursor.Done; pages++ {
		if pages > 10 {
			t.Fatal("keyset did not terminate")
		}
		batch, next, err := importLegacyPage(context.Background(), reader, legacyTestSelection, cursor, 2)
		if err != nil {
			t.Fatalf("page %d (cursor %+v): %v", pages, cursor, err)
		}
		all = observedcatalog.Merge(all, batch)
		cursor = next
	}
	if len(all.Keys) != 7 || len(all.Values) != 5 {
		t.Fatalf("import lost key-only types or mixed in another tuple: %+v", all)
	}
	for _, row := range all.Values {
		if row.AttributeKey == "label" && (row.FirstSeen != "2025-12-01 00:00:00.000000" || row.LastSeen != "2026-06-01 00:00:00.000006") {
			t.Fatalf("physical duplicate min/max lost: %+v", row)
		}
	}
	// Publish the converted rows into a second unique test database and prove
	// replay preserves the seven key identities, including the key-only types.
	_, target, sink := localCatalog(t)
	for range 2 {
		if err := sink.Insert(context.Background(), all); err != nil {
			t.Fatal(err)
		}
	}
	got := localSQL(t, origin, "SELECT uniqExact(tuple(attribute_key, attribute_type)) FROM "+target+".observed_attribute_keys")
	if strings.TrimSpace(got) != "7" {
		t.Fatal("observed key-only import/replay mismatch", got)
	}
	got = localSQL(t, origin, "SELECT uniqExact(tuple(source_kind, attribute_key, attribute_type, value_fingerprint, value_json)) FROM "+target+".observed_attribute_values")
	if strings.TrimSpace(got) != "5" {
		t.Fatal("observed value import/replay mismatch", got)
	}
	// The newest tombstone is inspected, not filtered away to expose old live
	// state. Then add a same-version live disagreement and require conflict.
	tombstone := legacyTestDefinition("empty", "1", "array")
	tombstone["states"].([]any)[0].([]any)[6] = json.Number("1")
	tombstone["states"].([]any)[0].([]any)[7] = legacyTestLast
	legacyInsertDefinition(t, origin, database, tombstone, 3)
	batch, next, err := importLegacyPage(context.Background(), reader, legacyTestSelection, legacyCursor{}, 2)
	if err == nil || !strings.Contains(err.Error(), "tombstoned") || !batch.Empty() || next != (legacyCursor{}) {
		t.Fatalf("latest tombstone resurrected: %+v %+v %v", batch, next, err)
	}
	legacyInsertDefinition(t, origin, database, definitions[0], 3)
	batch, _, err = importLegacyPage(context.Background(), reader, legacyTestSelection, legacyCursor{}, 2)
	if err == nil || !strings.Contains(err.Error(), "conflicting") || !batch.Empty() {
		t.Fatalf("latest disagreement imported: %+v %v", batch, err)
	}
	// Values cannot bypass a definition that was subsequently tombstoned in
	// the selected history, even when the caller resumes directly into values.
	tombstone = legacyTestDefinition("label", "4", "string")
	tombstone["states"].([]any)[0].([]any)[6] = json.Number("1")
	legacyInsertDefinition(t, origin, database, tombstone, 3)
	before := legacyCursor{Values: true}
	batch, next, err = importLegacyPage(context.Background(), reader, legacyTestSelection, before, 2)
	if err == nil || !strings.Contains(err.Error(), "tombstoned") || !batch.Empty() || next != before {
		t.Fatalf("value resurrected tombstone: %+v %+v %v", batch, next, err)
	}
}
