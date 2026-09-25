package chwriter

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

type confirmationRoundTripper func(*http.Request) (*http.Response, error)

func (f confirmationRoundTripper) RoundTrip(r *http.Request) (*http.Response, error) {
	return f(r)
}

type confirmationReadError struct{ err error }

func (r confirmationReadError) Read([]byte) (int, error) { return 0, r.err }

type confirmationBody struct {
	io.Reader
	closed bool
}

func (b *confirmationBody) Close() error { b.closed = true; return nil }

// These transport contracts do not open listeners or contact ClickHouse.
func TestInsertConfirmationRequestSettings(t *testing.T) {
	for _, async := range []bool{false, true} {
		for _, curated := range []bool{false, true} {
			t.Run(fmt.Sprintf("async=%t/curated=%t", async, curated), func(t *testing.T) {
				cfg := mkConfig(t, "http://offline.invalid")
				cfg.AsyncInsert = async
				w, err := New(cfg)
				if err != nil {
					t.Fatal(err)
				}
				t.Cleanup(func() { _ = w.Close() })
				calls := 0
				w.client.Transport = confirmationRoundTripper(func(r *http.Request) (*http.Response, error) {
					calls++
					q := r.URL.Query()
					wantAsync, wantWait, wantParams := "0", "", 3
					if async {
						wantAsync, wantWait, wantParams = "1", "0", 4
					}
					table := "spans"
					if curated {
						table = "end_users"
					}
					if q.Get("async_insert") != wantAsync || q.Get("wait_for_async_insert") != wantWait || len(q) != wantParams {
						t.Errorf("unexpected insert settings: %v", q)
					}
					if q.Get("database") != cfg.Database || q.Get("query") != "INSERT INTO "+table+" FORMAT JSONEachRow" || r.Method != http.MethodPost {
						t.Errorf("insert target or method changed: %s %v", r.Method, q)
					}
					return &http.Response{StatusCode: http.StatusOK, Header: make(http.Header), Body: http.NoBody, Request: r}, nil
				})
				rows := []map[string]any{{"id": "one"}}
				if curated {
					err = w.InsertBestEffort(context.Background(), "end_users", rows)
				} else {
					err = w.Insert(context.Background(), rows)
				}
				if err != nil || calls != 1 {
					t.Fatalf("successful insert: calls=%d err=%v", calls, err)
				}
			})
		}
	}
}

