package server

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/alicebob/miniredis/v2"
	"github.com/future-agi/future-agi/fi-collector/pkg/auth"
	"github.com/future-agi/future-agi/fi-collector/pkg/chwriter"
	"github.com/future-agi/future-agi/fi-collector/pkg/trajectory"
	"github.com/redis/go-redis/v9"
	"go.opentelemetry.io/collector/pdata/pcommon"
	"go.opentelemetry.io/collector/pdata/ptrace"
	"go.opentelemetry.io/collector/pdata/ptrace/ptraceotlp"
)

type recordedCHInsert struct {
	Table string
	Rows  []map[string]any
	// RawQuery is the request's URL query string, byte for byte.
	RawQuery string
}

// fakeTrajectoryCH is a ClickHouse HTTP double: it records inserts, answers
// the gate's metadata queries (system.columns / system.tables /
// system.replicas) from a configurable 040 schema, and injects failures.
type fakeTrajectoryCH struct {
	mu           sync.Mutex
	inserts      []recordedCHInsert
	failTrajItem bool
	// trajItemAttempts counts trajectory_items insert requests.
	trajItemAttempts int
	// missingColumns are omitted from system.columns answers.
	missingColumns map[string]bool
	// engines overrides system.tables engines (default ReplacingMergeTree).
	engines map[string]string
	// metadataCalls counts system.* queries.
	metadataCalls int
	// strictSpansError, when set, answers a spans insert that carries
	// input_format_skip_unknown_fields=0 with HTTP 400 and this body.
	strictSpansError string
	// failInlineSpans answers every spans insert without
	// input_format_skip_unknown_fields=0 with HTTP 500.
	failInlineSpans bool
	// failStrictSpans answers every spans insert with
	// input_format_skip_unknown_fields=0 with HTTP 500 (not a schema
	// refusal), so the writer dead-letters the pointer batch.
	failStrictSpans bool
}

func (f *fakeTrajectoryCH) handler() http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		q := r.URL.Query().Get("query")
		body, _ := io.ReadAll(r.Body)
		if q == "" && bytes.Contains(body, []byte("FROM system.")) {
			f.answerMetadata(w, r, string(body))
			return
		}
		table := "unknown"
		switch {
		case strings.Contains(q, "INTO trajectory_items"):
			table = "trajectory_items"
		case strings.Contains(q, "INTO spans"):
			table = "spans"
		case strings.Contains(q, "INTO traces"):
			table = "traces"
		case strings.Contains(q, "INTO trace_sessions"):
			table = "trace_sessions"
		case strings.Contains(q, "INTO end_users"):
			table = "end_users"
		}

		f.mu.Lock()
		if table == "trajectory_items" {
			f.trajItemAttempts++
		}
		fail := table == "trajectory_items" && f.failTrajItem
		strictErr := ""
		if table == "spans" && r.URL.Query().Get("input_format_skip_unknown_fields") == "0" {
			strictErr = f.strictSpansError
			fail = fail || f.failStrictSpans
		} else if table == "spans" && f.failInlineSpans {
			fail = true
		}
		f.mu.Unlock()
		if fail {
			http.Error(w, "simulated "+table+" failure", http.StatusInternalServerError)
			return
		}

		var rows []map[string]any
		for _, line := range bytes.Split(bytes.TrimSpace(body), []byte("\n")) {
			if len(bytes.TrimSpace(line)) == 0 {
				continue
			}
			var m map[string]any
			if err := json.Unmarshal(line, &m); err == nil {
				rows = append(rows, m)
			}
		}
		f.mu.Lock()
		f.inserts = append(f.inserts, recordedCHInsert{Table: table, Rows: rows, RawQuery: r.URL.RawQuery})
		f.mu.Unlock()
		if strictErr != "" {
			w.Header().Set("X-ClickHouse-Exception-Code", "117")
			http.Error(w, strictErr, http.StatusBadRequest)
			return
		}
		w.WriteHeader(http.StatusOK)
	})
}

func (f *fakeTrajectoryCH) answerMetadata(w http.ResponseWriter, r *http.Request, sql string) {
	table := r.URL.Query().Get("param_table")
	f.mu.Lock()
	defer f.mu.Unlock()
	f.metadataCalls++
	var out bytes.Buffer
	enc := json.NewEncoder(&out)
	switch {
	case strings.Contains(sql, "system.columns"):
		var contract []trajectory.ColumnContract
		switch table {
		case "spans":
			contract = append([]trajectory.ColumnContract{{Name: "span_id", Type: "String"}}, trajectory.SpansPointerColumns...)
		case trajectory.ItemsTable:
			contract = trajectory.ItemsColumns
		}
		for _, col := range contract {
			if !f.missingColumns[col.Name] {
				_ = enc.Encode(map[string]string{"name": col.Name, "type": col.Type})
			}
		}
	case strings.Contains(sql, "system.tables"):
		engine := "ReplacingMergeTree"
		if e, ok := f.engines[table]; ok {
			engine = e
		}
		if engine != "" {
			_ = enc.Encode(map[string]string{"engine": engine})
		}
	case strings.Contains(sql, "system.replicas"):
		_ = enc.Encode(map[string]int{"total_replicas": 1, "active_replicas": 1})
	}
	w.WriteHeader(http.StatusOK)
	_, _ = w.Write(out.Bytes())
}

func (f *fakeTrajectoryCH) setMissing(cols ...string) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.missingColumns = map[string]bool{}
	for _, c := range cols {
		f.missingColumns[c] = true
	}
}

func (f *fakeTrajectoryCH) reset() {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.inserts = nil
	f.trajItemAttempts = 0
}

func (f *fakeTrajectoryCH) counts() (metadata, itemAttempts int) {
	f.mu.Lock()
	defer f.mu.Unlock()
	return f.metadataCalls, f.trajItemAttempts
}

// testClock is a race-safe settable clock for the gate.
type testClock struct{ nanos atomic.Int64 }

func newTestClock() *testClock {
	c := &testClock{}
	c.nanos.Store(time.Date(2026, 11, 28, 12, 0, 0, 0, time.UTC).UnixNano())
	return c
}

func (c *testClock) Now() time.Time          { return time.Unix(0, c.nanos.Load()).UTC() }
func (c *testClock) Advance(d time.Duration) { c.nanos.Add(int64(d)) }

