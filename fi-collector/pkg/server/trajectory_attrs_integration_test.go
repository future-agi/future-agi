package server

import (
	"context"
	"fmt"
	"io"
	"log/slog"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/future-agi/future-agi/fi-collector/pkg/chwriter"
	"go.opentelemetry.io/collector/pdata/ptrace/ptraceotlp"
)

// Real ClickHouse 25.3 + Redis tests for the attribute and dead-letter rules
// of the trajectory store. They use the owned databases of
// trajectory_integration_test.go and run in the fi-collector-ci integration
// job (FI_LIVE_STAGE=ci).

// liveAttrsTurn is liveTurn's request for pid with flattened OpenInference
// message keys on the span.
func liveAttrsTurn(t *testing.T, pid string, turn int) ([]byte, string) {
	t.Helper()
	body, input := liveTurn(t, pid, turn)
	req := ptraceotlp.NewExportRequest()
	if err := req.UnmarshalProto(body); err != nil {
		t.Fatal(err)
	}
	attrs := req.Traces().ResourceSpans().At(0).ScopeSpans().At(0).Spans().At(0).Attributes()
	attrs.PutStr("llm.input_messages.0.message.role", "system")
	attrs.PutStr("llm.input_messages.0.message.content", "Integration system prompt "+strings.Repeat("policy ", 150))
	attrs.PutStr("llm.output_messages.0.message.content", fmt.Sprintf("Final reply %d", turn))
	attrs.PutStr("gen_ai.completion.0.finish_reason", "stop")
	wire, err := req.MarshalProto()
	if err != nil {
		t.Fatal(err)
	}
	return wire, input
}

// TestClickHouseTrajectoryAttrsMatchDedupOff: the same three turns go to a
// dedup collector (full 040, open gate) and a dedup-off collector. Every span
// stored as items_v1 has attrs_string equal to the dedup-off twin's, the
// flattened message keys present, and attributes_extra equal to the twin's
// except that input.value/output.value are replaced by the sentinels.
func TestClickHouseTrajectoryAttrsMatchDedupOff(t *testing.T) {
	e := requireLiveTrajectoryEnv(t)
	dedupDB := e.database(t, "attrs_dedup", true, true)
	baseDB := e.database(t, "attrs_base", false, false)
	wDedup := e.writer(t, dedupDB, "", "")
	wBase := e.writer(t, baseDB, "", "")
	gate, _ := e.gate(t, wDedup, true)
	if !gate.Open() {
		t.Fatal("full 040: the gate must open")
	}
	pid := e.project(t)
	dedup := liveDedupServer(t, wDedup, e.rdb, gate, nil)
	base := New(Config{BatchMaxRows: 1000, BatchMaxAge: time.Minute}, wBase, nil, NoopUsageEmitter{}, NoopMetering{},
		WithLogger(slog.New(slog.NewTextHandler(io.Discard, nil))))
	for turn := 1; turn <= 3; turn++ {
		body, _ := liveAttrsTurn(t, pid, turn)
		postTrajOTLP(t, dedup, trajTestOID, pid, body)
		dedup.drainNow(context.Background())
		postTrajOTLP(t, base, trajTestOID, pid, body)
		base.drainNow(context.Background())
	}
	where := " FROM spans WHERE project_id = '" + pid + "' ORDER BY id FORMAT TSV"
	if got := observedLocalSQL(t, e.origin, dedupDB, "SELECT countIf(payload_encoding = 'items_v1'), count() FROM spans WHERE project_id = '"+pid+"'"); got != "3\t3" {
		t.Fatalf("dedup spans items_v1/all = %q", got)
	}
	dedupAttrs := observedLocalSQL(t, e.origin, dedupDB, "SELECT id, toJSONString(attrs_string)"+where)
	baseAttrs := observedLocalSQL(t, e.origin, baseDB, "SELECT id, toJSONString(attrs_string)"+where)
	if dedupAttrs != baseAttrs || !strings.Contains(dedupAttrs, "llm.input_messages.0.message.content") {
		t.Fatalf("attrs_string differs from the dedup-off twin:\n%s\nwant\n%s", dedupAttrs, baseAttrs)
	}
	// attributes_extra: the twin's, with the promoted keys swapped for the
	// sentinels. JSONExtractKeys reads the top level of the JSON column.
	extraSQL := "SELECT id, arraySort(JSONExtractKeys(toJSONString(attributes_extra)))" + where
	dedupKeys := observedLocalSQL(t, e.origin, dedupDB, extraSQL)
	baseKeys := observedLocalSQL(t, e.origin, baseDB, extraSQL)
	if !strings.Contains(dedupKeys, "__fi_promoted_input_key") || strings.Contains(baseKeys, "__fi_promoted") {
		t.Fatalf("sentinels: dedup %s, dedup off %s", dedupKeys, baseKeys)
	}
	if got := observedLocalSQL(t, e.origin, dedupDB, "SELECT countIf(JSONExtractString(toJSONString(attributes_extra), '__fi_promoted_input_key') = 'input.value' AND JSONExtractString(toJSONString(attributes_extra), '__fi_promoted_output_key') = 'output.value') FROM spans WHERE project_id = '"+pid+"'"); got != "3" {
		t.Fatalf("every items_v1 span must carry both sentinels, got %s", got)
	}
	if got := observedLocalSQL(t, e.origin, baseDB, "SELECT countIf(JSONHas(toJSONString(attributes_extra), 'input', 'value') AND JSONHas(toJSONString(attributes_extra), 'output', 'value')) FROM spans WHERE project_id = '"+pid+"'"); got != "3" {
		t.Fatalf("fixture: the twin must store input.value and output.value, got %s", got)
	}
	if got := observedLocalSQL(t, e.origin, dedupDB, "SELECT countIf(JSONHas(toJSONString(attributes_extra), 'input', 'value') OR JSONHas(toJSONString(attributes_extra), 'output', 'value')) FROM spans WHERE project_id = '"+pid+"'"); got != "0" {
		t.Fatalf("the promoted copies must leave attributes_extra, %s spans keep one", got)
	}
}

