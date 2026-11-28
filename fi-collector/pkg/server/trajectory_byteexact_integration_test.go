package server

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"net/url"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/future-agi/future-agi/fi-collector/pkg/chwriter"
	"github.com/future-agi/future-agi/fi-collector/pkg/trajectory"
	"go.opentelemetry.io/collector/pdata/pcommon"
	"go.opentelemetry.io/collector/pdata/ptrace"
	"go.opentelemetry.io/collector/pdata/ptrace/ptraceotlp"
)

// TestClickHouseTrajectoryByteExactRoundTrip posts a 10-turn pretty-printed
// agent conversation over OTLP HTTP to two collectors writing two sibling
// databases on a real ClickHouse: one with the trajectory store on (002 plus
// 040), one with it off (002 only, as dev writes). Every span the dedup
// collector stored rebuilds from its envelope and trajectory_items, as read
// back from ClickHouse, to exactly the bytes that were sent and that the
// dedup-off collector stored in spans.input/output.
func TestClickHouseTrajectoryByteExactRoundTrip(t *testing.T) {
	origin := observedIntegrationOrigin(t)
	suffix := fmt.Sprint(time.Now().UnixNano())
	dedupDB, devDB := "traj_byteexact_on_"+suffix, "traj_byteexact_off_"+suffix
	observedDatabases(t, origin, dedupDB)
	observedDatabases(t, origin, devDB)
	for _, statement := range strings.Split(observedDDL(t, "schema", "040_trajectory_items.sql"), ";") {
		if strings.TrimSpace(statement) != "" {
			observedLocalSQL(t, origin, dedupDB, statement)
		}
	}

	user, password := observedTestCredentials()
	newWriter := func(database string) *chwriter.Writer {
		w, err := chwriter.New(chwriter.Config{URL: origin, Database: database, Table: "spans", Username: user, Password: password, MaxRetries: 1, RequestTimeout: 10 * time.Second, DeadLetterFile: filepath.Join(t.TempDir(), database+"-deadletter.jsonl")})
		if err != nil {
			t.Fatal(err)
		}
		t.Cleanup(func() { w.Close() })
		return w
	}
	dedupWriter, devWriter := newWriter(dedupDB), newWriter(devDB)
	quiet := WithLogger(slog.New(slog.NewTextHandler(io.Discard, nil)))
	dedup := New(Config{BatchMaxRows: 1000, BatchMaxAge: time.Minute}, dedupWriter, nil, NoopUsageEmitter{}, NoopMetering{}, quiet,
		WithTrajectory(trajectory.Config{Mode: trajectory.ModeOn}.Normalize(), trajectory.NewSeenStore(nil, 10_000, time.Hour, time.Hour)),
		WithTrajectoryGate(byteExactOpenGate{t: t}))
	dev := New(Config{BatchMaxRows: 1000, BatchMaxAge: time.Minute}, devWriter, nil, NoopUsageEmitter{}, NoopMetering{}, quiet)

	const (
		projectID = "44444444-4444-4444-8444-444444444444"
		orgID     = "55555555-5555-4555-8555-555555555555"
	)
	ctx := context.Background()
	sent := map[string][2]string{} // span id -> {input, output}
	tools := []any{
		map[string]any{"type": "function", "function": map[string]any{"name": "read_file", "description": "Read a file from the workspace", "parameters": map[string]any{"type": "object"}}},
		map[string]any{"type": "function", "function": map[string]any{"name": "run_tests", "description": "Run the test suite", "parameters": map[string]any{"type": "object"}}},
	}
	history := []any{map[string]any{"role": "system", "content": "You are a careful coding agent. " + strings.Repeat("Explain each step. ", 10)}}
	for turn := 1; turn <= 10; turn++ {
		history = append(history, map[string]any{"role": "user", "content": fmt.Sprintf("Turn %d: café   <tag> & \"quote\" %s", turn, strings.Repeat("context ", 8))})
		input, err := json.MarshalIndent(map[string]any{"model": "gpt-4o", "temperature": 0.25, "tools": tools, "messages": history}, "", "  ")
		if err != nil {
			t.Fatal(err)
		}
		reply := map[string]any{"role": "assistant", "content": fmt.Sprintf("Reply %d: %s", turn, strings.Repeat("reasoning ", 6))}
		output, err := json.MarshalIndent(map[string]any{"id": fmt.Sprintf("chatcmpl-%d", turn), "choices": []any{map[string]any{"index": 0, "message": reply}}}, "", "  ")
		if err != nil {
			t.Fatal(err)
		}
		history = append(history, reply)
		spanID, body := byteExactAgentRequest(t, projectID, orgID, byte(turn), string(input), string(output))
		sent[spanID] = [2]string{string(input), string(output)}
		for _, srv := range []*Server{dedup, dev} {
			postTrajOTLP(t, srv, orgID, projectID, body)
			srv.drainNow(ctx)
		}
	}
	for name, w := range map[string]*chwriter.Writer{"dedup": dedupWriter, "dev": devWriter} {
		if stats := w.Snapshot(); stats.RowsDeadLettered != 0 {
			t.Fatalf("%s writer dead-lettered rows: %+v", name, stats)
		}
	}

	devRows := byteExactQuery(t, origin, devDB, "SELECT id, input, output FROM spans FINAL")
	dedupRows := byteExactQuery(t, origin, dedupDB, "SELECT id, input, output, input_item_hashes, output_item_hashes, tool_def_hashes, item_scope_id, payload_encoding, payload_envelope FROM spans FINAL")
	itemRows := byteExactQuery(t, origin, dedupDB, "SELECT scope_id, item_hash, content FROM trajectory_items FINAL")
	if len(devRows) != len(sent) || len(dedupRows) != len(sent) {
		t.Fatalf("stored spans: dev=%d dedup=%d, want %d each", len(devRows), len(dedupRows), len(sent))
	}
	devByID := map[string]map[string]any{}
	for _, row := range devRows {
		devByID[row["id"].(string)] = row
	}
	items := make([]trajectory.Item, 0, len(itemRows))
	for _, row := range itemRows {
		items = append(items, trajectory.Item{ScopeID: row["scope_id"].(string), ItemHash: row["item_hash"].(string), Content: row["content"].(string)})
	}

	previewed := 0
	for _, row := range dedupRows {
		id := row["id"].(string)
		want, ok := sent[id]
		if !ok {
			t.Fatalf("unexpected span %s", id)
		}
		if devByID[id]["input"] != want[0] || devByID[id]["output"] != want[1] {
			t.Fatalf("span %s: the dedup-off database did not store the sent bytes", id)
		}
		if row["payload_encoding"] != trajectory.EncodingItemsV1 {
			t.Fatalf("span %s stayed %v", id, row["payload_encoding"])
		}
		env, err := trajectory.ParseEnvelope(row["payload_envelope"].(string))
		if err != nil || env.Input == nil || env.Output == nil {
			t.Fatalf("span %s envelope %v: %v", id, row["payload_envelope"], err)
		}
		resolve := trajectory.ItemsResolver(row["item_scope_id"].(string), items)
		gotIn, err := trajectory.ReconstructSide(env.Input, byteExactStrings(row["input_item_hashes"]), byteExactStrings(row["tool_def_hashes"]), resolve)
		if err != nil || gotIn != want[0] || gotIn != devByID[id]["input"] {
			t.Fatalf("span %s input not byte-exact (err=%v)\n got=%q\nwant=%q", id, err, gotIn, want[0])
		}
		gotOut, err := trajectory.ReconstructSide(env.Output, byteExactStrings(row["output_item_hashes"]), nil, resolve)
		if err != nil || gotOut != want[1] || gotOut != devByID[id]["output"] {
			t.Fatalf("span %s output not byte-exact (err=%v)\n got=%q\nwant=%q", id, err, gotOut, want[1])
		}
		if row["input"] != want[0] {
			previewed++
		}
	}
	if previewed == 0 {
		t.Fatal("no span stored a preview, so the round trip proved nothing")
	}
	// Distinct items: 1 system prompt and 2 tools shared by every turn, 10
	// user and 9 assistant history messages, and 10 output choices. The 10
	// inputs reference 3*10 + 100 items, so reuse kept 22 instead of 130.
	if len(items) != 1+2+10+9+10 {
		t.Fatalf("trajectory_items rows = %d, want 32 distinct items", len(items))
	}
}