// newTestGate builds a gate over the writer's own metadata queries and a
// test-unique Redis key, runs one check, and returns it. wantOpen states what
// that check must decide.
func newTestGate(t *testing.T, writer *chwriter.Writer, rdb *redis.Client, clock *testClock, log *slog.Logger, wantOpen bool) *trajectory.Gate {
	t.Helper()
	key := "fi:trajectory:test-gate:" + t.Name()
	if err := rdb.Set(context.Background(), key, "1", 0).Err(); err != nil {
		t.Fatalf("set gate key: %v", err)
	}
	if log == nil {
		log = slog.New(slog.NewTextHandler(io.Discard, nil))
	}
	g := trajectory.NewGate(trajectory.GateConfig{Prober: writer, Redis: rdb, Key: key, Now: clock.Now, Log: log})
	g.Check(context.Background())
	if g.Open() != wantOpen {
		state, reason := g.State()
		t.Fatalf("gate open=%v after the first check, want %v (state %s, reason %q)", g.Open(), wantOpen, state, reason)
	}
	return g
}

func boolPtr(b bool) *bool { return &b }

func (f *fakeTrajectoryCH) snapshot() []recordedCHInsert {
	f.mu.Lock()
	defer f.mu.Unlock()
	out := make([]recordedCHInsert, len(f.inserts))
	copy(out, f.inserts)
	return out
}

func makeAgentHTTPReq(t *testing.T, projectID, orgID, sessionID string, traceByte, spanByte byte, msgs []map[string]any, reply string) []byte {
	t.Helper()
	td := ptrace.NewTraces()
	rs := td.ResourceSpans().AppendEmpty()
	rs.Resource().Attributes().PutStr("service.name", "agent-test")
	rs.Resource().Attributes().PutStr("project_name", "agent-proj")
	rs.Resource().Attributes().PutStr("fi.project_id", projectID)
	rs.Resource().Attributes().PutStr("fi.org_id", orgID)
	rs.Resource().Attributes().PutStr("project_type", "observe")
	ss := rs.ScopeSpans().AppendEmpty()
	sp := ss.Spans().AppendEmpty()
	var tid [16]byte
	tid[0] = traceByte
	tid[15] = 0x11
	var sid [8]byte
	sid[0] = spanByte
	sid[7] = 0x22
	sp.SetTraceID(pcommon.TraceID(tid))
	sp.SetSpanID(pcommon.SpanID(sid))
	sp.SetName("llm.turn")
	now := time.Now().UTC()
	sp.SetStartTimestamp(pcommon.NewTimestampFromTime(now))
	sp.SetEndTimestamp(pcommon.NewTimestampFromTime(now.Add(100 * time.Millisecond)))
	sp.Attributes().PutStr("fi.span.kind", "LLM")
	sp.Attributes().PutStr("session.id", sessionID)

	inBytes, _ := json.Marshal(map[string]any{"model": "gpt-4o", "messages": msgs})
	outBytes, _ := json.Marshal(map[string]any{
		"choices": []any{map[string]any{"index": 0, "message": map[string]any{"role": "assistant", "content": reply}}},
	})
	sp.Attributes().PutStr("input.value", string(inBytes))
	sp.Attributes().PutStr("output.value", string(outBytes))

	req := ptraceotlp.NewExportRequestFromTraces(td)
	wire, err := req.MarshalProto()
	if err != nil {
		t.Fatalf("marshal OTLP proto: %v", err)
	}
	return wire
}

func postTrajOTLP(t *testing.T, srv *Server, orgID, projectID string, body []byte) {
	t.Helper()
	r := httptest.NewRequest(http.MethodPost, "/v1/traces", bytes.NewReader(body))
	r.Header.Set("Content-Type", "application/x-protobuf")
	r = r.WithContext(auth.WithResolvedContext(r.Context(), &auth.ResolveResult{
		OrgID:       orgID,
		WorkspaceID: "88888888-8888-4888-8888-888888888888",
		Projects:    map[string]string{"agent-proj": projectID},
	}, "test-key"))
	w := httptest.NewRecorder()
	srv.handleHTTPTraces(w, r)
	if w.Code != http.StatusOK {
		t.Fatalf("POST /v1/traces status=%d body=%s", w.Code, w.Body.String())
	}
}