func TestInsertConfirmationResponse(t *testing.T) {
	for _, tc := range []struct {
		name, body, exception, trailer string
		readErr                        error
		length                         int64
		confirmed                      bool
	}{
		{name: "empty", confirmed: true},
		{name: "whitespace", body: " \r\n\t", length: 4, confirmed: true},
		{name: "unknown length", length: -1, confirmed: true},
		{name: "exception header", exception: "241"},
		{name: "exception trailer", trailer: "241"},
		{name: "exception body", body: "Code: 241. DB::Exception: private-source-value", length: -1},
		{name: "late exception body", body: strings.Repeat(" ", 1024) + "DB::Exception: private-source-value", length: -1},
		{name: "unexpected body", body: "private-source-value", length: -1},
		{name: "truncated framing", body: " ", length: 2},
		{name: "unexpected EOF", body: " ", length: -1, readErr: io.ErrUnexpectedEOF},
		{name: "read error", length: -1, readErr: errors.New("response interrupted")},
		{name: "oversized response", body: strings.Repeat(" ", 4097), length: -1},
	} {
		for _, curated := range []bool{false, true} {
			t.Run(fmt.Sprintf("%s/curated=%t", tc.name, curated), func(t *testing.T) {
				cfg := mkConfig(t, "http://offline.invalid")
				cfg.InitialBackoff, cfg.MaxBackoff = time.Nanosecond, time.Nanosecond
				w, err := New(cfg)
				if err != nil {
					t.Fatal(err)
				}
				t.Cleanup(func() { _ = w.Close() })
				rows := []map[string]any{{"id": "private-span", "attrs_string": map[string]any{"key": "<a&b>"}}}
				want := []byte("{\"attrs_string\":{\"key\":\"<a&b>\"},\"id\":\"private-span\"}\n")
				var bodies [][]byte
				var responses []*confirmationBody
				w.client.Transport = confirmationRoundTripper(func(r *http.Request) (*http.Response, error) {
					body, err := io.ReadAll(r.Body)
					if err != nil {
						t.Fatal(err)
					}
					bodies = append(bodies, body)
					var reader io.Reader = strings.NewReader(tc.body)
					if tc.readErr != nil {
						reader = io.MultiReader(reader, confirmationReadError{tc.readErr})
					}
					responseBody := &confirmationBody{Reader: reader}
					responses = append(responses, responseBody)
					resp := &http.Response{StatusCode: http.StatusOK, Header: make(http.Header), Trailer: make(http.Header), Body: responseBody, ContentLength: tc.length, Request: r}
					if tc.exception != "" {
						resp.Header.Set("X-ClickHouse-Exception-Code", tc.exception)
					}
					if tc.trailer != "" {
						resp.Trailer.Set("X-ClickHouse-Exception-Code", tc.trailer)
					}
					return resp, nil
				})
				if curated {
					err = w.InsertBestEffort(context.Background(), "end_users", rows)
				} else {
					err = w.Insert(context.Background(), rows)
				}
				if (err == nil) != tc.confirmed {
					t.Errorf("confirmed=%t, got err=%v", tc.confirmed, err)
				}
				if err != nil && strings.Contains(err.Error(), "private-") {
					t.Error("unconfirmed response exposed source payload")
				}
				if err != nil && !curated && !tc.confirmed &&
					(!strings.Contains(err.Error(), "after 1 attempts") || !strings.Contains(err.Error(), "reconcile canonical source before replay")) {
					t.Errorf("missing single-attempt reconciliation diagnostic: %v", err)
				}
				if len(bodies) != 1 || !bytes.Equal(bodies[0], want) {
					t.Fatalf("canonical insert replayed or changed; calls=%d", len(bodies))
				}
				if after, _ := encodeBatch(rows); !bytes.Equal(after, want) {
					t.Error("caller rows mutated")
				}
				if !responses[0].closed {
					t.Error("response body not closed")
				}
				var wantStats Stats
				if curated {
					if tc.confirmed {
						wantStats.CuratedBatchesInserted = 1
					} else {
						wantStats.CuratedBatchesFailed = 1
					}
				} else if tc.confirmed {
					wantStats.BatchesInserted, wantStats.RowsInserted = 1, 1
				} else {
					wantStats.BatchesFailed, wantStats.RowsDeadLettered = 1, 1
				}
				if got := w.Snapshot(); got != wantStats {
					t.Errorf("stats=%+v want=%+v", got, wantStats)
				}
				dead, readErr := os.ReadFile(cfg.DeadLetterFile)
				if curated || tc.confirmed {
					if !os.IsNotExist(readErr) {
						t.Error("unexpected canonical dead-letter")
					}
				} else {
					var row map[string]any
					if readErr != nil || json.Unmarshal(dead, &row) != nil {
						t.Fatal("missing or invalid canonical dead-letter")
					}
					if encoded, _ := encodeBatch([]map[string]any{row}); !bytes.Equal(encoded, want) {
						t.Error("canonical dead-letter row changed")
					}
				}
			})
		}
	}
}

func TestInsertConfirmationPreservesExistingRetryPolicy(t *testing.T) {
	for _, firstStatus := range []int{0, http.StatusInternalServerError, http.StatusTooManyRequests, http.StatusBadRequest} {
		t.Run(fmt.Sprint(firstStatus), func(t *testing.T) {
			cfg := mkConfig(t, "http://offline.invalid")
			cfg.InitialBackoff, cfg.MaxBackoff = time.Nanosecond, time.Nanosecond
			w, err := New(cfg)
			if err != nil {
				t.Fatal(err)
			}
			t.Cleanup(func() { _ = w.Close() })
			calls := 0
			w.client.Transport = confirmationRoundTripper(func(r *http.Request) (*http.Response, error) {
				calls++
				if calls == 1 && firstStatus == 0 {
					return nil, errors.New("existing transport failure")
				}
				status := http.StatusOK
				if calls == 1 {
					status = firstStatus
				}
				return &http.Response{StatusCode: status, Header: make(http.Header), Body: http.NoBody, Request: r}, nil
			})
			err = w.Insert(context.Background(), []map[string]any{{"id": "one"}})
			if firstStatus == http.StatusBadRequest {
				if err == nil || calls != 1 || w.Snapshot().BatchesRetried != 0 {
					t.Fatalf("4xx policy changed: calls=%d err=%v", calls, err)
				}
			} else if err != nil || calls != 2 || w.Snapshot().BatchesRetried != 1 {
				t.Fatalf("retry policy changed: calls=%d err=%v", calls, err)
			}
		})
	}
}

func mkConfig(t *testing.T, url string) Config {
	t.Helper()
	dir := t.TempDir()
	return Config{
		URL:            url,
		Database:       "default",
		Table:          "spans",
		MaxRetries:     3,
		InitialBackoff: 5 * time.Millisecond,
		MaxBackoff:     20 * time.Millisecond,
		RequestTimeout: 1 * time.Second,
		DeadLetterFile: filepath.Join(dir, "dead.jsonl"),
	}
}

