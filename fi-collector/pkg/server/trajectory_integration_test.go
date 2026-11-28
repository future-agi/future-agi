package server

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/future-agi/future-agi/fi-collector/pkg/chwriter"
	"github.com/future-agi/future-agi/fi-collector/pkg/trajectory"
	"github.com/redis/go-redis/v9"
)

// Real ClickHouse 25.3 + Redis 7 trajectory tests. They run in the
// fi-collector-ci integration job, which sets FI_LIVE_STAGE=ci together with
// FI_LIVE_CH_URL, FI_LIVE_REDIS_ADDR and FI_LIVE_V2_SCHEMA (the v2 schema
// rendered by test/observed-catalog/render_v2_schema.py). Without
// FI_LIVE_STAGE they are skipped (unit job); with it, a missing prerequisite
// fails the test instead of skipping it.

type liveTrajectoryEnv struct {
	origin string
	rdb    *redis.Client
	schema map[string][]string // file name -> statements
	suffix string
}

func requireLiveTrajectoryEnv(t *testing.T) *liveTrajectoryEnv {
	t.Helper()
	if os.Getenv("FI_LIVE_STAGE") == "" {
		t.Skip("set FI_LIVE_STAGE=ci with FI_LIVE_CH_URL, FI_LIVE_REDIS_ADDR and FI_LIVE_V2_SCHEMA for the real ClickHouse + Redis trajectory tests")
	}
	origin := os.Getenv("FI_LIVE_CH_URL")
	redisAddr := os.Getenv("FI_LIVE_REDIS_ADDR")
	schemaPath := os.Getenv("FI_LIVE_V2_SCHEMA")
	if origin == "" || redisAddr == "" || schemaPath == "" {
		t.Fatal("FI_LIVE_STAGE is set: FI_LIVE_CH_URL, FI_LIVE_REDIS_ADDR and FI_LIVE_V2_SCHEMA are required (not skipping)")
	}
	u, err := url.Parse(origin)
	if err != nil || u.Scheme != "http" || u.Hostname() != "127.0.0.1" || u.Port() == "" || u.User != nil || u.RawQuery != "" {
		t.Fatal("trajectory integration requires an explicit loopback ClickHouse HTTP origin")
	}
	if host, _, ok := strings.Cut(redisAddr, ":"); !ok || host != "127.0.0.1" {
		t.Fatal("trajectory integration requires an explicit loopback Redis address")
	}
	raw, err := os.ReadFile(schemaPath)
	if err != nil {
		t.Fatalf("rendered v2 schema missing (render_v2_schema.py): %v", err)
	}
	var rendered struct {
		Files map[string][]string `json:"files"`
	}
	if err := json.Unmarshal(raw, &rendered); err != nil {
		t.Fatalf("rendered v2 schema unreadable: %v", err)
	}
	for _, need := range []string{"002_spans_v2.sql", "040_trajectory_items.sql"} {
		if len(rendered.Files[need]) == 0 {
			t.Fatalf("rendered v2 schema lacks %s", need)
		}
	}
	rdb := redis.NewClient(&redis.Options{Addr: redisAddr})
	t.Cleanup(func() { rdb.Close() })
	if err := rdb.Ping(context.Background()).Err(); err != nil {
		t.Fatalf("redis ping: %v", err)
	}
	return &liveTrajectoryEnv{origin: origin, rdb: rdb, schema: rendered.Files, suffix: fmt.Sprint(time.Now().UnixNano())}
}

// trajectory040 returns 040's statements: the trajectory_items table and the
// spans pointer columns.
func (e *liveTrajectoryEnv) trajectory040(t *testing.T) (itemsTable, spansColumns string) {
	t.Helper()
	for _, stmt := range e.schema["040_trajectory_items.sql"] {
		stmt = strings.TrimSuffix(strings.TrimSpace(stmt), ";")
		switch {
		case strings.HasPrefix(stmt, "CREATE"):
			itemsTable = stmt
		case strings.HasPrefix(stmt, "ALTER"):
			spansColumns = stmt
		}
	}
	if itemsTable == "" || spansColumns == "" {
		t.Fatal("040 no longer has exactly the items table and the spans columns")
	}
	return itemsTable, spansColumns
}