func TestServerTrajectoryItemFirstAndFallback(t *testing.T) {
	ch := &fakeTrajectoryCH{}
	chSrv := httptest.NewServer(ch.handler())
	defer chSrv.Close()

	writer, err := chwriter.New(chwriter.Config{
		URL:            chSrv.URL,
		Database:       "default",
		Table:          "spans",
		MaxRetries:     1,
		DeadLetterFile: filepath.Join(t.TempDir(), "dlq.jsonl"),
	})
	if err != nil {
		t.Fatalf("chwriter.New: %v", err)
	}
	defer writer.Close()

	mr := miniredis.RunT(t)
	rdb := redis.NewClient(&redis.Options{Addr: mr.Addr()})
	defer rdb.Close()

	cfg := trajectory.Config{Mode: trajectory.ModeOn, PreviewMaxBytes: 128}.Normalize()
	seen := trajectory.NewSeenStore(rdb, 10_000, time.Hour, time.Hour)
	clock := newTestClock()
	gate := newTestGate(t, writer, rdb, clock, nil, true)
	srv := New(Config{BatchMaxRows: 100, BatchMaxAge: time.Minute}, writer, nil, NoopUsageEmitter{}, NoopMetering{},
		WithLogger(slog.New(slog.NewTextHandler(io.Discard, nil))),
		WithTrajectory(cfg, seen), WithTrajectoryGate(gate),
	)

	pid := "11111111-1111-4111-8111-111111111111"
	oid := "99999999-9999-4999-8999-999999999999"
	sysPrompt := "System prompt " + strings.Repeat("rules ", 100)
	msgs1 := []map[string]any{
		{"role": "system", "content": sysPrompt},
		{"role": "user", "content": "Hello turn 1 " + strings.Repeat("x", 200)},
	}
	postTrajOTLP(t, srv, oid, pid, makeAgentHTTPReq(t, pid, oid, "sess-1", 0x01, 0x01, msgs1, "Reply turn 1"))
	srv.drainNow(context.Background())

	inserts := ch.snapshot()
	trajIdx, spanIdx := -1, -1
	for i, ins := range inserts {
		if ins.Table == "trajectory_items" && trajIdx == -1 {
			trajIdx = i
		}
		if ins.Table == "spans" && spanIdx == -1 {
			spanIdx = i
		}
	}
	if trajIdx == -1 || spanIdx == -1 || trajIdx >= spanIdx {
		t.Fatalf("expected trajectory_items insert BEFORE spans insert; got order: %+v", inserts)
	}
	if len(inserts[trajIdx].Rows) != 3 {
		t.Fatalf("turn 1 should write 3 trajectory_items (system, user, assistant), got %d", len(inserts[trajIdx].Rows))
	}
	if inserts[spanIdx].Rows[0]["payload_encoding"] != trajectory.EncodingItemsV1 {
		t.Fatalf("turn 1 span payload_encoding = %v, want items_v1", inserts[spanIdx].Rows[0]["payload_encoding"])
	}

	// Now simulate ClickHouse trajectory_items 500 failure on Turn 2!
	ch.mu.Lock()
	ch.failTrajItem = true
	ch.inserts = nil
	ch.mu.Unlock()

	msgs2 := append(msgs1,
		map[string]any{"role": "assistant", "content": "Reply turn 1"},
		map[string]any{"role": "user", "content": "Hello turn 2 " + strings.Repeat("y", 200)},
	)
	postTrajOTLP(t, srv, oid, pid, makeAgentHTTPReq(t, pid, oid, "sess-1", 0x01, 0x02, msgs2, "Reply turn 2"))
	srv.drainNow(context.Background())

	afterFail := ch.snapshot()
	var fallbackSpan map[string]any
	for _, ins := range afterFail {
		if ins.Table == "spans" && len(ins.Rows) == 1 {
			fallbackSpan = ins.Rows[0]
		}
	}
	if fallbackSpan == nil {
		t.Fatalf("expected spans insert to succeed via inline fallback")
	}
	if _, hasEnc := fallbackSpan["payload_encoding"]; hasEnc {
		t.Fatalf("fallback span must NOT carry payload_encoding, got %v", fallbackSpan["payload_encoding"])
	}
	if !strings.Contains(fallbackSpan["input"].(string), "Hello turn 2") {
		t.Fatalf("fallback span did not restore full original input: %v", fallbackSpan["input"])
	}

	// The failed items insert paused dedup (breaker); it resumes after the hold.
	if gate.Open() {
		t.Fatal("a failed trajectory_items insert must pause dedup")
	}
	clock.Advance(trajectory.DefaultBreakerHold + time.Second)
	if !gate.Open() {
		t.Fatal("dedup must resume once the breaker hold has passed")
	}

	// Recover ClickHouse: Turn 2's NEW items were never marked seen, so Turn 3 re-emits them!
	ch.mu.Lock()
	ch.failTrajItem = false
	ch.inserts = nil
	ch.mu.Unlock()

	postTrajOTLP(t, srv, oid, pid, makeAgentHTTPReq(t, pid, oid, "sess-1", 0x01, 0x03, msgs2, "Reply turn 2"))
	srv.drainNow(context.Background())

	recovered := ch.snapshot()
	foundItems := 0
	for _, ins := range recovered {
		if ins.Table == "trajectory_items" {
			foundItems += len(ins.Rows)
		}
	}
	if foundItems < 2 {
		t.Fatalf("expected forgotten turn 2 items to be re-emitted after recovery, got %d items", foundItems)
	}
}

func TestServerCrossPodDedupBillingShadowAndLive(t *testing.T) {
	ch := &fakeTrajectoryCH{}
	chSrv := httptest.NewServer(ch.handler())
	defer chSrv.Close()

	writer, err := chwriter.New(chwriter.Config{
		URL:            chSrv.URL,
		Database:       "default",
		Table:          "spans",
		MaxRetries:     1,
		DeadLetterFile: filepath.Join(t.TempDir(), "dlq.jsonl"),
	})
	if err != nil {
		t.Fatalf("chwriter.New: %v", err)
	}
	defer writer.Close()

	mr := miniredis.RunT(t)
	rdb := redis.NewClient(&redis.Options{Addr: mr.Addr()})
	defer rdb.Close()

	pid := "11111111-1111-4111-8111-111111111111"
	oid := "99999999-9999-4999-8999-999999999999"
	log := slog.New(slog.NewTextHandler(io.Discard, nil))
	usage := auth.NewUsageEmitter(rdb, nil, log, 1000)

	// Build a big system prompt + 10 prior turns so Turn 11 has massive dedup savings.
	sysPrompt := "System instructions: " + strings.Repeat("Always obey policy X and verify Y. ", 120)
	baseMsgs := []map[string]any{{"role": "system", "content": sysPrompt}}
	for i := 1; i <= 10; i++ {
		baseMsgs = append(baseMsgs,
			map[string]any{"role": "user", "content": fmt.Sprintf("Prior user msg %d %s", i, strings.Repeat("u", 300))},
			map[string]any{"role": "assistant", "content": fmt.Sprintf("Prior assistant msg %d %s", i, strings.Repeat("a", 300))},
		)
	}
	nextMsgs := append(append([]map[string]any(nil), baseMsgs...), map[string]any{"role": "user", "content": "Final short question"})

	for _, tc := range []struct {
		name        string
		billDeduped *bool
		wantBasis   string
	}{
		{name: "kill_switch_false_bills_raw", billDeduped: boolPtr(false), wantBasis: "raw_shadow"},
		{name: "explicit_true_bills_stored", billDeduped: boolPtr(true), wantBasis: "deduped_storage"},
		{name: "unset_defaults_to_stored_with_dedup", billDeduped: nil, wantBasis: "deduped_storage"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			mr.FlushAll()
			cfg := trajectory.Config{
				Mode:             trajectory.ModeOn,
				BillDedupedBytes: tc.billDeduped,
			}.Normalize()
			clock := newTestClock()
			// Two completely separate collector pods sharing ONLY Redis!
			podA := New(Config{BatchMaxRows: 100, BatchMaxAge: time.Minute}, writer, nil, usage, NoopMetering{},
				WithLogger(log), WithTrajectory(cfg, trajectory.NewSeenStore(rdb, 10_000, time.Hour, time.Hour)),
				WithTrajectoryGate(newTestGate(t, writer, rdb, clock, log, true)))
			podB := New(Config{BatchMaxRows: 100, BatchMaxAge: time.Minute}, writer, nil, usage, NoopMetering{},
				WithLogger(log), WithTrajectory(cfg, trajectory.NewSeenStore(rdb, 10_000, time.Hour, time.Hour)),
				WithTrajectoryGate(newTestGate(t, writer, rdb, clock, log, true)))

			// Turn 10 lands on Pod A and commits durably to ClickHouse.
			postTrajOTLP(t, podA, oid, pid, makeAgentHTTPReq(t, pid, oid, "sess-bill", 0x10, 0x01, baseMsgs, "Prior reply 10"))
			podA.drainNow(context.Background())
			// Turn 11 (reusing all 21 committed messages!) lands on Pod B and drains.
			postTrajOTLP(t, podB, oid, pid, makeAgentHTTPReq(t, pid, oid, "sess-bill", 0x11, 0x02, nextMsgs, "Final short reply"))
			podB.drainNow(context.Background())

			entries, err := rdb.XRange(context.Background(), "usage:events", "-", "+").Result()
			if err != nil {
				t.Fatalf("XRange usage:events: %v", err)
			}
			if len(entries) != 2 {
				t.Fatalf("want 2 usage events, got %d", len(entries))
			}

			// Inspect Pod B's billing event (entries[1]).
			ev := entries[1].Values
			if ev["event_type"] != "observe_add" {
				t.Fatalf("event_type = %v, want observe_add", ev["event_type"])
			}
			var props struct {
				BillingBasis    string `json:"billing_basis"`
				RawBytes        int64  `json:"raw_bytes"`
				StoredBytes     int64  `json:"stored_bytes"`
				NewItemBytes    int64  `json:"new_item_bytes"`
				DedupSavedBytes int64  `json:"dedup_saved_bytes"`
				NewItems        int    `json:"new_items"`
				ReusedItems     int    `json:"reused_items"`
			}
			if err := json.Unmarshal([]byte(ev["properties"].(string)), &props); err != nil {
				t.Fatalf("unmarshal properties: %v", err)
			}
			if props.BillingBasis != tc.wantBasis {
				t.Fatalf("billing_basis = %q, want %q", props.BillingBasis, tc.wantBasis)
			}
			if props.ReusedItems != 21 || props.NewItems != 2 {
				t.Fatalf("cross-pod item counts wrong: new=%d reused=%d, want 2/21", props.NewItems, props.ReusedItems)
			}
			if props.DedupSavedBytes <= 0 || props.StoredBytes >= props.RawBytes {
				t.Fatalf("expected significant dedup savings on Pod B: %+v", props)
			}

			var billedAmount int64
			if _, err := fmt.Sscanf(ev["amount"].(string), "%d", &billedAmount); err != nil {
				t.Fatalf("parse billed amount %q: %v", ev["amount"], err)
			}
			if tc.wantBasis == "deduped_storage" {
				if billedAmount != props.StoredBytes {
					t.Fatalf("live mode: amount=%d, want stored_bytes=%d (raw=%d)", billedAmount, props.StoredBytes, props.RawBytes)
				}
				ratio := float64(props.RawBytes) / float64(billedAmount)
				t.Logf("live dedup billing on Pod B: raw=%d B -> billed=%d B (%.1fx savings!)", props.RawBytes, billedAmount, ratio)
				if ratio < 4.0 {
					t.Fatalf("expected >= 4x billing reduction on Pod B Turn 11, got %.2fx", ratio)
				}
			} else {
				if billedAmount != props.RawBytes {
					t.Fatalf("shadow mode: amount=%d, want raw_bytes=%d", billedAmount, props.RawBytes)
				}
			}
		})
	}
}