func TestInsertSuccess(t *testing.T) {
	var bodies []string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Query().Get("database") != "default" {
			t.Errorf("missing database query param: %s", r.URL.RawQuery)
		}
		if !strings.Contains(r.URL.Query().Get("query"), "INSERT INTO spans FORMAT JSONEachRow") {
			t.Errorf("unexpected query: %s", r.URL.Query().Get("query"))
		}
		b, _ := io.ReadAll(r.Body)
		bodies = append(bodies, string(b))
		w.WriteHeader(200)
	}))
	defer srv.Close()

	w, err := New(mkConfig(t, srv.URL))
	if err != nil {
		t.Fatal(err)
	}
	defer w.Close()

	rows := []map[string]any{
		{"id": "s1", "project_id": "00000000-0000-0000-0000-000000000001", "name": "root"},
		{"id": "s2", "project_id": "00000000-0000-0000-0000-000000000001", "name": "child"},
	}
	if err := w.Insert(context.Background(), rows); err != nil {
		t.Fatalf("Insert: %v", err)
	}
	if got := atomic.LoadUint64(&w.stats.BatchesInserted); got != 1 {
		t.Errorf("BatchesInserted=%d want 1", got)
	}
	if got := atomic.LoadUint64(&w.stats.RowsInserted); got != 2 {
		t.Errorf("RowsInserted=%d want 2", got)
	}
	if n := strings.Count(bodies[0], "\n"); n != 2 {
		t.Errorf("expected 2 newlines in JSONEachRow body, got %d in %q", n, bodies[0])
	}
}

func TestInsertRetriesOn5xx(t *testing.T) {
	var calls int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		n := atomic.AddInt32(&calls, 1)
		if n < 3 {
			http.Error(w, "transient", http.StatusInternalServerError)
			return
		}
		w.WriteHeader(200)
	}))
	defer srv.Close()

	w, _ := New(mkConfig(t, srv.URL))
	defer w.Close()

	if err := w.Insert(context.Background(), []map[string]any{{"id": "x"}}); err != nil {
		t.Fatalf("expected success after retries, got %v", err)
	}
	if atomic.LoadInt32(&calls) != 3 {
		t.Errorf("calls=%d want 3", calls)
	}
	if atomic.LoadUint64(&w.stats.BatchesRetried) != 2 {
		t.Errorf("BatchesRetried=%d want 2", w.stats.BatchesRetried)
	}
}

func TestInsert4xxDeadLettersWithoutRetry(t *testing.T) {
	var calls int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		atomic.AddInt32(&calls, 1)
		http.Error(w, "bad schema", http.StatusBadRequest)
	}))
	defer srv.Close()

	w, _ := New(mkConfig(t, srv.URL))
	defer w.Close()

	rows := []map[string]any{{"id": "s1"}, {"id": "s2"}}
	err := w.Insert(context.Background(), rows)
	if err == nil {
		t.Fatal("expected error on non-retryable status")
	}
	if atomic.LoadInt32(&calls) != 1 {
		t.Errorf("4xx should not retry; calls=%d", calls)
	}
	// Dead-letter file must contain both rows.
	b, err := os.ReadFile(w.cfg.DeadLetterFile)
	if err != nil {
		t.Fatalf("read dead-letter: %v", err)
	}
	if c := strings.Count(string(b), "\n"); c != 2 {
		t.Errorf("dead-letter should have 2 lines, got %d: %q", c, string(b))
	}
	for _, line := range strings.Split(strings.TrimRight(string(b), "\n"), "\n") {
		var m map[string]any
		if err := json.Unmarshal([]byte(line), &m); err != nil {
			t.Errorf("dead-letter line not valid JSON: %v", err)
		}
	}
	if atomic.LoadUint64(&w.stats.RowsDeadLettered) != 2 {
		t.Errorf("RowsDeadLettered=%d want 2", w.stats.RowsDeadLettered)
	}
}

func TestInsertEmptyBatchNoop(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		t.Error("server should not be called on empty batch")
	}))
	defer srv.Close()
	w, _ := New(mkConfig(t, srv.URL))
	defer w.Close()
	if err := w.Insert(context.Background(), nil); err != nil {
		t.Errorf("unexpected error: %v", err)
	}
}