// byteExactAgentRequest is one OTLP protobuf request with a single LLM span
// carrying input and output verbatim.
func byteExactAgentRequest(t *testing.T, projectID, orgID string, turn byte, input, output string) (string, []byte) {
	t.Helper()
	td := ptrace.NewTraces()
	rs := td.ResourceSpans().AppendEmpty()
	rs.Resource().Attributes().PutStr("service.name", "byte-exact-agent")
	rs.Resource().Attributes().PutStr("project_name", "agent-proj")
	rs.Resource().Attributes().PutStr("fi.project_id", projectID)
	rs.Resource().Attributes().PutStr("fi.org_id", orgID)
	sp := rs.ScopeSpans().AppendEmpty().Spans().AppendEmpty()
	tid := [16]byte{0xbe, 0xe0, 15: 0x01}
	sid := [8]byte{0x5e, turn, 7: 0x01}
	sp.SetTraceID(pcommon.TraceID(tid))
	sp.SetSpanID(pcommon.SpanID(sid))
	sp.SetParentSpanID([8]byte{0xaa})
	sp.SetName("llm.turn")
	start := time.Now().UTC().Add(time.Duration(turn) * time.Millisecond)
	sp.SetStartTimestamp(pcommon.NewTimestampFromTime(start))
	sp.SetEndTimestamp(pcommon.NewTimestampFromTime(start.Add(50 * time.Millisecond)))
	sp.Attributes().PutStr("fi.span.kind", "LLM")
	sp.Attributes().PutStr("session.id", "byte-exact-session")
	sp.Attributes().PutStr("input.value", input)
	sp.Attributes().PutStr("output.value", output)
	wire, err := ptraceotlp.NewExportRequestFromTraces(td).MarshalProto()
	if err != nil {
		t.Fatal(err)
	}
	return pcommon.SpanID(sid).String(), wire
}