func TestServerUncommittedItemsNotReusedAndFallbackBillsRawBytes(t *testing.T) {
	ch := &fakeTrajectoryCH{}
	chSrv := httptest.NewServer(ch.handler())
	defer chSrv.Close()

	writer, err := chwriter.New(chwriter.Config{
		URL:            chSrv.URL,
		Database:       "default",
		Table:          "spans",
		MaxRetries:     1,
		DeadLetterFile: filepath.Join(t.TempDir(), "dlq.jsonl"),
	})
	if err != nil {
		t.Fatalf("chwriter.New: %v", err)
	}
	defer writer.Close()

	mr := miniredis.RunT(t)
	rdb := redis.NewClient(&redis.Options{Addr: mr.Addr()})
	defer rdb.Close()

	pid := "11111111-1111-4111-8111-111111111111"
	oid := "99999999-9999-4999-8999-999999999999"
	log := slog.New(slog.NewTextHandler(io.Discard, nil))
	usage := auth.NewUsageEmitter(rdb, nil, log, 1000)

	cfg := trajectory.Config{
		Mode:             trajectory.ModeOn,
		BillDedupedBytes: boolPtr(true),
	}.Normalize()

	clock := newTestClock()
	podA := New(Config{BatchMaxRows: 100, BatchMaxAge: time.Minute}, writer, nil, usage, NoopMetering{},
		WithLogger(log), WithTrajectory(cfg, trajectory.NewSeenStore(rdb, 10_000, time.Hour, time.Hour)),
		WithTrajectoryGate(newTestGate(t, writer, rdb, clock, log, true)))
	podB := New(Config{BatchMaxRows: 100, BatchMaxAge: time.Minute}, writer, nil, usage, NoopMetering{},
		WithLogger(log), WithTrajectory(cfg, trajectory.NewSeenStore(rdb, 10_000, time.Hour, time.Hour)),
		WithTrajectoryGate(newTestGate(t, writer, rdb, clock, log, true)))

	msgs := []map[string]any{
		{"role": "system", "content": "Shared prompt " + strings.Repeat("s", 300)},
		{"role": "user", "content": "Concurrent question " + strings.Repeat("q", 300)},
	}

	// 1. Seen-before-commit guard: Pod A enqueues Turn 1 WITHOUT draining yet.
	//    Pod B receives Turn 2 concurrently and MUST NOT assume Pod A's items
	//    are committed — Pod B must write all items itself when it drains.
	postTrajOTLP(t, podA, oid, pid, makeAgentHTTPReq(t, pid, oid, "sess-race", 0x20, 0x01, msgs, "Reply A"))
	postTrajOTLP(t, podB, oid, pid, makeAgentHTTPReq(t, pid, oid, "sess-race", 0x20, 0x02, msgs, "Reply B"))

	// Even if Pod A never drains (simulating pod crash), Pod B's drain writes
	// every referenced trajectory_item durably before its span!
	podB.drainNow(context.Background())
	podBInserts := ch.snapshot()
	trajRows := 0
	for _, ins := range podBInserts {
		if ins.Table == "trajectory_items" {
			trajRows += len(ins.Rows)
		}
	}
	if trajRows != 3 {
		t.Fatalf("podB must write all 3 items when podA has not committed yet; got %d", trajRows)
	}

	// 2. Post-commit billing fallback guard: simulate trajectory_items outage
	//    on a fresh session with BillDedupedBytes=true. Usage MUST NOT be emitted
	//    until drainNow and MUST bill exact raw_bytes when falling back to inline.
	rdb.Del(context.Background(), "usage:events")
	ch.mu.Lock()
	ch.failTrajItem = true
	ch.mu.Unlock()

	fallbackMsgs := []map[string]any{
		{"role": "system", "content": "Outage prompt " + strings.Repeat("z", 400)},
		{"role": "user", "content": "Outage user turn " + strings.Repeat("w", 400)},
	}
	postTrajOTLP(t, podA, oid, pid, makeAgentHTTPReq(t, pid, oid, "sess-fb-bill", 0x21, 0x01, fallbackMsgs, "Reply fb"))

	// Before drainNow, no usage event for the deduped request may be emitted yet.
	beforeDrain, _ := rdb.XRange(context.Background(), "usage:events", "-", "+").Result()
	if len(beforeDrain) != 0 {
		t.Fatalf("usage emitted before trajectory_items commit: got %d events", len(beforeDrain))
	}

	podA.drainNow(context.Background())
	afterDrain, err := rdb.XRange(context.Background(), "usage:events", "-", "+").Result()
	if err != nil {
		t.Fatalf("XRange usage:events: %v", err)
	}
	// podA drained both the earlier sess-race request (which fell back too) and sess-fb-bill.
	if len(afterDrain) == 0 {
		t.Fatalf("expected usage events after drainNow")
	}
	last := afterDrain[len(afterDrain)-1].Values
	var props struct {
		RawBytes        int64 `json:"raw_bytes"`
		StoredBytes     int64 `json:"stored_bytes"`
		DedupSavedBytes int64 `json:"dedup_saved_bytes"`
	}
	if err := json.Unmarshal([]byte(last["properties"].(string)), &props); err != nil {
		t.Fatalf("unmarshal properties: %v", err)
	}
	var billed int64
	if _, err := fmt.Sscanf(last["amount"].(string), "%d", &billed); err != nil {
		t.Fatalf("parse amount: %v", err)
	}
	if props.DedupSavedBytes != 0 || props.StoredBytes != props.RawBytes || billed != props.RawBytes {
		t.Fatalf("inline fallback must bill raw_bytes=%d, got billed=%d stored=%d saved=%d",
			props.RawBytes, billed, props.StoredBytes, props.DedupSavedBytes)
	}
}

