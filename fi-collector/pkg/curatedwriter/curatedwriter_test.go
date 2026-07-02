package curatedwriter

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/future-agi/future-agi/fi-collector/pkg/chwriter"
)

// newTestWriter builds a chwriter.Writer pointed at the given httptest URL.
func newTestWriter(t *testing.T, url string) *chwriter.Writer {
	t.Helper()
	w, err := chwriter.New(chwriter.Config{
		URL:            url,
		Database:       "default",
		Table:          "spans",
		MaxRetries:     2,
		InitialBackoff: 2 * time.Millisecond,
		MaxBackoff:     10 * time.Millisecond,
		RequestTimeout: time.Second,
		DeadLetterFile: filepath.Join(t.TempDir(), "dead.jsonl"),
	})
	if err != nil {
		t.Fatalf("chwriter.New: %v", err)
	}
	return w
}

// captured is one insert the fake CH server received.
type captured struct {
	table string
	rows  []map[string]any
}

// fakeCH returns an httptest server that records each INSERT's target table and
// decoded JSONEachRow rows. `fail` (if set) forces an HTTP 500 so the
// best-effort path can be exercised.
func fakeCH(t *testing.T, fail bool, sink *[]captured) *httptest.Server {
	t.Helper()
	return httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		q := r.URL.Query().Get("query")
		table := tableFromQuery(q)
		body, _ := io.ReadAll(r.Body)
		var rows []map[string]any
		for _, line := range strings.Split(strings.TrimSpace(string(body)), "\n") {
			if line == "" {
				continue
			}
			var m map[string]any
			if err := json.Unmarshal([]byte(line), &m); err != nil {
				t.Errorf("server: bad JSONEachRow line %q: %v", line, err)
				continue
			}
			rows = append(rows, m)
		}
		*sink = append(*sink, captured{table: table, rows: rows})
		if fail {
			w.WriteHeader(http.StatusInternalServerError)
			_, _ = w.Write([]byte("DB::Exception: forced"))
			return
		}
		w.WriteHeader(http.StatusOK)
	}))
}

// tableFromQuery pulls the table name out of "INSERT INTO <table> FORMAT ...".
func tableFromQuery(q string) string {
	const pfx = "INSERT INTO "
	i := strings.Index(q, pfx)
	if i < 0 {
		return ""
	}
	rest := q[i+len(pfx):]
	if j := strings.IndexByte(rest, ' '); j >= 0 {
		return rest[:j]
	}
	return rest
}

func find(sink []captured, table string) (captured, bool) {
	for _, c := range sink {
		if c.table == table {
			return c, true
		}
	}
	return captured{}, false
}

// --------------------------------------------------------------------------
// Dedup: N identities across M distinct → exactly M rows.
// --------------------------------------------------------------------------

func TestBatch_DedupWithinBatch(t *testing.T) {
	b := NewBatch()
	// 5 "spans" across 2 distinct end_users and 3 distinct sessions. Duplicate
	// ids repeat; the batch must collapse to one row per distinct id.
	euA := EndUser{ProjectID: "p", EndUserID: "eu-A", OrganizationID: "o", UserID: "ua", UserIDType: "email"}
	euB := EndUser{ProjectID: "p", EndUserID: "eu-B", OrganizationID: "o", UserID: "ub"}
	b.AddEndUser(euA)
	b.AddEndUser(euA) // dup
	b.AddEndUser(euB)
	b.AddEndUser(euA) // dup
	b.AddEndUser(euB) // dup

	b.AddSession(Session{ProjectID: "p", TraceSessionID: "s-1", ExternalSessionID: "one"})
	b.AddSession(Session{ProjectID: "p", TraceSessionID: "s-2", ExternalSessionID: "two"})
	b.AddSession(Session{ProjectID: "p", TraceSessionID: "s-1", ExternalSessionID: "one"}) // dup
	b.AddSession(Session{ProjectID: "p", TraceSessionID: "s-3", ExternalSessionID: "three"})

	if got := len(b.EndUsers()); got != 2 {
		t.Errorf("end_users dedup: got %d rows want 2", got)
	}
	if got := len(b.Sessions()); got != 3 {
		t.Errorf("trace_sessions dedup: got %d rows want 3", got)
	}
	// First-occurrence wins → insertion order preserved.
	if b.EndUsers()[0].EndUserID != "eu-A" || b.EndUsers()[1].EndUserID != "eu-B" {
		t.Errorf("end_users order: %+v", b.EndUsers())
	}
}

func TestBatch_EmptyAndBlankIDsIgnored(t *testing.T) {
	b := NewBatch()
	if !b.Empty() {
		t.Error("fresh batch must be Empty")
	}
	b.AddEndUser(EndUser{EndUserID: ""}) // blank id → ignored
	b.AddSession(Session{TraceSessionID: ""})
	if !b.Empty() {
		t.Error("blank-id adds must not populate the batch")
	}
}