// database creates an owned database with 002's spans table and the chosen
// parts of 040, dropped when the test ends.
func (e *liveTrajectoryEnv) database(t *testing.T, name string, withItemsTable, withSpansColumns bool) string {
	t.Helper()
	db := "traj_it_" + name + "_" + e.suffix
	observedDatabases(t, e.origin, db)
	items, columns := e.trajectory040(t)
	if withItemsTable {
		observedLocalSQL(t, e.origin, db, items)
	}
	if withSpansColumns {
		observedLocalSQL(t, e.origin, db, columns)
	}
	return db
}

func (e *liveTrajectoryEnv) writer(t *testing.T, db, user, password string) *chwriter.Writer {
	t.Helper()
	if user == "" {
		user, password = observedTestCredentials()
	}
	w, err := chwriter.New(chwriter.Config{URL: e.origin, Database: db, Table: "spans", Username: user, Password: password,
		MaxRetries: 1, InitialBackoff: 10 * time.Millisecond, RequestTimeout: 10 * time.Second,
		DeadLetterFile: filepath.Join(t.TempDir(), db+"-dlq.jsonl")})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { w.Close() })
	return w
}

// gate builds a real gate over prober with a test-owned Redis key (set when
// keyPresent), deleted when the test ends, and runs one check.
func (e *liveTrajectoryEnv) gate(t *testing.T, prober trajectory.SchemaProber, keyPresent bool) (*trajectory.Gate, string) {
	t.Helper()
	key := "fi:trajectory:it:" + t.Name() + ":" + e.suffix
	if keyPresent {
		if err := e.rdb.Set(context.Background(), key, "1", time.Hour).Err(); err != nil {
			t.Fatal(err)
		}
	}
	t.Cleanup(func() { e.rdb.Del(context.Background(), key) })
	g := trajectory.NewGate(trajectory.GateConfig{Prober: prober, Redis: e.rdb, Key: key, Log: slog.New(slog.NewTextHandler(os.Stderr, nil))})
	g.Check(context.Background())
	return g, key
}

// project returns a fresh project id whose seen-store keys are removed when
// the test ends (keyed cleanup, never FLUSHALL).
func (e *liveTrajectoryEnv) project(t *testing.T) string {
	t.Helper()
	n := time.Now().UnixNano()
	pid := fmt.Sprintf("%08x-%04x-4%03x-8%03x-%012x", uint32(n), uint16(n>>32), uint16(n>>8)&0xfff, uint16(n>>20)&0xfff, n&0xffffffffffff)
	t.Cleanup(func() {
		ctx := context.Background()
		iter := e.rdb.Scan(ctx, 0, "traj:seen:"+pid+":*", 100).Iterator()
		for iter.Next(ctx) {
			e.rdb.Del(ctx, iter.Val())
		}
	})
	return pid
}

func liveDedupServer(t *testing.T, w *chwriter.Writer, rdb *redis.Client, gate trajectory.WriteGate, usage UsageEmitter) *Server {
	t.Helper()
	if usage == nil {
		usage = NoopUsageEmitter{}
	}
	return New(Config{BatchMaxRows: 1000, BatchMaxAge: time.Minute}, w, nil, usage, NoopMetering{},
		WithLogger(slog.New(slog.NewTextHandler(io.Discard, nil))),
		WithTrajectory(trajectory.Config{Mode: trajectory.ModeOn}, trajectory.NewSeenStore(rdb, 10_000, time.Hour, time.Hour)),
		WithTrajectoryGate(gate))
}