// lockedBuffer is a goroutine-safe log sink.
type lockedBuffer struct {
	mu  sync.Mutex
	buf bytes.Buffer
}

func (b *lockedBuffer) Write(p []byte) (int, error) {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.buf.Write(p)
}

func (b *lockedBuffer) String() string {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.buf.String()
}

// trajFixture is one collector pod over the fake ClickHouse and miniredis,
// with a real gate over the writer's metadata queries.
type trajFixture struct {
	ch     *fakeTrajectoryCH
	writer *chwriter.Writer
	mr     *miniredis.Miniredis
	rdb    *redis.Client
	clock  *testClock
	gate   *trajectory.Gate
	srv    *Server
	logs   *lockedBuffer
	dlq    string
}

const (
	trajTestPID = "11111111-1111-4111-8111-111111111111"
	trajTestOID = "99999999-9999-4999-8999-999999999999"
)

func newTrajFixture(t *testing.T, cfg trajectory.Config, ch *fakeTrajectoryCH, wantOpen bool) *trajFixture {
	t.Helper()
	f := &trajFixture{ch: ch, clock: newTestClock(), logs: &lockedBuffer{}, dlq: filepath.Join(t.TempDir(), "dlq.jsonl")}
	chSrv := httptest.NewServer(ch.handler())
	t.Cleanup(chSrv.Close)
	var err error
	f.writer, err = chwriter.New(chwriter.Config{
		URL: chSrv.URL, Database: "default", Table: "spans", MaxRetries: 5,
		InitialBackoff: time.Millisecond, MaxBackoff: 2 * time.Millisecond, DeadLetterFile: f.dlq,
	})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { f.writer.Close() })
	f.mr = miniredis.RunT(t)
	f.rdb = redis.NewClient(&redis.Options{Addr: f.mr.Addr()})
	t.Cleanup(func() { f.rdb.Close() })
	log := slog.New(slog.NewJSONHandler(f.logs, nil))
	f.gate = newTestGate(t, f.writer, f.rdb, f.clock, log, wantOpen)
	f.srv = New(Config{BatchMaxRows: 100, BatchMaxAge: time.Minute}, f.writer, nil, NoopUsageEmitter{}, NoopMetering{},
		WithLogger(log), WithTrajectory(cfg, trajectory.NewSeenStore(f.rdb, 10_000, time.Hour, time.Hour)), WithTrajectoryGate(f.gate))
	return f
}

func agentTurn(t *testing.T, turn int) []byte {
	t.Helper()
	msgs := []map[string]any{{"role": "system", "content": "System prompt " + strings.Repeat("rules ", 120)}}
	for i := 1; i <= turn; i++ {
		msgs = append(msgs, map[string]any{"role": "user", "content": fmt.Sprintf("Question %d %s", i, strings.Repeat("q", 300))})
	}
	return makeAgentHTTPReq(t, trajTestPID, trajTestOID, "sess-gate", 0x40, byte(turn), msgs, fmt.Sprintf("Reply %d", turn))
}

func insertsInto(inserts []recordedCHInsert, table string) []recordedCHInsert {
	var out []recordedCHInsert
	for _, ins := range inserts {
		if ins.Table == table {
			out = append(out, ins)
		}
	}
	return out
}

// requireNoPointerKeys checks rows carry no pointer column and no
// compaction sentinel: a row converted with the gate closed was never
// compacted, and a row restored inline at drain time is the dedup-off row.
func requireNoPointerKeys(t *testing.T, rows []map[string]any) {
	t.Helper()
	for _, row := range rows {
		for _, col := range trajectory.SpansPointerColumns {
			if _, ok := row[col.Name]; ok {
				t.Fatalf("row carries pointer column %s: %v", col.Name, row[col.Name])
			}
		}
		for k := range row["attributes_extra"].(map[string]any) {
			if strings.HasPrefix(k, "__fi_promoted_") {
				t.Fatalf("row carries compaction sentinel %s", k)
			}
		}
	}
}

// recordingTrajUsage records which emission path each request took.
type recordingTrajUsage struct {
	mu     sync.Mutex
	legacy int
	dedup  []bool // billDeduped per dedup event
	raw    []int64
	stored []int64
}

func (u *recordingTrajUsage) EmitIngestion(string, int, int, int64, string) {
	u.mu.Lock()
	defer u.mu.Unlock()
	u.legacy++
}

