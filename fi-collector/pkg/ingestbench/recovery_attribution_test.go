package ingestbench

import (
	"context"
	"io"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"testing"
	"time"
)

func TestFaultLossSurvivesCheckpointResume(t *testing.T) {
	c := testConfig()
	c.Rate, c.BatchSize, c.SpansPerTrace, c.VerifyChunk, c.VerifyQueryRetries = 4, 4, 1, 5, 0
	c.VerifyCheckpoint = filepath.Join(t.TempDir(), "loss.checkpoint.json")
	start := time.Now()
	store := &mockStorage{cfg: c, failChunk: 2}
	for _, seq := range []int64{1, 2, 3, 5, 9, 10} {
		store.rows = append(store.rows, storedRow(c, seq, start))
	}
	srv := httptest.NewServer(http.HandlerFunc(store.serve))
	defer srv.Close()
	c.ClickHouseURL = srv.URL
	ledger := newOutcomeLedger(4)
	for seq := int64(0); seq < 24; seq += 4 {
		o := exportOutcome{class: outcomeAcknowledged}
		if seq == 20 {
			o = exportOutcome{class: outcomePartial, rejected: 2}
		}
		if err := ledger.record(seq, o); err != nil {
			t.Fatal(err)
		}
	}
	tl := newTimeline(start)
	tl.add(start, 0, 24)
	// Intentionally unordered ends and equal ends. Request 4 lies after all
	// windows; request 5 is partial and must not enter full-acknowledgement loss.
	faults := []FaultResult{{Name: "later", EndS: 3.5}, {Name: "earlier", EndS: 1}, {Name: "equal-end", EndS: 3.5}}
	ch := clickHouse{cfg: c, client: c.readClient()}
	defer ch.client.CloseIdleConnections()
	v := verifyRun(context.Background(), ch, ledger, tl, 24, 0, nil, faults...)
	if v.Complete || v.Chunks != 1 {
		t.Fatalf("expected interrupted scan: %+v", v)
	}
	rec := RecoveryResult{Faults: append([]FaultResult(nil), faults...)}
	rec.attachVerification(c, v, BacklogResult{}, BudgetCheck{})
	for _, f := range rec.Faults {
		if f.AcknowledgedMissing != nil {
			t.Fatal("incomplete scan certified fault loss")
		}
	}
	_, v, err := ResumeVerification(context.Background(), c.VerifyCheckpoint, nil, nil)
	if err != nil || !v.Complete || v.ResumedAtChunk != 1 || v.MissingBy.AcknowledgedMissing != 16 {
		t.Fatalf("resume failed: %v %+v", err, v)
	}
	rec.attachVerification(c, v, BacklogResult{}, BudgetCheck{})
	for i, want := range []uint64{6, 4, 0} {
		if got := rec.Faults[i].AcknowledgedMissing; got == nil || *got != want {
			t.Fatalf("fault %d: got %v want %d; windows=%+v", i, got, want, v.FaultLoss)
		}
	}
	// Older checkpoints lack precise attribution; never fall back to ranges.
	v.FaultLoss = nil
	rec.attachVerification(c, v, BacklogResult{}, BudgetCheck{})
	for _, f := range rec.Faults {
		if f.AcknowledgedMissing != nil {
			t.Fatal("old result presented guessed loss as exact")
		}
	}
}

func TestFaultLossIndependentOfTruncatedRanges(t *testing.T) {
	a := verifyAccum{FaultLoss: []FaultLossWindow{{Name: "fault", EndS: 10000}}}
	for r := int64(0); r < 2*(maxMissingRanges+1); r++ {
		class := outcomeAcknowledged
		if r%2 != 0 {
			class = outcomeAmbiguous
		}
		a.trackMissing(r, class, 1, 1, 1)
	}
	a.closeRange()
	if a.RangesOmitted == 0 {
		t.Fatal("fixture did not truncate ranges")
	}
	v := Verification{Complete: true, MissingRangesOmitted: a.RangesOmitted, FaultLoss: a.FaultLoss}
	rec := RecoveryResult{Faults: []FaultResult{{Name: "fault", EndS: 10000}}}
	rec.attachVerification(testConfig(), v, BacklogResult{}, BudgetCheck{})
	if n := rec.Faults[0].AcknowledgedMissing; n == nil || *n != maxMissingRanges+1 {
		t.Fatalf("truncating diagnostic ranges lost exact attribution: %v", n)
	}
}

// Missing requests compact into one range, but their exact missing counts
// belong to distinct reporting windows. Different counts cannot be prorated.
func TestRecoveryLossCrossingFaultBoundaries(t *testing.T) {
	for _, tc := range []struct {
		name   string
		stored []int64
		want   []uint64
	}{
		{"all-missing", nil, []uint64{8, 8}},
		{"uneven-missing", []int64{1, 2, 3, 5, 9, 10}, []uint64{4, 6}},
	} {
		t.Run(tc.name, func(t *testing.T) {
			c := mockRunConfig()
			c.Rate, c.Duration, c.BatchSize, c.SpansPerTrace, c.VerifyChunk = 4, 4*time.Second, 4, 1, 5
			c.DirectProbes.Enabled, c.DurableLag = false, false
			c.DeclaredFaults = []DeclaredFault{{Name: "first", At: time.Second, Duration: 500 * time.Millisecond}, {Name: "second", At: 3 * time.Second, Duration: 500 * time.Millisecond}}
			store := &mockStorage{cfg: c}
			ch := httptest.NewServer(http.HandlerFunc(store.serve))
			defer ch.Close()
			// Seed rows only after preflight (when the first request arrives).
			collector := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				_, _ = io.Copy(io.Discard, r.Body)
				store.mu.Lock()
				if len(store.rows) == 0 {
					for _, seq := range tc.stored {
						store.rows = append(store.rows, storedRow(c, seq, time.Now()))
					}
				}
				store.mu.Unlock()
				w.Header().Set("Content-Type", "application/x-protobuf")
				w.WriteHeader(http.StatusOK)
			}))
			defer collector.Close()
			c.ClickHouseURL, c.Endpoint = ch.URL, collector.URL
			r, err := Run(context.Background(), c, Options{})
			if err != nil || !r.Verification.Complete || r.Counters.Acknowledged != 16 || r.Recovery == nil {
				t.Fatalf("run failed: err=%v verification=%+v acknowledged=%d", err, r.Verification, r.Counters.Acknowledged)
			}
			if len(r.Verification.MissingRanges) != 1 {
				t.Fatalf("expected compacted range, got %+v", r.Verification.MissingRanges)
			}
			for i, want := range tc.want {
				got := r.Recovery.Faults[i].AcknowledgedMissing
				if got == nil || *got != want {
					if got == nil {
						t.Errorf("fault %d has no loss attribution", i)
					} else {
						t.Errorf("fault %d missing=%d, want %d (global missing=%d)", i, *got, want, r.Verification.Missing)
					}
				}
			}
		})
	}
}