// liveTurn is one multi-turn agent request and the exact input it carries.
func liveTurn(t *testing.T, pid string, turn int) ([]byte, string) {
	t.Helper()
	msgs := []map[string]any{{"role": "system", "content": "Integration system prompt " + strings.Repeat("policy ", 150)}}
	for i := 1; i <= turn; i++ {
		msgs = append(msgs,
			map[string]any{"role": "user", "content": fmt.Sprintf("Integration question %d %s", i, strings.Repeat("u", 400))},
			map[string]any{"role": "assistant", "content": fmt.Sprintf("Integration answer %d %s", i, strings.Repeat("a", 400))})
	}
	input, _ := json.Marshal(map[string]any{"model": "gpt-4o", "messages": msgs})
	return makeAgentHTTPReq(t, pid, trajTestOID, "sess-it", 0x51, byte(turn), msgs, fmt.Sprintf("Final reply %d", turn)), string(input)
}

func liveSettingLog(t *testing.T, e *liveTrajectoryEnv) {
	t.Helper()
	t.Logf("ClickHouse input_format_skip_unknown_fields default = %s",
		observedLocalSQL(t, e.origin, "default", "SELECT value FROM system.settings WHERE name = 'input_format_skip_unknown_fields'"))
}

// TestClickHouseTrajectoryHalfApplied040StaysInline: only 040's items table
// exists. The gate stays closed, so a dedup project's span is stored with its
// full original input and nothing is written to trajectory_items.
func TestClickHouseTrajectoryHalfApplied040StaysInline(t *testing.T) {
	e := requireLiveTrajectoryEnv(t)
	liveSettingLog(t, e)
	db := e.database(t, "table_only", true, false)
	w := e.writer(t, db, "", "")
	gate, _ := e.gate(t, w, true)
	if gate.Open() {
		t.Fatal("half-applied 040 (no spans columns): the gate must stay closed")
	}
	pid := e.project(t)
	srv := liveDedupServer(t, w, e.rdb, gate, nil)
	body, input := liveTurn(t, pid, 3)
	postTrajOTLP(t, srv, trajTestOID, pid, body)
	srv.drainNow(context.Background())
	if got := observedLocalSQL(t, e.origin, db, "SELECT input = "+sqlString(input)+", length(input) FROM spans WHERE project_id = '"+pid+"'"); got != fmt.Sprintf("1\t%d", len(input)) {
		t.Fatalf("span input is not the full original: %q (want length %d)", got, len(input))
	}
	if got := observedLocalSQL(t, e.origin, db, "SELECT count() FROM trajectory_items"); got != "0" {
		t.Fatalf("trajectory_items rows %s, want 0", got)
	}
}

// TestLiveTrajectoryGateColumnsOnly: only 040's spans columns exist (no items
// table). The gate stays closed and the span is stored inline.
func TestLiveTrajectoryGateColumnsOnly(t *testing.T) {
	e := requireLiveTrajectoryEnv(t)
	db := e.database(t, "columns_only", false, true)
	w := e.writer(t, db, "", "")
	gate, _ := e.gate(t, w, true)
	if gate.Open() {
		t.Fatal("no trajectory_items table: the gate must stay closed")
	}
	pid := e.project(t)
	srv := liveDedupServer(t, w, e.rdb, gate, nil)
	body, input := liveTurn(t, pid, 2)
	postTrajOTLP(t, srv, trajTestOID, pid, body)
	srv.drainNow(context.Background())
	if got := observedLocalSQL(t, e.origin, db, "SELECT input = "+sqlString(input)+", payload_encoding FROM spans WHERE project_id = '"+pid+"'"); got != "1\tinline" {
		t.Fatalf("span not inline with its full input: %q", got)
	}
}