// byteExactQuery reads rows as JSONEachRow without the small response cap of
// observedLocalSQL, which payload columns can exceed.
func byteExactQuery(t *testing.T, origin, database, sql string) []map[string]any {
	t.Helper()
	query := url.Values{"database": {database}, "default_format": {"JSONEachRow"}, "output_format_json_quote_64bit_integers": {"1"}}
	req, err := http.NewRequest(http.MethodPost, origin+"?"+query.Encode(), strings.NewReader(sql))
	if err != nil {
		t.Fatal(err)
	}
	req.SetBasicAuth(observedTestCredentials())
	client := &http.Client{Timeout: 30 * time.Second, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}
	response, err := client.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer response.Body.Close()
	body, err := io.ReadAll(io.LimitReader(response.Body, 64<<20))
	if err != nil || response.StatusCode != http.StatusOK || response.Header.Get("X-ClickHouse-Exception-Code") != "" {
		t.Fatalf("read query failed: %s (%v)", truncateForLog(body), err)
	}
	var rows []map[string]any
	dec := json.NewDecoder(bytes.NewReader(body))
	for dec.More() {
		var row map[string]any
		if err := dec.Decode(&row); err != nil {
			t.Fatalf("decode row: %v", err)
		}
		rows = append(rows, row)
	}
	return rows
}

func byteExactStrings(v any) []string {
	list, _ := v.([]any)
	out := make([]string, 0, len(list))
	for _, item := range list {
		s, _ := item.(string)
		out = append(out, s)
	}
	return out
}

func truncateForLog(b []byte) string {
	if len(b) > 2048 {
		return string(b[:2048]) + "..."
	}
	return string(b)
}

// byteExactOpenGate is a dedup gate that stays open for the whole test: the
// test owns its databases (002 plus 040), so there is no operator key or
// schema probe to wait for. A breaker trip or a pointer-column refusal fails
// the test instead of closing the gate, because either would send the
// spans inline and hide what this test checks.
type byteExactOpenGate struct{ t *testing.T }

func (byteExactOpenGate) Open() bool                      { return true }
func (byteExactOpenGate) ItemsInsertSettings() url.Values { return nil }
func (g byteExactOpenGate) ReportItemsFailure(err error) {
	g.t.Errorf("trajectory_items insert failed: %v", err)
}
func (g byteExactOpenGate) ReportSpansSchemaError(err error) {
	g.t.Errorf("spans insert refused a pointer column: %v", err)
}