func (u *recordingTrajUsage) EmitIngestionDedup(_ string, _, _ int, raw, stored, _, _ int64, _, _ int, bill bool, _ string) {
	u.mu.Lock()
	defer u.mu.Unlock()
	u.dedup = append(u.dedup, bill)
	u.raw = append(u.raw, raw)
	u.stored = append(u.stored, stored)
}

func (u *recordingTrajUsage) counts() (legacy, dedup int) {
	u.mu.Lock()
	defer u.mu.Unlock()
	return u.legacy, len(u.dedup)
}

// TestServerDedupInactiveUntilSchemaReady: with 5 of the 6 pointer columns
// the gate stays closed, so the spans POST carries no pointer keys, nothing is
// sent to trajectory_items and usage takes dev's legacy path. Once the sixth
// column appears, one re-check opens the gate and pointer rows appear.
func TestServerDedupInactiveUntilSchemaReady(t *testing.T) {
	ch := &fakeTrajectoryCH{}
	ch.setMissing("payload_envelope")
	f := newTrajFixture(t, trajectory.Config{Mode: trajectory.ModeOn}, ch, false)
	usage := &recordingTrajUsage{}
	f.srv.usage = usage

	postTrajOTLP(t, f.srv, trajTestOID, trajTestPID, agentTurn(t, 1))
	f.srv.drainNow(context.Background())
	inserts := ch.snapshot()
	if len(insertsInto(inserts, "trajectory_items")) != 0 {
		t.Fatal("no trajectory_items insert may happen before the schema is complete")
	}
	spans := insertsInto(inserts, "spans")
	if len(spans) != 1 {
		t.Fatalf("spans inserts %d", len(spans))
	}
	requireNoPointerKeys(t, spans[0].Rows)
	if strings.Contains(spans[0].RawQuery, "input_format_skip_unknown_fields") {
		t.Fatal("an inline batch must use dev's insert URL")
	}
	if legacy, dedup := usage.counts(); legacy != 1 || dedup != 0 {
		t.Fatalf("usage legacy=%d dedup=%d, want the legacy event only", legacy, dedup)
	}
	if !strings.Contains(f.logs.String(), `"event":"trajectory_schema_missing"`) {
		t.Fatal("the missing column must be logged")
	}

	ch.setMissing()
	ch.reset()
	f.gate.Check(context.Background())
	if !f.gate.Open() {
		t.Fatal("one re-check with all six columns must open the gate")
	}
	postTrajOTLP(t, f.srv, trajTestOID, trajTestPID, agentTurn(t, 2))
	f.srv.drainNow(context.Background())
	inserts = ch.snapshot()
	if len(insertsInto(inserts, "trajectory_items")) != 1 {
		t.Fatal("open gate: items must be written")
	}
	spans = insertsInto(inserts, "spans")
	if spans[0].Rows[0]["payload_encoding"] != trajectory.EncodingItemsV1 {
		t.Fatal("open gate: pointer rows expected")
	}
}

// TestDrainRestoresInlineWhenGateFlipsMissing: the gate closes between
// conversion and drain, so the drain writes the spans inline and skips the
// items insert.
func TestDrainRestoresInlineWhenGateFlipsMissing(t *testing.T) {
	ch := &fakeTrajectoryCH{}
	f := newTrajFixture(t, trajectory.Config{Mode: trajectory.ModeOn}, ch, true)
	body := agentTurn(t, 1)
	postTrajOTLP(t, f.srv, trajTestOID, trajTestPID, body)
	ch.setMissing("tool_def_hashes")
	f.gate.Check(context.Background())
	if f.gate.Open() {
		t.Fatal("gate must close")
	}
	f.srv.drainNow(context.Background())
	inserts := ch.snapshot()
	if len(insertsInto(inserts, "trajectory_items")) != 0 {
		t.Fatal("closed gate at drain: no items insert")
	}
	spans := insertsInto(inserts, "spans")
	requireNoPointerKeys(t, spans[0].Rows)
	if !strings.Contains(spans[0].Rows[0]["input"].(string), "Question 1") {
		t.Fatal("the full input must be restored")
	}
	if strings.Contains(spans[0].RawQuery, "input_format_skip_unknown_fields") {
		t.Fatal("restored batch must use dev's insert URL")
	}
	if !strings.Contains(f.logs.String(), `"event":"trajectory_dedup_inline_due_to_gate"`) {
		t.Fatal("inline-due-to-gate not logged")
	}
}