// TestClickHouseTrajectoryItemsInsertSettingsAccepted: with the full 040 the
// gate opens against what ClickHouse 25.3 reports in system.columns, the items
// insert with insert_quorum / insert_quorum_timeout /
// distributed_foreground_insert succeeds, the strict pointer batch is
// accepted, pointer rows are stored and every pointer resolves to an item.
func TestClickHouseTrajectoryItemsInsertSettingsAccepted(t *testing.T) {
	e := requireLiveTrajectoryEnv(t)
	db := e.database(t, "full", true, true)
	w := e.writer(t, db, "", "")
	gate, _ := e.gate(t, w, true)
	if !gate.Open() {
		state, reason := gate.State()
		t.Fatalf("full 040: the gate must open (state %s, reason %q)", state, reason)
	}
	if q := gate.ItemsInsertSettings().Get("insert_quorum"); q != "auto" {
		t.Fatalf("single-node quorum %q, want auto", q)
	}
	pid := e.project(t)
	srv := liveDedupServer(t, w, e.rdb, gate, nil)
	for turn := 1; turn <= 3; turn++ {
		body, _ := liveTurn(t, pid, turn)
		postTrajOTLP(t, srv, trajTestOID, pid, body)
		srv.drainNow(context.Background())
	}
	if got := observedLocalSQL(t, e.origin, db, "SELECT countIf(payload_encoding = 'items_v1'), count() FROM spans WHERE project_id = '"+pid+"'"); got != "3\t3" {
		t.Fatalf("items_v1 spans/all = %q, want 3/3", got)
	}
	if got := observedLocalSQL(t, e.origin, db, "SELECT count() > 0 FROM trajectory_items WHERE project_id = '"+pid+"'"); got != "1" {
		t.Fatal("trajectory_items is empty right after the confirmed insert")
	}
	requireNoDanglingPointers(t, e, db, pid)
	if s := w.Snapshot(); s.RowsDeadLettered != 0 || s.TrajectoryBatchesFailed != 0 {
		t.Fatalf("inserts failed: %+v", s)
	}
}

// requireNoDanglingPointers checks that every hash a span points at exists
// in trajectory_items under the scope it resolves to.
func requireNoDanglingPointers(t *testing.T, e *liveTrajectoryEnv, db, pid string) {
	t.Helper()
	sql := `SELECT count() FROM (
	  SELECT toString(project_id) AS pid,
	         if(startsWith(h, 'p:'), '__project__', item_scope_id) AS scope,
	         if(startsWith(h, 'p:'), substring(h, 3), h) AS hash
	  FROM spans ARRAY JOIN arrayConcat(input_item_hashes, output_item_hashes, tool_def_hashes) AS h
	  WHERE project_id = '` + pid + `' AND payload_encoding = 'items_v1'
	) AS r
	LEFT ANTI JOIN (SELECT toString(project_id) AS pid, scope_id AS scope, toString(item_hash) AS hash FROM trajectory_items) AS i
	USING (pid, scope, hash)`
	if got := observedLocalSQL(t, e.origin, db, sql); got != "0" {
		t.Fatalf("%s span pointers resolve to no trajectory item", got)
	}
	if got := observedLocalSQL(t, e.origin, db, "SELECT count() FROM spans ARRAY JOIN input_item_hashes AS h WHERE project_id = '"+pid+"'"); got == "0" {
		t.Fatal("no pointers were written, so the dangling check is vacuous")
	}
}

// TestLiveTrajectoryStopButton: deleting the Redis key closes the gate on the
// next check and later requests are stored inline.
func TestLiveTrajectoryStopButton(t *testing.T) {
	e := requireLiveTrajectoryEnv(t)
	db := e.database(t, "stop", true, true)
	w := e.writer(t, db, "", "")
	gate, key := e.gate(t, w, true)
	if !gate.Open() {
		t.Fatal("full 040 and key: open")
	}
	pid := e.project(t)
	srv := liveDedupServer(t, w, e.rdb, gate, nil)
	body, _ := liveTurn(t, pid, 1)
	postTrajOTLP(t, srv, trajTestOID, pid, body)
	srv.drainNow(context.Background())
	if err := e.rdb.Del(context.Background(), key).Err(); err != nil {
		t.Fatal(err)
	}
	gate.Check(context.Background())
	if gate.Open() {
		t.Fatal("deleting the key must close the gate on the next check")
	}
	body, input := liveTurn(t, pid, 2)
	postTrajOTLP(t, srv, trajTestOID, pid, body)
	srv.drainNow(context.Background())
	got := observedLocalSQL(t, e.origin, db, "SELECT payload_encoding, input = "+sqlString(input)+" FROM spans WHERE project_id = '"+pid+"' ORDER BY id")
	if got != "items_v1\t0\ninline\t1" {
		t.Fatalf("before/after the stop button: %q", got)
	}
}