func TestInsertBestEffort_TargetsNamedTable(t *testing.T) {
	// InsertBestEffort must POST to a per-call table (the curated end_users /
	// trace_sessions RMTs) — not the pinned spans table. Verify the query names
	// the requested table and the Curated* stats move (not the span stats).
	var gotQueries []string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotQueries = append(gotQueries, r.URL.Query().Get("query"))
		w.WriteHeader(200)
	}))
	defer srv.Close()

	w, err := New(mkConfig(t, srv.URL)) // pinned Table = spans
	if err != nil {
		t.Fatal(err)
	}
	defer w.Close()

	rows := []map[string]any{{"end_user_id": "eu-1", "project_id": "p"}}
	if err := w.InsertBestEffort(context.Background(), "end_users", rows); err != nil {
		t.Fatalf("InsertBestEffort end_users: %v", err)
	}
	if err := w.InsertBestEffort(context.Background(), "trace_sessions", rows); err != nil {
		t.Fatalf("InsertBestEffort trace_sessions: %v", err)
	}
	// Pinned Insert still goes to spans.
	if err := w.Insert(context.Background(), rows); err != nil {
		t.Fatalf("Insert: %v", err)
	}

	if len(gotQueries) != 3 {
		t.Fatalf("expected 3 inserts, got %d", len(gotQueries))
	}
	if !strings.Contains(gotQueries[0], "INSERT INTO end_users FORMAT JSONEachRow") {
		t.Errorf("query[0] should target end_users: %q", gotQueries[0])
	}
	if !strings.Contains(gotQueries[1], "INSERT INTO trace_sessions FORMAT JSONEachRow") {
		t.Errorf("query[1] should target trace_sessions: %q", gotQueries[1])
	}
	if !strings.Contains(gotQueries[2], "INSERT INTO spans FORMAT JSONEachRow") {
		t.Errorf("query[2] should target the pinned spans table: %q", gotQueries[2])
	}
	// Curated counters move; span counters reflect ONLY the one span insert.
	s := w.Snapshot()
	if s.CuratedBatchesInserted != 2 {
		t.Errorf("CuratedBatchesInserted=%d want 2", s.CuratedBatchesInserted)
	}
	if s.BatchesInserted != 1 {
		t.Errorf("span BatchesInserted=%d want 1 (curated must not bump span stats)", s.BatchesInserted)
	}
}

// TestInsertBestEffort_FailureNoRetryNoDeadLetter proves the best-effort
// contract: a 5xx is NOT retried, does NOT write the span dead-letter file, and
// bumps only CuratedBatchesFailed (never the span BatchesFailed / RowsDeadLettered).
func TestInsertBestEffort_FailureNoRetryNoDeadLetter(t *testing.T) {
	var calls int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		atomic.AddInt32(&calls, 1)
		http.Error(w, "boom", http.StatusInternalServerError)
	}))
	defer srv.Close()

	w, _ := New(mkConfig(t, srv.URL))
	defer w.Close()

	err := w.InsertBestEffort(context.Background(), "end_users",
		[]map[string]any{{"end_user_id": "eu-1"}})
	if err == nil {
		t.Fatal("expected error from 5xx")
	}
	if c := atomic.LoadInt32(&calls); c != 1 {
		t.Errorf("best-effort must NOT retry; calls=%d want 1", c)
	}
	// Span dead-letter file must NOT exist (curated failures don't dead-letter).
	if _, statErr := os.Stat(w.cfg.DeadLetterFile); statErr == nil {
		t.Error("best-effort curated failure must not create the span dead-letter file")
	}
	s := w.Snapshot()
	if s.CuratedBatchesFailed != 1 {
		t.Errorf("CuratedBatchesFailed=%d want 1", s.CuratedBatchesFailed)
	}
	if s.BatchesFailed != 0 || s.RowsDeadLettered != 0 {
		t.Errorf("span failure stats must stay 0; got BatchesFailed=%d RowsDeadLettered=%d",
			s.BatchesFailed, s.RowsDeadLettered)
	}
}

func TestEncodeBatchEscapesUnescapedHTML(t *testing.T) {
	// CH treats &, <, > literally; we must NOT use the json package's default
	// HTML-escape which would turn "a&b" into "a&b" and bloat payloads.
	b, err := encodeBatch([]map[string]any{{"k": "<a&b>"}})
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(b), "<a&b>") {
		t.Errorf("HTML should not be escaped: %q", string(b))
	}
}

func TestContextCancellationStopsRetries(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		http.Error(w, "5xx", http.StatusInternalServerError)
	}))
	defer srv.Close()
	cfg := mkConfig(t, srv.URL)
	cfg.MaxRetries = 100 // would take ~10s without cancellation
	cfg.InitialBackoff = 50 * time.Millisecond
	w, _ := New(cfg)
	defer w.Close()

	ctx, cancel := context.WithTimeout(context.Background(), 100*time.Millisecond)
	defer cancel()
	t0 := time.Now()
	err := w.Insert(ctx, []map[string]any{{"id": "x"}})
	if err == nil {
		t.Fatal("expected error from cancelled context")
	}
	if elapsed := time.Since(t0); elapsed > 500*time.Millisecond {
		t.Errorf("context cancellation should bound runtime; ran for %v", elapsed)
	}
}
