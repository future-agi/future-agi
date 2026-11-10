package chwriter

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

// slowCH is a stub ClickHouse whose inserts take delay each; it records the
// rows it stored and the most inserts it saw at once. Inserts holding a row
// whose id is in unavailable answer 503.
type slowCH struct {
	delay       time.Duration
	unavailable map[int]bool
	inFlight    atomic.Int32
	maxSeen     atomic.Int32
	mu          sync.Mutex
	stored      map[int]int
	requests    atomic.Int32
}

func newSlowCH(t *testing.T, delay time.Duration, unavailable map[int]bool) (*Writer, *slowCH, string) {
	t.Helper()
	ch := &slowCH{delay: delay, unavailable: unavailable, stored: map[int]int{}}
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		b, _ := io.ReadAll(r.Body)
		ch.requests.Add(1)
		n := ch.inFlight.Add(1)
		defer ch.inFlight.Add(-1)
		for m := ch.maxSeen.Load(); n > m && !ch.maxSeen.CompareAndSwap(m, n); m = ch.maxSeen.Load() {
		}
		time.Sleep(ch.delay)
		var ids []int
		for _, line := range bytes.Split(bytes.TrimSpace(b), []byte("\n")) {
			var row struct {
				ID int `json:"id"`
			}
			if json.Unmarshal(line, &row) == nil {
				ids = append(ids, row.ID)
			}
		}
		for _, id := range ids {
			if ch.unavailable[id] {
				http.Error(w, "Code: 202. DB::Exception: too many simultaneous queries", http.StatusServiceUnavailable)
				return
			}
		}
		ch.mu.Lock()
		for _, id := range ids {
			ch.stored[id]++
		}
		ch.mu.Unlock()
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

// With SetMaxConcurrentInserts, a batch's inserts run at once, never more
// than the cap across every Insert call; every row is stored exactly once;
// and Insert returns only when all of its inserts have ended.
func TestConcurrentInsertsOfABatch(t *testing.T) {
	w, ch, _ := newSlowCH(t, 150*time.Millisecond, nil)
	w.SetMaxInsertBytes(10 << 10)
	w.SetMaxConcurrentInserts(3)
	rows := chunkRows(120, 1000) // 12 or so inserts of about 10 KiB
	start := time.Now()
	if err := w.Insert(context.Background(), rows); err != nil {
		t.Fatal(err)
	}
	took := time.Since(start)
	if n := ch.inFlight.Load(); n != 0 {
		t.Fatalf("Insert returned with %d inserts still in flight", n)
	}
	if got := ch.maxSeen.Load(); got != 3 {
		t.Errorf("%d inserts at once, want the cap of 3", got)
	}
	reqs := ch.requests.Load()
	if serial := time.Duration(reqs) * ch.delay; took > serial*2/3 {
		t.Errorf("%d inserts took %v, want well under the %v they take one at a time", reqs, took, serial)
	}
	for i := range rows {
		if ch.stored[i] != 1 {
			t.Errorf("row %d stored %d times", i, ch.stored[i])
		}
	}
	if s := w.Snapshot(); s.RowsInserted != uint64(len(rows)) || s.BatchesInserted != uint64(reqs) {
		t.Errorf("stats rows %d batches %d, want %d %d", s.RowsInserted, s.BatchesInserted, len(rows), reqs)
	}

	// Two batches at once share the cap.
	ch.maxSeen.Store(0)
	var wg sync.WaitGroup
	for k := 0; k < 2; k++ {
		wg.Add(1)
		go func(k int) {
			defer wg.Done()
			more := chunkRows(60, 1000)
			for _, r := range more {
				r["id"] = 1000*(k+1) + r["id"].(int)
			}
			if err := w.Insert(context.Background(), more); err != nil {
				t.Error(err)
			}
		}(k)
	}
	wg.Wait()
	if got := ch.maxSeen.Load(); got > 3 {
		t.Errorf("%d inserts at once across two batches, over the cap of 3", got)
	}
}

// When ClickHouse keeps refusing one of a batch's inserts, the rows not sent
// yet are dead-lettered at once, inserts already in flight finish, and every
// row is either stored or dead-lettered exactly once.
func TestConcurrentInsertsDeadLetterTheRestWhenClickHouseIsUnavailable(t *testing.T) {
	w, ch, dl := newSlowCH(t, 100*time.Millisecond, map[int]bool{0: true})
	w.SetMaxInsertBytes(10 << 10)
	w.SetMaxConcurrentInserts(2)
	rows := chunkRows(200, 1000)
	err := w.Insert(context.Background(), rows)
	if err == nil {
		t.Fatal("Insert succeeded with ClickHouse refusing an insert")
	}
	if n := ch.inFlight.Load(); n != 0 {
		t.Fatalf("Insert returned with %d inserts in flight", n)
	}
	raw, rerr := os.ReadFile(dl)
	if rerr != nil {
		t.Fatal(rerr)
	}
	dead := map[int]int{}
	for _, line := range strings.Split(strings.TrimSpace(string(raw)), "\n") {
		var row struct {
			ID int `json:"id"`
		}
		if json.Unmarshal([]byte(line), &row) != nil {
			t.Fatalf("dead-letter line %q", line)
		}
		dead[row.ID]++
	}
	if dead[0] != 1 {
		t.Errorf("the refused insert's first row dead-lettered %d times, want 1", dead[0])
	}
	for i := range rows {
		if got := ch.stored[i] + dead[i]; got != 1 {
			t.Errorf("row %d: stored %d, dead-lettered %d; want exactly one of them", i, ch.stored[i], dead[i])
		}
	}
	if reqs := ch.requests.Load(); int(reqs) >= 2*len(rows)/10 {
		t.Errorf("%d requests: the rows after the refused insert should be dead-lettered unsent, not each tried", reqs)
	}
	if s := w.Snapshot(); s.RowsDeadLettered != uint64(len(dead)) {
		t.Errorf("stats dead-lettered %d, want %d", s.RowsDeadLettered, len(dead))
	}
}
