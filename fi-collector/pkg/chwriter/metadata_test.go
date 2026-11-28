package chwriter

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/future-agi/future-agi/fi-collector/pkg/trajectory"
)

// topologyCH answers the metadata queries from fixed engines/replica counts
// and records every other request's URL query.
type topologyCH struct {
	mu       sync.Mutex
	engine   string
	total    int
	active   int
	queries  []url.Values
	metadata []string
	status   int // answer for inserts (0: 200)
}

func (c *topologyCH) handler() http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		q := r.URL.Query()
		c.mu.Lock()
		defer c.mu.Unlock()
		if q.Get("query") == "" {
			c.metadata = append(c.metadata, r.URL.RawQuery+"\n"+string(body))
			switch {
			case strings.Contains(string(body), "system.tables"):
				if c.engine != "" {
					_ = json.NewEncoder(w).Encode(map[string]string{"engine": c.engine})
				}
			case strings.Contains(string(body), "system.replicas"):
				_ = json.NewEncoder(w).Encode(map[string]int{"total_replicas": c.total, "active_replicas": c.active})
			case strings.Contains(string(body), "system.columns"):
				_ = json.NewEncoder(w).Encode(map[string]string{"name": "payload_encoding", "type": "LowCardinality(String)"})
				_ = json.NewEncoder(w).Encode(map[string]string{"name": "item_scope_id", "type": "String"})
			}
			return
		}
		c.queries = append(c.queries, q)
		if c.status != 0 {
			http.Error(w, "Code: 241. DB::Exception: unavailable", c.status)
		}
	}
}

// TestInsertTableQuorumByTopology: the items insert URL carries the quorum
// the topology calls for, the quorum timeout and distributed_foreground_insert.
func TestInsertTableQuorumByTopology(t *testing.T) {
	for _, tc := range []struct {
		engine        string
		total, active int
		want          string
		readsReplicas bool
	}{
		{"ReplacingMergeTree", 0, 0, "auto", false},
		{"ReplicatedReplacingMergeTree", 1, 1, "1", true},
		{"ReplicatedReplacingMergeTree", 2, 2, "2", true},
		{"ReplicatedReplacingMergeTree", 3, 2, "3", true},
	} {
		t.Run(fmt.Sprintf("%s-%d-%d", tc.engine, tc.total, tc.active), func(t *testing.T) {
			ch := &topologyCH{engine: tc.engine, total: tc.total, active: tc.active}
			srv := httptest.NewServer(ch.handler())
			defer srv.Close()
			w, err := New(mkConfig(t, srv.URL))
			if err != nil {
				t.Fatal(err)
			}
			defer w.Close()
			topo, err := w.TableTopology(context.Background(), trajectory.ItemsTable)
			if err != nil {
				t.Fatal(err)
			}
			if topo.TotalReplicas != tc.total || topo.ActiveReplicas != tc.active {
				t.Fatalf("replicas %d/%d, want %d/%d", topo.TotalReplicas, topo.ActiveReplicas, tc.total, tc.active)
			}
			quorum, ok := topo.InsertQuorum()
			if !ok || quorum != tc.want {
				t.Fatalf("quorum %q ok=%v, want %q", quorum, ok, tc.want)
			}
			ch.mu.Lock()
			readReplicas := false
			for _, m := range ch.metadata {
				if strings.Contains(m, "system.replicas") {
					readReplicas = true
				}
			}
			ch.mu.Unlock()
			if readReplicas != tc.readsReplicas {
				t.Fatalf("system.replicas read=%v, want %v (plain tables must not need replication grants)", readReplicas, tc.readsReplicas)
			}
			rows := []map[string]any{{"item_hash": strings.Repeat("a", 32)}}
			if err := w.InsertTableWith(context.Background(), trajectory.ItemsTable, rows, TableInsertOptions{
				Settings: trajectory.ItemsInsertSettingsFor(quorum, trajectory.DefaultItemsInsertTimeout),
			}); err != nil {
				t.Fatal(err)
			}
			ch.mu.Lock()
			defer ch.mu.Unlock()
			q := ch.queries[len(ch.queries)-1]
			if q.Get("insert_quorum") != tc.want || q.Get("insert_quorum_timeout") != "4000" ||
				q.Get("distributed_foreground_insert") != "1" || q.Get("async_insert") != "0" ||
				q.Get("query") != "INSERT INTO trajectory_items FORMAT JSONEachRow" {
				t.Fatalf("items insert query %v", q)
			}
		})
	}
}