// TestItemsV1BatchUnknownColumnClosesGate: a pointer batch is sent with
// input_format_skip_unknown_fields=0, so a ClickHouse replica without the 040
// columns refuses it instead of storing previews as whole payloads. The refusal
// closes the gate and the request is billed raw. The refused rows are not
// dead-lettered: the drain restores them inline and sends them once more on
// dev's URL, together with the rows of a project without dedup that shared the
// batch, so every span is stored. The next request is inline on dev's URL.
func TestItemsV1BatchUnknownColumnClosesGate(t *testing.T) {
	const otherPID = "22222222-2222-4222-8222-222222222222"
	ch := &fakeTrajectoryCH{strictSpansError: "Code: 117. DB::Exception: Unknown field found while parsing JSONEachRow format: payload_envelope: (at row 1)"}
	f := newTrajFixture(t, trajectory.Config{Mode: trajectory.ModeCanary, CanaryProjectList: []string{trajTestPID}}, ch, true)
	usage := &recordingTrajUsage{}
	f.srv.usage = usage

	postTrajOTLP(t, f.srv, trajTestOID, trajTestPID, agentTurn(t, 1))
	otherMsgs := []map[string]any{{"role": "user", "content": "Other project question " + strings.Repeat("o", 300)}}
	postTrajOTLP(t, f.srv, trajTestOID, otherPID, makeAgentHTTPReq(t, otherPID, trajTestOID, "sess-other", 0x41, 1, otherMsgs, "Other reply"))
	f.srv.drainNow(context.Background())
	spans := insertsInto(ch.snapshot(), "spans")
	if len(spans) != 2 {
		t.Fatalf("want the refused strict insert and one inline resend, got %d spans inserts", len(spans))
	}
	q, _ := url.ParseQuery(spans[0].RawQuery)
	if q.Get("input_format_skip_unknown_fields") != "0" {
		t.Fatalf("a pointer batch must disable skip_unknown_fields: %s", spans[0].RawQuery)
	}
	if spans[1].RawQuery != strings.SplitN(spans[0].RawQuery, "&input_format_skip_unknown_fields=0", 2)[0] {
		t.Fatalf("the resend must use dev's URL: %s", spans[1].RawQuery)
	}
	resent := spans[1].Rows
	if len(resent) != 2 {
		t.Fatalf("both projects' rows must be resent, got %d", len(resent))
	}
	requireNoPointerKeys(t, resent)
	var gotDedup, gotOther bool
	for _, row := range resent {
		in, _ := row["input"].(string)
		switch row["project_id"] {
		case trajTestPID:
			gotDedup = strings.Contains(in, "Question 1 "+strings.Repeat("q", 300))
		case otherPID:
			gotOther = strings.Contains(in, "Other project question")
		}
	}
	if !gotDedup || !gotOther {
		t.Fatalf("resent rows must carry their full inputs (dedup project %v, other project %v)", gotDedup, gotOther)
	}
	if f.gate.Open() {
		t.Fatal("a refused pointer column must close the gate")
	}
	if dead, err := os.ReadFile(f.dlq); err == nil && len(dead) != 0 {
		t.Fatalf("resent rows must not be dead-lettered: %d bytes", len(dead))
	}
	if s := f.writer.Snapshot(); s.RowsDeadLettered != 0 {
		t.Fatalf("rows dead-lettered: %+v", s)
	}
	if !strings.Contains(f.logs.String(), `"event":"trajectory_spans_inline_resend"`) {
		t.Fatal("the inline resend must be logged")
	}
	usage.mu.Lock()
	billedRaw := len(usage.dedup) >= 1 && len(usage.dedup)+usage.legacy == 2
	for i := range usage.raw {
		billedRaw = billedRaw && usage.stored[i] == usage.raw[i]
	}
	usage.mu.Unlock()
	if !billedRaw {
		t.Fatalf("every request of a refused pointer batch must bill raw bytes: %+v", usage)
	}

	// The next request is converted inline and sent on dev's URL.
	ch.reset()
	postTrajOTLP(t, f.srv, trajTestOID, trajTestPID, agentTurn(t, 2))
	f.srv.drainNow(context.Background())
	spans = insertsInto(ch.snapshot(), "spans")
	requireNoPointerKeys(t, spans[0].Rows)
	if strings.Contains(spans[0].RawQuery, "input_format_skip_unknown_fields") {
		t.Fatal("inline batches must use dev's URL")
	}
}

// TestItemsV1BatchRefusedResendFailureDeadLettersInline: when the inline
// resend of a refused pointer batch also fails, the writer dead-letters the
// rows as they were resent: inline, with no pointer column, so a replay into
// spans stores the full payloads.
func TestItemsV1BatchRefusedResendFailureDeadLettersInline(t *testing.T) {
	ch := &fakeTrajectoryCH{
		strictSpansError: "Code: 117. DB::Exception: Unknown field found while parsing JSONEachRow format: payload_envelope: (at row 1)",
		failInlineSpans:  true,
	}
	f := newTrajFixture(t, trajectory.Config{Mode: trajectory.ModeOn}, ch, true)
	postTrajOTLP(t, f.srv, trajTestOID, trajTestPID, agentTurn(t, 1))
	f.srv.drainNow(context.Background())
	dead, err := os.ReadFile(f.dlq)
	if err != nil || len(dead) == 0 {
		t.Fatalf("the failed resend must be dead-lettered: %v", err)
	}
	var rows []map[string]any
	for _, line := range bytes.Split(bytes.TrimSpace(dead), []byte("\n")) {
		var m map[string]any
		if err := json.Unmarshal(line, &m); err != nil {
			t.Fatal(err)
		}
		rows = append(rows, m)
	}
	if len(rows) != 1 {
		t.Fatalf("dead-lettered rows %d, want the span once (not the refused pointer row too)", len(rows))
	}
	requireNoPointerKeys(t, rows)
	if in, _ := rows[0]["input"].(string); !strings.Contains(in, "Question 1 "+strings.Repeat("q", 300)) {
		t.Fatal("the dead-lettered row must carry the full input")
	}
	if !strings.Contains(f.logs.String(), `"event":"trajectory_spans_inline_resend_failed"`) {
		t.Fatal("the failed resend must be logged")
	}
}

// TestDrainItemsFailureUsesOneShortBudget: trajectory_items gets
// items_max_retries+1 = 2 attempts per drain (not the spans writer's 6), with
// the quorum settings on its URL, then the drain falls back inline.
func TestDrainItemsFailureUsesOneShortBudget(t *testing.T) {
	ch := &fakeTrajectoryCH{failTrajItem: true}
	f := newTrajFixture(t, trajectory.Config{Mode: trajectory.ModeOn}, ch, true)
	postTrajOTLP(t, f.srv, trajTestOID, trajTestPID, agentTurn(t, 1))
	f.srv.drainNow(context.Background())
	if _, attempts := ch.counts(); attempts != 2 {
		t.Fatalf("trajectory_items attempts %d, want 2", attempts)
	}
	spans := insertsInto(ch.snapshot(), "spans")
	requireNoPointerKeys(t, spans[0].Rows)
	if !strings.Contains(f.logs.String(), `"event":"trajectory_items_fallback_inline"`) {
		t.Fatal("fallback not logged")
	}

	// A successful items insert carries the topology's quorum settings.
	ch.mu.Lock()
	ch.failTrajItem = false
	ch.mu.Unlock()
	ch.reset()
	f.clock.Advance(trajectory.DefaultBreakerHold + time.Second)
	postTrajOTLP(t, f.srv, trajTestOID, trajTestPID, agentTurn(t, 2))
	f.srv.drainNow(context.Background())
	items := insertsInto(ch.snapshot(), "trajectory_items")
	if len(items) != 1 {
		t.Fatalf("items inserts %d", len(items))
	}
	q, _ := url.ParseQuery(items[0].RawQuery)
	if q.Get("insert_quorum") != "auto" || q.Get("insert_quorum_timeout") != "4000" || q.Get("distributed_foreground_insert") != "1" {
		t.Fatalf("items insert settings: %s", items[0].RawQuery)
	}
}