// TestClickHouseDeadLetterReplayNeedsNoItems: the spans insert of a pointer
// batch cannot be stored (its table is missing), so the drain dead-letters
// it. Replaying dead_letter.jsonl into a database that has dev's spans table
// and no trajectory_items stores every span with its full original input and
// input.value, and no sentinel: the file needs no trajectory item.
func TestClickHouseDeadLetterReplayNeedsNoItems(t *testing.T) {
	e := requireLiveTrajectoryEnv(t)
	db := e.database(t, "dlq_src", true, true)
	replayDB := e.database(t, "dlq_replay", false, false)
	user, password := observedTestCredentials()
	dlq := filepath.Join(t.TempDir(), "dead_letter.jsonl")
	w, err := chwriter.New(chwriter.Config{URL: e.origin, Database: db, Table: "spans_not_created", Username: user, Password: password,
		MaxRetries: 1, InitialBackoff: 10 * time.Millisecond, RequestTimeout: 10 * time.Second, DeadLetterFile: dlq})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { w.Close() })
	// The gate probes the real spans and trajectory_items tables of db.
	gate, _ := e.gate(t, w, true)
	if !gate.Open() {
		t.Fatal("full 040: the gate must open")
	}
	pid := e.project(t)
	srv := liveDedupServer(t, w, e.rdb, gate, nil)
	inputs := map[string]string{}
	for turn := 1; turn <= 2; turn++ {
		body, input := liveAttrsTurn(t, pid, turn)
		inputs[fmt.Sprintf("%02x000000000000%02x", byte(turn), 0x22)] = input
		postTrajOTLP(t, srv, trajTestOID, pid, body)
		srv.drainNow(context.Background())
	}
	if got := observedLocalSQL(t, e.origin, db, "SELECT count() > 0 FROM trajectory_items WHERE project_id = '"+pid+"'"); got != "1" {
		t.Fatal("fixture: the items insert must have succeeded")
	}
	dead, err := os.ReadFile(dlq)
	if err != nil || len(strings.TrimSpace(string(dead))) == 0 {
		t.Fatalf("nothing was dead-lettered: %v", err)
	}
	if strings.Contains(string(dead), "__fi_promoted") || strings.Contains(string(dead), "payload_encoding") {
		t.Fatal("the dead-letter file must hold inline rows only")
	}
	observedLocalSQL(t, e.origin, replayDB, "INSERT INTO spans FORMAT JSONEachRow\n"+string(dead))
	for id, input := range inputs {
		got := observedLocalSQL(t, e.origin, replayDB, "SELECT input = "+sqlString(input)+", JSONExtractString(toJSONString(attributes_extra), 'input', 'value') = "+sqlString(input)+" FROM spans WHERE id = '"+id+"'")
		if got != "1\t1" {
			t.Fatalf("replayed span %s: input and input.value full = %q, want 1\t1", id, got)
		}
	}
	if got := observedLocalSQL(t, e.origin, replayDB, "SELECT count() FROM spans WHERE project_id = '"+pid+"' AND position(toJSONString(attributes_extra), '__fi_promoted') > 0"); got != "0" {
		t.Fatalf("%s replayed spans carry a sentinel", got)
	}
}