func TestInsertTableWithAttemptBudget(t *testing.T) {
	ch := &topologyCH{status: http.StatusServiceUnavailable}
	srv := httptest.NewServer(ch.handler())
	defer srv.Close()
	cfg := mkConfig(t, srv.URL)
	cfg.MaxRetries = 5
	w, err := New(cfg)
	if err != nil {
		t.Fatal(err)
	}
	defer w.Close()
	rows := []map[string]any{{"item_hash": "x"}}
	if err := w.InsertTableWith(context.Background(), "trajectory_items", rows, TableInsertOptions{MaxAttempts: 2}); err == nil {
		t.Fatal("want an error")
	}
	ch.mu.Lock()
	got := len(ch.queries)
	ch.queries = nil
	ch.mu.Unlock()
	if got != 2 {
		t.Fatalf("attempts %d, want 2", got)
	}
	// The default budget is unchanged (MaxRetries + 1).
	if err := w.InsertTable(context.Background(), "trajectory_items", rows); err == nil {
		t.Fatal("want an error")
	}
	ch.mu.Lock()
	defer ch.mu.Unlock()
	if len(ch.queries) != 6 {
		t.Fatalf("default attempts %d, want 6", len(ch.queries))
	}
	if _, err := os.Stat(cfg.DeadLetterFile); err == nil {
		t.Fatal("trajectory_items must never be dead-lettered into the spans file")
	}
}

func TestInsertTableWithAttemptTimeout(t *testing.T) {
	release := make(chan struct{})
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		select {
		case <-release:
		case <-r.Context().Done():
		}
	}))
	defer srv.Close()
	defer close(release)
	cfg := mkConfig(t, srv.URL)
	cfg.RequestTimeout = 30 * time.Second
	w, err := New(cfg)
	if err != nil {
		t.Fatal(err)
	}
	defer w.Close()
	start := time.Now()
	err = w.InsertTableWith(context.Background(), "trajectory_items", []map[string]any{{"a": 1}}, TableInsertOptions{MaxAttempts: 1, AttemptTimeout: 100 * time.Millisecond})
	if err == nil {
		t.Fatal("want a timeout")
	}
	if time.Since(start) > 10*time.Second {
		t.Fatal("the per-attempt deadline was not applied")
	}
}

// TestInsertConfirmedWithStrictColumns: the zero options send dev's URL byte
// for byte; StrictColumns adds input_format_skip_unknown_fields=0 only.
func TestInsertConfirmedWithStrictColumns(t *testing.T) {
	var mu sync.Mutex
	var raw []string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		mu.Lock()
		raw = append(raw, r.URL.RawQuery)
		mu.Unlock()
	}))
	defer srv.Close()
	w, err := New(mkConfig(t, srv.URL))
	if err != nil {
		t.Fatal(err)
	}
	defer w.Close()
	rows := []map[string]any{{"span_id": "a"}}
	if err := w.Insert(context.Background(), rows); err != nil {
		t.Fatal(err)
	}
	if _, err := w.InsertConfirmed(context.Background(), rows); err != nil {
		t.Fatal(err)
	}
	if _, err := w.InsertConfirmedWith(context.Background(), rows, InsertOptions{}); err != nil {
		t.Fatal(err)
	}
	if _, err := w.InsertConfirmedWith(context.Background(), rows, InsertOptions{StrictColumns: true}); err != nil {
		t.Fatal(err)
	}
	mu.Lock()
	defer mu.Unlock()
	if raw[0] != raw[1] || raw[1] != raw[2] {
		t.Fatalf("default inserts must share dev's URL: %q", raw)
	}
	if raw[3] != raw[0]+"&input_format_skip_unknown_fields=0" {
		t.Fatalf("strict URL %q", raw[3])
	}
}