// TestLiveTrajectorySkipUnknownFieldsZeroOnItemsV1Batch: the gate checked a
// complete schema, but the spans insert reaches a table without the pointer
// columns (as a replica that has not applied 040's columns would be). Real
// ClickHouse must refuse the strict batch, the collector must recognize the
// refusal and close the gate, no preview may be stored as a payload, and the
// refused span must be stored inline (resent on dev's URL), not left in the
// dead-letter file.
func TestLiveTrajectorySkipUnknownFieldsZeroOnItemsV1Batch(t *testing.T) {
	e := requireLiveTrajectoryEnv(t)
	liveSettingLog(t, e)
	full := e.database(t, "probe_full", true, true)
	lagging := e.database(t, "lagging", true, false)
	gate, _ := e.gate(t, e.writer(t, full, "", ""), true)
	if !gate.Open() {
		t.Fatal("the probed database is complete: open")
	}
	w := e.writer(t, lagging, "", "")
	pid := e.project(t)
	usage := &recordingTrajUsage{}
	srv := liveDedupServer(t, w, e.rdb, gate, usage)
	body, input := liveTurn(t, pid, 2)
	postTrajOTLP(t, srv, trajTestOID, pid, body)
	srv.drainNow(context.Background())
	if got := observedLocalSQL(t, e.origin, lagging, "SELECT count(), countIf(input = "+sqlString(input)+") FROM spans WHERE project_id = '"+pid+"'"); got != "1\t1" {
		t.Fatalf("the refused span must be stored once with its full input (count, full) = %q", got)
	}
	if s := w.Snapshot(); s.RowsDeadLettered != 0 {
		t.Fatalf("the refused span was dead-lettered instead of resent inline: %+v", s)
	}
	if gate.Open() {
		t.Fatal("ClickHouse's refusal must be recognized as a schema mismatch and close the gate")
	}
	usage.mu.Lock()
	billedRaw := len(usage.raw) == 1 && usage.raw[0] == usage.stored[0]
	usage.mu.Unlock()
	if !billedRaw {
		t.Fatal("the refused request must be billed raw")
	}
	// The next request is stored inline in the same table.
	body, input = liveTurn(t, pid, 3)
	postTrajOTLP(t, srv, trajTestOID, pid, body)
	srv.drainNow(context.Background())
	if got := observedLocalSQL(t, e.origin, lagging, "SELECT count(), countIf(input = "+sqlString(input)+") FROM spans WHERE project_id = '"+pid+"'"); got != "2\t1" {
		t.Fatalf("the request after the refusal must be stored inline: %q", got)
	}
}

// TestClickHouseTrajectoryHalfApplied040NoDuplicateMVRows: the spans insert
// reaches a table without 040's pointer columns and a materialized view reads
// spans. ClickHouse 25.3 must refuse the strict batch before any block is
// written, so the inline resend is the only insert the view sees: one view row
// per span, and the span stored with its full input.
func TestClickHouseTrajectoryHalfApplied040NoDuplicateMVRows(t *testing.T) {
	e := requireLiveTrajectoryEnv(t)
	full := e.database(t, "mv_probe_full", true, true)
	lagging := e.database(t, "mv_lagging", true, false)
	observedLocalSQL(t, e.origin, lagging, "CREATE TABLE span_rows (id String, project_id UUID) ENGINE = MergeTree ORDER BY id")
	observedLocalSQL(t, e.origin, lagging, "CREATE MATERIALIZED VIEW span_rows_mv TO span_rows AS SELECT id, project_id FROM spans")
	gate, _ := e.gate(t, e.writer(t, full, "", ""), true)
	if !gate.Open() {
		t.Fatal("the probed database is complete: open")
	}
	w := e.writer(t, lagging, "", "")
	pid := e.project(t)
	srv := liveDedupServer(t, w, e.rdb, gate, nil)
	body, input := liveTurn(t, pid, 4)
	postTrajOTLP(t, srv, trajTestOID, pid, body)
	srv.drainNow(context.Background())
	if gate.Open() {
		t.Fatal("the refusal must close the gate")
	}
	if got := observedLocalSQL(t, e.origin, lagging, "SELECT count() FROM span_rows WHERE project_id = '"+pid+"'"); got != "1" {
		t.Fatalf("materialized view rows %s, want 1: the refused insert must not write a block", got)
	}
	if got := observedLocalSQL(t, e.origin, lagging, "SELECT count(), countIf(input = "+sqlString(input)+") FROM spans WHERE project_id = '"+pid+"'"); got != "1\t1" {
		t.Fatalf("spans (count, full input) = %q, want 1/1", got)
	}
	if s := w.Snapshot(); s.RowsDeadLettered != 0 {
		t.Fatalf("rows dead-lettered: %+v", s)
	}
}