// --------------------------------------------------------------------------
// Write: rows land in the right tables with the 017/018 column mapping.
// --------------------------------------------------------------------------

func TestWrite_EndUsersAndSessions_ColumnMapping(t *testing.T) {
	var sink []captured
	srv := fakeCH(t, false, &sink)
	defer srv.Close()

	cw := New(newTestWriter(t, srv.URL))

	b := NewBatch()
	b.AddEndUser(EndUser{
		ProjectID:      "11111111-1111-4111-8111-111111111111",
		EndUserID:      "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
		OrganizationID: "22222222-2222-4222-8222-222222222222",
		UserID:         "sarthak@futureagi.com",
		UserIDType:     "email",
		UserIDHash:     "deadbeef",
		Metadata:       `{"plan":"pro"}`,
	})
	b.AddSession(Session{
		ProjectID:         "11111111-1111-4111-8111-111111111111",
		TraceSessionID:    "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
		ExternalSessionID: "sess-123",
	})

	now := time.Date(2026, 6, 1, 12, 30, 45, 123456000, time.UTC)
	if err := cw.Write(context.Background(), b, now); err != nil {
		t.Fatalf("Write: %v", err)
	}

	// end_users row — every 017 column present + correct.
	euCap, ok := find(sink, tableEndUsers)
	if !ok {
		t.Fatalf("no insert into %s; captured: %+v", tableEndUsers, sink)
	}
	if len(euCap.rows) != 1 {
		t.Fatalf("end_users rows: got %d want 1", len(euCap.rows))
	}
	eu := euCap.rows[0]
	wantEU := map[string]any{
		"project_id":      "11111111-1111-4111-8111-111111111111",
		"end_user_id":     "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
		"organization_id": "22222222-2222-4222-8222-222222222222",
		"user_id":         "sarthak@futureagi.com",
		"user_id_type":    "email",
		"user_id_hash":    "deadbeef",
		"metadata":        `{"plan":"pro"}`,
	}
	for k, want := range wantEU {
		if eu[k] != want {
			t.Errorf("end_users[%s]: got %#v want %#v", k, eu[k], want)
		}
	}
	// Exactly the 017 columns — no extras, no missing.
	wantCols := []string{
		"project_id", "end_user_id", "organization_id", "user_id", "user_id_type",
		"user_id_hash", "metadata", "first_seen", "version", "is_deleted",
	}
	assertExactKeys(t, "end_users", eu, wantCols)

	// is_deleted = 0 (JSONEachRow decodes the UInt8 as a number).
	if f, _ := eu["is_deleted"].(float64); f != 0 {
		t.Errorf("end_users.is_deleted: got %#v want 0", eu["is_deleted"])
	}
	// version + first_seen parse as a CH DateTime64(6) text value.
	assertDateTime64(t, "end_users.version", eu["version"])
	assertDateTime64(t, "end_users.first_seen", eu["first_seen"])

	// trace_sessions row — 018 columns.
	sCap, ok := find(sink, tableTraceSessions)
	if !ok {
		t.Fatalf("no insert into %s", tableTraceSessions)
	}
	if len(sCap.rows) != 1 {
		t.Fatalf("trace_sessions rows: got %d want 1", len(sCap.rows))
	}
	s := sCap.rows[0]
	if s["project_id"] != "11111111-1111-4111-8111-111111111111" ||
		s["trace_session_id"] != "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb" ||
		s["external_session_id"] != "sess-123" {
		t.Errorf("trace_sessions row mismatch: %#v", s)
	}
	assertExactKeys(t, "trace_sessions", s, []string{
		"project_id", "trace_session_id", "external_session_id",
		"first_seen", "version", "is_deleted",
	})
	assertDateTime64(t, "trace_sessions.version", s["version"])
}

// TestWrite_EmptyTypeIsSQLNull proves the "" sentinel → SQL NULL: an untyped
// end_user must omit user_id_type as a NULL (the dict's single source of truth
// round-trips NULL for the ~85% untyped rows). JSONEachRow encodes a nil map
// value as JSON null → decodes back to Go nil.
func TestWrite_EmptyTypeIsSQLNull(t *testing.T) {
	var sink []captured
	srv := fakeCH(t, false, &sink)
	defer srv.Close()
	cw := New(newTestWriter(t, srv.URL))

	b := NewBatch()
	b.AddEndUser(EndUser{
		ProjectID: "p", EndUserID: "eu-1", OrganizationID: "o",
		UserID: "u1", UserIDType: "", UserIDHash: "", Metadata: "{}",
	})
	if err := cw.Write(context.Background(), b, time.Now().UTC()); err != nil {
		t.Fatalf("Write: %v", err)
	}
	euCap, _ := find(sink, tableEndUsers)
	row := euCap.rows[0]
	v, present := row["user_id_type"]
	if !present {
		t.Fatal("user_id_type key must be present (as JSON null), not omitted")
	}
	if v != nil {
		t.Errorf("empty user_id_type must serialize as SQL NULL (JSON null); got %#v", v)
	}
}

