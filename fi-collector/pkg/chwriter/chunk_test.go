package chwriter

import (
	"bufio"
	"bytes"
	"context"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"sync"
	"testing"
	"time"
)

// chunkCH records insert bodies; failRow makes every insert holding that row
// fail.
type chunkCH struct {
	mu     sync.Mutex
	bodies [][]byte
}

func newChunkWriter(t *testing.T, failRow string) (*Writer, *chunkCH, string) {
	t.Helper()
	ch := &chunkCH{}
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		b, _ := io.ReadAll(r.Body)
		ch.mu.Lock()
		ch.bodies = append(ch.bodies, b)
		ch.mu.Unlock()
		if failRow != "" && bytes.Contains(b, []byte(failRow)) {
			http.Error(w, "Code: 27. DB::Exception: bad row", http.StatusBadRequest)
			return
		}
		w.WriteHeader(http.StatusOK)
	}))
	t.Cleanup(srv.Close)
	dl := t.TempDir() + "/dl.jsonl"
	w, err := New(Config{URL: srv.URL, MaxRetries: 1, InitialBackoff: time.Millisecond, MaxBackoff: time.Millisecond,
		RequestTimeout: 5 * time.Second, DeadLetterFile: dl})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = w.Close() })
	return w, ch, dl
}

func chunkRows(n, size int) []map[string]any {
	rows := make([]map[string]any, n)
	for i := range rows {
		rows[i] = map[string]any{"id": i, "pad": strings.Repeat("x", size)}
	}
	return rows
}

func lineCount(b []byte) int {
	n := 0
	sc := bufio.NewScanner(bytes.NewReader(b))
	sc.Buffer(nil, 1<<26)
	for sc.Scan() {
		n++
	}
	return n
}

// With a maximum insert size, a batch goes out as several inserts of about
// that size, every row exactly once, in order; a row larger than the size is
// an insert of its own. Without one, a batch is one insert.
func TestInsertSplitsBatchesBySize(t *testing.T) {
	w, ch, _ := newChunkWriter(t, "")
	w.SetMaxInsertBytes(10 << 10)
	rows := chunkRows(100, 1000) // about 1 KiB each
	rows[50]["pad"] = strings.Repeat("y", 64<<10)
	if err := w.Insert(context.Background(), rows); err != nil {
		t.Fatal(err)
	}
	total := 0
	for i, b := range ch.bodies {
		total += lineCount(b)
		if len(b) > 10<<10+66<<10 {
			t.Errorf("insert %d is %d bytes, want about 10 KiB plus at most one row", i, len(b))
		}
	}
	if total != len(rows) || len(ch.bodies) < 9 {
		t.Errorf("%d rows in %d inserts, want %d rows in about 10", total, len(ch.bodies), len(rows))
	}
	if s := w.Snapshot(); s.RowsInserted != uint64(len(rows)) || s.BatchesInserted != uint64(len(ch.bodies)) {
		t.Errorf("stats %+v, want %d rows in %d inserts", s, len(rows), len(ch.bodies))
	}

	one, ch1, _ := newChunkWriter(t, "")
	if err := one.Insert(context.Background(), chunkRows(100, 1000)); err != nil {
		t.Fatal(err)
	}
	if len(ch1.bodies) != 1 {
		t.Errorf("no maximum: %d inserts, want 1", len(ch1.bodies))
	}
}

// An insert that fails dead-letters its own rows only; the others are still
// sent, and Insert reports the failure.
func TestInsertDeadLettersOnlyTheFailedInsert(t *testing.T) {
	w, ch, dl := newChunkWriter(t, `"id":42,`)
	w.SetMaxInsertBytes(10 << 10)
	rows := chunkRows(100, 1000)
	err := w.Insert(context.Background(), rows)
	if err == nil || !strings.Contains(err.Error(), "dead-lettered") {
		t.Fatalf("Insert: %v, want the dead-letter error", err)
	}
	b, rerr := os.ReadFile(dl)
	if rerr != nil {
		t.Fatal(rerr)
	}
	dead := lineCount(b)
	s := w.Snapshot()
	if dead == 0 || dead >= len(rows) || s.RowsDeadLettered != uint64(dead) || s.RowsInserted != uint64(len(rows)-dead) {
		t.Errorf("%d rows dead-lettered, stats %+v; want only the failed insert's rows", dead, s)
	}
	if !bytes.Contains(b, []byte(`"id":42,`)) {
		t.Error("the failing row is not in the dead-letter file")
	}
	if len(ch.bodies) < 9 {
		t.Errorf("%d inserts sent, want the rest sent after the failure", len(ch.bodies))
	}
}

// When ClickHouse keeps failing an insert of a batch through every retry,
// the rest of the batch is dead-lettered without being sent, so the drain
// (and the memory its rows hold) does not wait through every retry of every
// insert. A body ClickHouse refuses as bad does not stop the others.
func TestInsertDeadLettersTheRestWhenClickHouseIsDown(t *testing.T) {
	var requests sync.Map
	count := func(k string) int { v, _ := requests.LoadOrStore(k, new(int)); return *v.(*int) }
	var mu sync.Mutex
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = io.Copy(io.Discard, r.Body)
		mu.Lock()
		v, _ := requests.LoadOrStore("all", new(int))
		*v.(*int)++
		mu.Unlock()
		http.Error(w, "unavailable", http.StatusServiceUnavailable)
	}))
	t.Cleanup(srv.Close)
	dl := t.TempDir() + "/dl.jsonl"
	w, err := New(Config{URL: srv.URL, MaxRetries: 2, InitialBackoff: time.Millisecond, MaxBackoff: time.Millisecond,
		RequestTimeout: 5 * time.Second, DeadLetterFile: dl})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = w.Close() })
	w.SetMaxInsertBytes(4 << 10)
	rows := chunkRows(40, 1<<10) // about ten inserts
	if err := w.Insert(context.Background(), rows); err == nil {
		t.Fatal("Insert succeeded against a ClickHouse that refuses everything")
	}
	mu.Lock()
	got := count("all")
	mu.Unlock()
	if got != 3 {
		t.Errorf("%d requests, want 3 (the first insert and its 2 retries)", got)
	}
	b, _ := os.ReadFile(dl)
	if n := lineCount(b); n != len(rows) {
		t.Errorf("%d rows dead-lettered, want all %d", n, len(rows))
	}
	if st := w.Snapshot(); st.RowsDeadLettered != uint64(len(rows)) {
		t.Errorf("RowsDeadLettered %d, want %d", st.RowsDeadLettered, len(rows))
	}

	// A body refused as bad (4xx) is not an outage: the other inserts go on.
	w2, ch, _ := newChunkWriter(t, `"id":0,`)
	w2.SetMaxInsertBytes(4 << 10)
	_ = w2.Insert(context.Background(), rows)
	ch.mu.Lock()
	sent := len(ch.bodies)
	ch.mu.Unlock()
	if sent < 5 {
		t.Errorf("%d inserts sent after one was refused as bad, want every insert of the batch", sent)
	}
}
