package server

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/future-agi/future-agi/fi-collector/pkg/auth"
	"github.com/future-agi/future-agi/fi-collector/pkg/chwriter"
	"github.com/future-agi/future-agi/fi-collector/pkg/detid"
	"go.opentelemetry.io/collector/pdata/ptrace"
)

const nativeHeaderProject = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"

type nativeProjectResolver struct{ ingestResolver }

func (*nativeProjectResolver) GetOrCreateProject(_ context.Context, _, _, name, _ string) (string, error) {
	switch name {
	case "anthropic-csharp-contract":
		return calProject, nil
	case "header-project":
		return nativeHeaderProject, nil
	default:
		return "", fmt.Errorf("unexpected project name %q", name)
	}
}

// Real HTTP protobuf -> auth middleware/project stamping -> converter -> real
// ClickHouse spans, curated identities and session rollup. Only key/project
// persistence is injected; this is not a PostgreSQL credential or browser test.
// CI runs this against its isolated ClickHouse fixture and rejects skipped runs.
func TestClickHouseNativeAnthropicOTLP(t *testing.T) {
	origin := observedIntegrationOrigin(t)
	for _, version := range []string{"12.56.0", "13.3.0"} {
		for _, scenario := range []string{"content", "concurrent", "tool-content", "tool-metadata", "header-project"} {
			t.Run(version+"/"+scenario, func(t *testing.T) {
				db := fmt.Sprintf("native_anthropic_%d", time.Now().UnixNano())
				observedDatabases(t, origin, db)
				for _, name := range []string{"015_traces_and_trace_dict.sql", "018_trace_sessions.sql"} {
					ddl, _, _ := strings.Cut(observedDDL(t, "schema", name), ";")
					observedLocalSQL(t, origin, db, ddl)
				}
				rollup := observedDDL(t, "schema", "008_per_session_rollup.sql")
				// Captures have fixed timestamps: retention is not under test.
				rollup = strings.ReplaceAll(rollup, "TTL toDateTime(hour_first_seen) + INTERVAL 90 DAY DELETE", "")
				for _, ddl := range strings.Split(rollup, ";") {
					if strings.TrimSpace(ddl) != "" {
						observedLocalSQL(t, origin, db, ddl)
					}
				}
				user, password := observedTestCredentials()
				writer, err := chwriter.New(chwriter.Config{URL: origin, Database: db, Table: "spans", Username: user, Password: password, MaxRetries: 1, RequestTimeout: 10 * time.Second, DeadLetterFile: filepath.Join(t.TempDir(), "dead.jsonl")})
				if err != nil {
					t.Fatal(err)
				}
				t.Cleanup(func() { writer.Close() })
				authenticator, err := auth.NewWithResolver(auth.Config{}, &nativeProjectResolver{}, nil, nil)
				if err != nil {
					t.Fatal(err)
				}
				t.Cleanup(authenticator.Close)
				s := New(Config{}, writer, authenticator, NoopUsageEmitter{}, NoopMetering{})
				mux := http.NewServeMux()
				mux.HandleFunc("/tracer/v1/traces", s.handleHTTPTraces)
				httpServer := httptest.NewServer(authenticator.HTTPMiddleware(mux))
				t.Cleanup(httpServer.Close)

				fixture := scenario
				if fixture == "header-project" {
					fixture = "content"
				}
				body, err := os.ReadFile(filepath.Join("../../../futureagi/tracer/tests/fixtures/anthropic_csharp", version+"-"+fixture+".pb"))
				if err != nil {
					t.Fatal(err)
				}
				traces, err := (&ptrace.ProtoUnmarshaler{}).UnmarshalTraces(body)
				if err != nil {
					t.Fatal(err)
				}
				wantProject := calProject
				for i := 0; i < traces.ResourceSpans().Len(); i++ {
					a := traces.ResourceSpans().At(i).Resource().Attributes()
					a.PutStr("fi.project_id", "00000000-0000-4000-8000-000000000001")
					a.PutStr("fi.org_id", "00000000-0000-4000-8000-000000000002")
					if scenario == "header-project" {
						a.Remove("project_name")
						wantProject = nativeHeaderProject
					}
				}
				body, err = (&ptrace.ProtoMarshaler{}).MarshalTraces(traces)
				if err != nil {
					t.Fatal(err)
				}
				for _, secret := range []string{"invalid", "s"} {
					req, err := http.NewRequest(http.MethodPost, httpServer.URL+"/tracer/v1/traces", bytes.NewReader(body))
					if err != nil {
						t.Fatal(err)
					}
					req.Header.Set("Content-Type", "application/x-protobuf")
					req.Header.Set("X-Api-Key", "k")
					req.Header.Set("X-Secret-Key", secret)
					req.Header.Set("X-Project-Name", "header-project")
					response, err := httpServer.Client().Do(req)
					if err != nil {
						t.Fatal(err)
					}
					io.Copy(io.Discard, response.Body)
					response.Body.Close()
					wantStatus := http.StatusOK
					if secret == "invalid" {
						wantStatus = http.StatusUnauthorized
					}
					if response.StatusCode != wantStatus {
						t.Fatalf("HTTP %d, want %d", response.StatusCode, wantStatus)
					}
				}
				ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
				defer cancel()
				s.drainNow(ctx)
				if stats := writer.Snapshot(); stats.RowsDeadLettered != 0 || stats.RowsInserted < uint64(traces.SpanCount()) {
					t.Fatalf("insert failed: %+v", stats)
				}
				if got := observedLocalSQL(t, origin, db, "SELECT count() FROM spans FINAL"); got != fmt.Sprint(traces.SpanCount()) {
					t.Fatalf("stored rows %s, want %d", got, traces.SpanCount())
				}
				if got := observedLocalSQL(t, origin, db, "SELECT count() FROM spans WHERE project_id != '"+wantProject+"' OR org_id != '"+calOrg+"'"); got != "0" {
					t.Fatal("project/resource stamping failed", got)
				}
				raw := observedLocalSQL(t, origin, db, "SELECT observation_type, input, output, total_tokens FROM spans FINAL ORDER BY start_time FORMAT JSONEachRow")
				counts := map[string]int{}
				for _, line := range strings.Split(raw, "\n") {
					var row struct {
						Kind   string `json:"observation_type"`
						Input  string `json:"input"`
						Output string `json:"output"`
						Tokens int    `json:"total_tokens"`
					}
					if err := json.Unmarshal([]byte(line), &row); err != nil {
						t.Fatal(err)
					}
					counts[row.Kind]++
					if row.Kind == "llm" && row.Tokens != 17 {
						t.Fatal("cache tokens counted twice", row.Tokens)
					}
					if fixture == "content" && row.Kind == "llm" && (!strings.Contains(row.Input, "SYSTEM_SENTINEL") || !strings.Contains(row.Input, "PROMPT_SENTINEL") || !strings.Contains(row.Output, "RESPONSE_SENTINEL")) {
						t.Fatal("persisted model content incomplete", row)
					}
					if scenario == "tool-content" && row.Kind == "tool" && (row.Input != "{}" || !strings.Contains(row.Output, "TOOL_RESULT_SENTINEL")) {
						t.Fatal("persisted tool content incomplete", row)
					}
					if (scenario == "tool-metadata" || scenario == "concurrent") && (row.Input != "" || row.Output != "") {
						t.Fatal("metadata privacy changed", row)
					}
				}
				if strings.HasPrefix(scenario, "tool-") && (counts["agent"] != 1 || counts["llm"] != 2 || counts["tool"] != 1) {
					t.Fatal("persisted runner types", counts)
				}
				if scenario == "concurrent" {
					// The same session IDs join curated labels, stored spans and the
					// production per-session aggregate used by session analytics.
					for _, name := range []string{"conversation-a", "conversation-b"} {
						id := detid.TraceSessionID(wantProject, name).String()
						if got := observedLocalSQL(t, origin, db, "SELECT external_session_id FROM trace_sessions FINAL WHERE trace_session_id = '"+id+"'"); got != name {
							t.Fatal("curated session label", got)
						}
						if got := observedLocalSQL(t, origin, db, "SELECT count(), sum(total_tokens) FROM spans FINAL WHERE project_id = '"+wantProject+"' AND trace_session_id = '"+id+"'"); got != "1\t17" {
							t.Fatal("session span grouping", name, got)
						}
						if got := observedLocalSQL(t, origin, db, "SELECT countMerge(span_count), sumMerge(total_tokens_sum) FROM spans_per_session WHERE project_id = '"+wantProject+"' AND trace_session_id = '"+id+"'"); got != "1\t17" {
							t.Fatal("session rollup grouping", name, got)
						}
					}
				}
			})
		}
	}
}