// --------------------------------------------------------------------------
// Best-effort: a forced curated-write error does NOT panic / does NOT escalate.
// The writer dead-letters and returns an error for awareness, but the CALLER
// (server.drainNow) swallows it — proven here by asserting Write merely returns
// (no panic) and the span path is untouched.
// --------------------------------------------------------------------------

func TestWrite_BestEffort_ErrorDoesNotPanic(t *testing.T) {
	var sink []captured
	srv := fakeCH(t, true /*fail*/, &sink)
	defer srv.Close()
	cw := New(newTestWriter(t, srv.URL))

	b := NewBatch()
	b.AddEndUser(EndUser{ProjectID: "p", EndUserID: "eu-1", OrganizationID: "o", UserID: "u1", Metadata: "{}"})
	b.AddSession(Session{ProjectID: "p", TraceSessionID: "s-1", ExternalSessionID: "x"})

	// Must not panic; returns the best-effort error for awareness. Both targets
	// are still attempted despite the first failing.
	err := cw.Write(context.Background(), b, time.Now().UTC())
	if err == nil {
		t.Fatal("expected a non-nil error from the forced-failure CH (best-effort returns it for the caller to swallow)")
	}
	if _, ok := find(sink, tableEndUsers); !ok {
		t.Error("end_users insert should have been attempted")
	}
	if _, ok := find(sink, tableTraceSessions); !ok {
		t.Error("trace_sessions insert should have been attempted even though end_users failed")
	}
}

func TestWrite_NilAndEmptyBatch_Noop(t *testing.T) {
	var sink []captured
	srv := fakeCH(t, false, &sink)
	defer srv.Close()
	cw := New(newTestWriter(t, srv.URL))

	if err := cw.Write(context.Background(), nil, time.Now().UTC()); err != nil {
		t.Errorf("nil batch must be a no-op, got %v", err)
	}
	if err := cw.Write(context.Background(), NewBatch(), time.Now().UTC()); err != nil {
		t.Errorf("empty batch must be a no-op, got %v", err)
	}
	if len(sink) != 0 {
		t.Errorf("no-op writes must not hit CH; captured %d", len(sink))
	}
}

// --------------------------------------------------------------------------
// Helpers
// --------------------------------------------------------------------------

func assertExactKeys(t *testing.T, label string, row map[string]any, want []string) {
	t.Helper()
	wantSet := make(map[string]bool, len(want))
	for _, k := range want {
		wantSet[k] = true
		if _, ok := row[k]; !ok {
			t.Errorf("%s: missing column %q", label, k)
		}
	}
	for k := range row {
		if !wantSet[k] {
			t.Errorf("%s: unexpected column %q (not in schema)", label, k)
		}
	}
}

// assertDateTime64 checks the value is the CH DateTime64(6) text form
// "YYYY-MM-DD HH:MM:SS.ffffff" — i.e. parses with that layout.
func assertDateTime64(t *testing.T, label string, v any) {
	t.Helper()
	s, ok := v.(string)
	if !ok {
		t.Errorf("%s: not a string (%T)", label, v)
		return
	}
	if _, err := time.Parse("2006-01-02 15:04:05.000000", s); err != nil {
		t.Errorf("%s: %q is not DateTime64(6) text: %v", label, s, err)
	}
}

// traces: the root-span-derived CH `traces` row (schema 015) so trace_dict
// resolves project_id/name for direct-to-collector SDK & sim traffic.

func TestBatch_TraceDedupWithinBatch(t *testing.T) {
	b := NewBatch()
	// Only the root span calls AddTrace; even if a producer emits two roots, the
	// batch must collapse to one row.
	b.AddTrace(Trace{ID: "t-1", ProjectID: "p", Name: "first"})
	b.AddTrace(Trace{ID: "t-2", ProjectID: "p"})
	b.AddTrace(Trace{ID: "t-1", ProjectID: "p", Name: "dup-loses"}) // dup id → dropped
	b.AddTrace(Trace{ID: ""})                                       // blank id → ignored

	if got := len(b.Traces()); got != 2 {
		t.Fatalf("traces dedup: got %d want 2", got)
	}
	// First-occurrence wins → insertion order + first value preserved.
	if b.Traces()[0].ID != "t-1" || b.Traces()[0].Name != "first" {
		t.Errorf("first-occurrence-wins violated: %+v", b.Traces()[0])
	}
}