// TestClickHouseTrajectorySchemaProbeWithInsertOnlyUser: the gate probes as
// the ingest user. With INSERT on both tables it sees exactly what that user
// may see (and opens only if every column is visible); with INSERT on spans
// only, trajectory_items is invisible and the gate fails closed, never open.
func TestClickHouseTrajectorySchemaProbeWithInsertOnlyUser(t *testing.T) {
	e := requireLiveTrajectoryEnv(t)
	db := e.database(t, "grants", true, true)
	for _, tc := range []struct {
		name   string
		tables []string
	}{
		{"both_tables", []string{"spans", "trajectory_items"}},
		{"spans_only", []string{"spans"}},
	} {
		t.Run(tc.name, func(t *testing.T) {
			user := "traj_it_ingest_" + tc.name + "_" + e.suffix
			password := "p" + e.suffix
			observedLocalSQL(t, e.origin, "default", "CREATE USER "+user+" IDENTIFIED WITH sha256_password BY '"+password+"'")
			t.Cleanup(func() { observedLocalSQL(t, e.origin, "default", "DROP USER IF EXISTS "+user) })
			for _, table := range tc.tables {
				observedLocalSQL(t, e.origin, "default", "GRANT INSERT ON "+db+"."+table+" TO "+user)
			}
			w := e.writer(t, db, user, password)
			cols, err := w.TableColumns(context.Background(), trajectory.ItemsTable)
			gate, _ := e.gate(t, w, true)
			visible := err == nil && len(trajectory.SchemaProblems(trajectory.ItemsColumns, cols)) == 0
			t.Logf("insert-only user sees trajectory_items columns: %v (err %v); gate open: %v", visible, err, gate.Open())
			if tc.name == "spans_only" && gate.Open() {
				t.Fatal("trajectory_items is not visible to the ingest user: the gate must fail closed")
			}
			if tc.name == "both_tables" && (!visible || !gate.Open()) {
				state, reason := gate.State()
				t.Fatalf("INSERT on both tables: the ingest user must see every column and the gate must open (visible %v, err %v, state %s, reason %q)", visible, err, state, reason)
			}
			if gate.Open() && !visible {
				t.Fatal("the gate opened without seeing the items columns")
			}
		})
	}
}

