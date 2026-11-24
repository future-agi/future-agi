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
	"path/filepath"
	"strings"
	"sync"
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
}

type fakeTrajectoryCH struct {
	mu           sync.Mutex
	inserts      []recordedCHInsert
	failTrajItem bool
}

func (f *fakeTrajectoryCH) handler() http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		q := r.URL.Query().Get("query")
		body, _ := io.ReadAll(r.Body)
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
		fail := table == "trajectory_items" && f.failTrajItem
		f.mu.Unlock()
		if fail {
			http.Error(w, "simulated trajectory_items failure", http.StatusInternalServerError)
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
		f.inserts = append(f.inserts, recordedCHInsert{Table: table, Rows: rows})
		f.mu.Unlock()
		w.WriteHeader(http.StatusOK)
	})
}

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
	srv := New(Config{BatchMaxRows: 100, BatchMaxAge: time.Minute}, writer, nil, NoopUsageEmitter{}, NoopMetering{},
		WithLogger(slog.New(slog.NewTextHandler(io.Discard, nil))),
		WithTrajectory(cfg, seen),
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

	// Recover ClickHouse: Turn 2's NEW items were evicted by Forget(), so Turn 3 re-emits them!
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
		billDeduped bool
		wantBasis   string
	}{
		{name: "shadow_mode", billDeduped: false, wantBasis: "raw_shadow"},
		{name: "live_dedup_billing", billDeduped: true, wantBasis: "deduped_storage"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			mr.FlushAll()
			cfg := trajectory.Config{
				Mode:             trajectory.ModeOn,
				BillDedupedBytes: tc.billDeduped,
			}.Normalize()
			// Two completely separate collector pods sharing ONLY Redis!
			podA := New(Config{BatchMaxRows: 100, BatchMaxAge: time.Minute}, writer, nil, usage, NoopMetering{},
				WithLogger(log), WithTrajectory(cfg, trajectory.NewSeenStore(rdb, 10_000, time.Hour, time.Hour)))
			podB := New(Config{BatchMaxRows: 100, BatchMaxAge: time.Minute}, writer, nil, usage, NoopMetering{},
				WithLogger(log), WithTrajectory(cfg, trajectory.NewSeenStore(rdb, 10_000, time.Hour, time.Hour)))

			// Turn 10 lands on Pod A.
			postTrajOTLP(t, podA, oid, pid, makeAgentHTTPReq(t, pid, oid, "sess-bill", 0x10, 0x01, baseMsgs, "Prior reply 10"))
			// Turn 11 (reusing all 21 prior messages!) lands on Pod B.
			postTrajOTLP(t, podB, oid, pid, makeAgentHTTPReq(t, pid, oid, "sess-bill", 0x11, 0x02, nextMsgs, "Final short reply"))

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
			if tc.billDeduped {
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
