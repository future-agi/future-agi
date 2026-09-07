package propertycatalog

import (
	"fmt"
	"net/http"
	"strings"
	"testing"
)

// Regression from real CH25.3.14.14 run f4e09bb9218ad874: an explicitly
// submitted parallel=1 was absent in QueryFinish.Settings. Absence alone is
// still unknown; only a completed exact SQL statement supplies that witness.
func TestQueryFinishRequiresPositiveExecutionSettingsWitness(t *testing.T) {
	for _, n := range []int{1, 2} {
		for _, test := range []struct {
			name   string
			change func(map[string]any, string)
			want   bool
		}{
			{"exact-sql-omitted-defaults", func(r map[string]any, _ string) {}, true},
			{"exact-http-appended-lf", func(r map[string]any, _ string) { r["query"] = r["query"].(string) + "\n" }, true},
			{"unapproved-multiple-lf", func(r map[string]any, _ string) { r["query"] = r["query"].(string) + "\n\n" }, false},
			{"unapproved-whitespace-normalization", func(r map[string]any, _ string) { r["query"] = " " + r["query"].(string) }, false},
			{"exact-sql-present-settings", func(r map[string]any, quorum string) {
				r["async_insert"], r["insert_quorum"], r["insert_quorum_parallel"] = "0", quorum, "1"
			}, true},
			{"foreign-setting-async", func(r map[string]any, _ string) { r["async_insert"] = "1" }, false},
			{"foreign-setting-quorum", func(r map[string]any, _ string) { r["insert_quorum"] = "1" }, false},
			{"foreign-setting-parallel", func(r map[string]any, _ string) { r["insert_quorum_parallel"] = "0" }, false},
			{"weaker-sql", func(r map[string]any, _ string) {
				r["query"] = strings.Replace(r["query"].(string), "async_insert=0", "async_insert=1", 1)
			}, false},
			{"truncated-sql", func(r map[string]any, _ string) { r["query"] = r["query"].(string)[:80] }, false},
			{"null-sql", func(r map[string]any, _ string) { r["query"] = nil }, false},
			{"missing-sql", func(r map[string]any, _ string) { delete(r, "query") }, false},
			{"wrong-query-id", func(r map[string]any, _ string) { r["query_id"] = "different" }, false},
			{"query-start", func(r map[string]any, _ string) { r["type"] = "QueryStart" }, false},
			{"exception", func(r map[string]any, _ string) { r["exception_code"] = 242 }, false},
			{"foreign-database", func(r map[string]any, _ string) { r["current_database"] = "default" }, false},
			{"legacy-missing-default-stays-unresolved", func(r map[string]any, q string) {
				r["query"] = fmt.Sprintf("INSERT INTO %s (%s) FORMAT JSONEachRow", DefinitionTable, strings.Join(definitionColumns, ","))
				r["async_insert"], r["insert_quorum"] = "0", q
			}, false},
			{"legacy-all-positive-settings", func(r map[string]any, q string) {
				r["query"] = fmt.Sprintf("INSERT INTO %s (%s) FORMAT JSONEachRow", DefinitionTable, strings.Join(definitionColumns, ","))
				r["async_insert"], r["insert_quorum"], r["insert_quorum_parallel"] = "0", q, "1"
			}, true},
		} {
			t.Run(fmt.Sprintf("N%d/%s", n, test.name), func(t *testing.T) {
				proof, _ := proofHTTPFixture(t, n)
				a := testAttemptIntent()
				a.Endpoint = proof.policy.admission.Members[0].URL
				a.TopologySHA256 = proof.policy.admission.TopologySHA256
				a.QueryID = "45cc77aa-a585-4763-b43e-110cce1f6150"
				a.BodySHA256 = sha256Hex(a.Body)
				quorum := "0"
				if n > 1 {
					quorum = fmt.Sprint(n)
				}
				a.Settings["insert_quorum"] = quorum
				sql, err := exactInsertStatement(a.Table, a.Settings)
				if err != nil {
					t.Fatal(err)
				}
				row := map[string]any{"query_id": a.QueryID, "type": "QueryFinish", "exception_code": 0, "current_database": proof.policy.admission.Database,
					"query": sql, "async_insert": "", "insert_quorum": "", "insert_quorum_parallel": ""}
				test.change(row, quorum)
				proof.reader.client.Transport = roundTripFunc(func(r *http.Request) (*http.Response, error) { return proofJSONResponse(t, row), nil })
				settlement, err := proof.Resolve(boundedWriteTestContext(t), proof.policy, a)
				got := err == nil && settlement.Outcome == "settled_success"
				if got != test.want {
					t.Fatalf("settlement=%+v error=%v want success=%v", settlement, err, test.want)
				}
			})
		}
	}
}

func TestExactInsertSQLRejectsUnsafeOrNoncanonicalSettings(t *testing.T) {
	for _, quorum := range []string{"", "1", "02", "2;SELECT 1", "-1", "4294967296"} {
		a := testAttemptIntent()
		a.Settings["insert_quorum"] = quorum
		if _, err := exactInsertStatement(a.Table, a.Settings); err == nil {
			t.Fatalf("accepted quorum %q", quorum)
		}
	}
}