// runLiveTrajectoryCIStage is FI_LIVE_STAGE=ci of TestLiveTrajectoryE2EAndStress:
// a self-contained multi-turn round trip in owned databases with keyed Redis
// cleanup. Two collector pods share the Redis seen store and write a dedup
// database; a third writes a dedup-off twin. Every twin span keeps its exact
// input, every dedup span is a pointer row, no pointer dangles, later turns
// reuse items, and no request bills more than its raw bytes.
func runLiveTrajectoryCIStage(t *testing.T) {
	e := requireLiveTrajectoryEnv(t)
	dedupDB := e.database(t, "ci_dedup", true, true)
	baseDB := e.database(t, "ci_base", false, false)
	wDedup := e.writer(t, dedupDB, "", "")
	wBase := e.writer(t, baseDB, "", "")
	gateA, _ := e.gate(t, wDedup, true)
	gateB, _ := e.gate(t, wDedup, true)
	if !gateA.Open() || !gateB.Open() {
		t.Fatal("ci stage: the gates must open on the full schema")
	}
	pid := e.project(t)
	usage := &recordingTrajUsage{}
	podA := liveDedupServer(t, wDedup, e.rdb, gateA, usage)
	podB := liveDedupServer(t, wDedup, e.rdb, gateB, usage)
	base := New(Config{BatchMaxRows: 1000, BatchMaxAge: time.Minute}, wBase, nil, NoopUsageEmitter{}, NoopMetering{},
		WithLogger(slog.New(slog.NewTextHandler(io.Discard, nil))))
	const turns = 8
	inputs := map[int]string{}
	for turn := 1; turn <= turns; turn++ {
		body, input := liveTurn(t, pid, turn)
		inputs[turn] = input
		pod := podA
		if turn%2 == 0 {
			pod = podB
		}
		postTrajOTLP(t, pod, trajTestOID, pid, body)
		pod.drainNow(context.Background())
		postTrajOTLP(t, base, trajTestOID, pid, body)
		base.drainNow(context.Background())
	}
	for turn, input := range inputs {
		spanID := fmt.Sprintf("%02x000000000000%02x", byte(turn), 0x22)
		if got := observedLocalSQL(t, e.origin, baseDB, "SELECT input = "+sqlString(input)+" FROM spans WHERE id = '"+spanID+"'"); got != "1" {
			t.Fatalf("twin span %s lost its input: %q", spanID, got)
		}
	}
	if got := observedLocalSQL(t, e.origin, dedupDB, "SELECT countIf(payload_encoding = 'items_v1'), count() FROM spans WHERE project_id = '"+pid+"'"); got != fmt.Sprintf("%d\t%d", turns, turns) {
		t.Fatalf("dedup spans items_v1/all = %q", got)
	}
	requireNoDanglingPointers(t, e, dedupDB, pid)
	usage.mu.Lock()
	defer usage.mu.Unlock()
	if len(usage.raw) != turns {
		t.Fatalf("usage events %d, want %d", len(usage.raw), turns)
	}
	for i := range usage.raw {
		if usage.stored[i] <= 0 || usage.stored[i] > usage.raw[i] {
			t.Fatalf("turn %d bills stored=%d raw=%d", i+1, usage.stored[i], usage.raw[i])
		}
	}
	if usage.stored[turns-1]*2 > usage.raw[turns-1] {
		t.Fatalf("turn %d saved less than half (stored=%d raw=%d): cross-pod reuse is not happening", turns, usage.stored[turns-1], usage.raw[turns-1])
	}
	t.Logf("ci stage: %d turns, last turn raw=%d stored=%d", turns, usage.raw[turns-1], usage.stored[turns-1])
}

// newLiveTrajectoryGate is the gate of the manual FI_LIVE_STAGE stages: the
// production key over the stage's writer. Those stages FLUSHALL their own
// Redis, so the key is set here, after that.
func newLiveTrajectoryGate(t *testing.T, w *chwriter.Writer, rdb *redis.Client) *trajectory.Gate {
	t.Helper()
	if err := rdb.Set(context.Background(), trajectory.DefaultGateKey, "1", 0).Err(); err != nil {
		t.Fatalf("set gate key: %v", err)
	}
	g := trajectory.NewGate(trajectory.GateConfig{Prober: w, Redis: rdb, Log: slog.New(slog.NewTextHandler(io.Discard, nil))})
	g.Check(context.Background())
	if !g.Open() {
		state, reason := g.State()
		t.Fatalf("live stage gate did not open (state %s, reason %q): apply 040 first", state, reason)
	}
	return g
}

func liveBoolPtr(b bool) *bool { return &b }

func sqlString(s string) string {
	return "'" + strings.NewReplacer(`\`, `\\`, `'`, `\'`).Replace(s) + "'"
}