// TestTrajectoryBreakerSkipsExtractionAfterFailure: within 30 s of a failed
// items insert, requests are converted inline (no pointer keys, no items
// POST); after 30 s extraction resumes.
func TestTrajectoryBreakerSkipsExtractionAfterFailure(t *testing.T) {
	ch := &fakeTrajectoryCH{failTrajItem: true}
	f := newTrajFixture(t, trajectory.Config{Mode: trajectory.ModeOn}, ch, true)
	postTrajOTLP(t, f.srv, trajTestOID, trajTestPID, agentTurn(t, 1))
	f.srv.drainNow(context.Background())

	ch.mu.Lock()
	ch.failTrajItem = false
	ch.mu.Unlock()
	ch.reset()
	f.clock.Advance(trajectory.DefaultBreakerHold - time.Second)
	postTrajOTLP(t, f.srv, trajTestOID, trajTestPID, agentTurn(t, 2))
	f.srv.drainNow(context.Background())
	inserts := ch.snapshot()
	if _, attempts := ch.counts(); attempts != 0 {
		t.Fatal("no items POST while the breaker is open")
	}
	requireNoPointerKeys(t, insertsInto(inserts, "spans")[0].Rows)

	ch.reset()
	f.clock.Advance(2 * time.Second)
	postTrajOTLP(t, f.srv, trajTestOID, trajTestPID, agentTurn(t, 3))
	f.srv.drainNow(context.Background())
	spans := insertsInto(ch.snapshot(), "spans")
	if spans[0].Rows[0]["payload_encoding"] != trajectory.EncodingItemsV1 {
		t.Fatal("extraction must resume after the breaker hold")
	}
}

// TestBillFlagAloneEmitsLegacyEvent: the billing flag without dedup (or with
// the gate closed) emits exactly dev's stream entry, properties
// {"spans":N,"source":"fi-collector"}.
func TestBillFlagAloneEmitsLegacyEvent(t *testing.T) {
	ch := &fakeTrajectoryCH{}
	chSrv := httptest.NewServer(ch.handler())
	defer chSrv.Close()
	writer, err := chwriter.New(chwriter.Config{URL: chSrv.URL, Database: "default", Table: "spans", MaxRetries: 1, DeadLetterFile: filepath.Join(t.TempDir(), "dlq.jsonl")})
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	mr := miniredis.RunT(t)
	rdb := redis.NewClient(&redis.Options{Addr: mr.Addr()})
	defer rdb.Close()
	log := slog.New(slog.NewTextHandler(io.Discard, nil))
	usage := auth.NewUsageEmitter(rdb, nil, log, 1000)
	closedGate := trajectory.NewGate(trajectory.GateConfig{Prober: writer, Redis: rdb, Key: "absent-key", Log: log})
	closedGate.Check(context.Background())

	streamEntry := func(srv *Server) map[string]any {
		t.Helper()
		mr.Del("usage:events")
		postTrajOTLP(t, srv, trajTestOID, trajTestPID, agentTurn(t, 1))
		srv.drainNow(context.Background())
		entries, err := rdb.XRange(context.Background(), "usage:events", "-", "+").Result()
		if err != nil || len(entries) != 1 {
			t.Fatalf("want one usage event, got %d (%v)", len(entries), err)
		}
		v := entries[0].Values
		delete(v, "timestamp")
		return v
	}
	dev := streamEntry(New(Config{BatchMaxRows: 100, BatchMaxAge: time.Minute}, writer, nil, usage, NoopMetering{}, WithLogger(log)))
	if dev["properties"] != `{"spans":1,"source":"fi-collector"}` {
		t.Fatalf("dev properties changed: %v", dev["properties"])
	}
	for name, opts := range map[string][]Option{
		"bill flag, dedup off": {WithTrajectory(trajectory.Config{BillDedupedBytes: boolPtr(true)}, nil)},
		"bill flag, dedup on, gate closed": {
			WithTrajectory(trajectory.Config{Mode: trajectory.ModeOn, BillDedupedBytes: boolPtr(true)}, trajectory.NewSeenStore(rdb, 100, time.Hour, time.Hour)),
			WithTrajectoryGate(closedGate),
		},
		"dedup on, no gate": {WithTrajectory(trajectory.Config{Mode: trajectory.ModeOn}, nil)},
	} {
		got := streamEntry(New(Config{BatchMaxRows: 100, BatchMaxAge: time.Minute}, writer, nil, usage, NoopMetering{}, append([]Option{WithLogger(log)}, opts...)...))
		gb, _ := json.Marshal(got)
		db, _ := json.Marshal(dev)
		if string(gb) != string(db) {
			t.Fatalf("%s: stream entry differs from dev:\n got %s\nwant %s", name, gb, db)
		}
	}
}

// TestServerDormantDefaultsIdenticalToDev: with every trajectory setting at
// its default, the spans insert is sent on dev's URL byte for byte, the rows
// equal those of a server without the feature, and no metadata query runs.
func TestServerDormantDefaultsIdenticalToDev(t *testing.T) {
	body := agentTurn(t, 3) // one request for both runs: same timestamps
	run := func(opts ...Option) recordedCHInsert {
		t.Helper()
		ch := &fakeTrajectoryCH{}
		chSrv := httptest.NewServer(ch.handler())
		defer chSrv.Close()
		writer, err := chwriter.New(chwriter.Config{URL: chSrv.URL, Database: "default", Table: "spans", MaxRetries: 1, DeadLetterFile: filepath.Join(t.TempDir(), "dlq.jsonl")})
		if err != nil {
			t.Fatal(err)
		}
		defer writer.Close()
		srv := New(Config{BatchMaxRows: 100, BatchMaxAge: time.Minute}, writer, nil, NoopUsageEmitter{}, NoopMetering{},
			append([]Option{WithLogger(slog.New(slog.NewTextHandler(io.Discard, nil)))}, opts...)...)
		postTrajOTLP(t, srv, trajTestOID, trajTestPID, body)
		srv.drainNow(context.Background())
		if metadata, items := ch.counts(); metadata != 0 || items != 0 {
			t.Fatalf("dormant: %d metadata queries, %d items inserts", metadata, items)
		}
		spans := insertsInto(ch.snapshot(), "spans")
		if len(spans) != 1 {
			t.Fatalf("spans inserts %d", len(spans))
		}
		return spans[0]
	}
	dev := run()
	dormant := run(WithTrajectory(trajectory.Config{}, nil))
	if dev.RawQuery != dormant.RawQuery {
		t.Fatalf("insert URL changed: %q vs %q", dormant.RawQuery, dev.RawQuery)
	}
	if dev.RawQuery != "database=default&query=INSERT+INTO+spans+FORMAT+JSONEachRow&async_insert=0" {
		t.Fatalf("dev insert URL changed: %q", dev.RawQuery)
	}
	strip := func(rows []map[string]any) string {
		for _, r := range rows {
			delete(r, "_version")
		}
		b, _ := json.Marshal(rows)
		return string(b)
	}
	if strip(dev.Rows) != strip(dormant.Rows) {
		t.Fatal("dormant rows differ from dev rows")
	}
}
