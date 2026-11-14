package ingestbench

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"reflect"
	"testing"
	"time"
)

// A query failure inside a partially acknowledged request must still produce
// an importable diagnostic. Snapshot attribution must not alter the checkpoint.
func TestPartialRequestInterruptedReceiptAndResume(t *testing.T) {
	c := mockRunConfig()
	c.BatchSize, c.VerifyChunk, c.VerifyQueryRetries = 12, 8, 0
	c.DirectProbes.Enabled, c.DurableLag = false, false
	c.VerifyCheckpoint = filepath.Join(t.TempDir(), "verify.json")
	store := &mockStorage{cfg: c, failChunk: 2}
	ch := httptest.NewServer(http.HandlerFunc(store.serve))
	defer ch.Close()
	otlp := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"partialSuccess":{"rejectedSpans":"2"}}`))
	}))
	defer otlp.Close()
	c.Encoding, c.ClickHouseURL, c.Endpoint = EncodingJSON, ch.URL, otlp.URL
	store.cfg = c
	r, err := Run(context.Background(), c, Options{})
	if err != nil {
		t.Fatal(err)
	}
	v := r.Verification
	if r.Status != "incomplete" || v.Complete || v.DataLossDetected || v.ContractViolation || v.ScannedAttempted != 8 || v.Unscanned != 4 || v.Missing != 12 {
		t.Fatalf("interrupted result: %s %+v", r.Status, v)
	}
	// Reserve the whole request's rejected count before bounding loss in the
	// scanned prefix; none of the unscanned suffix is attributed to loss.
	want := MissingByAcknowledgement{AcknowledgedMissing: 6, PartialBeyondRejected: 6, PartialRejectedMissing: 2}
	if v.MissingBy != want {
		t.Errorf("snapshot attribution: %+v, want %+v", v.MissingBy, want)
	}
	assertDiagnostic := func(r Result) {
		t.Helper()
		raw, err := json.Marshal(r)
		if err != nil {
			t.Fatal(err)
		}
		var imported Result
		if err := json.Unmarshal(raw, &imported); err != nil {
			t.Fatal(err)
		}
		var out bytes.Buffer
		if err := Report(&out, imported); err != nil || out.Len() == 0 {
			t.Fatalf("report cannot render Run diagnostic: %v", err)
		}
		p, err := Publish(imported, PublishOptions{MinSustained: time.Nanosecond})
		if err != nil || p.Headline.Status != "withheld" {
			t.Fatalf("publish cannot retain diagnostic: %+v %v", p.Headline, err)
		}
	}
	assertDiagnostic(r)

	// Fail the next attempt at the same chunk too: neither rendering nor a
	// second incomplete snapshot may commit provisional counts to the ledger.
	store.mu.Lock()
	store.failChunk = store.chunkQueries + 1
	store.mu.Unlock()
	_, retry, err := ResumeVerification(context.Background(), c.VerifyCheckpoint, nil, nil)
	if err != nil || retry.Complete || retry.MissingBy != want {
		t.Fatalf("repeated interruption: %+v %v", retry, err)
	}
	r.Verification = retry
	assertDiagnostic(r)

	_, resumed, err := ResumeVerification(context.Background(), c.VerifyCheckpoint, nil, nil)
	want = MissingByAcknowledgement{AcknowledgedMissing: 10, PartialBeyondRejected: 10, PartialRejectedMissing: 2}
	if err != nil || !resumed.Complete || resumed.ResumedAtChunk != 1 || resumed.Unscanned != 0 || resumed.MissingBy != want || !resumed.DataLossDetected {
		t.Fatalf("resumed verification: %+v %v", resumed, err)
	}
	r.Verification = resumed
	assertDiagnostic(r)

	// An uninterrupted scan over the same outcomes must give the same answer.
	ledger := newOutcomeLedger(12)
	if err := ledger.record(0, exportOutcome{class: outcomePartial, rejected: 2}); err != nil {
		t.Fatal(err)
	}
	controlConfig := c
	controlConfig.VerifyCheckpoint = ""
	timeline := newTimeline(r.Started)
	timeline.add(r.Started, 0, 12)
	client := c.readClient()
	defer client.CloseIdleConnections()
	control := verifyRun(context.Background(), clickHouse{cfg: controlConfig, client: client}, ledger, timeline, 12, 0, nil)
	if !control.Complete || control.MissingBy != resumed.MissingBy || control.Missing != resumed.Missing || control.PersistedBy != resumed.PersistedBy || !reflect.DeepEqual(control.MissingRanges, resumed.MissingRanges) {
		t.Fatalf("resume differs from uninterrupted control: %+v vs %+v", resumed, control)
	}
	_, again, err := ResumeVerification(context.Background(), c.VerifyCheckpoint, nil, nil)
	if err != nil || again.MissingBy != resumed.MissingBy || again.Missing != resumed.Missing {
		t.Fatalf("completed checkpoint changed attribution: %+v %v", again, err)
	}
}

func TestPartialSnapshotBoundsWithoutMutatingCheckpoint(t *testing.T) {
	for _, prefix := range []int64{0, 12} {
		for _, rejected := range []int64{2, 10} {
			for present := int64(0); present <= 8; present++ {
				t.Run(fmt.Sprintf("prefix=%d/rejected=%d/present=%d", prefix, rejected, present), func(t *testing.T) {
					c := testConfig()
					c.BatchSize, c.VerifyChunk = 12, 8
					ledger := newOutcomeLedger(12)
					r := prefix / 12
					if err := ledger.record(prefix, exportOutcome{class: outcomePartial, rejected: rejected}); err != nil {
						t.Fatal(err)
					}
					cp := newCheckpoint(c, prefix+12, ledger, newTimeline(time.Now()))
					// Use a scanned prefix ending eight spans into the open request.
					cp.Chunks = []chunkSpec{{Lo: 0, Hi: prefix + 8}}
					cp.NextChunk = 1
					cp.Accum.Expected[outcomePartial] = uint64(prefix + 8)
					cp.Accum.Present[outcomePartial] = uint64(present)
					cp.Accum.Rows = uint64(present)
					cp.Accum.PartialOpen[r] = present
					if prefix > 0 {
						// A completed, fully missing partial request precedes this one.
						cp.Accum.PartialLoss, cp.Accum.PartialRejected = 10, 2
					}
					before, err := json.Marshal(cp)
					if err != nil {
						t.Fatal(err)
					}
					vf := verifier{cp: cp, ledger: ledger}
					var v, again Verification
					vf.fill(&v)
					vf.fill(&again)
					missing := uint64(8 - present)
					loss := cp.Accum.PartialLoss + uint64(max(0, int64(missing)-rejected))
					covered := cp.Accum.PartialRejected + min(missing, uint64(rejected))
					if v.MissingBy != (MissingByAcknowledgement{AcknowledgedMissing: loss, PartialBeyondRejected: loss, PartialRejectedMissing: covered}) || v.Unscanned != 4 || v.Missing != loss+covered+4 || v.PersistedBy.PartialExcess != uint64(max(0, present-(12-rejected))) || v.DataLossDetected || v.ContractViolation {
						t.Fatalf("not conservative scanned-prefix bounds: %+v", v)
					}
					after, err := json.Marshal(cp)
					if err != nil || !bytes.Equal(before, after) || !reflect.DeepEqual(v, again) {
						t.Fatalf("snapshot mutated resumable state: %v", err)
					}
				})
			}
		}
	}
}
