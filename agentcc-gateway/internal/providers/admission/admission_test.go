package admission

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

var entrySeq atomic.Int64

// entryName is an entry no other test, and no other run under -count, uses:
// the snapshot and its counters are process-wide.
func entryName(t *testing.T) string {
	return fmt.Sprintf("adm-%s-%d", strings.ReplaceAll(t.Name(), "/", "-"), entrySeq.Add(1))
}

func stateOf(t *testing.T, entry string) EntryState {
	t.Helper()
	for _, st := range Snapshot() {
		if st.Entry == entry {
			return st
		}
	}
	t.Fatalf("entry %q not in the snapshot", entry)
	return EntryState{}
}

func TestNewDefaults(t *testing.T) {
	l := New(entryName(t), 0, 0)
	defer l.Close()
	if l.Cap() != DefaultMaxConcurrent || l.QueueTimeout() != DefaultQueueTimeout {
		t.Fatalf("cap %d wait %v; want %d and %v", l.Cap(), l.QueueTimeout(), DefaultMaxConcurrent, DefaultQueueTimeout)
	}
	if DefaultMaxConcurrent != 1024 || DefaultConnPoolSize != 1024 || LocalMaxConcurrent != 10 || LocalConnPoolSize != 10 || DefaultQueueTimeout != 5*time.Second {
		t.Fatal("the documented defaults changed")
	}
	l2 := New(entryName(t), 7, 2*time.Second)
	defer l2.Close()
	if l2.Cap() != 7 || l2.QueueTimeout() != 2*time.Second {
		t.Fatalf("explicit values: cap %d wait %v", l2.Cap(), l2.QueueTimeout())
	}
}

// TestBusyAfterQueueTimeout: with every slot taken, a call waits the queue
// timeout and is refused provider_busy: 503, Retry-After 1, the entry named,
// no value of the request or the config in the message.
func TestBusyAfterQueueTimeout(t *testing.T) {
	entry := entryName(t)
	l := New(entry, 1, 80*time.Millisecond)
	defer l.Close()
	if err := l.Acquire(context.Background()); err != nil {
		t.Fatal(err)
	}
	start := time.Now()
	err := l.Acquire(context.Background())
	waited := time.Since(start)
	var apiErr *models.APIError
	if !errors.As(err, &apiErr) || !models.IsProviderBusy(err) {
		t.Fatalf("err %v; want provider_busy", err)
	}
	if apiErr.Status != http.StatusServiceUnavailable || apiErr.Code != models.CodeProviderBusy || apiErr.RetryAfterSec != 1 ||
		apiErr.Type != models.ErrTypeServer || !strings.Contains(apiErr.Message, `"`+entry+`"`) {
		t.Fatalf("refusal %+v", apiErr)
	}
	if rest := strings.ReplaceAll(apiErr.Message, entry, ""); strings.ContainsAny(rest, "0123456789") {
		t.Errorf("message carries a value beside the entry: %q", apiErr.Message)
	}
	if waited < 80*time.Millisecond || waited > 2*time.Second {
		t.Fatalf("refused after %v; the queue timeout is 80ms", waited)
	}
	if l.InFlight() != 1 || l.Queued() != 0 {
		t.Fatalf("in flight %d queued %d after the refusal", l.InFlight(), l.Queued())
	}
	st := stateOf(t, entry)
	if st.BusyRefusals != 1 || !st.Saturated || !st.Live || st.InFlight != 1 || st.MaxConcurrent != 1 {
		t.Fatalf("state %+v", st)
	}
	if st.Wait.Count != 2 || st.Wait.Cumulative[len(st.Wait.Cumulative)-1] != 2 {
		t.Fatalf("wait histogram %+v; want two waits", st.Wait)
	}
}

// TestSlotFreedDuringWait: a slot released while a call waits goes to it.
func TestSlotFreedDuringWait(t *testing.T) {
	l := New(entryName(t), 1, 5*time.Second)
	defer l.Close()
	if err := l.Acquire(context.Background()); err != nil {
		t.Fatal(err)
	}
	go func() {
		time.Sleep(30 * time.Millisecond)
		l.Release()
	}()
	if err := l.Acquire(context.Background()); err != nil {
		t.Fatalf("err %v; want the freed slot", err)
	}
	l.Release()
	if l.InFlight() != 0 {
		t.Fatalf("in flight %d", l.InFlight())
	}
}

// TestContextEndsTheWait: a ctx that ends before the queue timeout answers
// the gateway timeout the adapters always gave a cut-short wait.
func TestContextEndsTheWait(t *testing.T) {
	l := New(entryName(t), 1, 5*time.Second)
	defer l.Close()
	if err := l.Acquire(context.Background()); err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Millisecond)
	defer cancel()
	err := l.Acquire(ctx)
	var apiErr *models.APIError
	if !errors.As(err, &apiErr) || apiErr.Status != http.StatusGatewayTimeout || models.IsProviderBusy(err) {
		t.Fatalf("err %v; want the 504 gateway timeout", err)
	}
}

// TestNeverMoreThanCapInFlight: under contention the slots bound the calls.
func TestNeverMoreThanCapInFlight(t *testing.T) {
	l := New(entryName(t), 8, 5*time.Second)
	defer l.Close()
	var cur, peak atomic.Int32
	var wg sync.WaitGroup
	for i := 0; i < 200; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			if err := l.Acquire(context.Background()); err != nil {
				t.Error(err)
				return
			}
			n := cur.Add(1)
			for p := peak.Load(); n > p && !peak.CompareAndSwap(p, n); p = peak.Load() {
			}
			time.Sleep(time.Millisecond)
			cur.Add(-1)
			l.Release()
		}()
	}
	wg.Wait()
	if p := peak.Load(); p > 8 || l.InFlight() != 0 {
		t.Fatalf("peak %d in flight %d; cap 8", p, l.InFlight())
	}
}

// TestSnapshotSumsInstancesOfAnEntry: a key rotation's two instances of one
// entry add up; a closed instance leaves the gauges, its counters stay.
func TestSnapshotSumsInstancesOfAnEntry(t *testing.T) {
	entry := entryName(t)
	a, b := New(entry, 3, time.Second), New(entry, 5, time.Second)
	if err := a.Acquire(context.Background()); err != nil {
		t.Fatal(err)
	}
	if err := b.Acquire(context.Background()); err != nil {
		t.Fatal(err)
	}
	st := stateOf(t, entry)
	if st.MaxConcurrent != 8 || st.InFlight != 2 || st.Saturated {
		t.Fatalf("two instances: %+v", st)
	}
	a.Release()
	a.Close()
	if st = stateOf(t, entry); st.MaxConcurrent != 5 || st.InFlight != 1 || !st.Live {
		t.Fatalf("after one closed: %+v", st)
	}
	b.Release()
	b.Close()
	if st = stateOf(t, entry); st.Live || st.Saturated || st.Wait.Count != 2 {
		t.Fatalf("after both closed: %+v", st)
	}
}

// TestSaturatedWhileFull: an entry with every slot taken reads saturated,
// and counts in SaturatedEntries, until a slot frees.
func TestSaturatedWhileFull(t *testing.T) {
	entry := entryName(t)
	l := New(entry, 1, time.Second)
	defer l.Close()
	if err := l.Acquire(context.Background()); err != nil {
		t.Fatal(err)
	}
	if !stateOf(t, entry).Saturated || SaturatedEntries() < 1 {
		t.Fatal("a full entry is not saturated")
	}
	l.Release()
	if stateOf(t, entry).Saturated {
		t.Fatal("an idle entry with no refusal reads saturated")
	}
}