func TestIsSchemaMismatch(t *testing.T) {
	for _, tc := range []struct {
		code, body string
		want       bool
	}{
		{"117", "Code: 117. DB::Exception: Unknown field found while parsing JSONEachRow format: payload_envelope: (at row 1)", true},
		{"", "Code: 16. DB::Exception: No such column payload_envelope in table default.spans. (NO_SUCH_COLUMN_IN_TABLE)", true},
		{"47", "Code: 47. DB::Exception: Unknown identifier", true},
		{"117", "Code: 117. DB::Exception: Cannot parse input", false},
		{"241", "Code: 241. DB::Exception: Memory limit exceeded", false},
	} {
		srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			if tc.code != "" {
				w.Header().Set("X-ClickHouse-Exception-Code", tc.code)
			}
			http.Error(w, tc.body, http.StatusBadRequest)
		}))
		w, err := New(mkConfig(t, srv.URL))
		if err != nil {
			t.Fatal(err)
		}
		_, err = w.InsertConfirmedWith(context.Background(), []map[string]any{{"a": 1}}, InsertOptions{StrictColumns: true})
		if got := IsSchemaMismatch(err); got != tc.want {
			t.Fatalf("%q: IsSchemaMismatch=%v, want %v (err %v)", tc.body, got, tc.want, err)
		}
		if !strings.Contains(err.Error(), "CH HTTP 400: ") {
			t.Fatalf("the error text changed: %v", err)
		}
		w.Close()
		srv.Close()
	}
	if IsSchemaMismatch(errors.New("Unknown field found")) || IsSchemaMismatch(nil) {
		t.Fatal("only ClickHouse responses count")
	}
}

// TestTableColumnsReadOnlyBoundParameters: the metadata query binds the
// database and table as parameters under readonly=1, and its errors never
// carry the URL's credentials.
func TestTableColumnsReadOnlyBoundParameters(t *testing.T) {
	ch := &topologyCH{}
	srv := httptest.NewServer(ch.handler())
	defer srv.Close()
	cfg := mkConfig(t, srv.URL)
	cfg.Database = "tenant_db"
	w, err := New(cfg)
	if err != nil {
		t.Fatal(err)
	}
	defer w.Close()
	cols, err := w.TableColumns(context.Background(), "spans'; --")
	if err != nil {
		t.Fatal(err)
	}
	if cols["payload_encoding"] != "LowCardinality(String)" || cols["item_scope_id"] != "String" {
		t.Fatalf("columns %v", cols)
	}
	ch.mu.Lock()
	m := ch.metadata[0]
	ch.mu.Unlock()
	rawQuery, body, _ := strings.Cut(m, "\n")
	q, _ := url.ParseQuery(rawQuery)
	if q.Get("readonly") != "1" || q.Get("param_db") != "tenant_db" || q.Get("param_table") != "spans'; --" || q.Get("database") != "tenant_db" {
		t.Fatalf("metadata params %v", q)
	}
	if strings.Contains(body, "spans'") || !strings.Contains(body, "{table:String}") {
		t.Fatalf("the table name must be bound, not interpolated: %s", body)
	}

	// Credentials in the URL never reach an error.
	bad, err := New(mkConfig(t, "http://user:s3cretPass@127.0.0.1:1"))
	if err != nil {
		t.Fatal(err)
	}
	defer bad.Close()
	_, err = bad.TableColumns(context.Background(), "spans")
	if err == nil || strings.Contains(err.Error(), "s3cretPass") {
		t.Fatalf("metadata error leaks credentials or is nil: %v", err)
	}
}