func TestBatch_TraceContributesToNonEmpty(t *testing.T) {
	b := NewBatch()
	if !b.Empty() {
		t.Fatal("fresh batch must be Empty")
	}
	// A trace-only batch (no end_user / session) must still flush.
	b.AddTrace(Trace{ID: "t-1", ProjectID: "p"})
	if b.Empty() {
		t.Error("a batch with only a trace must NOT be Empty (else the trace never flushes)")
	}
}

func TestBatch_Merge_FoldsTraces(t *testing.T) {
	a := NewBatch()
	a.AddTrace(Trace{ID: "t-1", ProjectID: "p"})
	other := NewBatch()
	other.AddTrace(Trace{ID: "t-1", ProjectID: "p"}) // dup across batches
	other.AddTrace(Trace{ID: "t-2", ProjectID: "p"})
	a.Merge(other)
	if got := len(a.Traces()); got != 2 {
		t.Errorf("Merge must dedup traces across batches: got %d want 2", got)
	}
}

func TestWrite_Traces_ColumnMapping(t *testing.T) {
	var sink []captured
	srv := fakeCH(t, false, &sink)
	defer srv.Close()
	cw := New(newTestWriter(t, srv.URL))

	b := NewBatch()
	b.AddTrace(Trace{
		ID:        "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
		ProjectID: "11111111-1111-4111-8111-111111111111",
		Name:      "agent.run",
		Input:     "hi",
		Output:    "hello",
		SessionID: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
		CreatedAt: "2026-06-01 12:30:45.123456",
		Version:   1700000000000000000,
	})
	now := time.Date(2026, 6, 1, 12, 30, 46, 0, time.UTC)
	if err := cw.Write(context.Background(), b, now); err != nil {
		t.Fatalf("Write: %v", err)
	}

	tCap, ok := find(sink, tableTraces)
	if !ok {
		t.Fatalf("no insert into %s; captured: %+v", tableTraces, sink)
	}
	if len(tCap.rows) != 1 {
		t.Fatalf("traces rows: got %d want 1", len(tCap.rows))
	}
	row := tCap.rows[0]
	wantStr := map[string]any{
		"id":         "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
		"project_id": "11111111-1111-4111-8111-111111111111",
		"name":       "agent.run",
		"input":      "hi",
		"output":     "hello",
		"session_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
		"created_at": "2026-06-01 12:30:45.123456",
	}
	for k, want := range wantStr {
		if row[k] != want {
			t.Errorf("traces[%s]: got %#v want %#v", k, row[k], want)
		}
	}
	assertExactKeys(t, "traces", row, []string{
		"id", "project_id", "name", "session_id", "input", "output",
		"created_at", "updated_at", "is_deleted", "_version",
	})
	assertDateTime64(t, "traces.updated_at", row["updated_at"])
	if f, _ := row["is_deleted"].(float64); f != 0 {
		t.Errorf("traces.is_deleted: got %#v want 0", row["is_deleted"])
	}
	// _version (UInt64 → JSONEachRow → float64) must be the root span's start
	// nanos, so the app mirror's later time.time_ns wins.
	if f, _ := row["_version"].(float64); f != 1700000000000000000 {
		t.Errorf("traces._version: got %#v want 1700000000000000000", row["_version"])
	}
}

// TestWrite_Traces_NameSessionOmittedWhenEmpty: empty Name/SessionID must be
// OMITTED so the Nullable columns land as SQL NULL, not an empty string that
// would shadow it.
func TestWrite_Traces_NameSessionOmittedWhenEmpty(t *testing.T) {
	var sink []captured
	srv := fakeCH(t, false, &sink)
	defer srv.Close()
	cw := New(newTestWriter(t, srv.URL))

	b := NewBatch()
	b.AddTrace(Trace{
		ID:        "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
		ProjectID: "11111111-1111-4111-8111-111111111111",
		CreatedAt: "2026-06-01 12:30:45.123456",
		Version:   1700000000000000000,
	})
	if err := cw.Write(context.Background(), b, time.Now().UTC()); err != nil {
		t.Fatalf("Write: %v", err)
	}
	tCap, _ := find(sink, tableTraces)
	row := tCap.rows[0]
	if _, present := row["name"]; present {
		t.Errorf("empty Name must be omitted (→ SQL NULL), got %#v", row["name"])
	}
	if _, present := row["session_id"]; present {
		t.Errorf("empty SessionID must be omitted (→ SQL NULL), got %#v", row["session_id"])
	}
	// input/output keep their non-null String DEFAULT '' (present, empty).
	if row["input"] != "" || row["output"] != "" {
		t.Errorf("input/output must be present empty strings: in=%#v out=%#v", row["input"], row["output"])
	}
}
