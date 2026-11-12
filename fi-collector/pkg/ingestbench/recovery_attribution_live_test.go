package ingestbench

import (
	"context"
	"testing"
	"time"
)

// Real storage/query proof, with synthetic request acknowledgements: unequal
// per-request missing counts cross both chunk and fault reporting boundaries.
func TestLiveClickHouseFaultLossAttribution(t *testing.T) {
	lt := requireLiveTarget(t)
	c := lt.apply(testConfig())
	c.RunID, c.ClickHouseURL = NewRunID(), lt.url
	c.Rate, c.BatchSize, c.SpansPerTrace, c.VerifyChunk = 4, 4, 1, 5
	start := time.Now()
	ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
	defer cancel()
	writer := lt.fixtureWriter(t)
	var rows []map[string]any
	for _, seq := range []int64{1, 2, 3, 5, 9, 10} {
		rows = append(rows, storedRow(c, seq, start))
	}
	if err := writer.Insert(ctx, rows); err != nil {
		t.Fatal(err)
	}
	ledger := newOutcomeLedger(4)
	for seq := int64(0); seq < 16; seq += 4 {
		if err := ledger.record(seq, exportOutcome{class: outcomeAcknowledged}); err != nil {
			t.Fatal(err)
		}
	}
	tl := newTimeline(start)
	tl.add(start, 0, 16)
	faults := []FaultResult{{Name: "first", EndS: 1.5}, {Name: "second", EndS: 3.5}}
	ch := clickHouse{cfg: c, client: c.readClient(), headers: lt.headers}
	defer ch.client.CloseIdleConnections()
	v := verifyRun(ctx, ch, ledger, tl, 16, 0, nil, faults...)
	if !v.Complete || v.MissingBy.AcknowledgedMissing != 10 || len(v.MissingRanges) != 1 {
		t.Fatalf("verification failed: %+v", v)
	}
	r := RecoveryResult{Faults: faults}
	r.attachVerification(c, v, BacklogResult{}, BudgetCheck{})
	for i, want := range []uint64{4, 6} {
		if got := r.Faults[i].AcknowledgedMissing; got == nil || *got != want {
			t.Fatalf("fault %d missing=%v want=%d windows=%+v", i, got, want, v.FaultLoss)
		}
	}
	t.Logf("real ClickHouse: 6 stored of 16 expected identities, missing attributed 4/6 across two windows; synthetic acknowledgements, run_id=%s", c.RunID)
}
